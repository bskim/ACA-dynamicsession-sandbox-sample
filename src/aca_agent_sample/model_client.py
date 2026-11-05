from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from openai import OpenAI


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ModelResponse:
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    def as_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
                for call in self.tool_calls
            ]
        return message


class ModelAdapter(Protocol):
    def complete(self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]) -> ModelResponse: ...


class FoundryOpenAIAdapter:
    def __init__(self, base_url: str, deployment: str, token_provider: Callable[[], str], *, client: Any = None):
        from .config import validate_foundry_base_url
        validate_foundry_base_url(base_url)
        if not deployment:
            raise ValueError("Model deployment is required")
        self.deployment = deployment
        self.client = client if client is not None else OpenAI(
            base_url=base_url, api_key=token_provider, max_retries=0, timeout=60,
        )

    def complete(self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]) -> ModelResponse:
        params: dict[str, Any] = {"model": self.deployment, "messages": list(messages)}
        if tools:
            params.update(tools=list(tools), parallel_tool_calls=False)
        response = self.client.chat.completions.create(**params)
        if not response.choices:
            raise ValueError("Model returned no choices")
        message = response.choices[0].message
        calls = []
        for call in message.tool_calls or []:
            if call.type != "function" or not call.id or not call.function.name or not isinstance(call.function.arguments, str):
                raise ValueError("Model returned an unsupported tool call")
            calls.append(ToolCall(call.id, call.function.name, call.function.arguments))
        if message.content is not None and not isinstance(message.content, str):
            raise ValueError("Model returned unsupported content")
        return ModelResponse(message.content, tuple(calls))

    def close(self) -> None:
        self.client.close()


class FakeModelAdapter:
    def __init__(self, responses: Sequence[ModelResponse]):
        self._responses = iter(responses)
        self.requests: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

    def complete(self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]) -> ModelResponse:
        self.requests.append((list(messages), list(tools)))
        try:
            return next(self._responses)
        except StopIteration:
            raise RuntimeError("Fake model response sequence exhausted") from None
