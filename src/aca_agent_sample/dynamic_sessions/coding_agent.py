"""Coding tools bind a session in the host, never in model-supplied arguments."""
from __future__ import annotations

import json
import secrets
from typing import Any

from ..agent_loop import AgentResult, run_agent
from ..model_client import ModelAdapter
from ..tool_policy import ToolPolicy, ToolPolicyError, _forbidden, _reject_constant, _unique_object
from ..types import ToolExecutionResult
from .client import DynamicSessionsError, SessionStateLost, _path
from .session_registry import SessionRegistry


class _CodingPolicy:
    def __init__(self) -> None:
        self._limits = ToolPolicy(allowed_tools=("execute_python",))
        self.max_input_length = self._limits.max_input_length
        self.max_response_length = self._limits.max_response_length
        self.max_timeout = self._limits.max_timeout

    def truncate(self, text: str) -> str: return self._limits.truncate(text)

    def schemas(self) -> list[dict[str, Any]]:
        specs = {
            "execute_python": ("Run Python code in the active session.", {"code": {"type": "string"}}),
            "upload_text_file": ("Write UTF-8 text to a session file under /mnt/data.", {"path": {"type": "string"}, "content": {"type": "string"}}),
            "read_text_file": ("Read a UTF-8 file under /mnt/data.", {"path": {"type": "string"}}),
            "list_session_files": ("List files in the active session.", {}),
        }
        return [{"type": "function", "function": {"name": name, "description": desc, "strict": True,
                    "parameters": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}}}
                for name, (desc, props) in specs.items()]

    def validate(self, name: str, arguments: str) -> dict[str, Any]:
        expected = {"execute_python": {"code"}, "upload_text_file": {"path", "content"},
                    "read_text_file": {"path"}, "list_session_files": set()}
        if name not in expected or not isinstance(arguments, str) or len(arguments) > self._limits.max_arguments_length:
            raise ToolPolicyError("Tool or arguments are invalid")
        try:
            args = json.loads(arguments, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            if not isinstance(args, dict) or _forbidden(args) or set(args) != expected[name]: raise ValueError()
            if any(not isinstance(value, str) for value in args.values()): raise ValueError()
            if "path" in args: _path(args["path"])
            if "code" in args and (not args["code"].strip() or len(args["code"]) > self._limits.max_code_length): raise ValueError()
            if len(args.get("content", "")) > self._limits.max_code_length: raise ValueError()
        except (ValueError, RecursionError):
            raise ToolPolicyError("Tool arguments do not match schema") from None
        return args


class _BoundExecutor:
    def __init__(self, client: Any, session_id: str, ensure_state: Any):
        self.client, self.session_id, self.ensure_state = client, session_id, ensure_state

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        try:
            self.ensure_state()
            if name == "execute_python":
                return self.client.execute_python(self.session_id, arguments["code"], arguments["timeout"])
            if name == "upload_text_file":
                self.client.upload_file(self.session_id, arguments["path"], arguments["content"].encode("utf-8"))
                return ToolExecutionResult("succeeded", stdout="File uploaded", exit_code=0)
            if name == "read_text_file":
                data = self.client.download_file(self.session_id, arguments["path"])
                return ToolExecutionResult("succeeded", stdout=data.decode("utf-8"), exit_code=0)
            if name == "list_session_files":
                listing = self.client.list_files(self.session_id)
                return ToolExecutionResult("succeeded", stdout=json.dumps(listing, ensure_ascii=False), exit_code=0)
            return ToolExecutionResult("failed", stderr="Unknown tool", error_kind="schema_mismatch")
        except SessionStateLost as exc:
            return ToolExecutionResult("failed", stderr=str(exc), error_kind="state_lost")
        except DynamicSessionsError as exc:
            return ToolExecutionResult("failed", stderr=str(exc), error_kind=exc.error_kind)
        except UnicodeDecodeError:
            return ToolExecutionResult("failed", stderr="File is not UTF-8", error_kind="execution_failed")


class _ConversationModel:
    """Keep complete prior user/tool turns, never an orphaned tool call."""
    def __init__(self, model: ModelAdapter):
        self.model = model
        self.history: list[dict[str, Any]] = []
        self.current: list[dict[str, Any]] = []

    def complete(self, messages: Any, tools: Any) -> Any:
        combined = self.history + list(messages)
        if len(json.dumps(combined, ensure_ascii=False)) > 150000:
            raise ValueError("Conversation transcript exceeds limit; start a new conversation")
        answer = self.model.complete(combined, tools)
        self.current = list(messages) + [answer.as_message()]
        return answer

    def finish(self, completed: bool) -> None:
        if completed:
            self.history.extend(self.current)
        self.current = []

class DynamicSessionsCodingAgent:
    def __init__(self, model: ModelAdapter, client: Any, registry: SessionRegistry, *, owner: str,
                 conversation_id: str, session_id: str | None = None):
        self.model, self.client, self.registry = _ConversationModel(model), client, registry
        self.owner, self.conversation_id = owner, conversation_id
        self.session_id = registry.get_or_create(owner, conversation_id, session_id)
        self._marker = "_aca_state_" + secrets.token_hex(16)
        self._initialized = False
        self.policy = _CodingPolicy()
        self.executor = _BoundExecutor(client, self.session_id, self._ensure_state)

    def _ensure_state(self) -> None:
        self.registry.require_owner(self.owner, self.conversation_id, self.session_id)
        if not self._initialized:
            result = self.client.execute_python(self.session_id, self._marker + " = True")
            if result.status != "succeeded":
                raise DynamicSessionsError(result.error_kind or "execution_failed", "Session initialization failed")
            self._initialized = True
        else:
            code = f"if '{self._marker}' in globals():\n    print('ACA_STATE_PRESENT')\nelse:\n    print('ACA_STATE_LOST')"
            result = self.client.execute_python(self.session_id, code)
            if result.status != "succeeded":
                raise DynamicSessionsError(result.error_kind or "execution_failed", "Session state probe failed")
            if result.stdout.strip() != "ACA_STATE_PRESENT":
                raise SessionStateLost("Session state has expired or was lost; previous state cannot be restored")

    def run_turn(self, prompt: str, *, max_turns: int = 8) -> AgentResult:
        try:
            self._ensure_state()
        except SessionStateLost:
            return AgentResult("state_lost", "Session state lost; start a new conversation.", (), 0)
        except DynamicSessionsError as exc:
            return AgentResult(exc.error_kind, str(exc), (), 0)
        self.model.current = []
        try:
            result = run_agent(self.model, self.executor, self.policy, prompt, max_turns=max_turns)
        except Exception:
            self.model.finish(False)
            raise
        self.model.finish(result.status == "completed")
        return result


