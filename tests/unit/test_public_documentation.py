from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = ["README", "SECURITY", *("docs/" + name for name in (
    "architecture", "dynamic-sessions", "sandbox", "snapshot-comparison", "troubleshooting", "how-to-deploy-and-run"
))]


def executable_blocks(text):
    blocks = re.findall(r"```(?:powershell|text)\n(.*?)```", text, re.S)
    return [line.strip() for block in blocks for line in block.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


@pytest.mark.parametrize("name", DOCUMENTS)
def test_public_document_pairs_keep_commands_and_learn_targets_in_sync(name):
    korean = (ROOT / (name + ".md")).read_text(encoding="utf-8")
    english = (ROOT / (name + "-en.md")).read_text(encoding="utf-8")
    assert executable_blocks(korean) == executable_blocks(english)
    pattern = r"https://learn\.microsoft\.com/([^\s)<>]+)"
    ko_links = re.findall(pattern, korean)
    en_links = re.findall(pattern, english)
    assert all(link.startswith("ko-kr/") for link in ko_links)
    assert all(link.startswith("en-us/") for link in en_links)
    assert [link.removeprefix("ko-kr/") for link in ko_links] == [link.removeprefix("en-us/") for link in en_links]


README_COMMANDS = [line.split()[1:] for line in executable_blocks((ROOT / "README.md").read_text(encoding="utf-8"))
                   if line.startswith(".\\.venv\\Scripts\\python.exe ")
                   and (" examples\\" in line or " -m aca_agent_sample.cli " in line)]


@pytest.mark.parametrize("arguments", README_COMMANDS, ids=lambda args: " ".join(args))
def test_readme_offline_python_commands_run_as_documented(arguments):
    assert "--live" not in arguments
    env = {**os.environ, "ENABLE_LIVE_AZURE": "false", "ENABLE_PROVISIONING": "false", "PYTHONUTF8": "1"}
    result = subprocess.run([sys.executable, *arguments], cwd=ROOT, env=env, capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    if "--format" in arguments:
        assert "simulated" in result.stdout.lower()
    elif "command_agent.py" in " ".join(arguments) or "command-agent" in arguments:
        assert "Sandbox demo complete" in result.stdout
    else:
        assert '"mode": "offline"' in result.stdout
