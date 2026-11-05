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
    parser.add_argument("--step", choices=("all", "init", "reuse", "check"), default="all",
                        help="Run all steps at once (default) or run init / reuse / check across separate CLI calls with --session-id")
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
        if args.step != "all":
            if not args.session_id:
                parser.error("--step init/reuse/check requires an explicit --session-id")
            if args.step == "init":
                code = (
                    "import os\nos.makedirs('/mnt/data/agent-demo', exist_ok=True)\n"
                    "turn_counter = 100\n"
                    f"nonce = {nonce!r}\n"
                    f"open({filename!r}, 'w').write({original!r})\n"
                    "print(turn_counter)\nprint(nonce)"
                )
                res = client.execute_python(a, code)
                if res.status != "succeeded" or res.stdout.splitlines() != ["100", nonce]:
                    raise RuntimeError("Init failed: " + res.stderr)
                if client.download_file(a, filename).decode("utf-8") != original:
                    raise RuntimeError("Init file verification failed")
                print(json.dumps({
                    "step": "init",
                    "session_id": args.session_id,
                    "turn_counter": 100,
                    "nonce": nonce,
                    "file_created": filename,
                }, ensure_ascii=False, indent=2))
                return 0
            if args.step == "reuse":
                code = (
                    "assert 'turn_counter' in globals()\n"
                    "assert 'nonce' in globals()\n"
                    "turn_counter += 1\n"
                    "print(turn_counter)\n"
                    "print(nonce)"
                )
                res = client.execute_python(a, code)
                if res.status != "succeeded":
                    raise RuntimeError("Reuse failed (session state missing): " + res.stderr)
                lines = res.stdout.strip().splitlines()
                if len(lines) != 2 or not lines[0].isdigit() or int(lines[0]) <= 100 or not lines[1]:
                    raise RuntimeError("Reuse state verification failed")
                file_content = client.download_file(a, filename).decode("utf-8")
                if file_content != original:
                    raise RuntimeError("Reuse file content mismatch")
                print(json.dumps({
                    "step": "reuse",
                    "session_id": args.session_id,
                    "variable_preserved": True,
                    "turn_counter": int(lines[0]),
                    "nonce": lines[1],
                    "file_content": file_content,
                }, ensure_ascii=False, indent=2))
                return 0
            if args.step == "check":
                res = client.execute_python(a, "print('turn_counter' in globals())\nprint('nonce' in globals())")
                if res.status != "succeeded":
                    raise RuntimeError("Check failed: " + res.stderr)
                if res.stdout.strip().splitlines() != ["False", "False"]:
                    raise RuntimeError("Isolation check failed: session variables leaked")
                try:
                    client.download_file(a, filename)
                except Exception as exc:
                    from aca_agent_sample.dynamic_sessions import DynamicSessionsError
                    if not isinstance(exc, DynamicSessionsError) or exc.status_code != 404:
                        raise
                else:
                    raise RuntimeError("Isolation check failed: session file leaked")
                print(json.dumps({
                    "step": "check",
                    "session_id": args.session_id,
                    "variable_exists": False,
                    "turn_counter": None,
                    "file_exists": False,
                }, ensure_ascii=False, indent=2))
                return 0

        if args.step == "all":
            print(f"[session-reuse] [1/4] Session A ({mask_session_id(a)}) --step init -> turn_counter=100, nonce set, writing {filename}", file=sys.stderr, flush=True)
            init_res = client.execute_python(a, "import os\nos.makedirs('/mnt/data/agent-demo', exist_ok=True)\nturn_counter = 100\nnonce = " + repr(nonce) + "\nopen(" + repr(filename) + ", 'w').write(" + repr(original) + ")\nprint(turn_counter)\nprint(nonce)")
            if init_res.status != "succeeded" or init_res.stdout.splitlines() != ["100", nonce]:
                raise RuntimeError("Turn 1 (init) failed: " + init_res.stderr)
            expected_hash = hashlib.sha256(original.encode()).hexdigest()
            before = client.download_file(a, filename)
            if hashlib.sha256(before).hexdigest() != expected_hash:
                raise RuntimeError("Original file hash mismatch")
            print(json.dumps({"step": "1_init_session_a", "session": mask_session_id(a), "turn_counter": 100, "nonce_set": True, "file_created": filename, "original_sha256": expected_hash}, ensure_ascii=False), file=sys.stderr, flush=True)

            print(f"[session-reuse] [2/4] Session A ({mask_session_id(a)}) --step reuse #1 (same ID) -> incrementing turn_counter to 101", file=sys.stderr, flush=True)
            r1 = client.execute_python(a, "assert turn_counter == 100\nturn_counter += 1\nprint(turn_counter)\nprint(nonce)")
            if r1.status != "succeeded" or r1.stdout.splitlines() != ["101", nonce]:
                raise RuntimeError("Reuse #1 state mismatch")
            print(json.dumps({"step": "2_reuse_session_a_first", "session": mask_session_id(a), "variable_preserved": True, "turn_counter": 101, "nonce_preserved": True}, ensure_ascii=False), file=sys.stderr, flush=True)

            print(f"[session-reuse] [3/4] Session A ({mask_session_id(a)}) --step reuse #2 (same ID) -> incrementing turn_counter to 102 and updating {filename}", file=sys.stderr, flush=True)
            r2 = client.execute_python(a, "assert turn_counter == 101\nturn_counter += 1\nprint(turn_counter)\nprint(nonce)\nopen(" + repr(filename) + ", 'w').write(" + repr(original + "# modified\n") + ")")
            if r2.status != "succeeded" or r2.stdout.splitlines() != ["102", nonce]:
                raise RuntimeError("Reuse #2 state mismatch")
            after = client.download_file(a, filename)
            if after == before or after != (original + "# modified\n").encode():
                raise RuntimeError("File modification mismatch")
            print(json.dumps({"step": "3_reuse_session_a_second", "session": mask_session_id(a), "variable_preserved": True, "turn_counter": 102, "nonce_preserved": True, "file_modified": True}, ensure_ascii=False), file=sys.stderr, flush=True)

            print(f"[session-reuse] [4/4] Session B ({mask_session_id(b)}) --step check (different ID) -> verifying variable and file isolation", file=sys.stderr, flush=True)
            negative = client.execute_python(b, "print('turn_counter' in globals())")
            if negative.status != "succeeded":
                raise RuntimeError("Negative-control execution failed: " + negative.stderr)
            if negative.stdout.strip() != "False":
                raise RuntimeError("Negative control leaked variables")
            try:
                client.download_file(b, filename)
            except Exception as exc:
                from aca_agent_sample.dynamic_sessions import DynamicSessionsError
                if not isinstance(exc, DynamicSessionsError) or exc.status_code != 404:
                    raise
            else:
                raise RuntimeError("Negative control leaked file")
            print(json.dumps({"step": "4_check_session_b_isolation", "session": mask_session_id(b), "variable_exists": False, "turn_counter": None, "file_exists": False}, ensure_ascii=False), file=sys.stderr, flush=True)

            expiry = "skipped: use --live --wait-for-ttl after choosing the pool cooldown"
            if not args.live or args.wait_for_ttl:
                if args.live:
                    time.sleep(args.wait_for_ttl)
                else:
                    client.expire_session(a)
                probe = client.execute_python(a, "print('turn_counter' in globals())")
                if probe.status != "succeeded":
                    raise RuntimeError("Expiry probe failed")
                expiry = "state_lost" if probe.stdout.strip() == "False" else "state_present: cooldown has not elapsed"
                if expiry != "state_lost":
                    raise RuntimeError(expiry)
                try:
                    client.download_file(a, filename)
                except Exception as exc:
                    from aca_agent_sample.dynamic_sessions import DynamicSessionsError
                    if not isinstance(exc, DynamicSessionsError) or exc.status_code != 404:
                        raise
                else:
                    raise RuntimeError("Expired session unexpectedly retained the file")
            print(json.dumps({
                "mode": "live" if args.live else "offline",
                "session": mask_session_id(a),
                "other_session": mask_session_id(b),
                "reuse": "passed",
                "counter_progression": [100, 101, 102],
                "counter": 102,
                "nonce_preserved": True,
                "original_sha256": expected_hash,
                "file_modified": True,
                "other_session_isolated": {"variable_exists": False, "turn_counter": None, "file_exists": False},
                "negative_control": "passed",
                "expiry": expiry,
            }, ensure_ascii=False))
            return 0
    finally:
        client.close()
        if credential: credential.close()


if __name__ == "__main__":
    raise SystemExit(main())


