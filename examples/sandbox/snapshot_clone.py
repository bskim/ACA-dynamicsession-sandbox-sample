"""Create an explicit snapshot and an independent clone, not a stop/resume."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

from azure.core.exceptions import ResourceNotFoundError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aca_agent_sample.auth import create_credential
from aca_agent_sample.config import load_config
from aca_agent_sample.sandboxes.agent_tools import provision_agent_tools, tool_evidence
from aca_agent_sample.sandboxes.client import AcaSandboxAdapter, FakeSandboxAdapter, snapshot_is_ready

ARTIFACT = "/workspace/clone-demo/artifact.txt"


def execute(adapter, sandbox_id, command):
    result = adapter.exec(sandbox_id, command)
    if result.status != "succeeded":
        raise RuntimeError("Sandbox command failed")
    return result.stdout


def artifact_hash(adapter, sandbox_id):
    return hashlib.sha256(execute(adapter, sandbox_id, f"cat {ARTIFACT}").encode("utf-8")).hexdigest()


def wait_for_snapshot(adapter, snapshot_id):
    if getattr(adapter, "simulated", False):
        adapter.get_snapshot(snapshot_id)
        return
    timeout = getattr(adapter, "polling_timeout", 180)
    interval = getattr(adapter, "polling_interval", 3)
    deadline = time.monotonic() + timeout
    while True:
        try:
            snapshot = adapter.get_snapshot(snapshot_id)
        except ResourceNotFoundError:
            snapshot = None
        status = str(getattr(snapshot, "status", "")).casefold()
        if snapshot_is_ready(snapshot, snapshot_id):
            return
        if status in {"failed", "error", "deleted", "deleting"}:
            raise RuntimeError("Snapshot did not become ready")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Snapshot readiness timed out")
        time.sleep(min(interval, remaining))

def run_clone(adapter):
    """Delete only resources created during this run, even after partial creation."""
    existing_sandboxes = set(adapter.sandboxes)
    existing_snapshots = set(adapter.snapshots)
    def _progress(msg: str) -> None:
        print(f"[snapshot-clone] {msg}", file=sys.stderr, flush=True)
    try:
        from aca_agent_sample.sandboxes.lifecycle_benchmark import fixture_source, fixture_install_command, WORKER_PATH
        _progress("1/6 Creating source sandbox (Memory mode)...")
        source = adapter.create(mode="Memory", auto_suspend_seconds=3600)
        adapter.set_lifecycle_policy(source, mode="Memory", interval=3600)
        _progress("2/6 Installing CLI toolchain (git, jq, sqlite3, agent-tool) & starting RAM index server on source...")
        installed = provision_agent_tools(adapter, source)
        execute(adapter, source, "repo-audit init")
        execute(adapter, source, fixture_install_command(fixture_source()))
        execute(adapter, source, f"python3 {WORKER_PATH} start")
        source_worker_at_snapshot = json.loads(execute(adapter, source, f"python3 {WORKER_PATH} probe"))
        if not source_worker_at_snapshot.get("worker_alive"):
            raise RuntimeError("Source RAM index server did not start")
        execute(adapter, source, "mkdir -p /workspace/clone-demo")
        execute(adapter, source, f"echo snapshot-original > {ARTIFACT}")
        original = artifact_hash(adapter, source)
        expected = hashlib.sha256(b"snapshot-original\n").hexdigest()
        if original != expected:
            raise RuntimeError("Source artifact hash mismatch")
        _progress("3/6 Capturing explicit snapshot of warm source sandbox (disk + running RAM process)...")
        snapshot = adapter.create_snapshot(source, name=f"clone-demo-{uuid.uuid4().hex}")
        wait_for_snapshot(adapter, snapshot)
        _progress("4/6 Cloning new sandbox from snapshot (0 package reinstall + 0 process cold-start)...")
        cloned = adapter.clone(snapshot)
        if source == cloned:
            raise RuntimeError("Clone must be a different sandbox")
        cloned_tools = tool_evidence(adapter, cloned)
        if installed['toolchain_sha256'] != cloned_tools['toolchain_sha256']:
            raise RuntimeError('Clone did not preserve installed executable tools/packages/config')
        cloned_worker_initial = json.loads(execute(adapter, cloned, f"python3 {WORKER_PATH} probe"))
        if (not cloned_worker_initial.get("worker_alive")
                or cloned_worker_initial.get("worker_boot_nonce") != source_worker_at_snapshot.get("worker_boot_nonce")
                or cloned_worker_initial.get("in_memory_counter") != source_worker_at_snapshot["in_memory_counter"] + 1):
            raise RuntimeError("Clone did not preserve warm running RAM process from snapshot")
        execute(adapter, cloned, 'repo-audit analyze')
        cloned_hash = artifact_hash(adapter, cloned)
        if cloned_hash != original:
            raise RuntimeError("Snapshot artifact hash mismatch")
        _progress("5/6 Running Memory stop -> resume on the CLONED sandbox itself...")
        adapter.set_lifecycle_policy(cloned, mode="Memory", interval=3600)
        adapter.stop(cloned)
        adapter.resume(cloned)
        cloned_worker_after_resume = json.loads(execute(adapter, cloned, f"python3 {WORKER_PATH} probe"))
        if (not cloned_worker_after_resume.get("worker_alive")
                or cloned_worker_after_resume.get("worker_boot_nonce") != source_worker_at_snapshot.get("worker_boot_nonce")
                or cloned_worker_after_resume.get("in_memory_counter") != cloned_worker_initial["in_memory_counter"] + 1):
            raise RuntimeError("Cloned sandbox failed to preserve its own RAM across Memory stop/resume")
        _progress("6/6 Verifying state isolation between source and clone (both disk and RAM)...")
        execute(adapter, source, f"echo source-modified > {ARTIFACT}")
        modified_hash = artifact_hash(adapter, source)
        after_hash = artifact_hash(adapter, cloned)
        if modified_hash == original or after_hash != original:
            raise RuntimeError("Clone isolation failed")
        from aca_agent_sample.sandboxes.command_agent import PATCH_COMMAND
        clone_report = json.loads(execute(adapter, cloned, PATCH_COMMAND).strip().splitlines()[-1])
        source_project = json.loads(execute(adapter, source, 'repo-audit analyze'))
        if source_project['config']['retries'] != 1 or source_project['config']['audit'] is not False:
            raise RuntimeError('Clone patch affected source project')
        source_worker_after = json.loads(execute(adapter, source, f"python3 {WORKER_PATH} probe"))
        if source_worker_after.get("in_memory_counter") != source_worker_at_snapshot["in_memory_counter"] + 1:
            raise RuntimeError("Clone RAM activity leaked back into source RAM counter")
        return {"mode": "offline" if getattr(adapter, "simulated", False) else "live",
                "operation": "explicit_snapshot_clone", "artifact": ARTIFACT,
                "source_sha256_at_snapshot": original, "clone_sha256": cloned_hash,
                "source_sha256_after_change": modified_hash, "clone_sha256_after_change": after_hash,
                "source_project_unchanged": True, "clone_verification": clone_report,
                "installed_tool_preserved": True, "clone_provision_count": 0,
                "clone_ram_preserved_from_snapshot": True, "clone_worker_restarted": False,
                "clone_memory_stop_resume_passed": True,
                "counter_progression": {
                    "source_at_snapshot": source_worker_at_snapshot["in_memory_counter"],
                    "clone_immediately_after_create": cloned_worker_initial["in_memory_counter"],
                    "clone_after_own_memory_stop_resume": cloned_worker_after_resume["in_memory_counter"],
                    "source_after_independent_probe": source_worker_after["in_memory_counter"],
                },
                "tools_at_snapshot": installed, "tools_in_clone": cloned_tools,
                "distinct_sandboxes": True, "isolation_after_clone": True, "passed": True,
                "warning": "Explicit snapshot clone is separate from stop/resume; offline mode is simulated."}
    finally:
        errors = []
        for sandbox_id in reversed(list(adapter.sandboxes)):
            if sandbox_id not in existing_sandboxes:
                try:
                    adapter.delete(sandbox_id)
                except Exception:
                    errors.append(RuntimeError("Sandbox cleanup failed"))
        for snapshot_id in set(adapter.snapshots) - existing_snapshots:
            try:
                adapter.delete_snapshot(snapshot_id)
            except Exception:
                errors.append(RuntimeError("Snapshot cleanup failed"))
        if errors:
            raise ExceptionGroup("Cleanup incomplete; use the private recovery manifest", errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use Azure only when ENABLE_LIVE_AZURE=true")
    args = parser.parse_args(argv)
    credential = None
    try:
        if args.live:
            config = load_config()
            if not config.enable_live_azure:
                parser.error("--live requires ENABLE_LIVE_AZURE=true")
            config.validate("sandbox")
            credential = create_credential(config)
            manifest = ROOT / ".internal" / "runs" / f"clone-{uuid.uuid4().hex}.json"
            adapter = AcaSandboxAdapter(config, credential, manifest_path=str(manifest))
        else:
            adapter = FakeSandboxAdapter()
        print(json.dumps(run_clone(adapter), indent=2))
        return 0
    except Exception:
        print("Snapshot clone failed; details suppressed to protect sensitive configuration.", file=sys.stderr)
        return 1
    finally:
        if credential is not None:
            credential.close()


if __name__ == "__main__":
    raise SystemExit(main())


