import json
import pytest

from aca_agent_sample.tool_policy import ToolPolicy, ToolPolicyError


def test_valid_schemas_and_arguments():
    policy = ToolPolicy()
    assert policy.validate("execute_python", '{"code":"print(1)"}') == {"code": "print(1)"}
    assert policy.validate("run_command", '{"command":"echo ok","timeout":60}') == {"command": "echo ok", "timeout": 60}
    assert [s["function"]["name"] for s in policy.schemas()] == ["execute_python", "run_command"]


@pytest.mark.parametrize(("name", "args"), [
    ("other", '{"code":"x"}'),
    ("execute_python", '{"code":"x","session_id":"123"}'),
    ("execute_python", '{"code":"x","endpoint":"https://example.test"}'),
    ("execute_python", '{"code":"x","nested":{"token":"x"}}'),
    ("execute_python", '{broken'),
    ("execute_python", '{"code":"x","code":"y"}'),
    ("execute_python", '{"code":NaN}'),
    ("execute_python", '[]'),
    ("run_command", '{"command":"x","timeout":61}'),
    ("run_command", '{"command":"x","timeout":true}'),
    ("run_command", '{"command":"x"}'),
])
def test_reject_untrusted_calls(name, args):
    with pytest.raises(ToolPolicyError):
        ToolPolicy().validate(name, args)


def test_length_and_output_limits():
    policy = ToolPolicy(max_code_length=3, max_command_length=3, max_output_length=10)
    for name, args in [("execute_python", {"code": "1234"}), ("run_command", {"command": "1234", "timeout": 1})]:
        with pytest.raises(ToolPolicyError):
            policy.validate(name, json.dumps(args))
    assert len(policy.truncate("0123456789ABCDEF")) <= 10
    assert policy.truncate("short") == "short"


def test_timeout_never_exceeds_documented_python_limit():
    with pytest.raises(ValueError):
        ToolPolicy(max_timeout=221)


@pytest.mark.parametrize("key", ["session_id", "sandbox_id", "endpoint", "subscription_id",
                                 "tenant_id", "token", "api_key", "credentials", "resource_group"])
def test_authority_and_routing_fields_are_rejected(key):
    with pytest.raises(ToolPolicyError):
        ToolPolicy().validate("run_command", json.dumps({"command": "echo ok", "timeout": 1, key: "other"}))


def test_deep_or_oversized_json_is_rejected():
    policy = ToolPolicy(max_arguments_length=3000)
    for arguments in [" " * 3001, '{"code":' + "[" * 1200 + "0" + "]" * 1200 + "}"]:
        with pytest.raises(ToolPolicyError):
            policy.validate("execute_python", arguments)


@pytest.mark.parametrize("limit", ["max_input_length", "max_response_length"])
def test_transcript_limits_must_be_positive_integers(limit):
    for value in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="positive integers"):
            ToolPolicy(**{limit: value})
