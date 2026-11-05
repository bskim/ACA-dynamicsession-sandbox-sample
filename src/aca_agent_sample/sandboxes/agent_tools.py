"""Trusted, one-time CLI provisioning; no Azure credentials enter the guest."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import shlex
from typing import Any

TOOL_PATH = "/usr/local/bin/agent-tool"
CONFIG_PATH = "/usr/local/share/aca-agent-tools.json"
PROJECT = "/workspace/agent-project"


def toolkit_source() -> str:
    return (Path(__file__).resolve().parents[3] / "examples" / "sandbox" / "fixtures" / "agent_toolkit.py").read_text(encoding="utf-8-sig")


def provision_command() -> str:
    source = base64.b64encode(toolkit_source().encode()).decode()
    program = (
        "import base64,json,os,secrets,subprocess;from pathlib import Path;"
        "subprocess.run(['apt-get','-o','Acquire::Languages=none','-o','Acquire::GzipIndexes=true','update','-qq'],check=True,timeout=120);"
        "subprocess.run(['apt-get','install','-y','-qq','--no-install-recommends','git','jq','sqlite3'],"
        "check=True,timeout=180,env=dict(os.environ,DEBIAN_FRONTEND='noninteractive'));"
        f"p=Path('{TOOL_PATH}');p.write_bytes(base64.b64decode('{source}'));p.chmod(0o755);"
        "[(Path('/usr/local/bin')/name).symlink_to('agent-tool') for name in ('repo-audit','agent-toolkit')];"
        f"c=Path('{CONFIG_PATH}');c.parent.mkdir(parents=True,exist_ok=True);"
        "c.write_text(json.dumps({'version':1,'provision_id':secrets.token_hex(16)}));"
        f"subprocess.run(['{TOOL_PATH}','--versions'],check=True)"
    )
    return "python3 -c " + shlex.quote(program)


def provision_agent_tools(adapter: Any, sandbox_id: str) -> dict:
    result = adapter.exec(sandbox_id, provision_command())
    if result.status != "succeeded":
        raise RuntimeError("Agent tool provisioning failed")
    # apt may emit informational output before the final structured evidence.
    return json.loads(result.stdout.strip().splitlines()[-1])


def tool_evidence(adapter: Any, sandbox_id: str) -> dict:
    result = adapter.exec(sandbox_id, f"{TOOL_PATH} --versions")
    if result.status != "succeeded":
        raise RuntimeError("Installed agent tools are not executable")
    return json.loads(result.stdout)
