from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from aca_agent_sample.dynamic_sessions import (
    DynamicSessionsClient, DynamicSessionsError, FakeDynamicSessionsClient,
    SessionRegistry, SessionStateLost, mask_session_id,
)
from aca_agent_sample.dynamic_sessions.coding_agent import DynamicSessionsCodingAgent
from aca_agent_sample.model_client import FakeModelAdapter, ModelResponse, ToolCall


def test_registry_ownership_and_masking():
    registry = SessionRegistry()
    session = registry.get_or_create("alice", "chat")
    assert 40 <= len(session) <= 128 and all(c.isalnum() or c in "-_" for c in session)
    assert registry.get_or_create("alice", "chat") == session
    assert registry.get_or_create("alice", "chat", session) == session
    assert registry.get_or_create("alice", "other") != session
    assert mask_session_id(session) == "[REDACTED]" and session not in mask_session_id(session)
    with pytest.raises(PermissionError): registry.get_or_create("bob", "chat", session)
    with pytest.raises(PermissionError): registry.require_owner("bob", "chat", session)
    with pytest.raises(ValueError): registry.get_or_create("carol", "chat", "../bad")
    assert SessionRegistry().get_or_create("alice", "chat", "known_id") == "known_id"


def make_client(handler, profile="containerapp-1.3.0b5"):
    transport = httpx.MockTransport(handler)
    return DynamicSessionsClient("https://example.test/pool", lambda: "token", wire_profile=profile,
                                 http_client=httpx.Client(transport=transport))


def test_execution_flat_contract_and_queries():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "Succeeded", "result": {"stdout": "hello\n", "stderr": ""}})
    client = make_client(handler)
    result = client.execute_python("opaque_123", "print('hello')", 27)
    assert result.status == "succeeded" and result.stdout == "hello\n"
    request = seen[0]
    assert request.method == "POST" and request.url.path == "/pool/executions"
    assert parse_qs(request.url.query.decode()) == {"api-version": ["2025-10-02-preview"], "identifier": ["opaque_123"]}
    assert json.loads(request.content) == {"codeInputType": "inline", "executionType": "synchronous", "code": "print('hello')", "timeoutInSeconds": 27}
    assert request.headers["authorization"] == "Bearer token"


def test_explicit_properties_profile_and_separate_shell_contract():
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"properties": {"status": "Succeeded", "result": {"stdout": "ok", "stderr": ""}}})
    client = make_client(handler, "learn-properties")
    assert client.execute_python("session1", "print(1)").status == "succeeded"
    assert calls[0]["properties"]["code"] == "print(1)"
    assert client.execute_shell("session1", "cd /mnt/data && pwd", 60).status == "succeeded"
    assert calls[1] == {"shellCommand": "cd /mnt/data && pwd", "timeoutInSeconds": 60}


def test_file_upload_list_metadata_download():
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/content"): return httpx.Response(200, content=b"hello")
        return httpx.Response(200, json={"value": []})
    client = make_client(handler)
    client.upload_file("session1", "/mnt/data/agent-demo/source.py", b"hello")
    assert client.list_files("session1") == {"value": []}
    assert client.file_info("session1", "agent-demo/source.py") == {"value": []}
    assert client.download_file("session1", "agent-demo/source.py") == b"hello"
    assert [r.method for r in requests] == ["POST", "GET", "GET", "GET"]
    assert requests[0].url.path == "/pool/files"
    assert b'name="file"' in requests[0].content and b'filename="source.py"' in requests[0].content
    assert requests[2].url.raw_path.startswith(b"/pool/files/source.py?")
    assert requests[3].url.raw_path.startswith(b"/pool/files/source.py/content?")
    for index, request in enumerate(requests):
        expected = {"api-version": ["2025-10-02-preview"], "identifier": ["session1"]}
        if index != 1:
            expected["path"] = ["agent-demo"]
        assert parse_qs(request.url.query.decode()) == expected
    with pytest.raises(ValueError): client.download_file("session1", "../secret")


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "forbidden"), (500, "outcome_unknown"), (404, "execution_failed")])
def test_execution_http_error_mapping_and_no_retry(status, kind):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status)
    result = make_client(handler).execute_python("session1", "print(1)")
    assert result.error_kind == kind and result.status == "failed" and len(calls) == 1


def test_timeout_and_connection_failure_are_unknown_for_execution():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("uncertain", request=request)
    client = make_client(handler)
    assert client.execute_python("session1", "x=1").error_kind == "outcome_unknown"
    assert len(calls) == 1
    with pytest.raises(DynamicSessionsError) as exc:
        client.list_files("session1")
    assert exc.value.error_kind == "timeout"


def test_execution_response_validation():
    client = make_client(lambda _: httpx.Response(200, json={"status": "Succeeded", "result": {"stdout": "test"}}))
    assert client.execute_python("session1", "print(1)").error_kind == "schema_mismatch"
    client = make_client(lambda _: httpx.Response(200, json={"status": "Failed", "result": {"stdout": "", "stderr": "failed"}}))
    assert client.execute_python("session1", "x=1").error_kind == "execution_failed"


def test_fake_reuse_isolation_and_expiry():
    client = FakeDynamicSessionsClient()
    assert client.execute_python("session_a", "turn_counter = 41\nnonce = 'random'\nopen('/mnt/data/agent-demo/source.py', 'w').write('original')").status == "succeeded"
    result = client.execute_python("session_a", "turn_counter += 1\nprint(turn_counter)\nprint(nonce)\nprint(open('/mnt/data/agent-demo/source.py').read())")
    assert result.stdout.splitlines() == ["42", "random", "original"]
    assert client.execute_python("session_b", "print('turn_counter' in globals())").stdout.strip() == "False"
    with pytest.raises(DynamicSessionsError) as missing:
        client.download_file("session_b", "agent-demo/source.py")
    assert missing.value.status_code == 404
    client.expire_session("session_a")
    assert client.execute_python("session_a", "print('turn_counter' in globals())").stdout.strip() == "False"
    with pytest.raises(DynamicSessionsError): client.download_file("session_a", "agent-demo/source.py")


def test_fake_does_not_execute_arbitrary_host_python():
    client = FakeDynamicSessionsClient()
    for code in ("import subprocess", "__import__('os')", "open('C:/secret', 'r')", "print.__self__"):
        assert client.execute_python("session1", code).status == "failed"


def response(call_id, name, args):
    return ModelResponse(tool_calls=(ToolCall(call_id, name, json.dumps(args)),))


def test_coding_agent_create_fail_repair_and_state_loss():
    path = "/mnt/data/agent-demo/source.py"
    bad = "def add(a,b):\n    return a-b\n"
    good = "def add(a,b):\n    return a+b\n"
    sequence = [
        response("a", "execute_python", {"code": f"turn_counter = 1\nsession_nonce = 'n'\nopen({path!r}, 'w').write({bad!r})"}), ModelResponse(content="created"),
        response("b", "execute_python", {"code": f"assert turn_counter == 1\nassert session_nonce == 'n'\nturn_counter += 1\nexec(open({path!r}).read())\nassert add(2, 3) == 5"}), ModelResponse(content="failed"),
        response("c", "execute_python", {"code": f"assert turn_counter == 2\nopen({path!r}, 'w').write({good!r})\nexec(open({path!r}).read())\nassert add(2, 3) == 5\nprint('PASS')"}), ModelResponse(content="passed")]
    model, client = FakeModelAdapter(sequence), FakeDynamicSessionsClient()
    agent = DynamicSessionsCodingAgent(model, client, SessionRegistry(), owner="alice", conversation_id="coding")
    one = agent.run_turn("create")
    two = agent.run_turn("test")
    three = agent.run_turn("fix")
    assert [x.status for x in (one, two, three)] == ["completed"] * 3
    assert two.turns[0].result.error_kind == "execution_failed"
    assert three.turns[0].result.stdout.strip() == "PASS"
    assert b"return a+b" in client.download_file(agent.session_id, path)
    client.expire_session(agent.session_id)
    assert agent.run_turn("continue").status == SessionStateLost.error_kind


def test_model_cannot_choose_session_or_endpoint_and_file_tools_work():
    model = FakeModelAdapter([
        response("1", "upload_text_file", {"path": "agent-demo/x.txt", "content": "ok"}),
        response("2", "read_text_file", {"path": "agent-demo/x.txt"}),
        response("3", "list_session_files", {}),
        response("4", "execute_python", {"code": "print(1)", "session_id": "somebody_else"}),
        ModelResponse(content="done")])
    client = FakeDynamicSessionsClient()
    agent = DynamicSessionsCodingAgent(model, client, SessionRegistry(), owner="alice", conversation_id="chat")
    result = agent.run_turn("Use files")
    assert [turn.result.status for turn in result.turns] == ["succeeded", "succeeded", "succeeded", "failed"]
    assert result.turns[1].result.stdout == "ok"
    assert result.turns[3].result.error_kind == "schema_mismatch"
    tools = model.requests[0][1]
    assert {t["function"]["name"] for t in tools} == {"execute_python", "upload_text_file", "read_text_file", "list_session_files"}
    assert all("session_id" not in t["function"]["parameters"]["properties"] for t in tools)


def test_conversation_history_is_preserved_across_turns():
    model = FakeModelAdapter([ModelResponse(content="first answer"), ModelResponse(content="second answer")])
    agent = DynamicSessionsCodingAgent(model, FakeDynamicSessionsClient(), SessionRegistry(), owner="alice", conversation_id="chat")
    assert agent.run_turn("first question").status == "completed"
    assert agent.run_turn("second question").status == "completed"
    assert [(item["role"], item.get("content")) for item in model.requests[1][0]] == [
        ("user", "first question"), ("assistant", "first answer"), ("user", "second question")]


def test_state_loss_between_tool_calls_stops_loop():
    client = FakeDynamicSessionsClient()
    class ExpiringModel:
        calls = 0
        def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 2:
                client.expire_session(agent.session_id)
            return response(str(self.calls), "execute_python", {"code": "print(1)"})
    model = ExpiringModel()
    agent = DynamicSessionsCodingAgent(model, client, SessionRegistry(), owner="alice", conversation_id="chat")
    result = agent.run_turn("two calls")
    assert result.status == "state_lost"
    assert len(result.turns) == 2 and result.turns[-1].result.error_kind == "state_lost"


@pytest.mark.parametrize("status", [408, 429, 502, 503, 504])
def test_execution_never_retries_or_switches_profile_on_transient_http_error(status):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status)
    client = make_client(handler)
    result = client.execute_python("session1", "counter += 1")
    assert result.status == "failed" and len(requests) == 1
    assert "properties" not in json.loads(requests[0].content)


@pytest.mark.parametrize("status,exit_code,expected", [("0", 0, "succeeded"), ("7", 7, "failed"), ("-1", -1, "failed")])
def test_live_shell_exit_status_contract(status, exit_code, expected):
    client = make_client(lambda _: httpx.Response(200, json={"status": status, "result": {"stdout": "out", "stderr": ""}}))
    result = client.execute_shell("session1", "pwd")
    assert result.status == expected and result.exit_code == exit_code


def test_root_file_has_basename_and_empty_directory():
    requests = []
    client = make_client(lambda request: (requests.append(request), httpx.Response(200, content=b"hello"))[1])
    assert client.download_file("session1", "source.py") == b"hello"
    assert requests[0].url.path == "/pool/files/source.py/content"
    assert requests[0].url.params["path"] == ""


@pytest.mark.parametrize("status", ["--1", "²", "", "unknown"])
def test_malformed_shell_status_fails_closed(status):
    client = make_client(lambda _: httpx.Response(200, json={"status": status, "result": {"stdout": "", "stderr": ""}}))
    assert client.execute_shell("session1", "pwd").error_kind == "schema_mismatch"
