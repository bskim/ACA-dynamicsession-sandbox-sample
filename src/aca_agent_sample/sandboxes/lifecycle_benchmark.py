"""Stop/resume state oracle; fake timings are simulations, not Azure measurements."""
from __future__ import annotations

import base64
import csv
from dataclasses import asdict, dataclass, fields
import io
import json
import math
from pathlib import Path
import re
import shlex
import statistics
import time
from typing import Any, Callable, Mapping

from .client import _validate_mode
from .agent_tools import provision_agent_tools

WORKER_PATH = "/workspace/demo/state_worker.py"
LATENCIES = ("stop_to_stopped_ms", "resume_to_running_ms", "first_exec_probe_ms", "worker_ready_ms")
_INSTALL_PREFIX = "import base64;from pathlib import Path;p=Path('/workspace/demo/state_worker.py');p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(base64.b64decode('"
_INSTALL_SUFFIX = "'))"


def fixture_source() -> str:
    return (Path(__file__).resolve().parents[3] / "examples" / "sandbox" / "fixtures" / "state_worker.py").read_text(encoding="utf-8-sig")


def fixture_install_command(source: str) -> str:
    return "python3 -c " + shlex.quote(_INSTALL_PREFIX + base64.b64encode(source.encode()).decode() + _INSTALL_SUFFIX)


def decode_fixture_install(parts: list[str]) -> str | None:
    """Recognize only the fixed trusted installer; never execute Python on the host."""
    if len(parts) != 3 or parts[:2] != ["python3", "-c"]:
        return None
    program = parts[2]
    if not program.startswith(_INSTALL_PREFIX) or not program.endswith(_INSTALL_SUFFIX):
        return None
    encoded = program[len(_INSTALL_PREFIX):-len(_INSTALL_SUFFIX)]
    return base64.b64decode(encoded, validate=True).decode("utf-8")


@dataclass(frozen=True)
class WorkerObservation:
    disk_exists: bool
    disk_sha256: str | None
    worker_alive: bool
    worker_boot_nonce: str | None = None
    in_memory_counter: int | None = None
    buffer_checksum: str | None = None
    process_start_fingerprint: str | None = None
    pid: int | None = None
    worker_started: bool | None = None
    installed_tool_runs: bool = False
    toolchain_sha256: str | None = None
    index_entry_count: int | None = None
    index_sha256: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> WorkerObservation:
        if not isinstance(value, Mapping):
            raise ValueError("Invalid worker observation")
        known = {item.name for item in fields(cls)}
        result = cls(**{key: item for key, item in value.items() if key in known})
        if type(result.disk_exists) is not bool or type(result.worker_alive) is not bool:
            raise ValueError("Invalid worker observation flags")
        return result

    @property
    def valid_disk(self) -> bool:
        return self.disk_exists is True and isinstance(self.disk_sha256, str) and bool(re.fullmatch(r"[0-9a-f]{64}", self.disk_sha256))

    @property
    def valid_tools(self) -> bool:
        return (self.installed_tool_runs is True and isinstance(self.toolchain_sha256, str)
                and bool(re.fullmatch(r"[0-9a-f]{64}", self.toolchain_sha256)))

    @property
    def valid_ram(self) -> bool:
        return (self.worker_alive is True and type(self.in_memory_counter) is int and self.in_memory_counter > 0
                and all(isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))
                        for value in (self.worker_boot_nonce, self.buffer_checksum, self.process_start_fingerprint)))


def _observation(value: WorkerObservation | Mapping[str, Any]) -> WorkerObservation:
    return value if isinstance(value, WorkerObservation) else WorkerObservation.from_mapping(value)


@dataclass(frozen=True)
class SnapshotModeOracle:
    mode: str
    disk_preserved: bool
    memory_preserved: bool
    worker_restarted: bool
    passed: bool
    failures: tuple[str, ...] = ()
    installed_tool_preserved: bool = False

    @classmethod
    def evaluate(cls, mode: str, before: WorkerObservation | Mapping[str, Any],
                 after: WorkerObservation | Mapping[str, Any],
                 restarted: WorkerObservation | Mapping[str, Any] | None = None) -> SnapshotModeOracle:
        return evaluate_mode_observation(mode, before, after, restarted)


def evaluate_mode_observation(mode: str, before: WorkerObservation | Mapping[str, Any],
                              after: WorkerObservation | Mapping[str, Any],
                              restarted: WorkerObservation | Mapping[str, Any] | None = None) -> SnapshotModeOracle:
    """`after` MUST be taken before bootstrap; PID equality is never evidence."""
    _validate_mode(mode)
    before, after = _observation(before), _observation(after)
    restarted = _observation(restarted) if restarted is not None else None
    disk = before.valid_disk and after.valid_disk and before.disk_sha256 == after.disk_sha256
    if restarted is not None:
        disk = disk and restarted.valid_disk and restarted.disk_sha256 == before.disk_sha256
    tools = before.valid_tools and after.valid_tools and before.toolchain_sha256 == after.toolchain_sha256
    if restarted is not None:
        tools = tools and restarted.valid_tools and restarted.toolchain_sha256 == before.toolchain_sha256
    memory = (before.valid_ram and after.valid_ram
              and before.worker_boot_nonce == after.worker_boot_nonce
              and before.buffer_checksum == after.buffer_checksum
              and before.process_start_fingerprint == after.process_start_fingerprint
              and after.in_memory_counter > before.in_memory_counter)
    changed_identity = before.valid_ram and after.valid_ram and (
        before.worker_boot_nonce != after.worker_boot_nonce
        or before.process_start_fingerprint != after.process_start_fingerprint)
    restarted_ok = (restarted is not None and before.valid_ram and restarted.valid_ram
                    and restarted.worker_started is True and restarted.worker_boot_nonce != before.worker_boot_nonce
                    and restarted.process_start_fingerprint != before.process_start_fingerprint
                    and restarted.in_memory_counter == 1 and restarted.in_memory_counter < before.in_memory_counter)
    worker_restarted = bool(changed_identity or restarted_ok)
    failures = []
    if not before.valid_ram:
        failures.append("invalid_baseline")
    if not tools:
        failures.append("installed_tool_not_preserved")
    if not disk:
        failures.append("disk_not_preserved")
    if mode == "Memory":
        if not memory:
            failures.append("memory_not_preserved")
        if restarted is not None or worker_restarted:
            failures.append("unexpected_worker_restart")
    else:
        if after.worker_alive:
            failures.append("old_worker_still_running")
        if not restarted_ok:
            failures.append("worker_restart_not_proven")
    return SnapshotModeOracle(mode, bool(disk), bool(memory), worker_restarted, not failures, tuple(failures), bool(tools))


@dataclass
class ModeComparisonMetrics:
    mode: str
    iteration: int = 1
    stop_to_stopped_ms: float | None = None
    resume_to_running_ms: float | None = None
    first_exec_probe_ms: float | None = None
    worker_ready_ms: float | None = None
    success: bool = False
    failure_code: str | None = None
    before: WorkerObservation | None = None
    after: WorkerObservation | None = None
    restarted: WorkerObservation | None = None
    oracle: SnapshotModeOracle | None = None
    simulated: bool = False


def aggregate_metrics(samples: list[ModeComparisonMetrics]) -> dict[str, Any]:
    """Latency statistics use successful runs; excluded failures remain explicit."""
    result = {}
    for mode in dict.fromkeys(sample.mode for sample in samples):
        selected = [sample for sample in samples if sample.mode == mode]
        success = [sample for sample in selected if sample.success]
        summary = {"sample_count": len(selected), "success_count": len(success),
                   "failure_count": len(selected) - len(success), "latencies": {}}
        for name in LATENCIES:
            values = sorted(value for sample in success if (value := getattr(sample, name)) is not None)
            summary["latencies"][name] = {
                "sample_count": len(values), "excluded_count": len(selected) - len(values),
                "mean_ms": statistics.mean(values) if values else None,
                "p50_ms": statistics.median(values) if values else None,
                "p95_ms": values[max(0, math.ceil(len(values) * 0.95) - 1)] if values else None,
                "min_ms": min(values) if values else None, "max_ms": max(values) if values else None,
            }
        result[mode] = summary
    return result


def metrics_to_json(samples: list[ModeComparisonMetrics]) -> str:
    return json.dumps({"samples": [asdict(sample) for sample in samples], "summary": aggregate_metrics(samples)}, indent=2, allow_nan=False)


def metrics_to_csv(samples: list[ModeComparisonMetrics]) -> str:
    output = io.StringIO(newline="")
    names = ["mode", "iteration", "simulated", "success", "failure_code", *LATENCIES,
             "installed_tool_preserved", "disk_preserved", "memory_preserved", "worker_restarted"]
    writer = csv.DictWriter(output, fieldnames=names)
    writer.writeheader()
    for sample in samples:
        row = {name: getattr(sample, name) for name in names if hasattr(sample, name)}
        if sample.oracle:
            row.update({name: getattr(sample.oracle, name) for name in names[-4:]})
        writer.writerow(row)
    return output.getvalue()


def metrics_to_markdown(samples: list[ModeComparisonMetrics]) -> str:
    simulation = "yes" if samples and all(sample.simulated for sample in samples) else "mixed" if any(sample.simulated for sample in samples) else "no"
    rows = [f"Simulation: {simulation} (simulated timings are not Azure benchmarks).", "",
            "| Mode | Samples | Success | Failure | Stage | Mean ms | p50 ms | p95 ms | Excluded |",
            "| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |"]
    for mode, summary in aggregate_metrics(samples).items():
        for stage, stats in summary["latencies"].items():
            values = ["n/a" if stats[key] is None else f"{stats[key]:.3f}" for key in ("mean_ms", "p50_ms", "p95_ms")]
            rows.append(f"| {mode} | {summary['sample_count']} | {summary['success_count']} | {summary['failure_count']} | {stage} | " + " | ".join(values) + f" | {stats['excluded_count']} |")
    return "\n".join(rows) + "\n"


class BenchmarkFailure(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SandboxLifecycleComparator:
    """Sequential, alternating paired runs, each with a fresh owned sandbox.

    Provisioning/fixture installation are outside all four measured stages. No
    command is retried and Memory never bootstraps after resume. Failures are
    returned as samples, including partial timing and cleanup failures.
    """
    def __init__(self, adapter: Any, *, source: str | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 hold_seconds: float = 0, auto_suspend_seconds: int = 3600):
        if not math.isfinite(hold_seconds) or hold_seconds < 0:
            raise ValueError("Hold duration must be finite and nonnegative")
        if type(auto_suspend_seconds) is not int or auto_suspend_seconds < 300:
            raise ValueError("Benchmark auto-suspend interval must be at least 300 seconds")
        self.adapter, self.source = adapter, fixture_source() if source is None else source
        self.clock, self.sleep = clock, sleep
        self.hold_seconds, self.auto_suspend_seconds = hold_seconds, auto_suspend_seconds

    def _exec(self, sid: str, command: str) -> str:
        result = self.adapter.exec(sid, command)
        if result.status != "succeeded":
            raise BenchmarkFailure(result.error_kind or "execution_failed")
        return result.stdout

    def _worker(self, sid: str, action: str) -> WorkerObservation:
        return WorkerObservation.from_mapping(json.loads(self._exec(sid, f"python3 {WORKER_PATH} {action}")))

    def run_once(self, mode: str, *, iteration: int = 1, volumes: Any = None) -> ModeComparisonMetrics:
        _validate_mode(mode, bool(volumes))
        if type(iteration) is not int or iteration <= 0:
            raise ValueError("Iteration must be a positive integer")
        sample = ModeComparisonMetrics(mode, iteration, simulated=bool(getattr(self.adapter, "simulated", False)))
        previous_ids = set(self.adapter.sandboxes)
        import sys
        from .client import AcaSandboxAdapter
        is_live = type(self.adapter) is AcaSandboxAdapter
        def _progress(msg: str) -> None:
            if is_live:
                print(f"[compare-modes][{mode} #{iteration}] {msg}", file=sys.stderr, flush=True)
        try:
            _progress("1/4 Creating sandbox & setting lifecycle policy...")
            sid = self.adapter.create(mode=mode, auto_suspend_seconds=self.auto_suspend_seconds, volumes=volumes)
            self.adapter.set_lifecycle_policy(sid, mode=mode, interval=self.auto_suspend_seconds)
            policy = self.adapter.get(sid).lifecycle
            if (policy.auto_suspend.mode != mode or not policy.auto_suspend.enabled
                    or policy.auto_suspend.interval != self.auto_suspend_seconds or policy.auto_delete.enabled):
                raise BenchmarkFailure("lifecycle_policy_mismatch")
            _progress("2/4 Installing agent CLI toolchain (git, jq, sqlite3, agent-tool) & starting RAM index server...")
            provision_agent_tools(self.adapter, sid)
            self._exec(sid, "repo-audit init")
            self._exec(sid, fixture_install_command(self.source))
            self._worker(sid, "start")
            for _ in range(3):
                self._worker(sid, "increment")
            sample.before = self._worker(sid, "probe")
            if not sample.before.valid_disk or not sample.before.valid_ram or not sample.before.valid_tools:
                raise BenchmarkFailure("invalid_baseline")
            if not sample.before.index_entry_count:
                raise BenchmarkFailure('empty_tool_server_index')
            _progress("3/4 Stopping and resuming sandbox (measuring stop/resume/exec/worker latency)...")
            started = self.clock()
            self.adapter.stop(sid)
            if str(self.adapter.get(sid).state).casefold() not in {"stopped", "suspended"}:
                raise BenchmarkFailure("sandbox_not_stopped")
            sample.stop_to_stopped_ms = (self.clock() - started) * 1000
            self.sleep(self.hold_seconds)
            started = self.clock()
            self.adapter.resume(sid)
            if str(self.adapter.get(sid).state).casefold() != "running":
                raise BenchmarkFailure("sandbox_not_running")
            running = self.clock()
            sample.resume_to_running_ms = (running - started) * 1000
            self._exec(sid, "echo ACA_EXEC_READY")
            sample.first_exec_probe_ms = (self.clock() - running) * 1000
            started = self.clock()
            sample.after = self._worker(sid, "probe")
            if mode == "Disk":
                # Preserve the pre-bootstrap observation even if startup repairs a file.
                sample.restarted = self._worker(sid, "start")
            sample.worker_ready_ms = (self.clock() - started) * 1000
            sample.oracle = evaluate_mode_observation(mode, sample.before, sample.after, sample.restarted)
            self._exec(sid, "echo resumed > /workspace/demo/resumed.txt")
            if self._exec(sid, "cat /workspace/demo/resumed.txt").strip() != "resumed":
                raise BenchmarkFailure("post_resume_write_failed")
            sample.success = sample.oracle.passed
            if not sample.success:
                sample.failure_code = ",".join(sample.oracle.failures)
        except Exception as exc:
            # Never include SDK exception text (which may contain private endpoints).
            sample.failure_code = exc.code if isinstance(exc, BenchmarkFailure) else type(exc).__name__
            sample.success = False
        finally:
            failures = []
            for sid in set(self.adapter.sandboxes) - previous_ids:
                try:
                    self.adapter.delete(sid)
                except Exception as exc:
                    failures.append(type(exc).__name__)
            if failures:
                sample.success = False
                sample.failure_code = (sample.failure_code + ";" if sample.failure_code else "") + "cleanup_failed:" + ",".join(failures)
        return sample

    def run(self, *, iterations: int = 1, mode: str | None = None, volumes: Any = None) -> list[ModeComparisonMetrics]:
        if type(iterations) is not int or iterations <= 0:
            raise ValueError("Iterations must be a positive integer")
        if mode is not None:
            _validate_mode(mode, bool(volumes))
        elif volumes:
            raise ValueError("Memory/Disk comparison cannot attach volumes")
        samples = []
        for iteration in range(1, iterations + 1):
            modes = (mode,) if mode else (("Memory", "Disk") if iteration % 2 else ("Disk", "Memory"))
            for current in modes:
                samples.append(self.run_once(current, iteration=iteration, volumes=volumes))
        return samples
