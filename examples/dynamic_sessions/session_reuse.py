"""Show session state reuse, isolation and expiry. Offline by default."""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from aca_agent_sample.auth import create_credential, dynamic_sessions_token_provider
from aca_agent_sample.config import load_config
from aca_agent_sample.dynamic_sessions import DynamicSessionsClient, FakeDynamicSessionsClient, SessionRegistry, mask_session_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use Azure; also requires ENABLE_LIVE_AZURE=true")
    parser.add_argument("--dry-run", action="store_true", help="Use the default offline simulation")
    parser.add_argument("--wire-profile", choices=("containerapp-1.3.0b5", "learn-properties"), default="containerapp-1.3.0b5", help="Select the execution wire profile explicitly; no automatic retry")
    parser.add_argument("--session-id", help="Reuse an explicitly supplied session identifier")
    parser.add_argument("--wait-for-ttl", type=int, default=0, help="Live only: seconds to wait for the pool cooldown (charges apply)")
    args = parser.parse_args()
    if args.live and args.dry_run: parser.error("--live and --dry-run are mutually exclusive")
    if args.wait_for_ttl < 0: parser.error("--wait-for-ttl must be nonnegative")
    credential = None
    if args.live:
        config = load_config()
        if not config.enable_live_azure: parser.error("--live requires ENABLE_LIVE_AZURE=true")
        config.validate("dynamic-session")
        credential = create_credential(config)
        client = DynamicSessionsClient(config.dynamic_sessions_pool_endpoint, dynamic_sessions_token_provider(credential, config.tenant_id), wire_profile=args.wire_profile)
    else:
        client = FakeDynamicSessionsClient()
    registry = SessionRegistry()
    a = registry.get_or_create("demo", "conversation-a", args.session_id)
    b = registry.get_or_create("demo", "conversation-b")
    filename = "/mnt/data/agent-demo/source.py"
    original = "def add(a, b):\n    return a + b\n"
    nonce = secrets.token_hex(16)
    try:
        result = client.execute_python(a, "import os\nos.makedirs('/mnt/data/agent-demo', exist_ok=True)\nturn_counter = 41\nnonce = " + repr(nonce) + "\nopen(" + repr(filename) + ", 'w').write(" + repr(original) + ")")
        if result.status != "succeeded": raise RuntimeError("Turn 1 failed: " + result.stderr)
        expected_hash = hashlib.sha256(original.encode()).hexdigest()
        before = client.download_file(a, filename)
        if hashlib.sha256(before).hexdigest() != expected_hash: raise RuntimeError("Original file hash mismatch")
        code = "assert turn_counter == 41\nturn_counter += 1\nprint(turn_counter)\nprint(nonce)\nopen(" + repr(filename) + ", 'w').write(" + repr(original + "# modified\n") + ")"
        result = client.execute_python(a, code)
        if result.status != "succeeded" or result.stdout.splitlines() != ["42", nonce]: raise RuntimeError("Turn 2 state mismatch")
        after = client.download_file(a, filename)
        if after == before or after != (original + "# modified\n").encode(): raise RuntimeError("File modification mismatch")
        negative = client.execute_python(b, "print('turn_counter' in globals())")
        if negative.status != "succeeded": raise RuntimeError("Negative-control execution failed: " + negative.stderr)
        if negative.stdout.strip() != "False": raise RuntimeError("Negative control leaked variables")
        try: client.download_file(b, filename)
        except Exception as exc:
            from aca_agent_sample.dynamic_sessions import DynamicSessionsError
            if not isinstance(exc, DynamicSessionsError) or exc.status_code != 404: raise
        else: raise RuntimeError("Negative control leaked file")
        expiry = "skipped: use --live --wait-for-ttl after choosing the pool cooldown"
        if not args.live or args.wait_for_ttl:
            if args.live: time.sleep(args.wait_for_ttl)
            else: client.expire_session(a)
            probe = client.execute_python(a, "print('turn_counter' in globals())")
            if probe.status != "succeeded": raise RuntimeError("Expiry probe failed")
            expiry = "state_lost" if probe.stdout.strip() == "False" else "state_present: cooldown has not elapsed"
            if expiry != "state_lost": raise RuntimeError(expiry)
            try: client.download_file(a, filename)
            except Exception as exc:
                from aca_agent_sample.dynamic_sessions import DynamicSessionsError
                if not isinstance(exc, DynamicSessionsError) or exc.status_code != 404: raise
            else: raise RuntimeError("Expired session unexpectedly retained the file")
        print(json.dumps({"mode": "live" if args.live else "offline", "session": mask_session_id(a), "reuse": "passed", "counter": 42, "nonce_preserved": True, "original_sha256": expected_hash, "file_modified": True, "negative_control": "passed", "expiry": expiry}, ensure_ascii=False))
        return 0
    finally:
        client.close()
        if credential: credential.close()


if __name__ == "__main__":
    raise SystemExit(main())


