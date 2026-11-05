"""Shell preview: each execution explicitly sets its working directory."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from aca_agent_sample.auth import create_credential, dynamic_sessions_token_provider
from aca_agent_sample.config import load_config
from aca_agent_sample.dynamic_sessions import DynamicSessionsClient, FakeDynamicSessionsClient, SessionRegistry, mask_session_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use a Shell session pool; requires ENABLE_LIVE_AZURE=true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--wire-profile", choices=("containerapp-1.3.0b5", "learn-properties"), default="containerapp-1.3.0b5", help="Select the execution wire profile explicitly; no automatic retry")
    parser.add_argument("--session-id")
    args = parser.parse_args()
    if args.live and args.dry_run: parser.error("--live and --dry-run are mutually exclusive")
    credential = None
    if args.live:
        config = load_config()
        if not config.enable_live_azure: parser.error("--live requires ENABLE_LIVE_AZURE=true")
        config.validate("dynamic-session")
        credential = create_credential(config)
        client = DynamicSessionsClient(config.dynamic_sessions_pool_endpoint, dynamic_sessions_token_provider(credential, config.tenant_id), wire_profile=args.wire_profile)
    else: client = FakeDynamicSessionsClient()
    session_id = SessionRegistry().get_or_create("demo", "shell", args.session_id)
    try:
        commands = [
            "cd /mnt/data && mkdir -p shell-demo && echo 'hello-from-shell-session' > shell-demo/note.txt && pwd",
            "cd /mnt/data && cat shell-demo/note.txt && pwd",
        ]
        results = []
        for idx, cmd in enumerate(commands, 1):
            if args.live:
                print(f"[shell-agent] Run {idx}/2 (Session {mask_session_id(session_id)}): $ {cmd}", file=sys.stderr, flush=True)
            res = client.execute_shell(session_id, cmd)
            if args.live:
                print(f"[shell-agent] Run {idx}/2 stdout:\n{res.stdout.strip()}", file=sys.stderr, flush=True)
            results.append(res)
        expected_lines = [
            ["/mnt/data"],
            ["hello-from-shell-session", "/mnt/data"],
        ]
        for result, expected in zip(results, expected_lines):
            if result.status != "succeeded": raise RuntimeError("Shell execution failed: " + result.stderr)
            if [line.strip() for line in result.stdout.strip().splitlines()] != expected:
                raise RuntimeError("Shell output or working directory mismatch")
        print(json.dumps({"mode": "live" if args.live else "offline", "session": mask_session_id(session_id), "runs": 2, "cwd_explicit_each_time": True}, ensure_ascii=False))
        return 0
    finally:
        client.close()
        if credential: credential.close()

if __name__ == "__main__": raise SystemExit(main())

