from __future__ import annotations

import argparse
import json
import runpy
import sys
from pathlib import Path
from typing import Any

from .agent_loop import run_agent
from .auth import create_credential, foundry_token_provider
from .config import ConfigError, load_config
from .model_client import FakeModelAdapter, FoundryOpenAIAdapter, ModelResponse, ToolCall
from .tool_policy import ToolPolicy
from .types import ToolExecutionResult


class _OfflineExecutor:
    def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        # Synthetic result only: never execute model-provided code on the host.
        return ToolExecutionResult("succeeded", stdout="Offline simulated execution", exit_code=0)


def _dry_run(kind: str) -> int:
    tool = "execute_python" if kind == "dynamic-session" else "run_command"
    args = {"code": "print('example')"} if tool == "execute_python" else {"command": "echo example", "timeout": 10}
    model = FakeModelAdapter([ModelResponse(tool_calls=(ToolCall("offline-1", tool, json.dumps(args)),)),
                              ModelResponse(content="Offline dry run complete")])
    result = run_agent(model, _OfflineExecutor(), ToolPolicy(allowed_tools=(tool,)), "Demonstrate an offline run")
    print(f"{kind}: {result.status}; simulated tool calls: {len(result.turns)}")
    return 0 if result.status == "completed" else 1


def _config_check() -> int:
    config = load_config()
    config.validate("foundry")
    print("Foundry configuration valid; execution resources are checked separately.")
    return 0


def _foundry_smoke() -> int:
    config = load_config()
    config.validate("foundry")
    if not config.enable_live_azure:
        raise ConfigError("Live Azure is disabled; set ENABLE_LIVE_AZURE=true to opt in")
    credential = create_credential(config)
    client = None
    try:
        client = FoundryOpenAIAdapter(config.foundry_openai_base_url, config.foundry_model_deployment,
                                     foundry_token_provider(credential, config.tenant_id))
        client.complete([{"role": "user", "content": "Reply with OK."}], [])
        print("Foundry smoke completed; inference charges may apply.")
        return 0
    finally:
        if client is not None:
            client.close()
        credential.close()


_EXAMPLES = {
    ("dynamic-session", "reuse"): "dynamic_sessions/session_reuse.py",
    ("dynamic-session", "coding-agent"): "dynamic_sessions/coding_agent.py",
    ("dynamic-session", "shell"): "dynamic_sessions/shell_agent.py",
    ("sandbox", "command-agent"): "sandbox/command_agent.py",
    ("sandbox", "compare-modes"): "sandbox/compare_snapshot_modes.py",
    ("sandbox", "snapshot-clone"): "sandbox/snapshot_clone.py",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aca-agent-sample")
    sub = parser.add_subparsers(dest="command", required=True)
    preflight = sub.add_parser("preflight", help="Validate configuration or explicitly opt in to a Foundry smoke test")
    preflight.add_argument("check", choices=("config-check", "foundry-smoke"))
    for name in ("dynamic-session", "sandbox"):
        runner = sub.add_parser(name, help="Run an example or the legacy offline simulation")
        runner.add_argument("--dry-run", action="store_true", help="Use the default offline simulation")
        actions = runner.add_subparsers(dest="action")
        for group, action in _EXAMPLES:
            if group == name:
                actions.add_parser(action, add_help=False, help="Run the example; add --help for its options")
    return parser


def _run_example(path: str, arguments: list[str]) -> int:
    example = Path(__file__).resolve().parents[2] / "examples" / path
    if not example.is_file():
        raise RuntimeError("Example scripts are unavailable; run from the source checkout")
    previous_argv = sys.argv
    try:
        sys.argv = [str(example), *arguments]
        namespace = runpy.run_path(str(example), run_name="aca_agent_sample_example")
        result = namespace["main"]()
        return 0 if result is None else int(result)
    finally:
        sys.argv = previous_argv


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(arguments) >= 2 and (arguments[0], arguments[1]) in _EXAMPLES:
            return _run_example(_EXAMPLES[(arguments[0], arguments[1])], arguments[2:])
        args = build_parser().parse_args(arguments)
        if args.command == "preflight":
            return _config_check() if args.check == "config-check" else _foundry_smoke()
        return _dry_run(args.command)
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2
    except Exception:
        print("Operation failed; details are suppressed to protect sensitive configuration.")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())


