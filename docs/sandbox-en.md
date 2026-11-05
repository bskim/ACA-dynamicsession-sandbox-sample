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

## Historical v1 live results (2026-11-05)

The hello.sh numbers below describe the previous minimal demo, not the current toolchain scenario or its performance.

See the [deployment, execution and cleanup How-To](how-to-deploy-and-run-en.md) for reproduction. In `command_agent.py --live`, `gpt-5.4-mini` made three successful tool calls to create and run `/workspace/demo/hello.sh` and verify `Hello from demo script`. The measured example-main wall was about 49.2 s, including model, host, network and cleanup but excluding process startup and prior imports.

| Stage | Count | Client-observed ms per call |
| --- | ---: | --- |
| create | 1 | 3811.842 |
| stop | 3 | 6980.071, 7054.588, 7149.089 |
| resume | 2 | 1544.888, 1257.362 |
| delete | 1 | 1322.698 |

After creation, remote commands created the file, ran it, and verified output; the same sandbox was stopped/resumed between commands and deleted at the end. These are client call measurements, not pure service processing times or an SLA.

`snapshot_clone.py --live` returned (IDs and manifest path omitted):

```json
{
  "mode": "live",
  "operation": "explicit_snapshot_clone",
  "artifact": "/workspace/clone-demo/artifact.txt",
  "source_sha256_at_snapshot": "40eb8719d44ea3ffc6da1359eead8a7c342c31372b44e20ece6d9090e44f7bc3",
  "clone_sha256": "40eb8719d44ea3ffc6da1359eead8a7c342c31372b44e20ece6d9090e44f7bc3",
  "source_sha256_after_change": "a433df8e0a560ca2d35d4fab337c0b5e1f7b01481cd5d606ff20b1659bfc46cd",
  "clone_sha256_after_change": "40eb8719d44ea3ffc6da1359eead8a7c342c31372b44e20ece6d9090e44f7bc3",
  "distinct_sandboxes": true,
  "isolation_after_clone": true,
  "passed": true,
  "warning": "Explicit snapshot clone is separate from stop/resume; offline mode is simulated."
}
```

Explicit `create_snapshot` makes a group/region-scoped snapshot; `snapshot_id` creates a **new sandbox**. Source and clone were distinct, and after the source changed the clone kept its snapshot-time hash. This proves file independence, not clone RAM continuity. Lifecycle auto-suspend stop/resume is a different operation that resumes the same sandbox.

In the observed live service, the default `ubuntu` image had no `/workspace`, so it was initialized after creation with `mkdir -p /workspace` from `/`. A GET following synchronous snapshot creation sometimes omitted `status`. In that case completion requires existence of the requested matching snapshot ID; `Creating` or failure statuses are not accepted as complete. Only observation GETs are retried for transient 404 within a deadline; snapshot creation POST is not automatically resent. These preview contracts can change by version.

## v2: installed agent tools and an in-memory tool server

`provision_agent_tools` runs `apt-get update` and `apt-get install -y --no-install-recommends git jq sqlite3` **inside the sandbox**, then installs `/usr/local/bin/agent-tool`, `repo-audit`, `agent-toolkit` and dedicated configuration. It does not install host tools or send Foundry credentials into the guest. Outbound access to Ubuntu package repositories and Python 3 in the guest image are required; installation adds time and cost. Provisioning runs once per sandbox.

1. **Turn 1**: the Foundry model checks installed tool versions and invokes `repo-audit init` to create a Git repository, JSON configuration and SQLite audit database. It analyzes them using `git`, `jq`, `sqlite3` and shell pipelines, starts the installed `state_worker.py` repository-index server, then calls its loopback API using `agent-tool cached-audit`.
2. **Host stop → resume**: the same sandbox resumes. The host compares successful execution and the SHA-256 fingerprint of installed files, package versions and configuration, plus server nonce, counter, RAM buffer and process-start identity. No tools are reinstalled and the server is not restarted.
3. **Turn 2**: the model reuses existing CLIs and the running tool server, patches configuration with `jq`, and inserts `patched` into SQLite. `repo-audit verify` generates `verification.json`, independently checked by the host. The model can repair known command errors; uncertain execution outcomes are never automatically replayed.

```powershell
.\.venv\Scripts\python.exe examples\sandbox\command_agent.py --live
.\.venv\Scripts\python.exe examples\sandbox\compare_snapshot_modes.py --live --iterations 3 --output-dir .internal\results\live-snapshot-compare-v2
.\.venv\Scripts\python.exe examples\sandbox\snapshot_clone.py --live
```

| Evidence | Memory | Disk |
| --- | --- | --- |
| `installed_tool_preserved` | `true`: reuse CLI, packages and configuration | `true`: reuse CLI, packages and configuration |
| `disk_preserved` | `true` | `true` |
| `memory_preserved` | `true`: no index-server restart | `false`: old server absent |
| `worker_restarted` | `false` | `true`: rerun the installed server program only |

The explicit snapshot/clone example installs the real toolchain and sample project in the source. With zero provisioning calls, the clone immediately uses its installed CLIs to patch and verify the project. Source configuration and the clone's snapshot-time artifact are also checked for independent state. This does not claim clone RAM continuity. The default offline mode simulates these contracts and does not run apt or real guest binaries.


### v2 observed live result (2026-11-05)

The final gpt-5.4-mini run succeeded with one tool call for Turn 1 and two for Turn 2 (several CLIs per call), with no failed commands. `installed_tool_preserved=true`, Memory server `memory_preserved=true`/`worker_restarted=false`. The explicit clone also passed with zero clone provisioning calls and independent source configuration. Memory/Disk comparisons each passed all three samples. Sandboxes, snapshots, temporary group/resources and created roles were removed; `.env` was unchanged. All 317 unit tests passed. These checks do not imply zero execution charges.
