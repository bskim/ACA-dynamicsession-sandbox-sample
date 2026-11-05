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
        prompt = ("In the sandbox create /workspace/demo, write a small shell script there, execute it, "
                  "and read its output in a subsequent command. Each command may stop and resume the sandbox.")
    else:
        adapter = FakeSandboxAdapter()
        commands = ["mkdir -p /workspace/demo", "echo 'echo hello' > /workspace/demo/script.sh",
                    "sh /workspace/demo/script.sh", "cat /workspace/demo/script.sh"]
        responses = [ModelResponse(tool_calls=(ToolCall(str(i), "run_sandbox_command", json.dumps({"command": command})),))
                     for i, command in enumerate(commands)]
        model = FakeModelAdapter([*responses, ModelResponse("Sandbox demo complete.")])
        prompt = "Create a working directory, write a result, and read it after resume."
    try:
        result = SandboxCommandAgent(model, adapter, pause_between_commands=True).run(prompt)
        for turn in result.turns:
            print(f"{turn.tool_name}: {turn.result.status} {turn.result.stdout.strip()}")
        print(result.final_response or result.status)
        if result.status != "completed":
            raise SystemExit(1)
    finally:
        if hasattr(model, "close"):
            model.close()
        if credential is not None:
            credential.close()


if __name__ == "__main__":
    main()



