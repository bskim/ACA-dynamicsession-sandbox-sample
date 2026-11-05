from __future__ import annotations

import copy
import hashlib
import secrets
import json
import os
import shlex
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from azure.containerapps.sandbox import (
    AutoDeletePolicy, AutoSuspendPolicy, LifecyclePolicy, SandboxClient,
    SandboxGroupClient, endpoint_for_region,
)

from ..auth import verify_token_tenant
from ..config import SampleConfig
from ..types import ToolExecutionResult


def snapshot_is_ready(snapshot: Any, snapshot_id: str) -> bool:
    status = getattr(snapshot, "status", None)
    if status is None:
        # The synchronous snapshot API omits status; a matching GET proves existence.
        return getattr(snapshot, "id", None) == snapshot_id
    return str(status).casefold() in {"ready", "succeeded", "available", "completed"}


def _validate_mode(mode: str, data_disk: bool = False) -> None:
    if mode not in {"Memory", "Disk"}:
        raise ValueError("Suspend mode must be Memory or Disk")
    if data_disk and mode == "Memory":
        raise ValueError("Memory mode cannot be used with a Data Disk volume")


def _data_disk(volumes: Any) -> bool:
    # SandboxVolume only exposes a volume name, not its backing type. Conservatively
    # reject Memory for any attachment whose type cannot be verified locally.
    return bool(volumes)

class _TenantCredential:
    def __init__(self, credential: Any, tenant_id: str):
        self.credential, self.tenant_id = credential, tenant_id

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        token = self.credential.get_token(*scopes, **kwargs)
        verify_token_tenant(token.token, self.tenant_id)
        return token


class AcaSandboxAdapter:
    """Only resources created by this instance may be operated on or deleted."""

    def __init__(self, config: SampleConfig, credential: Any = None, *, group_client: Any = None,
                 polling_timeout: int = 180, polling_interval: int = 3, manifest_path: str | None = None):
        if polling_timeout <= 0 or polling_interval <= 0:
            raise ValueError("Polling timeout and interval must be positive")
        self.polling_timeout, self.polling_interval = polling_timeout, polling_interval
        self.sandboxes: dict[str, SandboxClient] = {}
        self.snapshots: set[str] = set()
        self._data_disks: dict[str, bool] = {}
        self._snapshot_data_disks: dict[str, bool] = {}
        self._manifest_path: Path | None = None
        self._manifest = {"version": 1, "run_id": uuid.uuid4().hex,
                          "subscription_id": config.subscription_id, "resource_group": config.sandbox_resource_group,
                          "sandbox_group": config.sandbox_group_name, "region": config.location,
                          "sandbox_ids": [], "snapshot_ids": [], "session_pool_ids": []}
        if manifest_path:
            root = (Path(__file__).resolve().parents[3] / ".internal" / "runs").resolve()
            path = Path(manifest_path).resolve()
            if not path.is_relative_to(root) or path.suffix != ".json":
                raise ValueError("Manifest must be a JSON file inside .internal/runs")
            root.mkdir(parents=True, exist_ok=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as file:
                json.dump(self._manifest, file)
            self._manifest_path = path
        if group_client is None:
            config.validate("sandbox")
            if not config.enable_live_azure:
                raise ValueError("Live Azure must be explicitly enabled")
            if credential is None:
                raise ValueError("A credential is required")
            self.group = SandboxGroupClient(
                endpoint_for_region(config.location), _TenantCredential(credential, config.tenant_id),
                subscription_id=config.subscription_id, resource_group=config.sandbox_resource_group,
                sandbox_group=config.sandbox_group_name, api_version=config.sandbox_api_version,
                connection_timeout=10, read_timeout=60, retry_total=0,
            )
        else:
            self.group = group_client

    def _save_manifest(self) -> None:
        if self._manifest_path is not None:
            self._manifest["sandbox_ids"] = list(self.sandboxes)
            self._manifest["snapshot_ids"] = sorted(self.snapshots)
            pending = self._manifest_path.with_name(self._manifest_path.name + "." + uuid.uuid4().hex + ".partial")
            try:
                with pending.open("x", encoding="utf-8") as file:
                    json.dump(self._manifest, file, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                pending.replace(self._manifest_path)
            finally:
                pending.unlink(missing_ok=True)
    def _sandbox(self, sandbox_id: str) -> Any:
        try:
            return self.sandboxes[sandbox_id]
        except KeyError:
            raise ValueError("Sandbox is not owned by this adapter") from None

    def _register(self, sandbox: Any, data_disk: bool = False) -> str:
        sandbox_id = sandbox.sandbox_id
        if not isinstance(sandbox_id, str) or not sandbox_id:
            raise ValueError("Sandbox client returned no ID")
        self.sandboxes[sandbox_id] = sandbox
        self._data_disks[sandbox_id] = data_disk
        self._save_manifest()
        return sandbox_id

    def _await_created(self, poller: Any, data_disk: bool = False) -> str:
        # 0.1.0b4's poller retains the initial DTO before Running. Capture it so
        # a polling failure does not lose ownership of an already-created VM.
        method = getattr(poller, "polling_method", None)
        initial = getattr(method(), "_initial_resource", None) if callable(method) else None
        initial_id = getattr(initial, "id", None)
        if isinstance(initial_id, str) and initial_id:
            self._register(self.group.get_sandbox_client(initial_id), data_disk)
        sandbox = poller.result()
        if initial_id and initial_id != sandbox.sandbox_id:
            self._register(sandbox, data_disk)
            raise RuntimeError("Sandbox create returned inconsistent IDs")
        return self._register(sandbox, data_disk)
    def create(self, *, mode: str = "Memory", auto_suspend_seconds: int = 300,
               volumes: Any = None) -> str:
        data_disk = _data_disk(volumes)
        _validate_mode(mode, data_disk)
        if type(auto_suspend_seconds) is not int or auto_suspend_seconds <= 0:
            raise ValueError("Auto-suspend interval must be a positive integer")
        kwargs = dict(disk="ubuntu", cpu="1000m", memory="2048Mi",
                      auto_suspend_seconds=auto_suspend_seconds, auto_suspend_mode=mode,
                      polling_timeout=self.polling_timeout, polling_interval=self.polling_interval)
        if volumes is not None:
            kwargs["volumes"] = volumes
        sandbox_id = self._await_created(self.group.begin_create_sandbox(**kwargs), data_disk)
        # The stock Ubuntu disk does not contain the sample workspace.
        result = self.exec(sandbox_id, "mkdir -p /workspace", working_directory="/")
        if result.status != "succeeded":
            raise RuntimeError("Sandbox workspace initialization failed")
        return sandbox_id

    def set_lifecycle_policy(self, sandbox_id: str, *, mode: str, interval: int = 300) -> Any:
        sandbox = self._sandbox(sandbox_id)
        _validate_mode(mode, self._data_disks[sandbox_id])
        if type(interval) is not int or interval <= 0:
            raise ValueError("Auto-suspend interval must be a positive integer")
        return sandbox.set_lifecycle_policy(LifecyclePolicy(
            auto_suspend=AutoSuspendPolicy(enabled=True, interval=interval, mode=mode),
            auto_delete=AutoDeletePolicy(enabled=False),
        ))

    def get(self, sandbox_id: str) -> Any:
        return self._sandbox(sandbox_id).get()

    def _wait_stopped(self, sandbox: Any) -> None:
        deadline = time.monotonic() + self.polling_timeout
        while True:
            state = sandbox.get().state
            normalized = state.casefold() if isinstance(state, str) else ""
            if normalized in {"stopped", "suspended", "idle"}:
                return
            if normalized in {"failed", "deleting"}:
                raise RuntimeError(f"Sandbox entered {state}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Sandbox did not stop")
            time.sleep(min(self.polling_interval, remaining))

    def stop(self, sandbox_id: str) -> dict[str, float]:
        sandbox = self._sandbox(sandbox_id)
        start = time.monotonic()
        sandbox.stop()
        returned = time.monotonic()
        self._wait_stopped(sandbox)
        return {"stop_api_ms": (returned - start) * 1000,
                "stop_to_stopped_ms": (time.monotonic() - start) * 1000}

    def resume(self, sandbox_id: str) -> dict[str, float]:
        sandbox = self._sandbox(sandbox_id)
        start = time.monotonic()
        sandbox.resume()
        returned = time.monotonic()
        sandbox.wait_for_running(timeout=self.polling_timeout, poll_interval=self.polling_interval)
        return {"resume_api_ms": (returned - start) * 1000,
                "resume_to_running_ms": (time.monotonic() - start) * 1000}

    def exec(self, sandbox_id: str, command: str, *, working_directory: str = "/workspace") -> ToolExecutionResult:
        result = self._sandbox(sandbox_id).exec(command, working_directory=working_directory)
        if type(result.exit_code) is not int or not isinstance(result.stdout, str) or not isinstance(result.stderr, str):
            raise ValueError("Invalid sandbox exec result")
        return ToolExecutionResult("succeeded" if result.exit_code == 0 else "failed", result.stdout,
                                   result.stderr, result.exit_code,
                                   None if result.exit_code == 0 else "execution_failed")

    def create_snapshot(self, sandbox_id: str, *, name: str | None = None) -> str:
        snapshot = self._sandbox(sandbox_id).create_snapshot(name=name)
        snapshot_id = snapshot.id
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise ValueError("Snapshot returned no ID")
        self.snapshots.add(snapshot_id)
        self._snapshot_data_disks[snapshot_id] = self._data_disks[sandbox_id]
        self._save_manifest()
        return snapshot_id

    def get_snapshot(self, snapshot_id: str) -> Any:
        if snapshot_id not in self.snapshots:
            raise ValueError("Snapshot is not owned by this adapter")
        return self.group.get_snapshot(snapshot_id)

    def clone(self, snapshot_id: str) -> str:
        snapshot = self.get_snapshot(snapshot_id)
        if not snapshot_is_ready(snapshot, snapshot_id):
            raise RuntimeError("Snapshot is not ready")
        poller = self.group.begin_create_sandbox(snapshot_id=snapshot_id,
                    polling_timeout=self.polling_timeout, polling_interval=self.polling_interval)
        return self._await_created(poller, self._snapshot_data_disks[snapshot_id])

    def delete_snapshot(self, snapshot_id: str) -> None:
        if snapshot_id not in self.snapshots:
            raise ValueError("Snapshot is not owned by this adapter")
        self.group.delete_snapshot(snapshot_id)
        self.snapshots.remove(snapshot_id)
        del self._snapshot_data_disks[snapshot_id]
        self._save_manifest()

    def delete(self, sandbox_id: str) -> None:
        sandbox = self._sandbox(sandbox_id)
        sandbox.begin_delete(polling_timeout=self.polling_timeout, polling_interval=self.polling_interval).result()
        del self.sandboxes[sandbox_id]
        del self._data_disks[sandbox_id]
        self._save_manifest()

    def cleanup(self) -> None:
        errors = []
        for sandbox_id in list(self.sandboxes):
            try:
                self.delete(sandbox_id)
            except Exception as exc:
                errors.append(exc)
        for snapshot_id in list(self.snapshots):
            try:
                self.delete_snapshot(snapshot_id)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("Sandbox cleanup failed; ownership retained for retry", errors)


@dataclass
class _FakeSandbox:
    sandbox_id: str
    mode: str
    state: str = "Running"
    files: dict[str, str] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    data_disk: bool = False
    interval: int = 300

    @property
    def lifecycle(self) -> LifecyclePolicy:
        return LifecyclePolicy(auto_suspend=AutoSuspendPolicy(enabled=True, interval=self.interval, mode=self.mode),
                               auto_delete=AutoDeletePolicy(enabled=False))


class FakeSandboxAdapter:
    """Deterministic simulator, not a shell or a security boundary.

    Snapshot copies simulated filesystem and RAM state; this is not evidence that
    Azure snapshots preserve process memory. Unsupported commands fail closed.
    """
    simulated = True

    def __init__(self):
        self.sandboxes: dict[str, _FakeSandbox] = {}
        self.snapshots: dict[str, _FakeSandbox] = {}

    def _sandbox(self, sandbox_id: str) -> _FakeSandbox:
        try:
            return self.sandboxes[sandbox_id]
        except KeyError:
            raise ValueError("Sandbox is not owned by this adapter") from None

    def create(self, *, mode: str = "Memory", auto_suspend_seconds: int = 300, volumes: Any = None) -> str:
        data_disk = _data_disk(volumes)
        _validate_mode(mode, data_disk)
        if type(auto_suspend_seconds) is not int or auto_suspend_seconds <= 0:
            raise ValueError("Auto-suspend interval must be a positive integer")
        sandbox_id = uuid.uuid4().hex
        self.sandboxes[sandbox_id] = _FakeSandbox(sandbox_id, mode, data_disk=data_disk, interval=auto_suspend_seconds)
        return sandbox_id

    def set_lifecycle_policy(self, sandbox_id: str, *, mode: str, interval: int = 300) -> LifecyclePolicy:
        sandbox = self._sandbox(sandbox_id)
        _validate_mode(mode, sandbox.data_disk)
        if type(interval) is not int or interval <= 0:
            raise ValueError("Auto-suspend interval must be a positive integer")
        sandbox.mode, sandbox.interval = mode, interval
        return sandbox.lifecycle

    def get(self, sandbox_id: str) -> _FakeSandbox:
        return self._sandbox(sandbox_id)

    def stop(self, sandbox_id: str) -> dict[str, float]:
        sandbox = self._sandbox(sandbox_id)
        sandbox.state = "Stopped"
        if sandbox.mode == "Disk":
            sandbox.memory.clear()
        return {"stop_api_ms": 0.0, "stop_to_stopped_ms": 0.0}

    def resume(self, sandbox_id: str) -> dict[str, float]:
        self._sandbox(sandbox_id).state = "Running"
        return {"resume_api_ms": 0.0, "resume_to_running_ms": 0.0}

    def exec(self, sandbox_id: str, command: str, *, working_directory: str = "/workspace") -> ToolExecutionResult:
        sandbox = self._sandbox(sandbox_id)
        if sandbox.state != "Running":
            return ToolExecutionResult("failed", stderr="Sandbox is not running", error_kind="state_lost")
        try:
            parts = shlex.split(command, posix=True)
            if not parts:
                raise ValueError("Empty command")
            from .agent_tools import provision_command, TOOL_PATH, CONFIG_PATH
            if command == provision_command():
                if TOOL_PATH in sandbox.files:
                    raise ValueError("Tools are already provisioned")
                sandbox.files[TOOL_PATH] = "simulated agent-tool-v1"
                sandbox.files[CONFIG_PATH] = secrets.token_hex(16)
                for name in ("git", "jq", "sqlite3"):
                    sandbox.files[f"/usr/bin/{name}"] = f"simulated {name} package"
                sandbox.files['/usr/local/bin/repo-audit'] = 'agent-tool'
                return ToolExecutionResult("succeeded", stdout=json.dumps(self._tool_evidence(sandbox)), exit_code=0)
            if parts[0] in (TOOL_PATH, '/usr/local/bin/repo-audit', 'repo-audit', 'agent-tool'):
                if not self._tool_evidence(sandbox)['installed_tool_runs']:
                    raise ValueError("Agent tools are not installed")
                action = parts[1] if len(parts) == 2 else ''
                if action == '--versions':
                    data = self._tool_evidence(sandbox)
                elif action == 'cached-audit':
                    return self._worker_command(sandbox, 'increment')
                elif action == 'init':
                    sandbox.files['/workspace/agent-project/config.json'] = json.dumps({'service': 'demo', 'retries': 1, 'audit': False})
                    sandbox.files['/workspace/agent-project/audit.db'] = 'initialized'
                    data = {'initialized': True}
                elif action in {'analyze', 'verify'}:
                    data = {'config': json.loads(sandbox.files['/workspace/agent-project/config.json']),
                            'events': sandbox.files['/workspace/agent-project/audit.db']}
                    if action == 'verify':
                        data['passed'] = data['config']['retries'] == 3 and data['config']['audit'] is True and 'patched' in data['events']
                        sandbox.files['/workspace/agent-project/verification.json'] = json.dumps(data)
                        if not data['passed']:
                            raise ValueError('Patch validation failed')
                else:
                    raise ValueError('Unsupported simulated agent tool')
                return ToolExecutionResult('succeeded', stdout=json.dumps(data), exit_code=0)
            from .command_agent import ANALYZE_COMMAND, PATCH_COMMAND
            if command == ANALYZE_COMMAND or command == PATCH_COMMAND:
                if not self._tool_evidence(sandbox)['installed_tool_runs']:
                    raise ValueError('Agent tools are not installed')
                if command == PATCH_COMMAND:
                    sandbox.files['/workspace/agent-project/config.json'] = json.dumps({'service': 'demo', 'retries': 3, 'audit': True})
                    sandbox.files['/workspace/agent-project/audit.db'] += ',patched'
                    return self.exec(sandbox_id, 'repo-audit verify')
                return self.exec(sandbox_id, 'repo-audit analyze')
            if parts[0] == "python3":
                from .lifecycle_benchmark import WORKER_PATH, decode_fixture_install
                source = decode_fixture_install(parts)
                if source is not None:
                    sandbox.files[WORKER_PATH] = source
                    return ToolExecutionResult("succeeded", exit_code=0)
                if len(parts) == 3 and parts[1] == WORKER_PATH and WORKER_PATH in sandbox.files:
                    return self._worker_command(sandbox, parts[2])
                raise ValueError("Unsupported simulated Python command")
            if parts[0] == "mkdir" and len(parts) >= 2:
                return ToolExecutionResult("succeeded", exit_code=0)
            if parts[0] == "sh" and len(parts) == 2:
                path = str(PurePosixPath(working_directory, parts[1]))
                script = sandbox.files.get(path, "").strip()
                if not script.startswith("echo ") or "\n" in script:
                    raise ValueError("Unsupported simulated script")
                return ToolExecutionResult("succeeded", stdout=script[5:] + "\n", exit_code=0)
            if parts[0] == "echo" and ">" in parts:
                marker = parts.index(">")
                if marker < 1 or marker + 2 != len(parts):
                    raise ValueError("Unsupported redirection")
                path = str(PurePosixPath(working_directory, parts[-1]))
                sandbox.files[path] = " ".join(parts[1:marker]) + "\n"
                return ToolExecutionResult("succeeded", exit_code=0)
            if parts[0] == "cat" and len(parts) == 2:
                path = str(PurePosixPath(working_directory, parts[1]))
                if path not in sandbox.files:
                    raise ValueError("File not found")
                return ToolExecutionResult("succeeded", stdout=sandbox.files[path], exit_code=0)
            if parts[0] == "echo":
                return ToolExecutionResult("succeeded", stdout=" ".join(parts[1:]) + "\n", exit_code=0)
            if parts[0] == "set_ram" and len(parts) == 3:
                sandbox.memory[parts[1]] = parts[2]
                return ToolExecutionResult("succeeded", exit_code=0)
            if parts[0] == "get_ram" and len(parts) == 2 and parts[1] in sandbox.memory:
                return ToolExecutionResult("succeeded", stdout=sandbox.memory[parts[1]] + "\n", exit_code=0)
            raise ValueError("Unsupported simulated command")
        except ValueError as exc:
            return ToolExecutionResult("failed", stderr=str(exc), exit_code=1, error_kind="execution_failed")

    @staticmethod
    def _tool_evidence(sandbox: _FakeSandbox) -> dict:
        from .agent_tools import TOOL_PATH, CONFIG_PATH
        paths = (TOOL_PATH, CONFIG_PATH, '/usr/local/bin/repo-audit', '/usr/bin/git', '/usr/bin/jq', '/usr/bin/sqlite3')
        installed = all(path in sandbox.files for path in paths)
        digest = hashlib.sha256(json.dumps({path: sandbox.files.get(path) for path in paths}, sort_keys=True).encode()).hexdigest()
        return {'installed_tool_runs': installed, 'toolchain_sha256': digest if installed else None,
                'versions': {name: 'simulated' for name in ('git', 'jq', 'sqlite3')}}

    def _worker_command(self, sandbox: _FakeSandbox, action: str) -> ToolExecutionResult:
        if action not in {"start", "probe", "increment", "stop"}:
            raise ValueError("Unknown worker command")
        path = "/workspace/demo/sentinel.txt"
        worker = sandbox.memory.get("state_worker")
        started = action == "start" and worker is None
        if started:
            sandbox.files.setdefault(path, "ACA sandbox lifecycle disk witness v1\n")
            worker = {"worker_alive": True, "worker_boot_nonce": secrets.token_hex(32),
                      "in_memory_counter": 0,
                      "buffer_checksum": hashlib.sha256(secrets.token_bytes(65536)).hexdigest(),
                      "process_start_fingerprint": secrets.token_hex(32), "pid": 101,
                      "tool_server_role": "repository-index-cache", "index_entry_count": 3, "index_sha256": hashlib.sha256(b'simulated repository index').hexdigest()}
            sandbox.memory["state_worker"] = worker
        if action == "increment" and worker is None:
            raise ValueError("Worker is not running")
        if action == "stop":
            sandbox.memory.pop("state_worker", None)
            worker = None
        elif worker is not None:
            worker["in_memory_counter"] += 1
        data = {"disk_exists": path in sandbox.files,
                "disk_sha256": hashlib.sha256(sandbox.files[path].encode()).hexdigest() if path in sandbox.files else None}
        data.update(self._tool_evidence(sandbox))
        data.update(worker or {"worker_alive": False})
        if action == "start":
            data["worker_started"] = started
        return ToolExecutionResult("succeeded", stdout=json.dumps(data), exit_code=0)

    def create_snapshot(self, sandbox_id: str, *, name: str | None = None) -> str:
        snapshot_id = uuid.uuid4().hex
        self.snapshots[snapshot_id] = copy.deepcopy(self._sandbox(sandbox_id))
        return snapshot_id

    def get_snapshot(self, snapshot_id: str) -> _FakeSandbox:
        try:
            return copy.deepcopy(self.snapshots[snapshot_id])
        except KeyError:
            raise ValueError("Snapshot is not owned by this adapter") from None

    def clone(self, snapshot_id: str) -> str:
        sandbox = self.get_snapshot(snapshot_id)
        sandbox.sandbox_id = uuid.uuid4().hex
        sandbox.state = "Running"
        self.sandboxes[sandbox.sandbox_id] = sandbox
        return sandbox.sandbox_id

    def delete_snapshot(self, snapshot_id: str) -> None:
        self.get_snapshot(snapshot_id)
        del self.snapshots[snapshot_id]

    def delete(self, sandbox_id: str) -> None:
        self._sandbox(sandbox_id)
        del self.sandboxes[sandbox_id]

    def cleanup(self) -> None:
        self.sandboxes.clear()
        self.snapshots.clear()






