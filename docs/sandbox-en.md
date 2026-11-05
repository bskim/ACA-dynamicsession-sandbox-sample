[한국어](./sandbox.md)

# ACA Sandbox: command execution and lifecycle

![Sandbox Memory and Disk modes and named snapshot clone](assets/sandbox-memory-vs-disk-snapshot.svg)

[Draw.io source](assets/sandbox-memory-vs-disk-snapshot.drawio) · [README and setup](../README-en.md) · [Experiment details](snapshot-comparison-en.md)

A SandboxGroup is a management boundary and each sandbox is an isolated micro VM. Configure the group with `infra/sandbox/main.bicep` and `scripts/setup-sandbox.ps1`. Setup defaults to dry run; actual deployment requires `-Execute` and PowerShell `ENABLE_PROVISIONING=true`. The Python SDK path works without the separate `aca` CLI. Deployment, commands, and snapshots may incur costs.

```powershell
.\.venv\Scripts\python.exe examples\sandbox\command_agent.py
.\.venv\Scripts\python.exe examples\sandbox\compare_snapshot_modes.py --iterations 3 --format markdown
.\.venv\Scripts\python.exe examples\sandbox\snapshot_clone.py
```

The default is an offline simulation. For live calls configure `.env` and RBAC and specify both `ENABLE_LIVE_AZURE=true` and `--live`. The command agent uses an installed CLI toolchain and a loopback tool server, stopping/resuming the same sandbox between two agent tasks. Credentials are not sent inside the sandbox. This development example executes arbitrary model-proposed commands; additional controls are required before exposing it as a service.

| Behavior | Memory | Disk |
| --- | --- | --- |
| After stop/resume | Root disk and RAM processes/variables preserved | Root disk retained; VM and processes restart |
| Data Disk volume | Unsupported when attached | Supported mode |
| Measurement | Separate Running, first exec and worker readiness stages | Same stages; worker readiness includes bootstrap |

Even in Memory mode, revalidate external connections and tokens. A named snapshot is a separate group/region-scoped resource used to create a new sandbox clone. The example modifies the source and checks that the clone's file is independent. Do not confuse it with resuming an existing sandbox. Examples attempt to clean up their own sandboxes and snapshots; recover using the private manifest if cleanup fails. [Official Sandbox overview](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-overview) · [Lifecycle and state](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-snapshots-state-management).

In the observed live service, the default `ubuntu` image had no `/workspace`, so it was initialized after creation with `mkdir -p /workspace` from `/`. A GET following synchronous snapshot creation sometimes omitted `status`. In that case completion requires existence of the requested matching snapshot ID; `Creating` or failure statuses are not accepted as complete. Only observation GETs are retried for transient 404 within a deadline; snapshot creation POST is not automatically resent. These preview contracts can change by version.

## Exposing and invoking tools for an external agent: three stages

The brain in this example is the **policy orchestrator on the trusted host and the remote Foundry model**; the hands are the CLIs and loopback tool server installed inside the sandbox micro VM. The model itself does not run on the host. Putting the orchestrator inside the guest could require injecting inference tokens or Azure credentials into it, increasing the risk of token theft through untrusted code or prompt injection. The host manages credentials and does not inject them into the guest. The following describes `run_toolchain_demo`; the general `run` path does not automatically install the tools.

```text
Host: provision_agent_tools + fixture installation
  -> guest: git / jq / sqlite3 + agent-tool / repo-audit + state_worker.py
Host: prompt catalog + run_sandbox_command schema -> model tool call
  -> SandboxToolPolicy.validate -> _Executor (host-owned sandbox_id)
  -> AcaSandboxAdapter.exec -> sandbox.exec -> executeShellCommand
  -> guest CLI -> optional loopback state_worker.py /audit
  -> ToolExecutionResult -> role="tool" -> model
```

### Decoupling agent and sandbox update lifecycles

If agent prompts, orchestration logic, model version, and business rules are baked into the guest image or snapshot, even a small agent change creates tight coupling by requiring an image or snapshot update and redeployment. This sample instead separates the host agent from the guest's general CLI and repository audit/indexing tools.

1. **Independent agent updates:** while tool interfaces and compatibility remain stable, replacing the model (for example `gpt-5.4-mini` → `gpt-5.4`), editing prompts or host logic, or adding code review, security audit, or patching roles does not require rebuilding the sandbox image or snapshot. Verify tool-calling support and access when replacing the model.
2. **Sandbox reusability:** reuse a golden snapshot with heavyweight CLI packages and the base server program installed to clone sandboxes for different agent roles, or stop/resume an existing instance. Resume uses the same sandbox; a clone is a separate sandbox. Server RAM and process continuity depends on Memory/Disk mode and differs for clones; do not assume a running server is automatically restored from a snapshot.

The actual `snapshot_clone.py` makes a new clone from a named snapshot of a source with installed tools and checks `clone_provision_count=0`. This example does not connect multiple specialist agents, and `run_toolchain_demo` directly installs tools in a new sandbox. Having different agents use a golden snapshot requires separate integration, routing, ownership, and authorization controls. Guest tool, image configuration, or tool contract changes may require compatibility checks and a snapshot update.

### Stage 1 — Prepare the guest tool layer

After creating the sandbox and initializing `/workspace`, the host prepares tools inside the guest Ubuntu VM through the SDK's `sandbox.exec(...)`.

- **System CLIs**: `provision_agent_tools` runs `apt-get update` followed by `apt-get install -y --no-install-recommends git jq sqlite3`. Their executables are `/usr/bin/git`, `/usr/bin/jq`, and `/usr/bin/sqlite3`, respectively.
- **Custom CLI**: the same function installs `agent_toolkit.py` as `/usr/local/bin/agent-tool` and creates the `/usr/local/bin/repo-audit` and `/usr/local/bin/agent-toolkit` symlinks. Available commands include `agent-tool --versions`, `repo-audit init`, `repo-audit analyze`, `repo-audit verify`, and `agent-tool cached-audit`.
- **Loopback in-memory server**: the host copies `/workspace/demo/state_worker.py` using a separate fixture installation command. In the first task turn, after initializing the project, the model starts the server with `python3 /workspace/demo/state_worker.py start`. `agent-tool cached-audit` queries the RAM index cache from inside the guest using `POST http://127.0.0.1:18765/audit`, without opening an external port. Installation alone does not start the server.

### Stage 2 — Expose a gateway and tool catalog to the model

`SandboxToolPolicy.schemas` provides a single gateway schema, `run_sandbox_command(command, working_directory)`, rather than one function per CLI. The task prompt lists installed tools and usage, including `git`, `jq`, `sqlite3`, `repo-audit`, and `agent-tool cached-audit`. This combines a general shell gateway with a prompt catalog; it does not claim that a particular commercial agent uses the same internal implementation. Rather than directly discovering or invoking the tools, the model composes the advertised commands with `&&`, `|`, redirection, and similar shell syntax, and proposes one or more gateway calls.

The following JSON example shows the actual function schema (`working_directory` is optional).

```text
{
  "type": "function",
  "function": {
    "name": "run_sandbox_command",
    "description": "Execute a command in the host-owned sandbox. This is not a host shell.",
    "parameters": {
      "type": "object",
      "properties": {
        "command": {
          "type": "string",
          "description": "Use /workspace for scratch files; never use /tmp, /var/tmp or mktemp."
        },
        "working_directory": {"type": "string", "default": "/workspace"}
      },
      "required": ["command"],
      "additionalProperties": false
    }
  }
}
```

### Stage 3 — Host validation and data-plane execution

`SandboxToolPolicy.validate` checks the function name, JSON structure, lengths, and arguments, rejecting routing or authorization fields such as `sandbox_id`, `endpoint`, and `token`, as well as extra fields. The default working directory is `/workspace` and the prompt instructs the model to use it for all scratch files. The policy rejects commands containing the literal strings `/tmp`, `/var/tmp`, or `mktemp`. **However, it also accepts other absolute working directories and does not fully prevent path bypasses or shell behavior.** Do not treat this validation as enforcing `/workspace` or filesystem confinement.

`_Executor` passes its own `sandbox_id`, not a model-selected ID, to `AcaSandboxAdapter.exec`. The adapter looks up only sandboxes it has registered and calls `sandbox.exec(...)`. The live SDK sends `POST https://management.<region>.azuredevcompute.io/.../sandboxes/<id>/executeShellCommand?api-version=2026-02-01-preview` to the ACA Sandbox data plane. This is an abbreviated path; the host manages the endpoint, authentication, and ID. Offline mode simulates this call.

The guest's `exit_code`, `stdout`, and `stderr` are normalized into `ToolExecutionResult`, then output is length-limited and sent to the model in a `role="tool"` message with `status` and `error_kind`. Host-only metadata is not sent to the model. Because this example can execute arbitrary shell commands, additional egress and resource limits and independent security controls are required.

## Installed agent tools and an in-memory tool server

`provision_agent_tools` runs `apt-get update` and `apt-get install -y --no-install-recommends git jq sqlite3` **inside the sandbox**, then installs `/usr/local/bin/agent-tool`, `repo-audit`, `agent-toolkit` and dedicated configuration. It does not install host tools or send Foundry credentials into the guest. Outbound access to Ubuntu package repositories and Python 3 in the guest image are required; installation adds time and cost. Provisioning runs once per sandbox.

1. **Turn 1**: the Foundry model checks installed tool versions and invokes `repo-audit init` to create a Git repository, JSON configuration and SQLite audit database. It analyzes them using `git`, `jq`, `sqlite3` and shell pipelines, starts the installed `state_worker.py` repository-index server, then calls its loopback API using `agent-tool cached-audit`.
2. **Host stop → resume**: the same sandbox resumes. The host compares successful execution and the SHA-256 fingerprint of installed files, package versions and configuration, plus server nonce, counter, RAM buffer and process-start identity. No tools are reinstalled and the server is not restarted.
3. **Turn 2**: the model reuses existing CLIs and the running tool server, patches configuration with `jq`, and inserts `patched` into SQLite. `repo-audit verify` generates `verification.json`, independently checked by the host. The model can repair known command errors; uncertain execution outcomes are never automatically replayed.

```powershell
.\.venv\Scripts\python.exe examples\sandbox\command_agent.py --live
.\.venv\Scripts\python.exe examples\sandbox\compare_snapshot_modes.py --live --iterations 3 --output-dir .internal\results\live-snapshot-compare
.\.venv\Scripts\python.exe examples\sandbox\snapshot_clone.py --live
```

| Evidence | Memory | Disk |
| --- | --- | --- |
| `installed_tool_preserved` | `true`: reuse CLI, packages and configuration | `true`: reuse CLI, packages and configuration |
| `disk_preserved` | `true` | `true` |
| `memory_preserved` | `true`: no index-server restart | `false`: old server absent |
| `worker_restarted` | `false` | `true`: rerun the installed server program only |

The explicit snapshot/clone example installs the real toolchain and sample project in the source. With zero provisioning calls, the clone immediately uses its installed CLIs to patch and verify the project. Source configuration and the clone's snapshot-time artifact are also checked for independent state. This does not claim clone RAM continuity. The default offline mode simulates these contracts and does not run apt or real guest binaries.


### Observed live results (2026-11-05)

The final gpt-5.4-mini run succeeded with one tool call for Turn 1 and two for Turn 2 (several CLIs per call), with no failed commands. `installed_tool_preserved=true`, Memory server `memory_preserved=true`/`worker_restarted=false`. The explicit clone also passed with zero clone provisioning calls and independent source configuration. Memory/Disk comparisons each passed all three samples. Sandboxes, snapshots, temporary group/resources and created roles were removed; `.env` was unchanged. All 317 unit tests passed. These checks do not imply zero execution charges.


Masked summary of `command_agent.py --live` (resource identifiers and observations omitted):

```json
{
  "mode": "live",
  "provision_count": 1,
  "installed_tool_preserved": true,
  "memory_preserved": true,
  "worker_restarted": false,
  "passed": true
}
```

Masked summary of `snapshot_clone.py --live`:

```json
{
  "mode": "live",
  "operation": "explicit_snapshot_clone",
  "installed_tool_preserved": true,
  "clone_provision_count": 0,
  "source_project_unchanged": true,
  "distinct_sandboxes": true,
  "isolation_after_clone": true,
  "passed": true
}
```

These results show installed CLI reuse and file-state isolation in a new clone, not preservation of the source server RAM. See the [deployment, execution and cleanup How-To](how-to-deploy-and-run-en.md) for reproduction.
