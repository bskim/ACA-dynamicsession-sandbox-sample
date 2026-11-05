from __future__ import annotations

import importlib.util
import json
import base64
import html
from urllib.parse import quote
import zlib
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import uuid

import pytest

from aca_agent_sample.cli import main
from aca_agent_sample.sandboxes.client import FakeSandboxAdapter

ROOT = Path(__file__).resolve().parents[2]
AUDIT_SHELLS = {}


def load_example(name):
    path = ROOT / "examples" / "sandbox" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("args,key,value", [
    (["dynamic-session", "reuse"], "reuse", "passed"),
    (["dynamic-session", "coding-agent"], "repair_test", "passed"),
    (["dynamic-session", "shell"], "cwd_explicit_each_time", True),
    (["sandbox", "snapshot-clone"], "isolation_after_clone", True),
])
def test_json_cli_examples(args, key, value, capsys):
    assert main(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "offline"
    assert data[key] == value


def test_command_agent_cli(capsys):
    assert main(["sandbox", "command-agent"]) == 0
    assert "Sandbox demo complete" in capsys.readouterr().out


def test_comparison_cli_json_and_markdown(capsys):
    assert main(["sandbox", "compare-modes", "--iterations", "2", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["simulation"] is True and len(data["samples"]) == 4
    for sample in data["samples"]:
        oracle = sample["oracle"]
        assert oracle["passed"] and oracle["disk_preserved"]
        assert oracle["memory_preserved"] == (sample["mode"] == "Memory")
        assert oracle["worker_restarted"] == (sample["mode"] == "Disk")
        assert sample["before"]["disk_sha256"] == sample["after"]["disk_sha256"]
    assert main(["sandbox", "compare-modes", "--format", "markdown"]) == 0
    text = capsys.readouterr().out
    for phrase in ("worker_boot_nonce", "in_memory_counter", "buffer_checksum", "Data Disk", "simulated", "stop_to_stopped_ms", "resume_to_running_ms", "first_exec_probe_ms", "worker_ready_ms"):
        assert phrase.lower() in text.lower()
    assert "| Memory | 1 | True | True | True | True | True | False | True |" in text
    assert "| Disk | 1 | True | False | False | False | False | True | True |" in text


def test_comparison_writes_all_formats(capsys):
    folder = ROOT / ".internal" / "results" / ("p5-test-" + uuid.uuid4().hex)
    try:
        assert main(["sandbox", "compare-modes", "--format", "text", "--output-dir", str(folder)]) == 0
        capsys.readouterr()
        assert {file.name for file in folder.iterdir()} == {"snapshot-comparison.json", "snapshot-comparison.csv", "snapshot-comparison.md"}
        assert len(json.loads((folder / "snapshot-comparison.json").read_text(encoding="utf-8"))["samples"]) == 2
        assert "memory_preserved" in (folder / "snapshot-comparison.csv").read_text(encoding="utf-8")
        assert "worker_boot_nonce" in (folder / "snapshot-comparison.md").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_clone_isolation_and_cleanup():
    module = load_example("snapshot_clone")
    adapter = FakeSandboxAdapter()
    result = module.run_clone(adapter)
    assert result["passed"] and result["distinct_sandboxes"]
    assert result["source_sha256_at_snapshot"] == result["clone_sha256_after_change"]
    assert result["source_sha256_after_change"] != result["clone_sha256_after_change"]
    assert not adapter.sandboxes and not adapter.snapshots


@pytest.mark.parametrize("step", ["create_snapshot", "clone", "exec"])
def test_clone_cleanup_on_error(step):
    module = load_example("snapshot_clone")
    adapter = FakeSandboxAdapter()
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure")
    setattr(adapter, step, fail)
    with pytest.raises(RuntimeError):
        module.run_clone(adapter)
    assert not adapter.sandboxes and not adapter.snapshots


@pytest.mark.parametrize("example", ["compare_snapshot_modes", "snapshot_clone"])
def test_direct_example_live_requires_opt_in(example, monkeypatch, capsys):
    monkeypatch.setenv("ENABLE_LIVE_AZURE", "false")
    module = load_example(example)
    with pytest.raises(SystemExit) as exc:
        module.main(["--live"])
    assert exc.value.code == 2
    assert "ENABLE_LIVE_AZURE" in capsys.readouterr().err


def test_cli_forwards_example_help_and_rejects_invalid_iterations(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["sandbox", "compare-modes", "--help"])
    assert exc.value.code == 0 and "--iterations" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(["sandbox", "compare-modes", "--iterations", "0"])
    assert exc.value.code == 2


def remove_readonly(function, path, error):
    if not isinstance(error, PermissionError):
        raise error
    Path(path).chmod(stat.S_IWRITE)
    function(path)


@pytest.fixture(params=["pwsh", "powershell.exe"])
def audit_repo(request):
    if not shutil.which(request.param) or not shutil.which("git"):
        pytest.skip("PowerShell and Git are required")
    repo = ROOT / ".internal" / ("p7 audit 한글 " + uuid.uuid4().hex)
    repo.mkdir(parents=True)
    try:
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts" / "check-public-readiness.ps1", repo / "scripts")
        shutil.copy2(ROOT / ".gitignore", repo / ".gitignore")
        AUDIT_SHELLS[repo] = request.param
        yield repo
    finally:
        AUDIT_SHELLS.pop(repo, None)
        shutil.rmtree(repo, onexc=remove_readonly)


def audit(repo, *args):
    return subprocess.run([AUDIT_SHELLS[repo], "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(repo / "scripts" / "check-public-readiness.ps1"), *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_readiness_default_and_required_docs(audit_repo):
    assert audit(audit_repo).returncode == 0
    result = audit(audit_repo, "-RequireDocs")
    assert result.returncode == 1 and "Required document missing" in result.stdout


def test_readiness_detects_private_guid_secret_and_tracked_ignore(audit_repo):
    repo = audit_repo
    (repo / "public.txt").write_text(str(uuid.uuid4()), encoding="utf-8")
    result = audit(repo)
    assert result.returncode == 1 and "Non-placeholder GUID" in result.stdout
    (repo / "public.txt").write_text("Bearer " + "eyJ" + "a" * 20 + "." + "eyJ" + "b" * 20 + "." + "c" * 20, encoding="utf-8")
    assert "Possible secret or token" in audit(repo).stdout
    (repo / "public.txt").unlink()
    (repo / ".env").write_text("private\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-f", ".env"], check=True)
    assert "Ignored file is tracked" in audit(repo).stdout


def test_readiness_documents_locale_diagram_and_mermaid(audit_repo):
    repo = audit_repo
    write_audit_diagram(repo)
    link = "![diagram](docs/assets/architecture.svg)\n[source](docs/assets/architecture.drawio)"
    (repo / "README.md").write_text("[English](README-en.md)\n\n" + link, encoding="utf-8")
    (repo / "README-en.md").write_text("[한국어](README.md)\n\n" + link, encoding="utf-8")
    assert audit(repo).returncode == 0
    (repo / "README.md").write_text("[English](README-en.md)\n" + link + "\nhttps://learn.microsoft.com/en-us/example\n```mermaid\nx\n```", encoding="utf-8")
    result = audit(repo)
    assert result.returncode == 1 and "Incorrect Microsoft Learn locale" in result.stdout and "Mermaid block" in result.stdout
    (repo / "docs" / "assets" / "architecture.drawio").unlink()
    assert "Missing Draw.io source" in audit(repo).stdout


def test_clone_cleanup_preserves_existing_resources_and_reverse_order():
    module = load_example("snapshot_clone")
    adapter = FakeSandboxAdapter()
    existing = adapter.create()
    existing_snapshot = adapter.create_snapshot(existing)
    deleted = []
    original_delete = adapter.delete
    def record_delete(sandbox_id):
        deleted.append(sandbox_id)
        original_delete(sandbox_id)
    adapter.delete = record_delete
    assert module.run_clone(adapter)["passed"]
    assert len(deleted) == 2 and deleted[0] != deleted[1]
    assert set(adapter.sandboxes) == {existing} and set(adapter.snapshots) == {existing_snapshot}
    adapter.cleanup()


def test_snapshot_waits_until_ready(monkeypatch):
    from types import SimpleNamespace
    module = load_example("snapshot_clone")
    responses = iter([SimpleNamespace(status="Creating"), SimpleNamespace(status="Ready")])
    adapter = SimpleNamespace(simulated=False, polling_timeout=5, polling_interval=0.01,
                              get_snapshot=lambda _: next(responses))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    module.wait_for_snapshot(adapter, "owned")


def test_readiness_checks_private_config_and_prefixed_secret(audit_repo):
    repo = audit_repo
    secret = "v" * 32
    (repo / "public.txt").write_text("FOUNDRY_API_KEY='" + secret + "'", encoding="utf-8")
    assert "Possible secret or token" in audit(repo).stdout
    unique = "sample-private-" + uuid.uuid4().hex
    (repo / ".env").write_text("FOUNDRY_ACCOUNT_NAME=" + unique + "\n", encoding="utf-8")
    (repo / "public.txt").write_text(unique, encoding="utf-8")
    result = audit(repo)
    assert "Private configuration value" in result.stdout and unique not in result.stdout


def test_clone_cleanup_failure_is_reported_and_other_deletions_attempted():
    module = load_example("snapshot_clone")
    adapter = FakeSandboxAdapter()
    original_delete = adapter.delete
    attempts = []
    def fail_first(sandbox_id):
        attempts.append(sandbox_id)
        if len(attempts) == 1:
            raise RuntimeError("sensitive endpoint")
        original_delete(sandbox_id)
    adapter.delete = fail_first
    with pytest.raises(ExceptionGroup) as exc:
        module.run_clone(adapter)
    assert "sensitive endpoint" not in str(exc.value)
    assert len(attempts) == 2 and len(adapter.sandboxes) == 1 and not adapter.snapshots
    adapter.cleanup()


def test_readiness_unquoted_key_and_private_name(audit_repo):
    repo = audit_repo
    (repo / "public.txt").write_text("AccountKey=" + "z" * 32, encoding="utf-8")
    assert "Possible secret or token" in audit(repo).stdout
    private_name = "rg" + "-foundry-" + "codex"
    (repo / "public.txt").write_text(private_name, encoding="utf-8")
    result = audit(repo)
    assert "Private resource identifier" in result.stdout and private_name not in result.stdout


def write_audit_diagram(repo, icon_text=""):
    assets = repo / "docs" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    icon = '<svg xmlns="http://www.w3.org/2000/svg"><title>' + icon_text + '</title><path d="M0 0h8v8z"/></svg>'
    uri = "data:image/svg+xml," + quote(icon, safe="")
    graph = '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/><mxCell id="icon" vertex="1" parent="1" style="shape=mxgraph.azure2.general.code;image=' + uri + ';"/><mxCell id="edge" edge="1" parent="1" source="icon" target="icon"/></root></mxGraphModel>'
    (assets / "architecture.drawio").write_text('<mxfile><diagram>' + graph + '</diagram></mxfile>', encoding="utf-8")
    (assets / "architecture.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><image href="' + uri + '"/></svg>', encoding="utf-8")
    return graph


@pytest.mark.parametrize("problem,expected", [
    ("malformed-render", "Invalid diagram XML"),
    ("wrong-namespace", "Invalid SVG root"),
    ("no-icons", "Azure icon library missing"),
    ("no-graphics", "Embedded Azure SVG graphics missing"),
    ("no-edges", "no editable shapes/edges"),
    ("mismatched-render", "Source/render icon mismatch"),
])
def test_readiness_rejects_invalid_diagram_artifacts(audit_repo, problem, expected):
    repo = audit_repo
    write_audit_diagram(repo)
    source = repo / "docs" / "assets" / "architecture.drawio"
    render = source.with_suffix(".svg")
    if problem == "malformed-render":
        render.write_text("<svg", encoding="utf-8")
    elif problem == "wrong-namespace":
        render.write_text(render.read_text(encoding="utf-8").replace("http://www.w3.org/2000/svg", "urn:not-svg"), encoding="utf-8")
    elif problem == "no-icons":
        source.write_text(source.read_text(encoding="utf-8").replace("mxgraph.azure2.general.code", "rectangle"), encoding="utf-8")
    elif problem == "no-graphics":
        source.write_text('<mxfile><diagram><mxGraphModel><root><mxCell vertex="1" style="shape=mxgraph.azure2.general.code"/><mxCell edge="1"/></root></mxGraphModel></diagram></mxfile>', encoding="utf-8")
    elif problem == "no-edges":
        source.write_text(source.read_text(encoding="utf-8").replace('edge="1"', 'edge="0"'), encoding="utf-8")
    else:
        render.write_text(render.read_text(encoding="utf-8").replace("M0%200h8v8z", "M0%200h9v9z"), encoding="utf-8")
    result = audit(repo)
    assert result.returncode == 1 and expected in result.stdout


@pytest.mark.parametrize("container", ["compressed-page", "svg-content", "base64-image"])
def test_readiness_inspects_encoded_private_diagram_data(audit_repo, container):
    repo = audit_repo
    graph = write_audit_diagram(repo)
    private = str(uuid.uuid4())
    assets = repo / "docs" / "assets"
    graph = graph.replace('id="icon"', 'value="' + private + '" id="icon"')
    compressor = zlib.compressobj(wbits=-15)
    compressed = base64.b64encode(compressor.compress(quote(graph, safe="").encode()) + compressor.flush()).decode()
    document = '<mxfile><diagram>' + compressed + '</diagram></mxfile>'
    if container == "compressed-page":
        (assets / "architecture.drawio").write_text(document, encoding="utf-8")
    elif container == "svg-content":
        render = assets / "architecture.svg"
        render.write_text(render.read_text(encoding="utf-8").replace('<svg ', '<svg content="' + html.escape(document, quote=True) + '" ', 1), encoding="utf-8")
    else:
        icon = '<svg xmlns="http://www.w3.org/2000/svg"><title>' + private + '</title><path d="M0 0h8v8z"/></svg>'
        uri = "data:image/svg+xml;base64," + base64.b64encode(icon.encode()).decode()
        (assets / "architecture.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><image href="' + uri + '"/></svg>', encoding="utf-8")
    result = audit(repo)
    assert result.returncode == 1 and "Non-placeholder GUID" in result.stdout
    assert private not in result.stdout and private not in result.stderr


def test_readiness_valid_compressed_page_and_svg_doctype(audit_repo):
    repo = audit_repo
    graph = write_audit_diagram(repo)
    compressor = zlib.compressobj(wbits=-15)
    compressed = base64.b64encode(compressor.compress(quote(graph, safe="").encode()) + compressor.flush()).decode()
    source = repo / "docs" / "assets" / "architecture.drawio"
    source.write_text('<mxfile><diagram>' + compressed + '</diagram></mxfile>', encoding="utf-8")
    render = source.with_suffix(".svg")
    render.write_text('<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">' + render.read_text(encoding="utf-8"), encoding="utf-8")
    result = audit(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_readiness_requires_all_eight_document_pairs_and_three_diagrams(audit_repo):
    result = audit(audit_repo, "-RequireDocs")
    assert result.returncode == 1
    for name in ("architecture", "dynamic-sessions", "sandbox", "snapshot-comparison", "troubleshooting", "how-to-deploy-and-run"):
        assert f"Required document missing: docs/{name}.md" in result.stdout
        assert f"Required document missing: docs/{name}-en.md" in result.stdout
    for name in ("overall-architecture", "dynamic-sessions-session-reuse", "sandbox-memory-vs-disk-snapshot"):
        for suffix in ("drawio", "svg"):
            assert f"Required diagram missing: docs/assets/{name}.{suffix}" in result.stdout


def test_readiness_source_link_and_tilde_mermaid(audit_repo):
    repo = audit_repo
    write_audit_diagram(repo)
    for name, other in (("README.md", "README-en.md"), ("README-en.md", "README.md")):
        (repo / name).write_text(f"[other]({other})\n![diagram](docs/assets/architecture.svg)\n~~~mermaid\nx\n~~~\n", encoding="utf-8")
    result = audit(repo)
    assert result.returncode == 1 and "Draw.io source link missing" in result.stdout and "Mermaid block" in result.stdout


def test_snapshot_wait_handles_eventual_404_and_statusless_sync_response(monkeypatch):
    from types import SimpleNamespace
    from azure.core.exceptions import ResourceNotFoundError
    module = load_example("snapshot_clone")
    responses = iter([ResourceNotFoundError("pending"), SimpleNamespace(id="owned", status=None)])
    def get_snapshot(_):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response
    adapter = SimpleNamespace(simulated=False, polling_timeout=5, polling_interval=0.01, get_snapshot=get_snapshot)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    module.wait_for_snapshot(adapter, "owned")

def public_role_ids():
    return ["-".join(parts) for parts in (
        ("0fb8eba5", "a2bb", "4abe", "b1c1", "49dfad359bb0"),
        ("c24cf47c", "5077", "412d", "a19c", "45202126392c"),
        ("5e0bd9bd", "7b93", "4f28", "af87", "19fc36ad61bd"),
    )]


def write_audit_guides(repo, body):
    docs = repo / "docs"
    docs.mkdir(exist_ok=True)
    for suffix, other in (("", "-en"), ("-en", "")):
        (docs / f"how-to-deploy-and-run{suffix}.md").write_text(
            f"[other](how-to-deploy-and-run{other}.md)\n\n```powershell\n{body}\n```\n",
            encoding="utf-8",
        )


def test_readiness_allows_only_public_role_arguments_in_guides(audit_repo):
    roles = public_role_ids()
    body = "\n".join(f"az role assignment create --role '{role}' --scope $scope" for role in roles)
    write_audit_guides(audit_repo, body)
    result = audit(audit_repo)
    assert result.returncode == 0, result.stdout + result.stderr
    (audit_repo / "public.txt").write_text(roles[0], encoding="utf-8")
    result = audit(audit_repo)
    assert result.returncode == 1 and "Non-placeholder GUID" in result.stdout


@pytest.mark.parametrize("field", ["--subscription", "--tenant"])
def test_readiness_role_exception_does_not_allow_identity_arguments(audit_repo, field):
    role = public_role_ids()[0]
    write_audit_guides(audit_repo, f"az role assignment create --role '{role}' {field} '{role}'")
    result = audit(audit_repo)
    assert result.returncode == 1 and "Non-placeholder GUID" in result.stdout
    assert role not in result.stdout + result.stderr


@pytest.mark.parametrize("field", ["AZURE_SUBSCRIPTION_ID", "AZURE_TENANT_ID"])
def test_readiness_guides_reject_private_subscription_and_tenant(audit_repo, field):
    private = str(uuid.uuid4())
    write_audit_guides(audit_repo, f"{field}={private}")
    result = audit(audit_repo)
    assert result.returncode == 1 and "Non-placeholder GUID" in result.stdout
    assert private not in result.stdout + result.stderr


def test_readiness_role_exception_never_overrides_private_env_values(audit_repo):
    role = public_role_ids()[0]
    (audit_repo / ".env").write_text(f"AZURE_TENANT_ID={role}\n", encoding="utf-8")
    write_audit_guides(audit_repo, f"az role assignment create --role '{role}' --scope $scope")
    result = audit(audit_repo)
    assert result.returncode == 1 and "Private configuration value" in result.stdout
    assert role not in result.stdout + result.stderr
