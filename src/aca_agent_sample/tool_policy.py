from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class ToolPolicyError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ToolPolicyError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ToolPolicyError("Invalid JSON constant")


def _forbidden(value: Any) -> bool:
    forbidden = {"endpoint", "subscription_id", "tenant_id", "token", "api_key", "credential", "credentials", "session_id", "sandbox_id", "resource_group"}
    if isinstance(value, dict):
        return any(key.casefold() in forbidden or _forbidden(child) for key, child in value.items())
    if isinstance(value, list):
        return any(_forbidden(child) for child in value)
    return False


@dataclass(frozen=True)
class ToolPolicy:
    """Character limits; the response limit includes the serialized assistant message."""
    allowed_tools: tuple[str, ...] = ("execute_python", "run_command")
    max_code_length: int = 12000
    max_command_length: int = 4000
    max_timeout: int = 60
    max_output_length: int = 10000
    max_arguments_length: int = 50000
    max_input_length: int = 12000
    max_response_length: int = 50000

    def __post_init__(self) -> None:
        if (not self.allowed_tools or len(set(self.allowed_tools)) != len(self.allowed_tools)
                or set(self.allowed_tools) - {"execute_python", "run_command"}):
            raise ValueError("Invalid tool allowlist")
        if any(type(n) is not int or n <= 0 for n in (self.max_code_length, self.max_command_length, self.max_timeout, self.max_output_length, self.max_arguments_length, self.max_input_length, self.max_response_length)):
            raise ValueError("Policy limits must be positive integers")
        if self.max_timeout > 220:
            raise ValueError("Timeout exceeds the code interpreter maximum")

    def schemas(self) -> list[dict[str, Any]]:
        schemas = []
        for name in self.allowed_tools:
            field = "code" if name == "execute_python" else "command"
            properties: dict[str, Any] = {field: {"type": "string"}}
            required = [field]
            if name == "run_command":
                properties["timeout"] = {"type": "integer", "minimum": 1, "maximum": self.max_timeout}
                required.append("timeout")
            schemas.append({"type": "function", "function": {"name": name,
                "description": "Execute in the configured remote isolation boundary.",
                "strict": True, "parameters": {"type": "object", "properties": properties,
                                                "required": required, "additionalProperties": False}}})
        return schemas

    def validate(self, name: str, arguments: str) -> dict[str, Any]:
        if name not in self.allowed_tools:
            raise ToolPolicyError("Tool is not allowed")
        if not isinstance(arguments, str) or len(arguments) > self.max_arguments_length:
            raise ToolPolicyError("Tool arguments are too large or invalid")
        try:
            parsed = json.loads(arguments, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        except (ValueError, RecursionError):
            raise ToolPolicyError("Invalid tool argument JSON") from None
        if not isinstance(parsed, dict):
            raise ToolPolicyError("Tool arguments must be a JSON object")
        try:
            if _forbidden(parsed):
                raise ToolPolicyError("Forbidden tool argument")
        except RecursionError:
            raise ToolPolicyError("Tool arguments are too deeply nested") from None
        key = "code" if name == "execute_python" else "command"
        expected = {key} if name == "execute_python" else {key, "timeout"}
        if set(parsed) != expected or not isinstance(parsed.get(key), str) or not parsed[key].strip():
            raise ToolPolicyError("Tool arguments do not match schema")
        limit = self.max_code_length if name == "execute_python" else self.max_command_length
        if len(parsed[key]) > limit:
            raise ToolPolicyError("Code or command length exceeds policy")
        if name == "run_command" and (type(parsed["timeout"]) is not int or not 1 <= parsed["timeout"] <= self.max_timeout):
            raise ToolPolicyError("Timeout exceeds policy or is invalid")
        return parsed

    def truncate(self, text: str) -> str:
        if len(text) <= self.max_output_length:
            return text
        marker = "...[truncated]"
        return text[:max(0, self.max_output_length - len(marker))] + marker[:self.max_output_length]
