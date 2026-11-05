r"""Real Foundry inference with simulated execution, never paid ACA resources.

Run explicitly from the checkout in PowerShell::

    $env:ENABLE_LIVE_AZURE = 'true'
    .\.venv\Scripts\python.exe -m pytest tests\integration -q -s

Only the model/authentication boundary is live. Model-generated Python and shell
commands go to the existing limited AST/command simulators, not the host or Azure
execution resources. This suite does not establish ACA data-plane compatibility.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

import pytest

from aca_agent_sample.auth import create_credential, foundry_token_provider
from aca_agent_sample.config import load_config
from aca_agent_sample.dynamic_sessions.client import FakeDynamicSessionsClient
from aca_agent_sample.dynamic_sessions.coding_agent import DynamicSessionsCodingAgent
from aca_agent_sample.dynamic_sessions.session_registry import SessionRegistry
from aca_agent_sample.model_client import FoundryOpenAIAdapter
from aca_agent_sample.sandboxes.client import FakeSandboxAdapter
from aca_agent_sample.sandboxes.command_agent import SandboxCommandAgent

ROOT = Path(__file__).resolve().parents[2]
# Read the process environment BEFORE .env loading. A local .env must never opt
# default pytest into paid inference, even if it contains ENABLE_LIVE_AZURE=true.
pytestmark = pytest.mark.skipif(
    os.environ.get("ENABLE_LIVE_AZURE", "").strip().lower() != "true",
    reason="Paid Foundry inference requires explicit ENABLE_LIVE_AZURE=true in the process environment",
)


@pytest.fixture
def live_model(monkeypatch, request):
    config = load_config(ROOT / ".env")
    config.validate("foundry")
    if not config.enable_live_azure:
        pytest.fail("Live Foundry configuration must be explicitly enabled", pytrace=False)
    if config.foundry_model_deployment != "gpt-5.4-mini":
        pytest.fail("This integration requires the configured gpt-5.4-mini deployment", pytrace=False)
    credential = create_credential(config)
    model = None
    evidence = {
        "test": request.node.name,
        "deployment": config.foundry_model_deployment,
        "execution_backend": "offline_simulators_not_azure",
        "provisioned_resources": 0,
        "calls": [],
    }
    try:
        model = FoundryOpenAIAdapter(
            config.foundry_openai_base_url, config.foundry_model_deployment,
            foundry_token_provider(credential, config.tenant_id),
        )
        original_create = model.client.chat.completions.create

        def record_completion(**kwargs):
            if len(evidence["calls"]) >= 20:
                raise AssertionError("Live inference request budget exceeded")
            # Bound cost without changing the production adapter's contract.
            kwargs.update(max_completion_tokens=1800, reasoning_effort="low")
            response = original_create(**kwargs)
            if response.usage is None:
                raise AssertionError("Foundry response did not include token usage")
            message = response.choices[0].message
            evidence["calls"].append({
                "index": len(evidence["calls"]) + 1,
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "tool_names": [call.function.name for call in message.tool_calls or []],
                "tool_results_in_context": sum(m["role"] == "tool" for m in kwargs["messages"]),
                "user_turns_in_context": sum(m["role"] == "user" for m in kwargs["messages"]),
            })
            return response

        monkeypatch.setattr(model.client.chat.completions, "create", record_completion)
        yield model, evidence
    finally:
        evidence["model_requests"] = len(evidence["calls"])
        evidence["usage"] = {
            key: sum(call[key] for call in evidence["calls"])
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        # Never emit endpoints, credentials, raw messages, arguments or session IDs.
        print("\nLIVE_FOUNDRY_EVIDENCE " + json.dumps(evidence, ensure_ascii=False))
        try:
            if model is not None:
                model.close()
        finally:
            credential.close()


def _assert_completed(result, expected_tools):
    assert result.status == "completed", result.status
    assert result.final_response
    assert result.turns, "A prose-only answer does not prove tool calling"
    assert expected_tools <= {turn.tool_name for turn in result.turns}
    assert all(turn.result.status == "succeeded" for turn in result.turns), [
        (turn.tool_name, turn.result.status, turn.result.error_kind) for turn in result.turns
    ]
    assert result.model_turns > 1, "A complete tool-result/model-observation loop is required"


def _turn_evidence(result):
    return {
        "status": result.status, "model_turns": result.model_turns,
        "tools": [{"name": turn.tool_name, "status": turn.result.status,
                   "stdout": turn.result.stdout} for turn in result.turns],
    }


def test_live_foundry_dynamic_sessions_two_user_turns(live_model):
    model, evidence = live_model
    client = FakeDynamicSessionsClient()
    session_id = "live-test-" + uuid.uuid4().hex
    agent = DynamicSessionsCodingAgent(
        model, client, SessionRegistry(), owner="integration-owner",
        conversation_id="integration-conversation", session_id=session_id,
    )
    tools = {"execute_python", "upload_text_file", "read_text_file"}
    path = "/mnt/data/live_state.py"
    try:
        first = agent.run_turn(
            "This is an integration test using a limited simulated Python interpreter. "
            "Use real tool calls, one at a time; do not just describe them. "
            "1. Use execute_python to define a function add(a, b) returning a + b, "
            "assign counter = 7, and print(add(counter, 5)). "
            f"2. Use upload_text_file to write exactly 'counter = counter + 5\\nprint(counter)\\n' "
            f"(actual newlines, not literal backslash-n) to {path}. "
            "3. Use read_text_file to verify its content. Do not execute this file yet. "
            "Only simple assignments, function definitions/calls, print and assert are supported; "
            "no imports, with, loops or attributes other than file read/write/close. "
            "Finish with TURN1_READY after the tools succeed.", max_turns=10,
        )
        _assert_completed(first, tools)
        assert "TURN1_READY" in first.final_response
        assert any("12" in t.result.stdout for t in first.turns if t.tool_name == "execute_python")
        initial = client.execute_python(session_id, "assert counter == 7\nassert add(2, 3) == 5")
        assert initial.status == "succeeded"
        assert client.download_file(session_id, path) == b"counter = counter + 5\nprint(counter)\n"
        second = agent.run_turn(
            "Continue in the same session; do not reinitialize counter or add. "
            f"1. Use read_text_file on {path}. "
            "2. Use execute_python to assert counter == 7 and add(2, 3) == 5; "
            f"then exec(open('{path}').read()), assert counter == 12, and print(counter). "
            f"3. Use upload_text_file to replace {path} with exactly 'print(counter + 8)\\n' "
            "(actual newline). 4. Use read_text_file to verify the replacement. "
            f"5. Use execute_python with exec(open('{path}').read()); assert counter == 12. "
            "Use only the supported simple Python subset. Finish with TURN2_VERIFIED "
            "and the observed number after every tool succeeds.", max_turns=10,
        )
        _assert_completed(second, tools)
        assert "TURN2_VERIFIED" in second.final_response
        assert any(t.result.stdout.strip() == "20" for t in second.turns if t.tool_name == "execute_python")
        assert agent.session_id == session_id
        assert client.execute_python(session_id, "assert counter == 12\nassert add(counter, 8) == 20").status == "succeeded"
        assert client.download_file(session_id, path) == b"print(counter + 8)\n"
        isolated = client.execute_python("isolated-" + uuid.uuid4().hex, "print(counter)")
        assert isolated.status == "failed" and "NameError" in isolated.stderr
        assert any(c["user_turns_in_context"] == 2 and c["tool_results_in_context"] >= 3 for c in evidence["calls"])
        evidence.update(user_turns=2, same_session_reused=True, isolation_verified=True,
                        agent_turns=[_turn_evidence(first), _turn_evidence(second)])
    finally:
        # These are virtual sessions only; no Azure delete request is made.
        for sid in list(client._sessions):
            client.expire_session(sid)
        client.close()


class _ObservedSandbox(FakeSandboxAdapter):
    def __init__(self):
        super().__init__()
        self.operations = []
        self.deleted_files = []

    def exec(self, sandbox_id, command, *, working_directory="/workspace"):
        result = super().exec(sandbox_id, command, working_directory=working_directory)
        self.operations.append((sandbox_id, result))
        return result

    def delete(self, sandbox_id):
        self.deleted_files.append(dict(self.get(sandbox_id).files))
        super().delete(sandbox_id)


def test_live_foundry_sandbox_multistep_command_loop(live_model):
    model, evidence = live_model
    adapter = _ObservedSandbox()
    agent = SandboxCommandAgent(model, adapter, mode="Memory", pause_between_commands=True)
    path = "/workspace/live-result.txt"
    try:
        result = agent.run(
            "Use run_sandbox_command for each of the following FOUR steps, one tool call "
            "at a time, waiting for and inspecting every result. Working directory is /workspace. "
            f"1. echo first-value > {path} "
            f"2. cat {path} (verify first-value) "
            f"3. echo updated-value > {path} "
            f"4. cat {path} (verify updated-value). "
            "The command simulator supports only a single echo or cat command per call. "
            "Do not combine commands, use &&, invoke any host shell, or use other commands. "
            "Finish with SANDBOX_VERIFIED and the last observed value only after all "
            "four calls succeed.", max_turns=8,
        )
        _assert_completed(result, {"run_sandbox_command"})
        assert "SANDBOX_VERIFIED" in result.final_response and "updated-value" in result.final_response
        assert len(result.turns) == 4
        assert [turn.result.stdout for turn in result.turns] == ["", "first-value\n", "", "updated-value\n"]
        assert len({sid for sid, _ in adapter.operations}) == 1
        assert adapter.deleted_files == [{path: "updated-value\n"}]
        assert not adapter.sandboxes and not adapter.snapshots
        assert evidence["calls"][-1]["tool_results_in_context"] == 4
        evidence.update(user_turns=1, same_sandbox_reused=True, cleanup_verified=True,
                        agent_turns=[_turn_evidence(result)])
    finally:
        adapter.cleanup()
