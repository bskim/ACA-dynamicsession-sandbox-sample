from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = ["README", "DEMO-PLAYBOOK", "SECURITY", *("docs/" + name for name in (
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


@pytest.mark.parametrize("name", DOCUMENTS)
def test_public_documents_do_not_describe_historical_sandbox_versions(name):
    for suffix in ("", "-en"):
        text = (ROOT / (name + suffix + ".md")).read_text(encoding="utf-8")
        assert "hello.sh" not in text
        assert all(value not in text for value in ("282.964", "786.028", "3811.842"))
        text = text.replace("/openai/v1/", "").replace("OpenAI v1", "")
        assert not re.search(r"(?<![\w-])v[12](?![\w-])", text, re.IGNORECASE)


@pytest.mark.parametrize("suffix", ["", "-en"])
def test_sandbox_live_summaries_document_actual_evidence(suffix):
    text = (ROOT / "docs" / ("sandbox" + suffix + ".md")).read_text(encoding="utf-8")
    summaries = [json.loads(block) for block in re.findall(r"\x60\x60\x60json\n(.*?)\x60\x60\x60", text, re.S)]
    command, clone = summaries
    assert command == {"mode": "live", "provision_count": 1, "installed_tool_preserved": True,
                       "memory_preserved": True, "worker_restarted": False, "passed": True}
    assert clone == {"mode": "live", "operation": "explicit_snapshot_clone",
                     "installed_tool_preserved": True, "clone_provision_count": 0,
                     "clone_ram_preserved_from_snapshot": True, "clone_worker_restarted": False,
                     "clone_memory_stop_resume_passed": True,
                     "counter_progression": {
                         "source_at_snapshot": 2,
                         "clone_immediately_after_create": 3,
                         "clone_after_own_memory_stop_resume": 4,
                         "source_after_independent_probe": 3,
                     },
                     "source_project_unchanged": True, "distinct_sandboxes": True,
                     "isolation_after_clone": True, "passed": True}
    for tool in ("git", "jq", "sqlite3", "agent-tool", "repo-audit", "state_worker.py"):
        assert tool in text


SAMPLES = [
    ("Memory", "1", "true", "true", "true", "false", "9106.398", "1348.855", "297.033", "399.850"),
    ("Disk", "1", "true", "true", "false", "true", "6922.497", "1452.435", "213.394", "936.704"),
    ("Disk", "2", "true", "true", "false", "true", "6802.469", "1325.619", "214.688", "891.404"),
    ("Memory", "2", "true", "true", "true", "false", "8900.459", "1087.710", "252.034", "412.084"),
    ("Memory", "3", "true", "true", "true", "false", "9503.373", "1284.911", "224.748", "451.825"),
    ("Disk", "3", "true", "true", "false", "true", "6790.159", "1437.641", "205.635", "987.945"),
]
STAGES = [
    ("Memory", "3", "3", "0", "stop_to_stopped_ms", "9170.077", "9106.398", "9503.373", "0"),
    ("Memory", "3", "3", "0", "resume_to_running_ms", "1240.492", "1284.911", "1348.855", "0"),
    ("Memory", "3", "3", "0", "first_exec_probe_ms", "257.938", "252.034", "297.033", "0"),
    ("Memory", "3", "3", "0", "worker_ready_ms", "421.253", "412.084", "451.825", "0"),
    ("Disk", "3", "3", "0", "stop_to_stopped_ms", "6838.375", "6802.469", "6922.497", "0"),
    ("Disk", "3", "3", "0", "resume_to_running_ms", "1405.231", "1437.641", "1452.435", "0"),
    ("Disk", "3", "3", "0", "first_exec_probe_ms", "211.239", "213.394", "214.688", "0"),
    ("Disk", "3", "3", "0", "worker_ready_ms", "938.684", "936.704", "987.945", "0"),
]


@pytest.mark.parametrize("suffix", ["", "-en"])
def test_comparison_documents_include_all_samples_and_stage_aggregates(suffix):
    text = (ROOT / "docs" / ("snapshot-comparison" + suffix + ".md")).read_text(encoding="utf-8")
    rows = [tuple(cell.strip() for cell in line.strip("|").split("|"))
            for line in text.splitlines() if line.startswith("| ")]
    assert all(row in rows for row in SAMPLES + STAGES)
    for field in ("installed_tool_preserved=true", "disk_preserved=true",
                  "memory_preserved=true", "memory_preserved=false",
                  "worker_restarted=false", "worker_restarted=true"):
        assert field in text


@pytest.mark.parametrize(("name", "labels"), [
    ("overall-architecture", {
        "vmt": "Sandbox lifecycle\ninstalled CLI tools + RAM server\nMemory / Disk • stop / resume",
        "mt": "B: Memory mode\ninstalled CLI + root disk kept\nRAM server kept; no Data Disk",
        "dt": "B: Disk mode\ninstalled CLI + root disk kept\nRAM server restarts",
    }),
    ("sandbox-memory-vs-disk-snapshot", {
        "m3t": "RAM tool server kept\nnonce + index cache\ncounter continues",
        "d3t": "RAM tool server restarts\nnew nonce + index cache",
        "m4t": "Installed CLI + disk kept\ngit/jq/sqlite3\n+ sentinel SHA",
        "d4t": "Installed CLI + disk kept\ngit/jq/sqlite3\n+ sentinel SHA",
        "s3t": "New clone (reinstall=0)\nimmediate CLI execution\ndifferent sandbox ID",
    }),
])
def test_drawio_and_svg_preserve_current_sandbox_labels(name, labels):
    source = ET.parse(ROOT / "docs" / "assets" / (name + ".drawio"))
    cells = {cell.get("id"): cell.get("value") for cell in source.iter("mxCell")}
    svg = ET.parse(ROOT / "docs" / "assets" / (name + ".svg"))
    rendered = [element.text for element in svg.iter("{http://www.w3.org/2000/svg}text")]
    for cell_id, label in labels.items():
        assert cells[cell_id] == label
        assert all(line in rendered for line in label.splitlines())


@pytest.mark.parametrize("name", DOCUMENTS)
def test_public_document_pairs_keep_numeric_results_in_sync(name):
    korean = (ROOT / (name + ".md")).read_text(encoding="utf-8")
    english = (ROOT / (name + "-en.md")).read_text(encoding="utf-8")
    pattern = r"\d+\.\d+(?:\.\d+)*"
    assert Counter(re.findall(pattern, korean)) == Counter(re.findall(pattern, english))


@pytest.mark.parametrize("name", [
    "overall-architecture", "dynamic-sessions-session-reuse", "sandbox-memory-vs-disk-snapshot",
])
def test_all_diagram_source_labels_are_rendered_without_historical_sandbox_results(name):
    source = ET.parse(ROOT / "docs" / "assets" / (name + ".drawio"))
    svg = ET.parse(ROOT / "docs" / "assets" / (name + ".svg"))
    labels = [cell.get("value", "") for cell in source.iter("mxCell")]
    rendered = [element.text for element in svg.iter("{http://www.w3.org/2000/svg}text")]
    for label in labels:
        assert all(line in rendered for line in label.splitlines() if line)
    text = " ".join(labels + [line for line in rendered if line])
    assert all(value not in text for value in ("hello.sh", "282.964", "786.028", "3811.842"))
    text = text.replace("/openai/v1/", "").replace("OpenAI v1", "")
    assert not re.search(r"(?<![\w-])v[12](?![\w-])", text, re.IGNORECASE)


@pytest.mark.parametrize("name", DOCUMENTS)
def test_korean_public_documents_use_polite_sentence_endings(name):
    text = (ROOT / (name + ".md")).read_text(encoding="utf-8")
    no_code = re.sub(r"```.*?```", "", text, flags=re.S)
    plain_endings = re.findall(
        r"(?:한다|이다|된다|않는다|있다|없다|했다|됐다|인다|란다|시킨다|보관한다|확인한다|필요하다|제외한다|포함된다)\.(?=\s|$)",
        no_code,
    )
    assert plain_endings == [], f"Plain-form endings found in {name}.md: {plain_endings}"

