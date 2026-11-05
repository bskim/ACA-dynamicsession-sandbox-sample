[한국어](./DEMO-PLAYBOOK.md)

# Step-by-Step Scenario Execution and Interpretation Playbook

This playbook walks developers through running the six core scenarios for **ACA Dynamic Sessions (code and shell execution)** and **ACA Sandbox (external agent tool execution and Memory vs. Disk snapshots)** in sequence, and explains how to interpret each JSON and table output.

[README](./README-en.md) · [Detailed architecture](docs/architecture-en.md) · [Deployment, execution and cleanup How-To](docs/how-to-deploy-and-run-en.md)

---

## 0. Scenario overview and wall-time reference (6 scenarios)

The root `.\run-demo.ps1` script automatically runs against live Azure when `ENABLE_LIVE_AZURE=true` in `.env`, or in offline simulation mode otherwise (pass `-Offline` to force offline simulation regardless of `.env`).

| Step | Command (`.\run-demo.ps1`) | What it verifies | Observed live wall time |
| :---: | :--- | :--- | :---: |
| **Step 0** | **`.\run-demo.ps1 0`** | Checks local tooling (`preflight.ps1`) and lists deployed resources in the configured subscription and resource group | **~2–3 s** |
| **Scenario 1** | **`.\run-demo.ps1 1`** | **Dynamic Sessions**: 3 consecutive calls on `Session A` (`100 → 101 → 102`, file retained) vs. calling a different ID `Session B` (`false`/`null`, isolated) | **~5–8 s** |
| **Scenario 2** | **`.\run-demo.ps1 2`** | **Dynamic Sessions coding agent**: `gpt-5.4-mini` writes buggy `/mnt/data/agent-demo/add.py` (`a - b`) → observes `AssertionError` → repairs to `a + b` and passes host verification | **~30–40 s** |
| **Scenario 3** | **`.\run-demo.ps1 3`** | **Dynamic Sessions Shell pool**: Automatically resolves the Shell session pool endpoint in the resource group, runs 2 shell commands, and restores the environment variable | **~3–5 s** |
| **Scenario 4** | **`.\run-demo.ps1 4`** | **ACA Sandbox agent toolchain**: Creates a sandbox + installs external CLIs (`git`/`jq`/`sqlite3`/`agent-tool`) via `apt` + starts RAM tool server → Turn 1 → `stop`/`resume` → Turn 2 patch and verification without reinstallation | **~45–65 s** *(includes new VM + `apt` install)* |
| **Scenario 5** | **`.\run-demo.ps1 5`** | **ACA Sandbox Memory vs. Disk comparison**: Creates 2 sandboxes and installs tools; shows that both modes preserve installed CLIs (`True`), while only `Memory` mode keeps the running RAM server alive without restart | **~75–100 s** *(includes 2 VMs + `apt` install)* |
| **Scenario 6** | **`.\run-demo.ps1 6`** | **ACA Sandbox explicit snapshot & clone**: Installs packages + warms up RAM server on source, captures a snapshot, clones a new sandbox (continuing without package reinstall, process restart, or cache rebuild), and verifies the clone's own Memory `stop`/`resume` and isolation | **~45–60 s** *(includes source + `apt` + snapshot/clone + stop/resume)* |

### Architecture diagrams and scope notes
1. **Diagram files (`docs/assets/`)**:
   - `docs/assets/overall-architecture.svg` (overall architecture: trusted host vs. Azure isolated execution boundary)
   - `docs/assets/dynamic-sessions-session-reuse.svg` (Scenarios 1–2: `identifier` routing, isolation, and TTL expiry)
   - `docs/assets/sandbox-memory-vs-disk-snapshot.svg` (Scenarios 4–6: `Memory` vs. `Disk` vs. `Snapshot Clone`)
2. **Dynamic Sessions (`PythonLTS`/`Shell` built-in pools vs. `Custom Container`)**:
   - Scenarios 1–3 use the platform's built-in `PythonLTS` and `Shell` session pools so you can verify session routing (`identifier`-based state reuse, cross-session isolation, and cooldown expiry) and execution/file APIs immediately without building a container image or provisioning ACR. The same host-side session ID routing, ownership registry, and tool policy patterns apply when extending to a `Custom Container` pool with your own HTTP execution server.
3. **ACA Sandbox (installing external agent tools and controlling state)**:
   - Scenarios 4–6 verify external CLI tool installation, local disk file and RAM process persistence, `Memory` vs. `Disk` stop/resume semantics, and explicit snapshot cloning with state isolation between source and clone.

---

## Step 0. Initial setup and preflight check

### A. Clone the repository and set up `.venv`

```powershell
# 1. Clone the repository and create the Python virtual environment (.venv)
git clone https://github.com/bskim/ACA-dynamicsession-sandbox-sample.git
Set-Location ACA-dynamicsession-sandbox-sample
py -3.13 -m venv .venv

# 2. Install dependencies and the package in editable mode
.\.venv\Scripts\python.exe -m pip install -e ".[test]"

# 3. Sign in to Azure CLI and install the workspace-local containerapp extension
az login
$env:AZURE_EXTENSION_DIR = Join-Path (Get-Location) '.venv\az-extensions'
$env:AZURE_EXTENSION_USE_DYNAMIC_INSTALL = 'no'
az extension add --name containerapp --allow-preview true --yes

# 4. Create .env and run preflight check (see docs/how-to-deploy-and-run-en.md for Azure Bicep provisioning)
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\scripts\preflight.ps1
```

### B. Check environment and resource status

```powershell
.\run-demo.ps1 0
```

---

## Scenario 1. Dynamic Sessions — Session ID (`identifier`) reuse vs. calling a different Session ID (`session_reuse.py`)

### 1) Overview
- Compares calling the pool repeatedly with the same `session_id` against calling with a different `session_id` in four steps:
  1. `[1-1] Session A (--step init)`: Sets `turn_counter = 100` and a random `nonce` in `Session A`’s Python memory and writes `/mnt/data/agent-demo/source.py`.
  2. `[1-2] Session A (--step reuse, 1st reuse call with same ID)`: Calling with the same `Session A` ID increments the in-memory `turn_counter` to `101` and reads back the file content.
  3. `[1-3] Session A (--step reuse, 2nd reuse call with same ID)`: Calling again with the same `Session A` ID advances `turn_counter` to `102`.
  4. `[1-4] Session B (--step check, call with different ID)`: Calling with a different identifier `Session B` returns `variable_exists: false, turn_counter: null, file_exists: false`, confirming complete isolation from `Session A`.

### 2) Commands

```powershell
# Run all 4 steps in sequence
.\run-demo.ps1 1

# Or invoke each step explicitly with --session-id across separate CLI calls
.\.venv\Scripts\python.exe examples\dynamic_sessions\session_reuse.py --live --session-id my-session-a --step init
.\.venv\Scripts\python.exe examples\dynamic_sessions\session_reuse.py --live --session-id my-session-a --step reuse
.\.venv\Scripts\python.exe examples\dynamic_sessions\session_reuse.py --live --session-id my-session-a --step reuse
.\.venv\Scripts\python.exe examples\dynamic_sessions\session_reuse.py --live --session-id other-session-b --step check
```

### 3) Output and interpretation
```json
{
  "step": "init",
  "session_id": "session-a-453",
  "turn_counter": 100,
  "nonce": "277ec8756f82316aee18838004684924",
  "file_created": "/mnt/data/agent-demo/source.py"
}
{
  "step": "reuse",
  "session_id": "session-a-453",
  "variable_preserved": true,
  "turn_counter": 101,
  "nonce": "277ec8756f82316aee18838004684924",
  "file_content": "def add(a, b):\n    return a + b\n"
}
{
  "step": "reuse",
  "session_id": "session-a-453",
  "variable_preserved": true,
  "turn_counter": 102,
  "nonce": "277ec8756f82316aee18838004684924",
  "file_content": "def add(a, b):\n    return a + b\n"
}
{
  "step": "check",
  "session_id": "session-b-453",
  "variable_exists": false,
  "turn_counter": null,
  "file_exists": false
}
```
- `turn_counter: 100 → 101 → 102` and matching `nonce`: Requests carrying the same `identifier` route to the same active container, preserving Python interpreter memory without reloading from files or an external database.
- `Session B` (`variable_exists: false`, `file_exists: false`): A different `identifier` routes to a separate Hyper-V isolated container that has no access to the previous session's memory or files.

---

## Scenario 2. Dynamic Sessions — Multi-turn coding agent with Foundry LLM (`coding_agent.py`)

### 1) Overview and code structure
- Remote file inside the Dynamic Session container (`/mnt/data/agent-demo/add.py`):
  - **Turn 1**: The model creates an intentionally buggy `def add(a, b): return a - b`.
  - **Turn 2**: In the same session, it runs `assert add(2, 3) == 5` and observes the uncaught `AssertionError` traceback.
  - **Turn 3**: The model updates `/mnt/data/agent-demo/add.py` to `def add(a, b): return a + b`, passes the test (`TEST PASSED`), and the host independently verifies the file AST and `add()` execution.
- Local host script (`examples/dynamic_sessions/coding_agent.py`):
  - You can customize the coding task in the `prompts` list. The tool schemas in `src/aca_agent_sample/dynamic_sessions/coding_agent.py` omit `session_id` so the model cannot redirect calls to another session.

### 2) Command
```powershell
.\run-demo.ps1 2
```

### 3) Output and interpretation
```text
[coding-agent] === Turn 1/3 (Session [REDACTED]) ===
[coding-agent] Tool Result (execute_python): status=succeeded, stdout='set globals', stderr=''
[coding-agent] Tool Result (upload_text_file): status=succeeded, stdout='File uploaded', stderr=''
[coding-agent] Current /mnt/data/agent-demo/add.py in session:
def add(a, b):
    return a - b

[coding-agent] === Turn 2/3 (Session [REDACTED]) ===
[coding-agent] Tool Result (execute_python): status=failed, stdout='', stderr="... ----> 5 assert add(2,3)==5\nAssertionError:"
[coding-agent] Current /mnt/data/agent-demo/add.py in session:
def add(a, b):
    return a - b

[coding-agent] === Turn 3/3 (Session [REDACTED]) ===
[coding-agent] Tool Result (execute_python): status=succeeded, stdout='TEST PASSED', stderr=''
[coding-agent] Current /mnt/data/agent-demo/add.py in session:
def add(a, b):
    return a + b
{"mode": "live", "session": "[REDACTED]", "creation": "passed", "initial_test": "failed_as_expected", "repair_test": "passed", "model_turns": [3, 2, 2]}
```

---

## Scenario 3. Dynamic Sessions — Shell session pool execution (`shell_agent.py`)

### 1) Overview
- Verifies running shell commands in a `Shell` session pool (`ContainerType=Shell`).
- **Run 1**: Executes `cd /mnt/data && mkdir -p shell-demo && echo 'hello-from-shell-session' > shell-demo/note.txt && pwd`
- **Run 2**: In the same session, executes `cd /mnt/data && cat shell-demo/note.txt && pwd` to read back the file created in Run 1.
- `.\run-demo.ps1 3` automatically resolves the Shell session pool's `poolManagementEndpoint` in the resource group, sets it for the run, and restores the environment variable afterwards.

### 2) Command
```powershell
.\run-demo.ps1 3
```

### 3) Output and interpretation
```text
[shell-agent] Run 1/2 (Session [REDACTED]): $ cd /mnt/data && mkdir -p shell-demo && echo 'hello-from-shell-session' > shell-demo/note.txt && pwd
[shell-agent] Run 1/2 stdout:
/mnt/data
[shell-agent] Run 2/2 (Session [REDACTED]): $ cd /mnt/data && cat shell-demo/note.txt && pwd
[shell-agent] Run 2/2 stdout:
hello-from-shell-session
/mnt/data
{"mode": "live", "session": "[REDACTED]", "runs": 2, "cwd_explicit_each_time": true}
```
- Files under `/mnt/data/shell-demo/note.txt` persist within the same `session_id`, while each `execute_shell` call starts a fresh shell process and therefore explicitly specifies `cd /mnt/data`.

---

## Scenario 4. ACA Sandbox — External agent invoking in-sandbox CLI toolchain + RAM tool server across Stop/Resume (`command_agent.py`)

### 1) Overview
- Separates the **brain (host orchestrator + Foundry LLM)** from the **hands (general-purpose CLIs `git`/`jq`/`sqlite3`/`agent-tool` + loopback RAM index server inside the sandbox)**, keeping credentials off the guest VM and decoupling agent update lifecycles from sandbox image/snapshot lifecycles. ([Detailed three-stage flow](docs/sandbox-en.md#exposing-and-invoking-tools-for-an-external-agent-three-stages))

### 2) Command
```powershell
.\run-demo.ps1 4
```

### 3) Output and interpretation
- `"provision_count": 1` & `"installed_tool_preserved": true`: `git`, `jq`, `sqlite3`, and `agent-tool` were installed once after sandbox creation and reused across `stop` → `resume` without reinstallation.
- `"tool_server_oracle": { "mode": "Memory", "disk_preserved": true, "memory_preserved": true, "worker_restarted": false, "passed": true }`: Stopping and resuming in `Memory` mode kept the background loopback RAM index server alive without restart, advancing `in_memory_counter` from `3` to `4`.
- `"verification": { "config": {"audit": true, "retries": 3, "service": "demo"}, "events": "initialized,patched", "passed": true }`: In Turn 2, the model combined the installed CLIs in shell pipelines to patch configuration and record the SQLite audit event.

---

## Scenario 5. ACA Sandbox — `Memory Snapshot` vs. `Disk Snapshot` comparison (`compare_snapshot_modes.py`)

### 1) Overview
- Compares state preservation and stage timings between ACA Sandbox's `Memory` and `Disk` lifecycle suspend modes under identical conditions.

### 2) Command
```powershell
.\run-demo.ps1 5
```

### 3) Output and interpretation

```markdown
| Mode | Iteration | Disk SHA-256 preserved | worker_boot_nonce preserved | in_memory_counter continuous | buffer_checksum preserved | Memory preserved | Worker restarted | Oracle passed | Installed tool preserved |
| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- |
| Memory | 1 | True | True | True | True | True | False | True | True |
| Disk | 1 | True | False | False | False | False | True | True | True |
```

1. **Key strength of `Memory` mode — survival of running processes and in-memory state (`Memory preserved = True`, `Worker restarted = False`)**:
   - Platform resume time (`resume_to_running_ms`) is roughly 1.2–1.4 seconds in both modes, and because `Memory` mode saves and restores RAM pages, `stop_to_stopped_ms` can take longer and single-call probe latency can vary with network jitter.
   - The primary reason to choose `Memory` mode is state continuity rather than raw resume speed: background processes started before stopping (in-memory index servers, language servers, local caches, REPL sessions) stay alive (`Worker restarted = False`), continuing with their `nonce`, memory buffer, and request counter (`in_memory_counter continuous = True`) intact.
2. **`Disk` mode still preserves installed external CLI tools (`Installed tool preserved = True`) and files (`Disk SHA-256 preserved = True`)**:
   - Stopping in `Disk` mode preserves the root filesystem containing `git`, `jq`, `sqlite3`, `agent-tool`, and project files without reinstallation; only the VM and RAM processes restart (`Worker restarted = True`).
3. **Selection summary**:
   - When continuity of live processes, sessions, or in-memory indexes matters → **`Memory` mode**
   - When preserving installed CLI tools and disk files is sufficient, or when attaching Data Disk volumes (which do not support `Memory` mode) → **`Disk` mode**

---

## Scenario 6. ACA Sandbox — Capturing and cloning an explicit snapshot with installed packages + running processes (`snapshot_clone.py`)

### 1) Overview (Container images vs. ACA Sandbox snapshots)
- Standard container images (Dockerfiles) store only the disk filesystem, requiring background processes (language servers, MCP tool servers, local index/cache daemons) to cold-start and warm up their memory indexes every time a new container starts.
- Scenario 6 captures an explicit snapshot (`create_snapshot`) after **1) installing external packages (`git`, `jq`, `sqlite3`, `agent-tool`) and 2) starting and warming up the background process (`state_worker.py` RAM index server)**, then clones a new sandbox (`clone`):
  1. **No package reinstallation (`clone_provision_count: 0`)**: The cloned sandbox immediately executes the installed CLI tools without rerunning `apt-get`.
  2. **No process restart or cache rebuild (`clone_ram_preserved_from_snapshot: true`, `clone_worker_restarted: false`)**: While restoring a new sandbox from the snapshot takes a few seconds at the platform level, once running, the clone continues directly from the already-running RAM process and warmed-up index cache (`counter: 2 → 3`) without restarting the server.
  3. **Independent Memory Stop/Resume on the clone (`clone_memory_stop_resume_passed: true`)**: Because the clone is a full independent micro VM, it can subsequently run its own `Memory` mode `stop` → `resume` cycle (`counter: 3 → 4`).
  4. **Complete state isolation (`isolation_after_clone: true`)**: File/DB patches and RAM counter increments (`4`) on the clone do not affect the source sandbox (`source_after_independent_probe: 3`).

### 2) Command
```powershell
.\run-demo.ps1 6
```

### 3) Output and interpretation
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
- `counter_progression` (`2 → 3 → 4`, source at `3`): The background tool server (`state_worker.py`) increments its in-memory `in_memory_counter` by `1` on each probe or cache query. Capturing the snapshot when the source counter is `2` and immediately probing the newly created clone without starting the server returns `3` (rather than resetting to `1`), and stopping/resuming the clone in `Memory` mode advances the next probe to `4`.

---

## Final Step. Cleaning up resources after testing

When you finish testing and want to remove the live Azure resources, run the script below. It checks `$LASTEXITCODE` after every lookup and delete command (retaining `.env` settings for recovery if anything fails or times out) and clears `.env` only after confirming that the resource group no longer exists (`exists == false`):

```powershell
$ErrorActionPreference = 'Stop'

# 1. Read subscription ID and test resource group from .env and enforce the protected-RG guard
$envMap = @{}
Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') { $envMap[$Matches[1]] = $Matches[2].Trim() }
}
$sub = $envMap['AZURE_SUBSCRIPTION_ID']
$rg = if ($envMap['DYNAMIC_SESSIONS_RESOURCE_GROUP']) { $envMap['DYNAMIC_SESSIONS_RESOURCE_GROUP'] } else { $envMap['SANDBOX_RESOURCE_GROUP'] }
$sandboxGroup = $envMap['SANDBOX_GROUP_NAME']
$location = if ($envMap['AZURE_LOCATION']) { $envMap['AZURE_LOCATION'] } else { 'eastus2' }
if (-not $sub -or $sub -match '[<>]' -or -not $rg -or $rg -match '[<>]' -or $rg -imatch '^rg-foundry(?:-|$)') {
    throw "Unsafe or protected resource group ($rg) / subscription ($sub). Aborting without modifying .env."
}

# Inspect data-plane (sandboxes/snapshots) and ARM resources before deleting the RG.
if ($sandboxGroup -and $sandboxGroup -notmatch '[<>]') {
    foreach ($collection in @('sandboxes', 'snapshots')) {
        $url = "https://management.$location.azuredevcompute.io/subscriptions/$sub/resourceGroups/$rg/sandboxGroups/$sandboxGroup/$collection"
        az rest --method get --url $url --url-parameters api-version=2026-02-01-preview --resource https://dynamicsessions.io/ --only-show-errors -o json
        if ($LASTEXITCODE -ne 0) { throw "Unable to list $collection; stop and inspect before deleting RG." }
    }
}
az resource list --subscription $sub -g $rg --query '[].{Name:name, Type:type, Location:location}' -o table
if ($LASTEXITCODE -ne 0) { throw "Resource lookup failed for $rg; aborting without modifying .env." }

# 2. Request test resource group deletion and wait until deletion completes (--subscription explicit)
az group delete --subscription $sub --name $rg --yes --no-wait --only-show-errors
if ($LASTEXITCODE -ne 0) { throw "RG delete request failed for $rg; .env settings retained for retry." }

$deadline = (Get-Date).AddMinutes(10)
do {
    $exists = az group exists --subscription $sub --name $rg -o tsv
    if ($LASTEXITCODE -ne 0) { throw "RG existence check failed; .env settings retained for inspection." }
    if ($exists -eq 'false') { break }
    if ((Get-Date) -ge $deadline) { throw "RG deletion did not complete within timeout; .env settings retained." }
    Start-Sleep -Seconds 5
} while ($true)

az resource list --subscription $sub --query "[?resourceGroup=='$rg'].{type:type,name:name}" -o json
if ($LASTEXITCODE -ne 0) { throw "Remaining resource verification failed; .env settings retained." }

# 3. Reset ENABLE_LIVE_AZURE and temporary resource fields in .env only after deletion is confirmed
$content = Get-Content .env -Raw
foreach ($key in @('ENABLE_LIVE_AZURE', 'ENABLE_PROVISIONING')) {
    $content = [regex]::Replace($content, "(?m)^$key=[^\r\n]*", "$key=false")
}
foreach ($key in @('DYNAMIC_SESSIONS_RESOURCE_GROUP', 'DYNAMIC_SESSIONS_POOL_ENDPOINT', 'SANDBOX_RESOURCE_GROUP', 'SANDBOX_GROUP_NAME')) {
    $content = [regex]::Replace($content, "(?m)^$key=[^\r\n]*", "$key=")
}
[IO.File]::WriteAllText((Resolve-Path .env), $content, [Text.UTF8Encoding]::new($false))
Remove-Item Env:ENABLE_LIVE_AZURE, Env:ENABLE_PROVISIONING, Env:DYNAMIC_SESSIONS_POOL_ENDPOINT -ErrorAction SilentlyContinue
Write-Host "Resource group '$rg' deletion confirmed and .env restored safely." -ForegroundColor Green
```
