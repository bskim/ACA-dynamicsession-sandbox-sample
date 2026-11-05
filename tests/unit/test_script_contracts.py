import json
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell is required for script contracts")


def invoke(script, *args):
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(ROOT / "scripts" / script), *args],
                          capture_output=True, text=True, encoding="utf-8", timeout=20)


@pytest.mark.parametrize("protected_target", ["group", "pool", "foreign_pool"])
def test_cleanup_protected_or_foreign_scopes_never_reach_azure(protected_target):
    folder = ROOT / ".internal" / "runs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"script-test-{uuid.uuid4().hex}.json"
    subscription = "11111111-1111-1111-1111-111111111111"
    group = "rg-foundry-production" if protected_target == "group" else "rg-sample"
    pool_group = "rg-foundry-production" if protected_target == "pool" else "rg-other"
    data = {"version": 1, "run_id": uuid.uuid4().hex, "subscription_id": subscription,
            "resource_group": group, "sandbox_group": "sandbox-sample", "region": "example",
            "sandbox_ids": [], "snapshot_ids": [], "session_pool_ids": []}
    if protected_target != "group":
        data["session_pool_ids"] = [f"/subscriptions/{subscription}/resourceGroups/{pool_group}/providers/Microsoft.App/sessionPools/sample"]
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
        result = invoke("cleanup.ps1", "-ManifestPath", str(path))
        assert result.returncode != 0 and "protected" in result.stderr
        assert "Selected:" not in result.stdout
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize("script,group_parameter", [
    ("setup-sandbox.ps1", "-ResourceGroup"), ("setup-dynamic-sessions.ps1", "-ResourceGroupName"),
])
def test_setup_protects_foundry_resource_family_before_azure(script, group_parameter):
    args = ["-SubscriptionId", "11111111-1111-1111-1111-111111111111", group_parameter,
            "rg-foundry-production", "-Location", "example"]
    args.extend(["-SandboxGroupName", "sample"] if script == "setup-sandbox.ps1" else ["-PoolName", "sample"])
    result = invoke(script, *args)
    assert result.returncode != 0 and "protected" in result.stderr


def test_all_provisioning_and_cleanup_scripts_parse_without_azure():
    command = "$errors = @(); Get-ChildItem scripts\\*.ps1 | ForEach-Object { $tokens = $null; $parseErrors = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$parseErrors); $errors += $parseErrors }; if ($errors.Count) { $errors | Out-String | Write-Error; exit 1 }"
    result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", command], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
