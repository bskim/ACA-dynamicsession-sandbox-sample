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

Even in Memory mode, revalidate external connections and tokens. A named snapshot is a separate group/region-scoped resource used to create a new sandbox clone. The example modifies the source and checks that the clone’s file is independent. Examples attempt to clean up their own sandboxes and snapshots; recover using the private manifest if cleanup fails. [Official Sandbox overview](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-overview) · [Lifecycle and state](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-snapshots-state-management).

In the observed live service, the default `ubuntu` image had no `/workspace`, so it was initialized after creation with `mkdir -p /workspace` from `/`. A GET following synchronous snapshot creation sometimes omitted `status`. In that case completion requires existence of the requested matching snapshot ID; `Creating` or failure statuses are not accepted as complete. Only observation GETs are retried for transient 404 within a deadline; snapshot creation POST is not automatically resent. These preview contracts can change by version.

## Exposing and invoking tools for an external agent: three stages

The brain in this example is the policy orchestrator on the trusted host and the remote Foundry model; execution tools are the CLIs and loopback server inside the sandbox micro VM. Putting the orchestrator inside the guest could require injecting inference tokens or Azure credentials, increasing the risk of token theft through untrusted code or prompt injection. The host manages credentials. The following describes `run_toolchain_demo`; the general `run` path requires separate tool installation.

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

1. Independent agent updates: while tool interfaces and compatibility remain stable, replacing the model (for example `gpt-5.4-mini` → `gpt-5.4`), editing prompts or host logic, or adding code review, security audit, or patching roles does not require rebuilding the sandbox image or snapshot. Verify tool-calling support and access when replacing the model.
2. Sandbox reusability (cloning both installed packages and warmed-up processes): standard container images capture only the disk filesystem, requiring background processes (such as language servers or local index/cache daemons) to cold-start on every new instance. An ACA Sandbox explicit snapshot captures both the installed CLI packages on disk and the running, warmed-up background server process in RAM. Cloning a new sandbox from this snapshot eliminates both package installation time (`clone_provision_count=0`) and process startup/warm-up time, and the cloned sandbox can subsequently run its own independent `Memory` mode stop/resume lifecycle.

`snapshot_clone.py` creates a new clone from a named snapshot of a source sandbox with installed tools and a running RAM index server, verifying `clone_provision_count=0`, `clone_ram_preserved_from_snapshot=true`, and the clone's own Memory stop/resume (`clone_memory_stop_resume_passed=true`). `run_toolchain_demo` directly installs tools in a new sandbox. Having different agents use a golden snapshot requires separate integration, routing, ownership, and authorization controls. Guest tool, image configuration, or tool contract changes may require compatibility checks and a snapshot update.

### Stage 1 — Prepare the guest tool layer

After creating the sandbox and initializing `/workspace`, the host prepares tools inside the guest Ubuntu VM through the SDK's `sandbox.exec(...)`.

- **System CLIs**: `provision_agent_tools` runs `apt-get update` followed by `apt-get install -y --no-install-recommends git jq sqlite3`. Their executables are `/usr/bin/git`, `/usr/bin/jq`, and `/usr/bin/sqlite3`, respectively.
- **Custom CLI**: the same function installs `agent_toolkit.py` as `/usr/local/bin/agent-tool` and creates the `/usr/local/bin/repo-audit` and `/usr/local/bin/agent-toolkit` symlinks. Available commands include `agent-tool --versions`, `repo-audit init`, `repo-audit analyze`, `repo-audit verify`, and `agent-tool cached-audit`.
- **Loopback in-memory server**: the host copies `/workspace/demo/state_worker.py` using a separate fixture installation command. In the first task turn, after initializing the project, the model starts the server with `python3 /workspace/demo/state_worker.py start`. `agent-tool cached-audit` queries the RAM index cache from inside the guest using `POST http://127.0.0.1:18765/audit`, without opening an external port. Installation alone does not start the server.

### Stage 2 — Expose a gateway and tool catalog to the model

`SandboxToolPolicy.schemas` provides a single gateway schema, `run_sandbox_command(command, working_directory)`. The task prompt lists installed tools and usage, including `git`, `jq`, `sqlite3`, `repo-audit`, and `agent-tool cached-audit`. The model composes the advertised commands with `&&`, `|`, redirection, and similar shell syntax, and proposes one or more gateway calls.

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

`SandboxToolPolicy.validate` checks the function name, JSON structure, lengths, and arguments, rejecting routing or authorization fields such as `sandbox_id`, `endpoint`, and `token`, as well as extra fields. The default working directory is `/workspace` and the prompt instructs the model to use it for all scratch files. The policy rejects commands containing the literal strings `/tmp`, `/var/tmp`, or `mktemp`. It accepts other absolute working directories, so restricting path bypasses and shell behavior requires separate filesystem isolation.

`_Executor` passes its own `sandbox_id`, not a model-selected ID, to `AcaSandboxAdapter.exec`. The adapter looks up only sandboxes it has registered and calls `sandbox.exec(...)`. The live SDK sends `POST https://management.<region>.azuredevcompute.io/.../sandboxes/<id>/executeShellCommand?api-version=2026-02-01-preview` to the ACA Sandbox data plane. This is an abbreviated path; the host manages the endpoint, authentication, and ID. Offline mode simulates this call.

The guest's `exit_code`, `stdout`, and `stderr` are normalized into `ToolExecutionResult`, then output is length-limited and sent to the model in a `role="tool"` message with `status` and `error_kind`. Host-only metadata is not sent to the model. Because this example can execute arbitrary shell commands, additional egress and resource limits and independent security controls are required.

## Installed agent tools and an in-memory tool server

`provision_agent_tools` runs `apt-get update` and `apt-get install -y --no-install-recommends git jq sqlite3` inside the sandbox, then installs `/usr/local/bin/agent-tool`, `repo-audit`, `agent-toolkit` and dedicated configuration. It does not install host tools or send Foundry credentials into the guest. Outbound access to Ubuntu package repositories and Python 3 in the guest image are required; installation adds time and cost. Provisioning runs once per sandbox.

1. Turn 1: the Foundry model checks installed tool versions and invokes `repo-audit init` to create a Git repository, JSON configuration and SQLite audit database. It analyzes them using `git`, `jq`, `sqlite3` and shell pipelines, starts the installed `state_worker.py` repository-index server, then calls its loopback API using `agent-tool cached-audit`.
2. Host stop → resume: the same sandbox resumes. The host compares successful execution and the SHA-256 fingerprint of installed files, package versions and configuration, plus server nonce, counter, RAM buffer and process-start identity. No tools are reinstalled and the server is not restarted.
3. Turn 2: the model reuses existing CLIs and the running tool server, patches configuration with `jq`, and inserts `patched` into SQLite. `repo-audit verify` generates `verification.json`, independently checked by the host. The model can repair known command errors; uncertain execution outcomes are never automatically replayed.

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

The explicit snapshot/clone example captures a source sandbox where the CLI toolchain is installed and the RAM index server is already running. With zero provisioning calls (`clone_provision_count=0`) and no process restart (`clone_worker_restarted=false`), the clone immediately uses the installed CLIs and warm RAM server, then verifies its own independent Memory mode stop/resume (`clone_memory_stop_resume_passed=true`) and state isolation from the source. The default offline mode simulates these contracts.


### Observed live results and interpretation (2026-11-05)

#### Scenario 4: `command_agent.py --live` (`.\run-demo.ps1 4`)
The `gpt-5.4-mini` run succeeded with one tool call in Turn 1 and two in Turn 2 (each combining multiple CLI commands).

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

- **`provision_count: 1` & `installed_tool_preserved: true`**: `git 2.56.0`, `jq-1.8.1`, `sqlite3 3.46.1`, and `/usr/local/bin/agent-tool` (`repo-audit`) were provisioned once after sandbox creation. Across `stop` → `resume` (`tools_before`, `tools_after_resume`, `tools_after_task`), the binaries and `toolchain_sha256` fingerprint remained identical, allowing Turn 2 patching and verification without reinstallation.
- **`memory_preserved: true` & `worker_restarted: false`**: Because the sandbox stopped and resumed in `Memory` mode, the background loopback RAM index server (`state_worker.py`) remained alive without restarting, and its in-memory request counter (`in_memory_counter`, never written to disk) advanced continuously from `3` before `stop`/`resume` to `4` immediately after resume.

#### Scenario 6: `snapshot_clone.py --live` (`.\run-demo.ps1 6`)
This scenario captures an explicit snapshot (`create_snapshot`) of a source sandbox where both external CLI packages are installed and the background RAM index server is warmed up and running, then clones a new sandbox (`clone`):

```json
{
  "mode": "live",
  "operation": "explicit_snapshot_clone",
  "installed_tool_preserved": true,
  "clone_provision_count": 0,
  "clone_ram_preserved_from_snapshot": true,
  "clone_worker_restarted": false,
  "clone_memory_stop_resume_passed": true,
  "counter_progression": {
    "source_at_snapshot": 2,
    "clone_immediately_after_create": 3,
    "clone_after_own_memory_stop_resume": 4,
    "source_after_independent_probe": 3
  },
  "source_project_unchanged": true,
  "distinct_sandboxes": true,
  "isolation_after_clone": true,
  "passed": true
}
```

- **Skipping both package reinstallation and process cold-start (`clone_provision_count: 0`, `clone_ram_preserved_from_snapshot: true`, `clone_worker_restarted: false`)**: Standard container images store only the filesystem and must cold-start background processes on every new instance. An ACA Sandbox snapshot captures both disk and running RAM processes, so the restored `clone` continues directly from the warmed-up state without rerunning `apt-get`, restarting the server, or rebuilding the in-memory index cache.
- **How `counter_progression` (`2 → 3 → 4`, source at `3`) works**:
  - `source_at_snapshot: 2`: The source sandbox starts the RAM index server (`1`) and runs one pre-snapshot probe (`2`) right before capturing the snapshot.
  - `clone_immediately_after_create: 3`: Immediately after creating the clone, probing the server without starting it returns `3`, continuing from the snapshot's in-memory counter (`2`).
  - `clone_after_own_memory_stop_resume: 4`: Stopping and resuming the cloned sandbox itself in `Memory` mode preserves its RAM process, advancing the next probe from `3` to `4`.
  - `source_after_independent_probe: 3`: Even after the clone reaches `4` and patches its project files/DB, the source sandbox remains an independent VM whose next probe returns `2 + 1 = 3` with its project configuration untouched (`source_project_unchanged: true`). See the [deployment, execution and cleanup How-To](how-to-deploy-and-run-en.md) for reproduction.
