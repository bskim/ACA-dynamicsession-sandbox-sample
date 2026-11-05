"""Offline simulator by default; --live plus ENABLE_LIVE_AZURE=true is required for Azure."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from aca_agent_sample.auth import create_credential, foundry_token_provider
from aca_agent_sample.config import load_config
from aca_agent_sample.model_client import FakeModelAdapter, FoundryOpenAIAdapter, ModelResponse, ToolCall
from aca_agent_sample.sandboxes.command_agent import ANALYZE_COMMAND, PATCH_COMMAND
from aca_agent_sample.sandboxes import AcaSandboxAdapter, FakeSandboxAdapter, SandboxCommandAgent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use explicitly enabled Azure resources")
    args = parser.parse_args()
    model = credential = None
    if args.live:
        config = load_config()
        if not config.enable_live_azure:
            raise SystemExit("Set ENABLE_LIVE_AZURE=true before using --live")
        config.validate("sandbox")
        config.validate("foundry")
        credential = create_credential(config)
        manifest_path = Path(__file__).resolve().parents[2] / ".internal" / "runs" / f"sandbox-{uuid.uuid4().hex}.json"
        adapter = AcaSandboxAdapter(config, credential, manifest_path=str(manifest_path))
        print(f"Private recovery manifest: {manifest_path}")
        model = FoundryOpenAIAdapter(config.foundry_openai_base_url, config.foundry_model_deployment,
                                     foundry_token_provider(credential, config.tenant_id))
    else:
        adapter = FakeSandboxAdapter()
        commands = ['agent-tool --versions', 'repo-audit init', ANALYZE_COMMAND,
                    'python3 /workspace/demo/state_worker.py start', 'agent-tool cached-audit']
        responses = [ModelResponse(tool_calls=(ToolCall(str(i), "run_sandbox_command", json.dumps({"command": command})),))
                     for i, command in enumerate(commands)]
        patch = ModelResponse(tool_calls=(ToolCall('patch', 'run_sandbox_command', json.dumps({'command': PATCH_COMMAND})),))
        cached = ModelResponse(tool_calls=(ToolCall('cache', 'run_sandbox_command', json.dumps({'command': 'agent-tool cached-audit'})),))
        model = FakeModelAdapter([*responses, ModelResponse("Analysis complete."), cached, patch, ModelResponse("Sandbox demo complete.")])
    try:
        agent = SandboxCommandAgent(model, adapter)
        try:
            result = agent.run_toolchain_demo()
        except Exception:
            print(json.dumps(getattr(agent, 'last_run', {}), indent=2), file=sys.stderr)
            raise
        print(json.dumps(result, indent=2))
    finally:
        if hasattr(model, "close"):
            model.close()
        if credential is not None:
            credential.close()


if __name__ == "__main__":
    main()



