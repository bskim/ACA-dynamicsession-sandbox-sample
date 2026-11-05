#!/usr/bin/env python3
"""Installed guest CLI: real git/jq/sqlite3 subprocesses, not host emulation."""
from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path('/workspace/agent-project')
CONFIG = Path('/usr/local/share/aca-agent-tools.json')


def run(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True, timeout=15).strip()


def versions():
    tools = {name: run(name, '--version') for name in ('git', 'jq', 'sqlite3')}
    paths = [Path(shutil.which(name)) for name in tools]
    paths += [Path('/usr/local/bin/agent-tool'), Path('/usr/local/bin/repo-audit'), CONFIG]
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    packages = run('dpkg-query', '-W', '-f=${Package}=${Version}\n', 'git', 'jq', 'sqlite3')
    config = json.loads(CONFIG.read_text())
    fingerprint = hashlib.sha256(json.dumps([tools, hashes, packages, config], sort_keys=True).encode()).hexdigest()
    return {'installed_tool_runs': True, 'toolchain_sha256': fingerprint,
            'versions': tools, 'packages': packages, 'config': config}


def initialize():
    ROOT.mkdir(parents=True, exist_ok=True)
    if (ROOT / 'config.json').exists():
        raise RuntimeError('Refusing to overwrite an existing project')
    (ROOT / 'config.json').write_text(json.dumps({'service': 'demo', 'retries': 1, 'audit': False}) + '\n')
    (ROOT / 'README.md').write_text('Agent toolchain demo\n')
    run('git', 'init', '-q', str(ROOT))
    run('git', '-C', str(ROOT), 'add', '.')
    run('git', '-C', str(ROOT), '-c', 'user.name=Sandbox Agent', '-c', 'user.email=agent@example.invalid', 'commit', '-qm', 'baseline')
    run('sqlite3', str(ROOT / 'audit.db'), 'CREATE TABLE audit(event TEXT); INSERT INTO audit VALUES("initialized");')
    return analyze()


def analyze():
    config = json.loads(run('jq', '-c', '.', str(ROOT / 'config.json')))
    events = run('sqlite3', str(ROOT / 'audit.db'), 'SELECT group_concat(event) FROM audit;')
    return {'config': config, 'events': events, 'git_status': run('git', '-C', str(ROOT), 'status', '--short')}


def verify():
    evidence = analyze()
    passed = evidence['config']['retries'] == 3 and evidence['config']['audit'] is True and 'patched' in evidence['events']
    report = evidence | {'passed': passed, 'tools': versions()}
    (ROOT / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    if not passed:
        raise RuntimeError('Patch validation failed')
    return report


def cached_audit():
    connection = http.client.HTTPConnection('127.0.0.1', 18765, timeout=5)
    try:
        connection.request('POST', '/audit', body=b'')
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError('Tool server failed')
        result = json.loads(response.read(8192))
        if result.get('protocol') != 'aca-state-worker-v1' or result.get('tool_server_role') != 'repository-index-cache':
            raise RuntimeError('Unexpected tool server')
        return result
    finally:
        connection.close()


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'analyze'
    functions = {'--versions': versions, 'init': initialize, 'analyze': analyze, 'verify': verify, 'cached-audit': cached_audit}
    print(json.dumps(functions[action](), sort_keys=True))


if __name__ == '__main__':
    main()
