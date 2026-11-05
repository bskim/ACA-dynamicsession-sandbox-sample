import json
from types import SimpleNamespace

import pytest

from aca_agent_sample.agent_loop import run_agent
from aca_agent_sample.model_client import FakeModelAdapter, FoundryOpenAIAdapter, ModelResponse, ToolCall
from aca_agent_sample.tool_policy import ToolPolicy
from aca_agent_sample.types import ToolExecutionResult


def response(id, code):
    return ModelResponse(tool_calls=(ToolCall(id, "execute_python", json.dumps({"code": code})),))


class Executor:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return next(self.results)


def test_failed_code_can_be_corrected_in_later_model_turn():
    model = FakeModelAdapter([response("1", "assert False"), response("2", "assert True"), ModelResponse("Success")])
    executor = Executor([ToolExecutionResult("failed", stderr="AssertionError", error_kind="execution_failed"),
                         ToolExecutionResult("succeeded", stdout="pass", exit_code=0, metadata={"session_id": "host-only"})])
    result = run_agent(model, executor, ToolPolicy(), "test")
    assert result.status == "completed" and result.final_response == "Success"
    assert result.model_turns == 3 and len(executor.calls) == 2
    assert executor.calls[0][1]["timeout"] == 60
    messages, _ = model.requests[2]
    tools = [m for m in messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["1", "2"]
    assert json.loads(tools[0]["content"])["stderr"] == "AssertionError"
    assert "host-only" not in tools[1]["content"]


def test_max_turns_stops_before_another_model_call():
    model = FakeModelAdapter([response("1", "x"), response("2", "y")])
    executor = Executor([ToolExecutionResult("succeeded"), ToolExecutionResult("succeeded")])
    result = run_agent(model, executor, ToolPolicy(), "test", max_turns=2)
    assert result.status == "max_turns" and len(model.requests) == 2 and len(executor.calls) == 2


@pytest.mark.parametrize("kind", ["outcome_unknown", "timeout", "state_lost"])
def test_ambiguous_or_lost_state_never_reexecutes(kind):
    model = FakeModelAdapter([response("1", "do_something()"), response("2", "do_something()")])
    executor = Executor([ToolExecutionResult("failed", error_kind=kind)])
    result = run_agent(model, executor, ToolPolicy(), "test")
    assert result.status == kind and len(executor.calls) == 1 and len(model.requests) == 1


def test_invalid_tool_is_feedback_not_execution():
    model = FakeModelAdapter([ModelResponse(tool_calls=(ToolCall("1", "evil", '{}'),)), ModelResponse("Done")])
    executor = Executor([])
    result = run_agent(model, executor, ToolPolicy(), "test")
    assert result.status == "completed" and result.turns[0].result.error_kind == "schema_mismatch"
    assert not executor.calls


def test_executor_exception_is_unknown_and_suppressed():
    class Broken:
        def execute(self, name, arguments):
            raise RuntimeError("sensitive value")
    result = run_agent(FakeModelAdapter([response("1", "x")]), Broken(), ToolPolicy(), "test")
    assert result.status == "outcome_unknown"
    assert "sensitive value" not in result.turns[0].result.stderr


def test_output_is_bounded_before_feedback():
    model = FakeModelAdapter([response("1", "x"), ModelResponse("Done")])
    executor = Executor([ToolExecutionResult("succeeded", stdout="x" * 100)])
    result = run_agent(model, executor, ToolPolicy(max_output_length=20), "test")
    assert len(result.turns[0].result.stdout) == 20
    assert len(json.loads(model.requests[1][0][-1]["content"])["stdout"]) == 20


def test_duplicate_tool_ids_rejected():
    model = FakeModelAdapter([response("1", "x"), response("1", "x")])
    executor = Executor([ToolExecutionResult("succeeded")])
    with pytest.raises(ValueError, match="repeated"):
        run_agent(model, executor, ToolPolicy(), "test")
    assert len(executor.calls) == 1


def test_foundry_adapter_normalizes_tool_calls_without_network():
    captured = []
    function = SimpleNamespace(name="execute_python", arguments='{"code":"1"}')
    call = SimpleNamespace(type="function", id="call-1", function=function)
    message = SimpleNamespace(content=None, tool_calls=[call])
    def create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    adapter = FoundryOpenAIAdapter("https://example.test/openai/v1/", "deployment", lambda: "token", client=client)
    result = adapter.complete([{"role": "user", "content": "Hi"}], ToolPolicy().schemas())
    assert result.tool_calls == (ToolCall("call-1", "execute_python", '{"code":"1"}'),)
    assert captured[0]["model"] == "deployment" and captured[0]["parallel_tool_calls"] is False


def test_foundry_client_disables_retries_and_uses_provider(monkeypatch):
    captured = {}
    def factory(**kwargs):
        captured.update(kwargs)
        return object()
    monkeypatch.setattr("aca_agent_sample.model_client.OpenAI", factory)
    provider = lambda: "token"
    FoundryOpenAIAdapter("https://example.test/openai/v1/", "deployment", provider)
    assert captured == {"base_url": "https://example.test/openai/v1/", "api_key": provider, "max_retries": 0, "timeout": 60}


def test_unknown_stops_remaining_calls_in_same_response():
    calls = (ToolCall("1", "execute_python", '{"code":"a"}'), ToolCall("2", "execute_python", '{"code":"b"}'))
    model = FakeModelAdapter([ModelResponse(tool_calls=calls)])
    executor = Executor([ToolExecutionResult("failed", error_kind="outcome_unknown"), ToolExecutionResult("succeeded")])
    assert run_agent(model, executor, ToolPolicy(), "test").status == "outcome_unknown"
    assert len(executor.calls) == 1



def test_oversized_tool_batch_is_rejected_before_execution():
    calls = tuple(ToolCall(str(i), "execute_python", '{"code":"x"}') for i in range(3))
    executor = Executor([])
    result = run_agent(FakeModelAdapter([ModelResponse(tool_calls=calls)]), executor, ToolPolicy(), "test", max_turns=2)
    assert result.status == "max_tool_calls" and not executor.calls


def test_duplicate_ids_in_same_response_are_rejected_before_execution():
    calls = (ToolCall("1", "execute_python", '{"code":"a"}'), ToolCall("1", "execute_python", '{"code":"b"}'))
    executor = Executor([])
    with pytest.raises(ValueError, match="repeated"):
        run_agent(FakeModelAdapter([ModelResponse(tool_calls=calls)]), executor, ToolPolicy(), "test")
    assert not executor.calls


@pytest.mark.parametrize("prompt", ["", "   ", None, "x" * 12001])
def test_invalid_or_oversized_prompt_never_reaches_model(prompt):
    model = FakeModelAdapter([ModelResponse("unused")])
    executor = Executor([])
    with pytest.raises(ValueError, match="prompt"):
        run_agent(model, executor, ToolPolicy(), prompt)
    assert not model.requests and not executor.calls


def test_final_model_output_is_bounded():
    result = run_agent(FakeModelAdapter([ModelResponse("x" * 100)]), Executor([]),
                       ToolPolicy(max_output_length=20), "test")
    assert result.status == "completed" and len(result.final_response) == 20


def test_intermediate_model_content_is_bounded_before_feedback():
    call = ToolCall("1", "execute_python", '{"code":"x"}')
    model = FakeModelAdapter([ModelResponse("x" * 100, (call,)), ModelResponse("Done")])
    executor = Executor([ToolExecutionResult("succeeded")])
    run_agent(model, executor, ToolPolicy(max_output_length=20), "test")
    assert len(model.requests[1][0][1]["content"]) == 20


def test_oversized_model_message_is_rejected_before_execution():
    call = ToolCall("1", "execute_python", '{"code":"x"}')
    model = FakeModelAdapter([ModelResponse("x" * 50001, (call,))])
    executor = Executor([])
    with pytest.raises(ValueError, match="response"):
        run_agent(model, executor, ToolPolicy(), "test")
    assert not executor.calls


def test_invalid_execution_status_stops_without_retry():
    class Broken:
        def __init__(self):
            self.calls = 0

        def execute(self, name, arguments):
            self.calls += 1
            return ToolExecutionResult("unknown")

    model = FakeModelAdapter([response("1", "non_idempotent()"), response("2", "non_idempotent()")])
    executor = Broken()
    result = run_agent(model, executor, ToolPolicy(), "test")
    assert result.status == "outcome_unknown"
    assert executor.calls == 1 and len(model.requests) == 1


@pytest.mark.parametrize("arguments", ['{broken', '{"code":"x","code":"y"}',
                                        '{"code":"x","subscription_id":"other"}'])
def test_invalid_json_and_authority_fields_never_dispatch(arguments):
    model = FakeModelAdapter([
        ModelResponse(tool_calls=(ToolCall("1", "execute_python", arguments),)),
        ModelResponse("Rejected"),
    ])
    executor = Executor([])
    result = run_agent(model, executor, ToolPolicy(), "test")
    assert result.turns[0].result.error_kind == "schema_mismatch"
    assert not executor.calls


@pytest.mark.parametrize("max_turns", [0, -1, True, 1.5])
def test_invalid_turn_limit_never_reaches_model(max_turns):
    model = FakeModelAdapter([ModelResponse("unused")])
    with pytest.raises(ValueError, match="max_turns"):
        run_agent(model, Executor([]), ToolPolicy(), "test", max_turns=max_turns)
    assert not model.requests


def test_oversized_invalid_arguments_are_not_relayed_or_executed():
    call = ToolCall("1", "execute_python", "x" * 50001)
    model = FakeModelAdapter([ModelResponse(tool_calls=(call,))])
    executor = Executor([])
    with pytest.raises(ValueError, match="response"):
        run_agent(model, executor, ToolPolicy(), "test")
    assert len(model.requests) == 1 and not executor.calls


def test_foundry_adapter_rejects_project_endpoint_before_client_creation(monkeypatch):
    def factory(**kwargs):
        pytest.fail("A client must not be constructed for a project endpoint")

    monkeypatch.setattr("aca_agent_sample.model_client.OpenAI", factory)
    with pytest.raises(ValueError, match="openai/v1"):
        FoundryOpenAIAdapter("https://example.test/api/projects/example", "deployment", lambda: "token")
