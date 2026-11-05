from __future__ import annotations

import csv
from dataclasses import replace
import importlib.util
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import uuid
from unittest.mock import Mock

import pytest

from aca_agent_sample.sandboxes.client import FakeSandboxAdapter
from aca_agent_sample.sandboxes.lifecycle_benchmark import (
    LATENCIES, ModeComparisonMetrics, SandboxLifecycleComparator, SnapshotModeOracle,
    WorkerObservation, aggregate_metrics, evaluate_mode_observation, fixture_install_command,
    fixture_source, metrics_to_csv, metrics_to_json, metrics_to_markdown,
)


def witness(**kwargs):
    return replace(WorkerObservation(True, "d" * 64, True, "a" * 64, 5, "b" * 64, "c" * 64, 101), **kwargs)


def restarted(**kwargs):
    return witness(worker_boot_nonce="e" * 64, process_start_fingerprint="f" * 64,
                   in_memory_counter=1, worker_started=True, **kwargs)


def test_memory_oracle_requires_disk_and_ram_continuity():
    result = SnapshotModeOracle.evaluate("Memory", witness(), witness(in_memory_counter=6))
    assert result.passed and result.disk_preserved and result.memory_preserved
    assert not result.worker_restarted


@pytest.mark.parametrize("change", [
    {"worker_boot_nonce": "e" * 64}, {"disk_sha256": "0" * 64},
    {"buffer_checksum": "0" * 64}, {"process_start_fingerprint": "0" * 64},
    {"in_memory_counter": 5}, {"in_memory_counter": 1}, {"worker_alive": False},
    {"disk_exists": False}, {"worker_boot_nonce": None}, {"disk_sha256": None},
    {"in_memory_counter": True}, {"buffer_checksum": "invalid"},
])
def test_memory_oracle_rejects_bad_evidence(change):
    after = witness(**({"in_memory_counter": 6} | change))
    result = evaluate_mode_observation("Memory", witness(), after)
    assert not result.passed


def test_pid_reuse_with_new_nonce_is_not_memory_preservation():
    before = witness()
    after = witness(worker_boot_nonce="e" * 64, in_memory_counter=6)
    assert before.pid == after.pid
    result = evaluate_mode_observation("Memory", before, after)
    assert result.worker_restarted and not result.memory_preserved and not result.passed


def test_pid_change_alone_does_not_decide_oracle():
    assert evaluate_mode_observation("Memory", witness(), witness(pid=909, in_memory_counter=6)).passed


def test_disk_oracle_observes_absence_before_explicit_restart():
    result = evaluate_mode_observation("Disk", witness(), witness(worker_alive=False), restarted())
    assert result.passed and result.disk_preserved and not result.memory_preserved and result.worker_restarted


@pytest.mark.parametrize("after,reboot", [
    (witness(in_memory_counter=6), restarted()),
    (witness(worker_alive=False), None),
    (witness(worker_alive=False), replace(restarted(), worker_boot_nonce="a" * 64)),
    (witness(worker_alive=False), replace(restarted(), in_memory_counter=6)),
    (witness(worker_alive=False), replace(restarted(), worker_started=False)),
    (witness(worker_alive=False), replace(restarted(), process_start_fingerprint="c" * 64)),
    (witness(worker_alive=False), replace(restarted(), disk_sha256="0" * 64)),
    (witness(worker_alive=False, disk_sha256="0" * 64), restarted()),
    (witness(worker_alive=False, disk_exists=False), restarted()),
])
def test_disk_oracle_rejects_incomplete_or_repaired_evidence(after, reboot):
    assert not evaluate_mode_observation("Disk", witness(), after, reboot).passed


def test_memory_does_not_accept_bootstrapped_worker():
    assert not evaluate_mode_observation("Memory", witness(), witness(in_memory_counter=6), restarted()).passed


def test_invalid_mode_and_observation():
    with pytest.raises(ValueError):
        evaluate_mode_observation("Other", witness(), witness())
    with pytest.raises(ValueError):
        WorkerObservation.from_mapping({"disk_exists": 1, "disk_sha256": "d" * 64, "worker_alive": True})


@pytest.mark.parametrize("mode", ["Memory", "Disk"])
def test_comparator_single_run_cleanup_and_measurement_boundaries(mode):
    adapter = FakeSandboxAdapter()
    adapter.exec = Mock(wraps=adapter.exec)
    ticks = iter(range(100))
    sample = SandboxLifecycleComparator(adapter, clock=lambda: next(ticks), sleep=lambda _: None).run_once(mode)
    assert sample.success and sample.oracle.passed and sample.simulated
    assert [getattr(sample, field) for field in LATENCIES] == [1000, 1000, 1000, 1000]
    commands = [call.args[1] for call in adapter.exec.call_args_list]
    starts = [command for command in commands if command.endswith("state_worker.py start")]
    assert len(starts) == (1 if mode == "Memory" else 2)
    assert not adapter.sandboxes
    if mode == "Memory":
        assert sample.before.worker_boot_nonce == sample.after.worker_boot_nonce
        assert sample.after.in_memory_counter > sample.before.in_memory_counter
    else:
        assert not sample.after.worker_alive and sample.restarted.in_memory_counter == 1
        assert sample.before.worker_boot_nonce != sample.restarted.worker_boot_nonce


def test_repeated_alternating_comparison_and_ownership():
    adapter = FakeSandboxAdapter()
    existing = adapter.create()
    samples = SandboxLifecycleComparator(adapter).run(iterations=3)
    assert [sample.mode for sample in samples] == ["Memory", "Disk", "Disk", "Memory", "Memory", "Disk"]
    assert all(sample.success for sample in samples)
    assert [sample.iteration for sample in samples] == [1, 1, 2, 2, 3, 3]
    assert set(adapter.sandboxes) == {existing}
    adapter.cleanup()


@pytest.mark.parametrize("iterations", [0, -1, True, 1.5])
def test_invalid_iterations(iterations):
    with pytest.raises(ValueError):
        SandboxLifecycleComparator(FakeSandboxAdapter()).run(iterations=iterations)


def test_data_disk_memory_rejected_before_create():
    adapter = FakeSandboxAdapter()
    comparator = SandboxLifecycleComparator(adapter)
    with pytest.raises(ValueError):
        comparator.run_once("Memory", volumes=[{"volume_name": "data"}])
    with pytest.raises(ValueError):
        comparator.run(volumes=[{"volume_name": "data"}])
    assert not adapter.sandboxes
    assert comparator.run(mode="Disk", volumes=[{"volume_name": "data"}])[0].success


@pytest.mark.parametrize("failure_point", ["set_lifecycle_policy", "exec", "stop", "resume"])
def test_exception_cleanup_and_no_execution_retry(failure_point):
    adapter = FakeSandboxAdapter()
    failing = Mock(side_effect=TimeoutError("private endpoint must never enter results"))
    setattr(adapter, failure_point, failing)
    sample = SandboxLifecycleComparator(adapter).run_once("Memory")
    assert not sample.success and sample.failure_code == "TimeoutError"
    assert failing.call_count == 1 and not adapter.sandboxes
    assert "private endpoint" not in metrics_to_json([sample])


def test_partial_create_cleanup():
    class PartialCreate(FakeSandboxAdapter):
        def create(self, **kwargs):
            super().create(**kwargs)
            raise TimeoutError("create response lost")
    adapter = PartialCreate()
    assert not SandboxLifecycleComparator(adapter).run_once("Memory").success
    assert not adapter.sandboxes


def test_cleanup_failure_retains_owned_id_and_fails_sample():
    adapter = FakeSandboxAdapter()
    adapter.delete = Mock(side_effect=RuntimeError("cleanup"))
    sample = SandboxLifecycleComparator(adapter).run_once("Memory")
    assert not sample.success and "cleanup_failed" in sample.failure_code and adapter.sandboxes
    adapter.cleanup()


def test_failed_samples_are_not_hidden_and_statistics_have_defined_percentile():
    samples = [ModeComparisonMetrics("Memory", iteration=i, success=True, stop_to_stopped_ms=value)
               for i, value in enumerate([1, 2, 3, 4, 100], 1)]
    samples.append(ModeComparisonMetrics("Memory", failure_code="timeout", stop_to_stopped_ms=9999))
    samples.append(ModeComparisonMetrics("Disk", failure_code="timeout"))
    summary = aggregate_metrics(samples)
    assert summary["Memory"]["sample_count"] == 6 and summary["Memory"]["failure_count"] == 1
    stop = summary["Memory"]["latencies"]["stop_to_stopped_ms"]
    assert (stop["mean_ms"], stop["p50_ms"], stop["p95_ms"], stop["excluded_count"]) == (22, 3, 100, 1)
    assert summary["Disk"]["latencies"]["stop_to_stopped_ms"]["mean_ms"] is None
    assert json.loads(metrics_to_json(samples))["summary"] == summary
    csv_rows = list(csv.DictReader(io.StringIO(metrics_to_csv(samples))))
    assert len(csv_rows) == len(samples) and csv_rows[-1]["failure_code"] == "timeout"
    assert "| Memory | 6 | 5 | 1 |" in metrics_to_markdown(samples)
    assert "n/a" in metrics_to_markdown(samples)
    assert aggregate_metrics([]) == {}


@pytest.mark.parametrize("kind", ["nonce", "disk", "missing_worker"])
def test_comparator_detects_nonce_or_disk_corruption(kind):
    class Corrupting(FakeSandboxAdapter):
        def resume(self, sid):
            result = super().resume(sid)
            if kind == "nonce":
                self.sandboxes[sid].memory["state_worker"]["worker_boot_nonce"] = "0" * 64
            elif kind == "disk":
                self.sandboxes[sid].files["/workspace/demo/sentinel.txt"] = "corrupt"
            else:
                self.sandboxes[sid].memory.clear()
            return result
    sample = SandboxLifecycleComparator(Corrupting()).run_once("Memory")
    assert not sample.success
    if kind == "nonce":
        assert sample.oracle.worker_restarted
    elif kind == "disk":
        assert not sample.oracle.disk_preserved
    else:
        assert not sample.oracle.memory_preserved


def test_fake_runs_direct_worker_commands_without_host_execution():
    adapter = FakeSandboxAdapter()
    sid = adapter.create(mode="Disk")
    assert adapter.exec(sid, fixture_install_command(fixture_source())).status == "succeeded"
    def command(action):
        return json.loads(adapter.exec(sid, f"python3 /workspace/demo/state_worker.py {action}").stdout)
    before = command("start")
    assert command("increment")["in_memory_counter"] == 2
    assert command("probe")["in_memory_counter"] == 3
    assert not command("stop")["worker_alive"]
    again = command("start")
    assert again["worker_boot_nonce"] != before["worker_boot_nonce"]
    assert adapter.exec(sid, "python3 -c 'import os; os.remove(\"host\")'").status == "failed"
    adapter.cleanup()


def test_real_stdlib_worker_cli_disk_and_ram_separation():
    root = Path(__file__).resolve().parents[2]
    script = root / "examples" / "sandbox" / "fixtures" / "state_worker.py"
    directory = root / ".worker-test-runs" / uuid.uuid4().hex
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    def command(action):
        process = subprocess.run([sys.executable, str(script), action, "--directory", str(directory),
                                  "--port", str(port), "--timeout", "3"], capture_output=True, text=True, timeout=15)
        assert process.returncode == 0, process.stderr
        return json.loads(process.stdout)
    try:
        assert not command("probe")["disk_exists"]
        before = command("start")
        assert before["worker_alive"] and before["in_memory_counter"] == 1
        assert command("increment")["in_memory_counter"] == 2
        after = command("probe")
        assert evaluate_mode_observation("Memory", before, after).passed
        assert command("start")["worker_started"] is False
        assert not command("stop")["worker_alive"]
        absent = command("probe")
        assert not absent["worker_alive"] and absent["disk_sha256"] == before["disk_sha256"]
        reboot = command("start")
        assert reboot["worker_boot_nonce"] != before["worker_boot_nonce"]
        assert reboot["buffer_checksum"] != before["buffer_checksum"]
        assert evaluate_mode_observation("Disk", after, absent, reboot).passed
        assert {file.name for file in directory.iterdir()} == {"sentinel.txt"}
        content = (directory / "sentinel.txt").read_bytes()
        for field in ("worker_boot_nonce", "buffer_checksum", "process_start_fingerprint"):
            assert before[field].encode() not in content
        (directory / "sentinel.txt").write_text("changed", encoding="utf-8")
        assert not evaluate_mode_observation("Memory", before, command("probe")).passed
    finally:
        try:
            command("stop")
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if directory.parent.exists() and not any(directory.parent.iterdir()):
            directory.parent.rmdir()


def test_worker_flushes_and_fsyncs_disk_witness(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    path = root / "examples" / "sandbox" / "fixtures" / "state_worker.py"
    spec = importlib.util.spec_from_file_location("state_worker_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    file = Mock()
    file.__enter__ = Mock(return_value=file)
    file.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(module.Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(module.Path, "exists", lambda *a: False)
    monkeypatch.setattr(module.Path, "open", lambda *a: file)
    monkeypatch.setattr(module, "disk_evidence", lambda *a: {})
    monkeypatch.setattr(module, "request", Mock(side_effect=[None, {"worker_alive": True}]))
    monkeypatch.setattr(module.subprocess, "Popen", Mock())
    sync = Mock()
    monkeypatch.setattr(module.os, "fsync", sync)
    module.run("start", Path("unused"), 18765, 1)
    file.write.assert_called_once_with(module.SENTINEL_CONTENT)
    file.flush.assert_called_once()
    sync.assert_called_once_with(file.fileno.return_value)
