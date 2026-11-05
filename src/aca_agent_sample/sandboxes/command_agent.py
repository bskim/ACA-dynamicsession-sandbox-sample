from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from ..agent_loop import AgentResult, run_agent
from ..model_client import ModelAdapter
from ..tool_policy import ToolPolicy, ToolPolicyError, _forbidden, _reject_constant, _unique_object
from ..types import ToolExecutionResult
from .agent_tools import provision_agent_tools, tool_evidence

ANALYZE_COMMAND = "git -C /workspace/agent-project status --short; jq . /workspace/agent-project/config.json | jq -c '{service,retries,audit}'; sqlite3 /workspace/agent-project/audit.db 'SELECT event FROM audit;'; repo-audit analyze"
PATCH_COMMAND = "set -eu; cd /workspace/agent-project; jq '.retries = 3 | .audit = true' config.json > config.next.json; mv config.next.json config.json; sqlite3 audit.db \"INSERT INTO audit VALUES('patched');\"; git diff -- config.json; repo-audit verify"

TURN_ONE = ("You are an agent using external CLI tools INSIDE an isolated Ubuntu sandbox. "
    "The trusted host has installed git, jq, sqlite3 and /usr/local/bin/repo-audit (alias agent-tool). "
    "Do not install/reinstall anything or use network or Azure. All files including scratch files must be inside /workspace; never use /tmp, /var/tmp or mktemp. First run agent-tool --versions. "
    "Run repo-audit init to create /workspace/agent-project (a Git repository, config.json, audit.db). "
    "Then analyze it using git, jq, sqlite3 and a shell pipeline as well as repo-audit analyze. "
    "Then start the installed repository-index server with python3 /workspace/demo/state_worker.py start "
    "This start command already daemonizes; run it synchronously without &, redirects, or a log file. "
    "Then call agent-tool cached-audit to exercise its loopback API and RAM cache. "
    "Report the current retries=1 and audit=false. Do not patch yet.")
TURN_TWO = ("The SAME sandbox was stopped and resumed. Do not install/reinstall tools or initialize the project. "
    "Use the existing agent-tool --versions, git, jq, sqlite3 and repo-audit. "
    "First call agent-tool cached-audit to use the still-running in-memory repository index; do not restart the server. "
    "Inside /workspace/agent-project set config.json retries to 3 and audit to true using jq, "
    "Use jq '.retries = 3 | .audit = true' config.json > config.next.json && mv config.next.json config.json. "
    "Never pipe jq back into the same input file using tee (it truncates input). All scratch files must be in /workspace; no /tmp, /var/tmp or mktemp. "
    "INSERT event 'patched' into the SQLite audit table, inspect git diff, then run repo-audit verify "
    "to write verification.json. Use shell pipelines. Finish only after successful verification.")


class SandboxToolPolicy(ToolPolicy):
    """The model cannot choose the sandbox or any connection information."""

    def schemas(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": "run_sandbox_command",
            "description": "Execute a command in the host-owned sandbox. This is not a host shell.",
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string", "description": "Use /workspace for scratch files; never use /tmp, /var/tmp or mktemp."}, "working_directory": {"type": "string", "default": "/workspace"}},
                "required": ["command"], "additionalProperties": False}}}]

    def validate(self, name: str, arguments: str) -> dict[str, Any]:
        if name != "run_sandbox_command":
            raise ToolPolicyError("Tool is not allowed")
        if not isinstance(arguments, str) or len(arguments) > self.max_arguments_length:
            raise ToolPolicyError("Tool arguments are too large or invalid")
        try:
            parsed = json.loads(arguments, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            if not isinstance(parsed, dict) or _forbidden(parsed):
                raise ToolPolicyError("Forbidden or invalid tool arguments")
        except (ValueError, RecursionError):
            raise ToolPolicyError("Invalid tool arguments") from None
        if set(parsed) - {"command", "working_directory"}:
            raise ToolPolicyError("Tool arguments do not match schema")
        command = parsed.get("command")
        directory = parsed.get("working_directory", "/workspace")
        if (not isinstance(command, str) or not command.strip() or len(command) > self.max_command_length
                or not isinstance(directory, str) or not directory.startswith("/")
                or len(directory) > 1024 or "\x00" in directory):
            raise ToolPolicyError("Invalid command or working directory")
        if any(value in command for value in ('/tmp', '/var/tmp', 'mktemp')):
            raise ToolPolicyError("Use /workspace for all scratch files")
        return {"command": command, "working_directory": directory}


class _Executor:
    def __init__(self, adapter: Any, sandbox_id: str, pause_between_commands: bool):
        self.adapter, self.sandbox_id = adapter, sandbox_id
        self.pause_between_commands = pause_between_commands
        self.paused = False

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        if self.paused:
            self.adapter.resume(self.sandbox_id)
            self.paused = False
        result = self.adapter.exec(self.sandbox_id, arguments["command"],
                                   working_directory=arguments["working_directory"])
        result = ToolExecutionResult(result.status, result.stdout, result.stderr, result.exit_code,
                                     result.error_kind, {**result.metadata, 'guest_command': arguments['command']})
        if self.pause_between_commands and result.error_kind not in {"outcome_unknown", "timeout", "state_lost"}:
            self.adapter.stop(self.sandbox_id)
            self.paused = True
        return result


class SandboxCommandAgent:
    def __init__(self, model: ModelAdapter, adapter: Any, *, mode: str = "Memory",
                 auto_suspend_seconds: int = 300, pause_between_commands: bool = False,
                 policy: SandboxToolPolicy | None = None):
        self.model, self.adapter = model, adapter
        self.mode, self.auto_suspend_seconds = mode, auto_suspend_seconds
        self.pause_between_commands = pause_between_commands
        self.policy = policy or SandboxToolPolicy()

    def run_toolchain_demo(self, *, max_turns: int = 12) -> dict[str, Any]:
        """Two model tasks separated by one explicit stop/resume; provisioning runs once."""
        self.last_run: dict[str, Any] = {}
        previous_ids = set(self.adapter.sandboxes)
        try:
            sid = self.adapter.create(mode=self.mode, auto_suspend_seconds=3600)
            self.adapter.set_lifecycle_policy(sid, mode=self.mode, interval=3600)
            from .lifecycle_benchmark import fixture_source, fixture_install_command, WorkerObservation, evaluate_mode_observation
            installed = provision_agent_tools(self.adapter, sid)
            fixture = self.adapter.exec(sid, fixture_install_command(fixture_source()))
            if fixture.status != 'succeeded':
                raise RuntimeError('Tool server installation failed')
            first = run_agent(self.model, _Executor(self.adapter, sid, False), self.policy, TURN_ONE, max_turns=max_turns)
            self.last_run["turn_one"] = asdict(first)
            if first.status != "completed" or not first.turns:
                raise RuntimeError("Agent analysis did not complete successfully")
            baseline = self.adapter.exec(sid, 'repo-audit analyze')
            if baseline.status != 'succeeded' or json.loads(baseline.stdout)['config'] != {'service': 'demo', 'retries': 1, 'audit': False}:
                raise RuntimeError('Agent did not create the expected project')
            before = self.adapter.exec(sid, 'python3 /workspace/demo/state_worker.py probe')
            if before.status != 'succeeded':
                raise RuntimeError('Agent tool server probe failed')
            worker_before = WorkerObservation.from_mapping(json.loads(before.stdout))
            if not worker_before.valid_ram or not worker_before.index_entry_count:
                raise RuntimeError('Agent did not start repository-index server')
            self.adapter.stop(sid)
            self.adapter.resume(sid)
            after = self.adapter.exec(sid, 'python3 /workspace/demo/state_worker.py probe')
            if after.status != 'succeeded':
                raise RuntimeError('Resumed tool server probe failed')
            worker_after = WorkerObservation.from_mapping(json.loads(after.stdout))
            oracle = evaluate_mode_observation(self.mode, worker_before, worker_after)
            if not oracle.passed:
                raise RuntimeError('Tool server memory continuity failed')
            resumed = tool_evidence(self.adapter, sid)
            if installed['toolchain_sha256'] != resumed['toolchain_sha256']:
                raise RuntimeError('Installed toolchain changed across resume')
            second = run_agent(self.model, _Executor(self.adapter, sid, False), self.policy, TURN_TWO, max_turns=max_turns)
            self.last_run["turn_two"] = asdict(second)
            if second.status != "completed" or not second.turns:
                raise RuntimeError("Agent patch did not complete successfully")
            verified = self.adapter.exec(sid, 'repo-audit verify')
            if verified.status != 'succeeded' or json.loads(verified.stdout).get('passed') is not True:
                raise RuntimeError('Agent patch validation failed')
            final_tools = tool_evidence(self.adapter, sid)
            if installed['toolchain_sha256'] != final_tools['toolchain_sha256']:
                raise RuntimeError('Agent modified or reinstalled toolchain')
            return {'mode': 'offline' if getattr(self.adapter, 'simulated', False) else 'live',
                    'provision_count': 1, 'installed_tool_preserved': True, 'stop_resume': True,
                    'tool_server_before': asdict(worker_before), 'tool_server_after': asdict(worker_after),
                    'tool_server_oracle': asdict(oracle), 'tools_before': installed, 'tools_after_resume': resumed, 'tools_after_task': final_tools,
                    'turn_one': asdict(first), 'turn_two': asdict(second),
                    'verification': json.loads(verified.stdout), 'passed': True}
        finally:
            failures = []
            for sid in set(self.adapter.sandboxes) - previous_ids:
                try:
                    self.adapter.delete(sid)
                except Exception as exc:
                    failures.append(exc)
            if failures:
                raise ExceptionGroup('Sandbox cleanup failed; ownership retained', failures)

    def run(self, prompt: str, *, max_turns: int = 8) -> AgentResult:
        previous_ids = set(self.adapter.sandboxes)
        try:
            sandbox_id = self.adapter.create(mode=self.mode, auto_suspend_seconds=self.auto_suspend_seconds)
            self.adapter.set_lifecycle_policy(sandbox_id, mode=self.mode, interval=self.auto_suspend_seconds)
            executor = _Executor(self.adapter, sandbox_id, self.pause_between_commands)
            return run_agent(self.model, executor, self.policy, prompt, max_turns=max_turns)
        finally:
            failures = []
            for sandbox_id in set(self.adapter.sandboxes) - previous_ids:
                try:
                    self.adapter.delete(sandbox_id)
                except Exception as exc:
                    failures.append(exc)
            if failures:
                raise ExceptionGroup("Sandbox cleanup failed; ownership retained", failures)

