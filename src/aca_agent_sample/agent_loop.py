from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .model_client import ModelAdapter
from .tool_policy import ToolPolicy, ToolPolicyError
from .types import ToolExecutionResult


class ToolExecutor(Protocol):
    """Use the trusted timeout as a deadline; an uncertain outcome must not be retried."""
    def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult: ...


@dataclass(frozen=True)
class ToolTurn:
    turn: int
    call_id: str
    tool_name: str
    result: ToolExecutionResult


@dataclass(frozen=True)
class AgentResult:
    status: str
    final_response: str | None
    turns: tuple[ToolTurn, ...]
    model_turns: int


def run_agent(model: ModelAdapter, executor: ToolExecutor, policy: ToolPolicy,
              prompt: str, *, max_turns: int = 8) -> AgentResult:
    """Bound local transcript sizes and stop rather than retry uncertain executions."""
    if type(max_turns) is not int or max_turns < 1:
        raise ValueError("max_turns must be a positive integer")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > policy.max_input_length:
        raise ValueError("The prompt must be nonempty and within the input limit")
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    history: list[ToolTurn] = []
    seen_ids: set[str] = set()
    for turn in range(1, max_turns + 1):
        response = model.complete(messages, policy.schemas())
        message = response.as_message()
        if len(json.dumps(message, ensure_ascii=False)) > policy.max_response_length:
            raise ValueError("Model response exceeds the response limit")
        if response.content is not None:
            message["content"] = policy.truncate(response.content)
        messages.append(message)
        if not response.tool_calls:
            return AgentResult("completed", message["content"] or "", tuple(history), turn)
        if len(history) + len(response.tool_calls) > max_turns:
            return AgentResult("max_tool_calls", None, tuple(history), turn)
        ids = [call.id for call in response.tool_calls]
        if not all(ids) or len(set(ids)) != len(ids) or any(id in seen_ids for id in ids):
            raise ValueError("Invalid or repeated model tool call ID")
        seen_ids.update(ids)
        for call in response.tool_calls:
            try:
                arguments = policy.validate(call.name, call.arguments)
            except ToolPolicyError as exc:
                result = ToolExecutionResult("failed", stderr=str(exc), error_kind="schema_mismatch")
            else:
                if call.name == "execute_python":
                    arguments["timeout"] = policy.max_timeout
                try:
                    result = executor.execute(call.name, arguments)
                    if not isinstance(result, ToolExecutionResult):
                        raise TypeError("Invalid tool result")
                except Exception:
                    # A transport failure may happen after a non-idempotent operation started.
                    result = ToolExecutionResult("failed", stderr="Execution outcome is unknown", error_kind="outcome_unknown")
            result = ToolExecutionResult(result.status, policy.truncate(result.stdout), policy.truncate(result.stderr),
                                         result.exit_code, result.error_kind, result.metadata)
            history.append(ToolTurn(turn, call.id, call.name, result))
            # Metadata may contain host-only resource identifiers, so it is never sent to the model.
            payload = {"status": result.status, "stdout": result.stdout, "stderr": result.stderr,
                       "exit_code": result.exit_code, "error_kind": result.error_kind}
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(payload, ensure_ascii=False)})
            if result.error_kind in {"outcome_unknown", "timeout", "state_lost"}:
                return AgentResult(result.error_kind, None, tuple(history), turn)
    return AgentResult("max_turns", None, tuple(history), max_turns)


