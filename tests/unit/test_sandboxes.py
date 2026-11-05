import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aca_agent_sample.config import SampleConfig
from aca_agent_sample.model_client import FakeModelAdapter, ModelResponse, ToolCall
from aca_agent_sample.sandboxes.client import AcaSandboxAdapter, FakeSandboxAdapter
from aca_agent_sample.sandboxes.command_agent import SandboxCommandAgent, SandboxToolPolicy
from aca_agent_sample.tool_policy import ToolPolicyError


def poller(value):
    return SimpleNamespace(result=Mock(return_value=value))


def sdk_adapter():
    sandbox = Mock()
    sandbox.sandbox_id = "owned-1"
    sandbox.get.return_value = SimpleNamespace(state="Running", lifecycle="lifecycle")
    sandbox.begin_delete.return_value = poller(None)
    sandbox.exec.return_value = SimpleNamespace(exit_code=0, stdout="ok", stderr="")
    sandbox.create_snapshot.return_value = SimpleNamespace(id="snapshot-1")
    group = Mock()
    group.begin_create_sandbox.return_value = poller(sandbox)
    group.get_snapshot.return_value = SimpleNamespace(id="snapshot-1", status="Ready")
    return AcaSandboxAdapter(SampleConfig(), group_client=group, polling_interval=1), group, sandbox


def call(id, command, **kwargs):
    return ModelResponse(tool_calls=(ToolCall(id, "run_sandbox_command", json.dumps({"command": command, **kwargs})),))


def test_create_exec_lifecycle_and_delete():
    adapter, group, sandbox = sdk_adapter()
    sid = adapter.create(mode="Disk", auto_suspend_seconds=600)
    assert sid == "owned-1"
    assert group.begin_create_sandbox.call_args.kwargs == {
        "disk": "ubuntu", "cpu": "1000m", "memory": "2048Mi", "auto_suspend_seconds": 600,
        "auto_suspend_mode": "Disk", "polling_timeout": 180, "polling_interval": 1}
    sandbox.exec.assert_called_once_with("mkdir -p /workspace", working_directory="/")
    sandbox.exec.reset_mock()
    result = adapter.exec(sid, "echo hi", working_directory="/workspace/demo")
    sandbox.exec.assert_called_once_with("echo hi", working_directory="/workspace/demo")
    assert result.status == "succeeded" and result.exit_code == 0 and result.stdout == "ok"
    sandbox.exec.return_value = SimpleNamespace(exit_code=2, stdout="", stderr="bad")
    assert adapter.exec(sid, "bad").error_kind == "execution_failed"
    assert adapter.get(sid).lifecycle == "lifecycle"
    for mode in ("Memory", "Disk"):
        adapter.set_lifecycle_policy(sid, mode=mode, interval=42)
        policy = sandbox.set_lifecycle_policy.call_args.args[0]
        assert policy.auto_suspend.enabled and policy.auto_suspend.interval == 42
        assert policy.auto_suspend.mode == mode and policy.auto_delete.enabled is False
    adapter.delete(sid)
    sandbox.begin_delete.assert_called_once_with(polling_timeout=180, polling_interval=1)
    assert not adapter.sandboxes
    with pytest.raises(ValueError):
        adapter.delete(sid)


def test_sdk_client_configuration_uses_regional_endpoint_and_disables_retries(monkeypatch):
    factory = Mock(return_value=Mock())
    endpoint = Mock(return_value="https://management.example.azuredevcompute.io")
    monkeypatch.setattr("aca_agent_sample.sandboxes.client.SandboxGroupClient", factory)
    monkeypatch.setattr("aca_agent_sample.sandboxes.client.endpoint_for_region", endpoint)
    config = SampleConfig(subscription_id="sub", tenant_id="tenant", location="example",
                          sandbox_resource_group="rg", sandbox_group_name="group", enable_live_azure=True)
    credential = Mock()
    AcaSandboxAdapter(config, credential)
    endpoint.assert_called_once_with("example")
    args, kwargs = factory.call_args
    assert args[0] == endpoint.return_value and args[1].credential is credential
    assert (kwargs["connection_timeout"], kwargs["read_timeout"], kwargs["retry_total"]) == (10, 60, 0)
    assert kwargs["subscription_id"] == "sub" and kwargs["sandbox_group"] == "group"


def test_reject_invalid_mode_and_attached_volumes():
    adapter, group, _ = sdk_adapter()
    with pytest.raises(ValueError):
        adapter.create(mode="Memory", volumes=[SimpleNamespace(volume_name="disk")])
    with pytest.raises(ValueError):
        adapter.create(mode="invalid")
    group.begin_create_sandbox.assert_not_called()
    sid = adapter.create(mode="Disk", volumes=[SimpleNamespace(volume_name="disk")])
    with pytest.raises(ValueError):
        adapter.set_lifecycle_policy(sid, mode="Memory")


def test_stop_polls_and_resume_waits(monkeypatch):
    adapter, _, sandbox = sdk_adapter()
    sid = adapter.create()
    sandbox.get.side_effect = [SimpleNamespace(state="Stopping"), SimpleNamespace(state="Suspended")]
    monkeypatch.setattr("aca_agent_sample.sandboxes.client.time.sleep", lambda _: None)
    stop = adapter.stop(sid)
    resume = adapter.resume(sid)
    sandbox.stop.assert_called_once_with()
    assert sandbox.get.call_count == 2 and stop["stop_to_stopped_ms"] >= stop["stop_api_ms"] >= 0
    sandbox.resume.assert_called_once_with()
    sandbox.wait_for_running.assert_called_once_with(timeout=180, poll_interval=1)
    assert resume["resume_to_running_ms"] >= resume["resume_api_ms"] >= 0


def test_snapshot_and_restore_only_pass_snapshot_and_polling():
    adapter, group, sandbox = sdk_adapter()
    sid = adapter.create()
    snap = adapter.create_snapshot(sid, name="checkpoint")
    sandbox.create_snapshot.assert_called_once_with(name="checkpoint")
    clone = Mock(sandbox_id="owned-2")
    clone.begin_delete.return_value = poller(None)
    group.begin_create_sandbox.return_value = poller(clone)
    assert adapter.clone(snap) == "owned-2"
    assert group.begin_create_sandbox.call_args.kwargs == {
        "snapshot_id": "snapshot-1", "polling_timeout": 180, "polling_interval": 1}
    adapter.delete_snapshot(snap)
    group.delete_snapshot.assert_called_once_with("snapshot-1")
    with pytest.raises(ValueError):
        adapter.clone("other")


def test_snapshot_not_ready_cannot_clone():
    adapter, group, _ = sdk_adapter()
    sid = adapter.create()
    snapshot = adapter.create_snapshot(sid)
    group.get_snapshot.return_value.status = "Creating"
    with pytest.raises(RuntimeError):
        adapter.clone(snapshot)
    assert group.begin_create_sandbox.call_count == 1


@pytest.mark.parametrize("mode,preserved", [("Memory", True), ("Disk", False)])
def test_fake_lifecycle_preserves_files_and_only_memory_mode_ram(mode, preserved):
    adapter = FakeSandboxAdapter()
    sid = adapter.create(mode=mode)
    assert adapter.exec(sid, "echo hello > /workspace/a").status == "succeeded"
    assert adapter.exec(sid, "set_ram nonce abc").status == "succeeded"
    adapter.stop(sid)
    assert adapter.exec(sid, "cat /workspace/a").error_kind == "state_lost"
    adapter.resume(sid)
    assert adapter.exec(sid, "cat /workspace/a").stdout == "hello\n"
    assert (adapter.exec(sid, "get_ram nonce").status == "succeeded") is preserved


def test_fake_snapshot_clones_independent_point_in_time():
    adapter = FakeSandboxAdapter()
    sid = adapter.create()
    adapter.exec(sid, "echo before > /workspace/a")
    adapter.exec(sid, "set_ram nonce before")
    snap = adapter.create_snapshot(sid)
    adapter.exec(sid, "echo after > /workspace/a")
    clone = adapter.clone(snap)
    assert clone != sid
    assert adapter.exec(clone, "cat /workspace/a").stdout == "before\n"
    assert adapter.exec(clone, "get_ram nonce").stdout == "before\n"
    adapter.exec(clone, "echo clone > /workspace/a")
    assert adapter.exec(sid, "cat /workspace/a").stdout == "after\n"
    adapter.cleanup()
    assert not adapter.sandboxes and not adapter.snapshots


def test_agent_multiturn_stop_resume_and_cleanup():
    model = FakeModelAdapter([call("1", "echo hello > /workspace/a"),
                              call("2", "cat /workspace/a"), ModelResponse("done")])
    adapter = FakeSandboxAdapter()
    adapter.stop = Mock(wraps=adapter.stop)
    adapter.resume = Mock(wraps=adapter.resume)
    adapter.delete = Mock(wraps=adapter.delete)
    result = SandboxCommandAgent(model, adapter, pause_between_commands=True).run("do it")
    assert result.final_response == "done" and result.turns[1].result.stdout == "hello\n"
    assert adapter.stop.call_count == 2 and adapter.resume.call_count == 1
    adapter.delete.assert_called_once()
    assert not adapter.sandboxes
    schema = model.requests[0][1][0]["function"]["parameters"]
    assert set(schema["properties"]) == {"command", "working_directory"}
    assert "sandbox_id" not in str(schema)


def test_agent_cleanup_when_model_or_lifecycle_fails():
    adapter = FakeSandboxAdapter()
    adapter.delete = Mock(wraps=adapter.delete)
    with pytest.raises(RuntimeError):
        SandboxCommandAgent(FakeModelAdapter([]), adapter).run("go")
    adapter.delete.assert_called_once()
    assert not adapter.sandboxes
    adapter.set_lifecycle_policy = Mock(side_effect=RuntimeError("failure"))
    with pytest.raises(RuntimeError):
        SandboxCommandAgent(FakeModelAdapter([]), adapter).run("go")
    assert not adapter.sandboxes


def test_policy_rejects_routing_arguments_and_duplicate_keys():
    policy = SandboxToolPolicy()
    for arguments in ('{"command":"echo hi","sandbox_id":"other"}',
                      '{"command":"a","command":"b"}', '{"command":"x","timeout":2}'):
        with pytest.raises(ValueError):
            policy.validate("run_sandbox_command", arguments)
    assert policy.validate("run_sandbox_command", '{"command":"echo hi"}')["working_directory"] == "/workspace"


def test_cleanup_retries_owned_resources_after_partial_failure():
    adapter, group, sandbox = sdk_adapter()
    sid = adapter.create()
    snapshot = adapter.create_snapshot(sid)
    sandbox.begin_delete.side_effect = RuntimeError("network")
    with pytest.raises(ExceptionGroup):
        adapter.cleanup()
    assert sid in adapter.sandboxes and snapshot not in adapter.snapshots
    group.delete_snapshot.assert_called_once()
    sandbox.begin_delete.side_effect = None
    adapter.cleanup()
    assert not adapter.sandboxes


def test_clone_with_data_disk_still_rejects_memory_policy():
    adapter, group, _ = sdk_adapter()
    sid = adapter.create(mode="Disk", volumes=[SimpleNamespace(volume_name="disk")])
    snapshot = adapter.create_snapshot(sid)
    clone = Mock(sandbox_id="owned-2")
    group.begin_create_sandbox.return_value = poller(clone)
    clone_id = adapter.clone(snapshot)
    with pytest.raises(ValueError):
        adapter.set_lifecycle_policy(clone_id, mode="Memory")


def test_unknown_exec_outcome_is_not_retried_and_is_cleaned_up():
    model = FakeModelAdapter([call("1", "echo hi"), call("2", "echo hi")])
    adapter = FakeSandboxAdapter()
    adapter.exec = Mock(side_effect=TimeoutError("response lost"))
    result = SandboxCommandAgent(model, adapter).run("do it")
    assert result.status == "outcome_unknown" and adapter.exec.call_count == 1
    assert not adapter.sandboxes


def test_explicit_pending_snapshot_status_must_be_confirmed():
    adapter, group, _ = sdk_adapter()
    sid = adapter.create()
    snapshot = adapter.create_snapshot(sid)
    group.get_snapshot.return_value.status = "Creating"
    with pytest.raises(RuntimeError):
        adapter.clone(snapshot)


def test_pending_create_is_owned_and_cleaned_if_polling_fails():
    adapter, group, sandbox = sdk_adapter()
    method = SimpleNamespace(_initial_resource=SimpleNamespace(id="owned-1"))
    pending = SimpleNamespace(polling_method=lambda: method, result=Mock(side_effect=TimeoutError("not ready")))
    group.begin_create_sandbox.return_value = pending
    group.get_sandbox_client.return_value = sandbox
    with pytest.raises(TimeoutError):
        SandboxCommandAgent(FakeModelAdapter([]), adapter).run("go")
    group.get_sandbox_client.assert_called_once_with("owned-1")
    sandbox.begin_delete.assert_called_once()
    assert not adapter.sandboxes


def test_manifest_persists_owned_ids_until_successful_deletion():
    from pathlib import Path
    from uuid import uuid4

    root = Path(__file__).resolve().parents[2]
    folder = root / ".internal" / "runs"
    folder_existed = folder.exists()
    path = folder / f"test-sandbox-{uuid4().hex}.json"
    base, group, sandbox = sdk_adapter()
    config = SampleConfig(subscription_id="sub", sandbox_resource_group="rg", sandbox_group_name="group")
    try:
        adapter = AcaSandboxAdapter(config, group_client=group, manifest_path=str(path))
        sid = adapter.create()
        snapshot = adapter.create_snapshot(sid)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["sandbox_ids"] == [sid] and manifest["snapshot_ids"] == [snapshot]
        adapter.cleanup()
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["sandbox_ids"] == [] and manifest["snapshot_ids"] == []
    finally:
        path.unlink(missing_ok=True)
        if not folder_existed:
            folder.rmdir()


def test_live_adapter_requires_explicit_opt_in_and_tenant():
    with pytest.raises(ValueError):
        AcaSandboxAdapter(SampleConfig(subscription_id="sub", tenant_id="tenant", location="eastus2",
                                       sandbox_resource_group="rg", sandbox_group_name="group"), Mock())
    with pytest.raises(ValueError):
        AcaSandboxAdapter(SampleConfig(subscription_id="sub", location="eastus2", sandbox_resource_group="rg",
                                       sandbox_group_name="group", enable_live_azure=True), Mock())


def test_agent_only_cleans_up_its_own_sandbox():
    adapter = FakeSandboxAdapter()
    existing = adapter.create()
    result = SandboxCommandAgent(FakeModelAdapter([ModelResponse("done")]), adapter).run("go")
    assert result.status == "completed" and set(adapter.sandboxes) == {existing}
    adapter.cleanup()


@pytest.mark.parametrize("override", ["labels", "environment", "volumes", "ports", "entrypoint", "cpu", "memory", "disk"])
def test_snapshot_clone_rejects_all_override_arguments(override):
    adapter, group, _ = sdk_adapter()
    snapshot = adapter.create_snapshot(adapter.create())
    group.begin_create_sandbox.reset_mock()
    with pytest.raises(TypeError):
        adapter.clone(snapshot, **{override: "forbidden"})
    group.begin_create_sandbox.assert_not_called()
    fake = FakeSandboxAdapter()
    snap = fake.create_snapshot(fake.create())
    with pytest.raises(TypeError):
        fake.clone(snap, **{override: "forbidden"})
    fake.cleanup()


def test_workspace_initialization_failure_retains_owned_sandbox():
    adapter, _, sandbox = sdk_adapter()
    sandbox.exec.return_value = SimpleNamespace(exit_code=255, stdout="", stderr="cwd failed")
    with pytest.raises(RuntimeError, match="workspace initialization"):
        adapter.create()
    assert "owned-1" in adapter.sandboxes
    adapter.cleanup()
    assert not adapter.sandboxes


def test_synchronous_snapshot_without_status_can_clone_after_matching_get():
    adapter, group, _ = sdk_adapter()
    sid = adapter.create()
    snap = adapter.create_snapshot(sid)
    group.get_snapshot.return_value = SimpleNamespace(id=snap, status=None)
    clone = Mock(sandbox_id="owned-2")
    group.begin_create_sandbox.return_value = poller(clone)
    assert adapter.clone(snap) == "owned-2"


@pytest.mark.parametrize("snapshot", [SimpleNamespace(id="wrong", status=None), SimpleNamespace(id="snapshot-1", status="Creating"), None])
def test_snapshot_absence_or_explicit_pending_is_not_ready(snapshot):
    from aca_agent_sample.sandboxes.client import snapshot_is_ready
    assert not snapshot_is_ready(snapshot, "snapshot-1")


@pytest.mark.parametrize('mode', ['Memory', 'Disk'])
def test_installed_tools_preserved_without_reprovisioning(mode):
    from aca_agent_sample.sandboxes.agent_tools import provision_agent_tools, tool_evidence
    adapter = FakeSandboxAdapter()
    sid = adapter.create(mode=mode)
    assert adapter.exec(sid, 'agent-tool --versions').status == 'failed'
    before = provision_agent_tools(adapter, sid)
    adapter.stop(sid)
    adapter.resume(sid)
    assert tool_evidence(adapter, sid) == before
    assert adapter.exec(sid, 'repo-audit init').status == 'succeeded'
    adapter.cleanup()


def test_toolchain_agent_uses_same_owned_sandbox_and_one_provision():
    from aca_agent_sample.sandboxes.command_agent import ANALYZE_COMMAND, PATCH_COMMAND
    from aca_agent_sample.sandboxes.agent_tools import provision_command
    model = FakeModelAdapter([call('v', 'agent-tool --versions'), call('i', 'repo-audit init'),
                              call('a', ANALYZE_COMMAND), call('start', 'python3 /workspace/demo/state_worker.py start'),
                              call('cache', 'agent-tool cached-audit'), ModelResponse('analysis'),
                              call('p', PATCH_COMMAND), ModelResponse('patched')])
    adapter = FakeSandboxAdapter()
    adapter.exec = Mock(wraps=adapter.exec)
    adapter.stop = Mock(wraps=adapter.stop)
    adapter.resume = Mock(wraps=adapter.resume)
    result = SandboxCommandAgent(model, adapter).run_toolchain_demo()
    assert result['passed'] and result['installed_tool_preserved']
    adapter.stop.assert_called_once()
    adapter.resume.assert_called_once_with(adapter.stop.call_args.args[0])
    assert sum(c.args[1] == provision_command() for c in adapter.exec.call_args_list) == 1
    assert not adapter.sandboxes


def test_toolchain_agent_false_success_still_fails_and_cleans_up():
    adapter = FakeSandboxAdapter()
    with pytest.raises(RuntimeError):
        SandboxCommandAgent(FakeModelAdapter([ModelResponse('done')]), adapter).run_toolchain_demo()
    assert not adapter.sandboxes


@pytest.mark.parametrize('command', ['echo x > /tmp/a', 'mkdir /var/tmp/x', 'mktemp'])
def test_model_scratch_files_must_stay_inside_workspace(command):
    with pytest.raises(ToolPolicyError):
        SandboxToolPolicy().validate('run_sandbox_command', json.dumps({'command': command}))
