[한국어](./README.md)

# Azure Container Apps agent sandbox sample

Run untrusted model-generated code and commands in isolated execution environments rather than on the local host. A trusted local Python CLI orchestrator validates Microsoft Foundry model tool calls and forwards them to the selected Azure execution path. This is a single-user **sample**, not a production security boundary or multi-tenant solution. Preview APIs and services can change. Documentation as of 2026-11-05; actual Azure validation and costs depend on your environment.

## Architecture and selection

![Trusted host and two Azure isolated execution paths](docs/assets/overall-architecture.svg)

[Draw.io source](docs/assets/overall-architecture.drawio) · [Detailed architecture](docs/architecture-en.md)

| | Dynamic Sessions | ACA Sandbox |
| --- | --- | --- |
| Managed unit | `Microsoft.App/sessionPools` + `identifier` | `Microsoft.App/sandboxGroups` + sandbox ID |
| State | Variables and files while active; lost on expiry | Same-sandbox stop/resume and explicit snapshot clone |
| Isolation and execution | Pool-managed sessions; PythonLTS, optional Shell | Isolated micro VM; command execution and lifecycle control |
| Suitable for | Short interactive code execution | Resumable long-lived workspace and lifecycle comparison |

A and B are independent alternatives. Only model-backed examples require Foundry.

## Validation scope

In addition to offline examples and Foundry + simulated-backend integration, six examples, TTL expiry, and three samples per mode were verified in actual Azure on 2026-11-05. Results are observations for that preview version and environment, not a performance or universal success guarantee. Sandbox Bicep deployed successfully despite the local BCP081 schema warning. Fresh installation/sign-in, actual setup-wrapper deployment and all CLI live dispatches were not rerun in that live session. [Deployment, execution and cleanup How-To](docs/how-to-deploy-and-run-en.md)

| Actual live scenario | Observed result |
| --- | --- |
| Session reuse | counter 42, nonce/file preserved and other ID isolated; `state_lost` after 330 seconds ([details](docs/dynamic-sessions-en.md)) |
| Coding agent | creation, expected failing test and repaired test passed, model turns `[2,2,2]`, wall about 61.9 s |
| Shell | 2 runs with explicit cwd each time, wall about 5.0 s |
| Sandbox command agent | 3 model tool calls succeeded, file created, executed and output verified ([details](docs/sandbox-en.md)) |
| Memory/Disk comparison | 3 samples each passed; files retained in both, RAM continuity only in Memory; mean worker-ready 282.964/786.028 ms ([full tables](docs/snapshot-comparison-en.md)) |
| Explicit snapshot clone | snapshot-time hash matched; clone file independent after source modification ([details](docs/sandbox-en.md)) |

After TTL the same ID points to a new empty session, not restored old state. Memory/Disk timings are small-sample client observations including polling, not an SLA; Disk worker-ready includes restart. Zero remaining resources does not mean zero charges.

## Prerequisites and configuration

Use Python 3.13 or later, PowerShell, Azure CLI, and an existing Foundry deployment supporting tool calling. Offline examples require no Azure login or resources and use fake adapters. The live paths require separate Azure resources and permissions. Python SDK `azure-containerapps-sandbox==0.1.0b4` is installed as a project dependency. The separate `aca` CLI is not required for these sandbox examples.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\scripts\preflight.ps1
```

Enter your own values only in `.env`; do not share or commit it. Set common `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`, `AZURE_LOCATION`, and settings for the chosen example. The loader rejects **all** `<...>` placeholders, including unused settings: clear unused values to empty strings. `FOUNDRY_OPENAI_BASE_URL` is an inference URL ending in `/openai/v1/`, not the project endpoint. Set `FOUNDRY_MODEL_DEPLOYMENT` to your actual deployment name. Dynamic Sessions needs `DYNAMIC_SESSIONS_RESOURCE_GROUP` and the actual pool's `DYNAMIC_SESSIONS_POOL_ENDPOINT`; Sandbox needs `SANDBOX_RESOURCE_GROUP` and `SANDBOX_GROUP_NAME`.

Before live execution sign in to Azure CLI with the correct tenant and subscription, and check the Foundry inference role (for example `Cognitive Services OpenAI User`), `Azure ContainerApps Session Executor` at pool scope, and `Container Apps SandboxGroup Data Owner` at sandbox group scope, respectively. Provisioning and role assignment need separate administrative permissions; propagation can take time. Actual Azure calls require **both** `ENABLE_LIVE_AZURE=true` in `.env` and `--live` on each example. Check cost, quota, and regional availability first. No automatic retries duplicate execution.

## Part 1: Dynamic Sessions coding agent

[Detailed guide](docs/dynamic-sessions-en.md) · [Draw.io source](docs/assets/dynamic-sessions-session-reuse.drawio)

![Session identifier reuse and expiry](docs/assets/dynamic-sessions-session-reuse.svg)

`infra/dynamic-sessions/main.bicep` and `scripts/setup-dynamic-sessions.ps1` deploy a PythonLTS pool. Replace the values below for your environment. The script can create the specified resource group and rejects protected Foundry groups. Setting the PowerShell environment variable is separate from editing `.env`.

```powershell
$env:ENABLE_PROVISIONING = 'true'
.\scripts\setup-dynamic-sessions.ps1 -SubscriptionId 'YOUR_SUBSCRIPTION_ID' -ResourceGroupName 'YOUR_DEMO_RESOURCE_GROUP' -PoolName 'YOUR_POOL_NAME' -Location 'YOUR_AZURE_REGION'
```

Copy the pool's returned `poolManagementEndpoint` into your private `.env` and grant the runtime role. Shell needs a separate `-ContainerType Shell` pool and its endpoint. The following are **offline simulations** by default.

```powershell
.\.venv\Scripts\python.exe examples\dynamic_sessions\session_reuse.py
.\.venv\Scripts\python.exe examples\dynamic_sessions\coding_agent.py
.\.venv\Scripts\python.exe examples\dynamic_sessions\shell_agent.py
```

The first verifies ID A's memory variables and file, isolation of ID B, and simulated expiry. The second verifies create → failing test → repair → passing test from actual tool results. The third sets the working directory for each Shell execution. For live examples append `--live` and enable the opt-in above. Run expiry separately using `session_reuse.py --live --wait-for-ttl SECONDS`, choosing a time sufficiently greater than the pool cooldown. Waiting may incur costs and a short wait fails. `state_lost` is an application observation, not an official Azure error code.

## Part 2: Sandbox command execution and snapshots

[Detailed guide](docs/sandbox-en.md) · [Comparison experiment](docs/snapshot-comparison-en.md) · [Draw.io source](docs/assets/sandbox-memory-vs-disk-snapshot.drawio)

![Memory and Disk resume and explicit snapshot cloning](docs/assets/sandbox-memory-vs-disk-snapshot.svg)

`infra/sandbox/main.bicep` and `scripts/setup-sandbox.ps1` deploy a sandbox group in an **existing demo resource group**. The default is a dry run; actual deployment requires both `-Execute` and the PowerShell environment variable `ENABLE_PROVISIONING=true`.

```powershell
$env:ENABLE_PROVISIONING = 'true'
.\scripts\setup-sandbox.ps1 -SubscriptionId 'YOUR_SUBSCRIPTION_ID' -ResourceGroup 'YOUR_DEMO_RESOURCE_GROUP' -SandboxGroupName 'YOUR_SANDBOX_GROUP' -Location 'YOUR_AZURE_REGION'
# After reviewing the target and permissions, append -Execute to the same command
```

| Property | Memory | Disk |
| --- | --- | --- |
| Root disk | Preserved | Preserved |
| RAM and processes | Preserved after resume | Lost; VM and worker restart |
| Workload readiness | Verify the existing worker | Explicit post-resume bootstrap included |
| Data Disk volume | **Unsupported when attached** | Supported mode |

Resume latency has distinct Running, first exec, and worker-readiness stages; it is not LLM response time. Do not assume Memory is always faster or guarantees a particular latency. External connections and tokens require revalidation.

```powershell
.\.venv\Scripts\python.exe examples\sandbox\command_agent.py
.\.venv\Scripts\python.exe examples\sandbox\compare_snapshot_modes.py --iterations 3 --format markdown
.\.venv\Scripts\python.exe examples\sandbox\snapshot_clone.py
```

Runs are simulated by default. `command_agent.py --live` connects to Foundry for remote commands and stop/resume. The comparison uses `state_worker.py`'s RAM nonce/counter and disk SHA-256 oracle and reports stage timings. `--live` incurs actual costs; keep raw observations from `--output-dir` in a private directory. Clone creates a **different** sandbox from a named snapshot and verifies independence from subsequent source changes. It is not resuming the same sandbox.

## Unified CLI

From the source checkout, these CLI commands run the same examples and forward their arguments (`--live`, the comparison's `--iterations`/`--format`, and so on). All commands below default to offline.

```powershell
.\.venv\Scripts\aca-agent-sample.exe dynamic-session reuse
.\.venv\Scripts\aca-agent-sample.exe dynamic-session coding-agent
.\.venv\Scripts\aca-agent-sample.exe dynamic-session shell
.\.venv\Scripts\aca-agent-sample.exe sandbox command-agent
.\.venv\Scripts\aca-agent-sample.exe sandbox compare-modes --iterations 3 --format markdown
.\.venv\Scripts\aca-agent-sample.exe sandbox snapshot-clone
```

## Tests, cleanup and public review

```powershell
.\.venv\Scripts\python.exe -m pytest
.\scripts\cleanup.ps1 -ManifestPath '.internal\runs\YOUR_RUN_MANIFEST.json'
# After checking the dry run, append -Execute to the same command
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-public-readiness.ps1 -RequireDocs
```

Examples attempt to clean up sandboxes and snapshots they create; check the private recovery manifest on failure. Cleanup deletes only run-owned resources listed in the manifest, not groups or Foundry. Setup-created pools and groups are not automatically written to the manifest: separately identify and delete only your own resources in Azure. Check remaining snapshots, volumes, and storage costs. Git ignore is not a security boundary; independently inspect staged files and all history. [Security](SECURITY-en.md) · [Troubleshooting](docs/troubleshooting-en.md)

## Official documentation

- [Dynamic Sessions overview](https://learn.microsoft.com/en-us/azure/container-apps/sessions) · [Routing and authentication](https://learn.microsoft.com/en-us/azure/container-apps/sessions-usage) · [Code interpreter](https://learn.microsoft.com/en-us/azure/container-apps/sessions-code-interpreter) · [Shell](https://learn.microsoft.com/en-us/azure/container-apps/sessions-tutorial-shell)
- [Sandbox overview](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-overview) · [State and snapshots](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-snapshots-state-management) · [Python SDK](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-quickstart-python-sdk)
- [Foundry Entra authentication](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/how-to/managed-identity) · [Azure icon terms](https://learn.microsoft.com/en-us/azure/architecture/icons/)
- [Sandbox developer documentation](https://sandboxes.azure.com/docs/sandboxes/snapshots) (no Korean-specific path). Some Korean Learn paths can also display English content.
