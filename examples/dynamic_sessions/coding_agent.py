"""Three-turn coding-agent demonstration; offline unless --live is requested."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from aca_agent_sample.auth import create_credential, dynamic_sessions_token_provider, foundry_token_provider
from aca_agent_sample.config import load_config
from aca_agent_sample.dynamic_sessions import DynamicSessionsClient, FakeDynamicSessionsClient, SessionRegistry, mask_session_id
from aca_agent_sample.dynamic_sessions.coding_agent import DynamicSessionsCodingAgent
from aca_agent_sample.model_client import FakeModelAdapter, FoundryOpenAIAdapter, ModelResponse, ToolCall


def fake_model() -> FakeModelAdapter:
    path = "/mnt/data/agent-demo/add.py"
    bad = "def add(a, b):\n    return a - b\n"
    good = "def add(a, b):\n    return a + b\n"
    scripts = [
        "import os\nos.makedirs('/mnt/data/agent-demo', exist_ok=True)\nturn_counter = 1\nsession_nonce = 'demo-nonce'\nopen(" + repr(path) + ", 'w').write(" + repr(bad) + ")",
        "assert turn_counter == 1\nassert session_nonce == 'demo-nonce'\nturn_counter += 1\nexec(open(" + repr(path) + ").read())\nassert add(2, 3) == 5",
        "assert turn_counter == 2\nopen(" + repr(path) + ", 'w').write(" + repr(good) + ")\nexec(open(" + repr(path) + ").read())\nassert add(2, 3) == 5\nprint('TEST PASSED')",
    ]
    responses = []
    for number, script in enumerate(scripts, 1):
        responses.extend([ModelResponse(tool_calls=(ToolCall(f"call-{number}", "execute_python", json.dumps({"code": script})),)), ModelResponse(content=f"Turn {number} observed.")])
    return FakeModelAdapter(responses)


def verify_turn_one(client: Any, session_id: str, turn_res: Any = None) -> None:
    if turn_res is not None and not turn_res.turns:
        raise RuntimeError("Turn 1 did not execute any tool calls")
    raw = client.download_file(session_id, "/mnt/data/agent-demo/add.py").decode("utf-8")
    if "def add" not in raw:
        raise RuntimeError("Turn 1 did not write add() to /mnt/data/agent-demo/add.py")
    probe = client.execute_python(
        session_id,
        "assert turn_counter == 1\n"
        "assert session_nonce == 'demo-nonce'\n"
        "exec(open('/mnt/data/agent-demo/add.py').read())\n"
        "assert add(2, 3) != 5\n"
        "print('HOST_VERIFIED_BROKEN')",
    )
    if probe.status != "succeeded" or "HOST_VERIFIED_BROKEN" not in probe.stdout:
        raise RuntimeError("Host verification failed after Turn 1: broken add.py or session variables missing")


def verify_turn_two(client: Any, session_id: str, turn_res: Any = None) -> None:
    if turn_res is not None:
        failed_turns = [t for t in turn_res.turns if t.result.status == "failed"]
        if not failed_turns or any(
            t.result.error_kind != "execution_failed" or "AssertionError" not in t.result.stderr
            for t in failed_turns
        ):
            raise RuntimeError("Turn 2 must fail specifically with AssertionError from the broken add() test")
    probe = client.execute_python(
        session_id,
        "assert turn_counter == 2\n"
        "assert session_nonce == 'demo-nonce'\n"
        "exec(open('/mnt/data/agent-demo/add.py').read())\n"
        "assert add(2, 3) != 5\n"
        "print('HOST_VERIFIED_STILL_BROKEN')",
    )
    if probe.status != "succeeded" or "HOST_VERIFIED_STILL_BROKEN" not in probe.stdout:
        raise RuntimeError("Host verification failed after Turn 2: expected turn_counter==2 and broken add.py")


def verify_turn_three(client: Any, session_id: str, turn_res: Any = None) -> None:
    if turn_res is not None and not any(t.result.status == "succeeded" and "TEST PASSED" in t.result.stdout for t in turn_res.turns):
        raise RuntimeError("Expected passing test output was not observed in Turn 3")
    raw = client.download_file(session_id, "/mnt/data/agent-demo/add.py").decode("utf-8")
    if "def add" not in raw:
        raise RuntimeError("Turn 3 did not preserve add() in /mnt/data/agent-demo/add.py")
    probe = client.execute_python(
        session_id,
        "assert turn_counter == 2\n"
        "assert session_nonce == 'demo-nonce'\n"
        "exec(open('/mnt/data/agent-demo/add.py').read())\n"
        "assert add(2, 3) == 5\n"
        "assert add(10, -4) == 6\n"
        "print('HOST_VERIFIED_REPAIRED')",
    )
    if probe.status != "succeeded" or "HOST_VERIFIED_REPAIRED" not in probe.stdout:
        raise RuntimeError("Host verification failed after Turn 3: repaired add() did not pass host assertions")


def run_coding_demo(agent: DynamicSessionsCodingAgent, client: Any, *, live: bool = False) -> dict[str, Any]:
    prompts = [
        "Use execute_python to set the global variables turn_counter = 1 and session_nonce = 'demo-nonce' directly in the Python interpreter globals, and write /mnt/data/agent-demo/add.py containing 'def add(a, b):\\n    return a - b\\n'. Report creation.",
        "In the same session, verify turn_counter == 1 and session_nonce == 'demo-nonce' in globals, increment turn_counter += 1, exec(open('/mnt/data/agent-demo/add.py').read()), and run assert add(2,3)==5 to demonstrate test failure. Do not catch AssertionError, use try/except, replace the assertion with a print, or repair the code in this turn. Let the tool return an actual failed execution, then report that observed failure.",
        "In the same session verify turn_counter == 2 in globals, fix /mnt/data/agent-demo/add.py so add returns a+b, execute it and assert add(2,3)==5. Print TEST PASSED and report the actual test result.",
    ]
    verifiers = (verify_turn_one, verify_turn_two, verify_turn_three)
    results = []
    for idx, (prompt, verifier) in enumerate(zip(prompts, verifiers), 1):
        if live:
            print(f"\n[coding-agent] === Turn {idx}/3 (Session {mask_session_id(agent.session_id)}) ===", file=sys.stderr, flush=True)
            print(f"[coding-agent] Prompt: {prompt}", file=sys.stderr, flush=True)
        turn_res = agent.run_turn(prompt)
        if live:
            for t in turn_res.turns:
                print(f"[coding-agent] Tool Result ({t.tool_name}): status={t.result.status}, stdout={t.result.stdout.strip()!r}, stderr={t.result.stderr.strip()!r}", file=sys.stderr, flush=True)
            try:
                file_bytes = client.download_file(agent.session_id, "/mnt/data/agent-demo/add.py")
                print(f"[coding-agent] Current /mnt/data/agent-demo/add.py in session:\n{file_bytes.decode('utf-8').rstrip()}", file=sys.stderr, flush=True)
            except Exception:
                pass
            print(f"[coding-agent] Assistant: {turn_res.final_response}", file=sys.stderr, flush=True)
        results.append(turn_res)
        if turn_res.status != "completed":
            raise RuntimeError("Agent did not complete all turns")
        verifier(client, agent.session_id, turn_res)
    return {
        "mode": "live" if live else "offline",
        "session": mask_session_id(agent.session_id),
        "creation": "passed",
        "initial_test": "failed_as_expected",
        "repair_test": "passed",
        "model_turns": [r.model_turns for r in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use Azure and Foundry, with explicit opt-in")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--wire-profile", choices=("containerapp-1.3.0b5", "learn-properties"), default="containerapp-1.3.0b5", help="Select the execution wire profile explicitly; no automatic retry")
    parser.add_argument("--session-id")
    args = parser.parse_args()
    if args.live and args.dry_run: parser.error("--live and --dry-run are mutually exclusive")
    credential = None
    if args.live:
        config = load_config()
        if not config.enable_live_azure: parser.error("--live requires ENABLE_LIVE_AZURE=true")
        config.validate("dynamic-session"); config.validate("foundry")
        credential = create_credential(config)
        client = DynamicSessionsClient(config.dynamic_sessions_pool_endpoint, dynamic_sessions_token_provider(credential, config.tenant_id), wire_profile=args.wire_profile)
        model = FoundryOpenAIAdapter(config.foundry_openai_base_url, config.foundry_model_deployment, foundry_token_provider(credential, config.tenant_id))
    else:
        client = FakeDynamicSessionsClient(); model = fake_model()
    agent = DynamicSessionsCodingAgent(model, client, SessionRegistry(), owner="demo", conversation_id="coding", session_id=args.session_id)
    try:
        summary = run_coding_demo(agent, client, live=args.live)
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    finally:
        client.close()
        if hasattr(model, "close"): model.close()
        if credential: credential.close()


if __name__ == "__main__":
    raise SystemExit(main())


