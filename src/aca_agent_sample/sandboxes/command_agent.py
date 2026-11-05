from __future__ import annotations

import json
from typing import Any

from ..agent_loop import AgentResult, run_agent
from ..model_client import ModelAdapter
from ..tool_policy import ToolPolicy, ToolPolicyError, _forbidden, _reject_constant, _unique_object
from ..types import ToolExecutionResult


class SandboxToolPolicy(ToolPolicy):
    """The model cannot choose the sandbox or any connection information."""

    def schemas(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": "run_sandbox_command",
            "description": "Execute a command in the host-owned sandbox. This is not a host shell.",
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string"}, "working_directory": {"type": "string", "default": "/workspace"}},
                "required": ["command"], "additionalProperties": False}}}]

    def validate(self, name: str, arguments: str) -> dict[str, Any]:
        if name != "run_sandbox_command":
            raise ToolPolicyError("Tool is not allowed")
        if not isinstance(arguments, str) or len(arguments) > self.max_arguments_length:
            raise ToolPolicyError("Tool arguments are too large or invalid")
        try:
            parsed = json.loads(arguments, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            if not isinstance(parsed, dict) or _forbidden(parsed):
                raise ToolPolicyError("Forbidden or invalid tool arguments")
        except (ValueError, RecursionError):
            raise ToolPolicyError("Invalid tool arguments") from None
        if set(parsed) - {"command", "working_directory"}:
            raise ToolPolicyError("Tool arguments do not match schema")
        command = parsed.get("command")
        directory = parsed.get("working_directory", "/workspace")
        if (not isinstance(command, str) or not command.strip() or len(command) > self.max_command_length
                or not isinstance(directory, str) or not directory.startswith("/")
                or len(directory) > 1024 or "\x00" in directory):
            raise ToolPolicyError("Invalid command or working directory")
        return {"command": command, "working_directory": directory}


class _Executor:
    def __init__(self, adapter: Any, sandbox_id: str, pause_between_commands: bool):
        self.adapter, self.sandbox_id = adapter, sandbox_id
        self.pause_between_commands = pause_between_commands
        self.paused = False

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        if self.paused:
            self.adapter.resume(self.sandbox_id)
            self.paused = False
        result = self.adapter.exec(self.sandbox_id, arguments["command"],
                                   working_directory=arguments["working_directory"])
        if self.pause_between_commands and result.error_kind not in {"outcome_unknown", "timeout", "state_lost"}:
            self.adapter.stop(self.sandbox_id)
            self.paused = True
        return result


class SandboxCommandAgent:
    def __init__(self, model: ModelAdapter, adapter: Any, *, mode: str = "Memory",
                 auto_suspend_seconds: int = 300, pause_between_commands: bool = False,
                 policy: SandboxToolPolicy | None = None):
        self.model, self.adapter = model, adapter
        self.mode, self.auto_suspend_seconds = mode, auto_suspend_seconds
        self.pause_between_commands = pause_between_commands
        self.policy = policy or SandboxToolPolicy()

    def run(self, prompt: str, *, max_turns: int = 8) -> AgentResult:
        previous_ids = set(self.adapter.sandboxes)
        try:
            sandbox_id = self.adapter.create(mode=self.mode, auto_suspend_seconds=self.auto_suspend_seconds)
            self.adapter.set_lifecycle_policy(sandbox_id, mode=self.mode, interval=self.auto_suspend_seconds)
            executor = _Executor(self.adapter, sandbox_id, self.pause_between_commands)
            return run_agent(self.model, executor, self.policy, prompt, max_turns=max_turns)
        finally:
            failures = []
            for sandbox_id in set(self.adapter.sandboxes) - previous_ids:
                try:
                    self.adapter.delete(sandbox_id)
                except Exception as exc:
                    failures.append(exc)
            if failures:
                raise ExceptionGroup("Sandbox cleanup failed; ownership retained", failures)

