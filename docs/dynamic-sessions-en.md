[한국어](./dynamic-sessions.md)

# Dynamic Sessions: coding agent and session ID reuse

![Multi-turn reuse, isolation and expiry of the identifier](assets/dynamic-sessions-session-reuse.svg)

[Draw.io source](assets/dynamic-sessions-session-reuse.drawio) · [README and setup](../README-en.md) · [Deployment, execution and cleanup How-To](how-to-deploy-and-run-en.md)

`PythonLTS` is a code interpreter; the host orchestrator handles model calls, tool policy, and result delivery. The session pool routes the same `identifier` (session ID) to the same active container and routes different `identifier` values to separate isolated containers. Once a session expires after its idle cooldown (`cooldownPeriodInSeconds`), calling the same `identifier` allocates a new empty session, which the sample reports as `state_lost`.

## Running the scenarios (unified runner and individual scripts)

Use `run-demo.ps1` at the repository root to run Scenarios 1–3 with step-by-step output (automatically running against live Azure when `ENABLE_LIVE_AZURE=true` in `.env`, or offline simulation otherwise), or invoke the Python scripts directly.

```powershell
# Run via the unified runner script (Scenarios 1 to 3)
.\run-demo.ps1 1
.\run-demo.ps1 2
.\run-demo.ps1 3

# Run the Python example scripts directly (offline by default; append --live for Azure)
.\.venv\Scripts\python.exe examples\dynamic_sessions\session_reuse.py
.\.venv\Scripts\python.exe examples\dynamic_sessions\coding_agent.py
.\.venv\Scripts\python.exe examples\dynamic_sessions\shell_agent.py
```

Live runs require both `ENABLE_LIVE_AZURE=true` and `--live` after configuring `.env` and RBAC. In addition to running all four steps in a single command (`--step all`, default), `session_reuse.py` supports `--step init`, `--step reuse`, and `--step check` with an explicit `--session-id` so you can observe state continuity and isolation across separate CLI invocations.

```powershell
# Step-by-step CLI calls comparing reuse of the same session ID (my-session-a) vs isolation of another ID (other-session-b)
.\.venv\Scripts\python.exe examples\dynamic_sessions\session_reuse.py --live --session-id my-session-a --step init
.\.venv\Scripts\python.exe examples\dynamic_sessions\session_reuse.py --live --session-id my-session-a --step reuse
.\.venv\Scripts\python.exe examples\dynamic_sessions\session_reuse.py --live --session-id my-session-a --step reuse
.\.venv\Scripts\python.exe examples\dynamic_sessions\session_reuse.py --live --session-id other-session-b --step check
```

[Routing and authentication](https://learn.microsoft.com/en-us/azure/container-apps/sessions-usage) · [Code interpreter API](https://learn.microsoft.com/en-us/azure/container-apps/sessions-code-interpreter) · [Shell](https://learn.microsoft.com/en-us/azure/container-apps/sessions-tutorial-shell)

---

## Scenario 1: Session ID reuse (`100 → 101 → 102`) vs. isolation across different IDs (`session_reuse.py`)

### Execution output (`.\run-demo.ps1 1` / `session_reuse.py --live`)

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

When run as a single command (`session_reuse.py --live`), the final summary JSON is:

```json
{"mode":"live","session":"[REDACTED]","other_session":"[REDACTED]","reuse":"passed","counter_progression":[100,101,102],"counter":102,"nonce_preserved":true,"original_sha256":"ba1a531f581d2e6094e978ed6f7aca7a8d92eeb62c6e7ad73ee692f7f18bc772","file_modified":true,"other_session_isolated":{"variable_exists":false,"turn_counter":null,"file_exists":false},"negative_control":"passed","expiry":"skipped: use --live --wait-for-ttl after choosing the pool cooldown"}
```

### Interpretation
- **Repeated calls with the same `session_id` (`100 → 101 → 102`)**: Step `[1-1]` sets `turn_counter = 100` and a random `nonce` in `Session A`’s Python interpreter memory and writes `/mnt/data/agent-demo/source.py`. Subsequent calls `[1-2]` and `[1-3]` with the same `session_id` route to the same active container, advancing the in-memory `turn_counter` to `101` and `102` and preserving the file without reloading from external storage.
- **Complete isolation with a different `session_id` (`variable_exists: false`, `file_exists: false`)**: Step `[1-4]` calls `Session B` using a different identifier, routing to a separate Hyper-V isolated session where `Session A`’s memory variables (`turn_counter: null`) and files (`file_exists: false`, HTTP 404 on download) are absent.
- **Calling again after TTL expiry (`--wait-for-ttl 330`)**: Waiting 330 seconds on a 300-second cooldown pool destroys the idle container; calling the same `session_id` afterwards allocates a fresh empty session and reports `expiry: "state_lost"`.

---

## Scenario 2: Multi-turn coding agent with Foundry LLM (`coding_agent.py`)

### How it works and where to customize
- The Foundry deployed model (`gpt-5.4-mini`) operates within a single fixed `session_id` across three turns: **1) generate buggy code → 2) run the test and observe the actual `AssertionError` → 3) patch the code and pass verification**.
- The tool schemas exposed to the model (`execute_python`, `upload_text_file`, `read_text_file`, `list_session_files` in `src/aca_agent_sample/dynamic_sessions/coding_agent.py`) omit `session_id` and `endpoint`; the host binds the conversation’s `session_id` automatically.
- To test a different coding task, edit the `prompts` list and host verification functions (`verify_turn_one` to `verify_turn_three`) in `examples/dynamic_sessions/coding_agent.py`.

### Execution output (`.\run-demo.ps1 2` / `coding_agent.py --live`)

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
```

```json
{"mode":"live","session":"[REDACTED]","creation":"passed","initial_test":"failed_as_expected","repair_test":"passed","model_turns":[3,2,2]}
```

### Interpretation
- **Turn 1 (buggy code creation and host check)**: The model sets `turn_counter = 1` and `session_nonce = 'demo-nonce'` in interpreter globals and writes `return a - b` to `/mnt/data/agent-demo/add.py`. The host verifies the AST and runs a probe confirming that the file and globals exist.
- **Turn 2 (observing failure in the same session)**: In the same session, the model increments `turn_counter` to `2` and executes `assert add(2, 3) == 5`, receiving an uncaught `AssertionError` from the remote container. The host parses the traceback summary to confirm the failure is specifically `AssertionError`.
- **Turn 3 (patching code and host re-verification)**: The model updates `/mnt/data/agent-demo/add.py` to `return a + b` and passes the test. Finally, the host reloads `add.py` from disk and independently verifies `add(2, 3) == 5` and `add(10, -4) == 6`.

---

## Scenario 3: Shell session pool execution (`shell_agent.py`)

### Execution and output (`.\run-demo.ps1 3` / `shell_agent.py --live`)

The `Shell` example targets a session pool provisioned with `ContainerType=Shell`. Running `.\run-demo.ps1 3` automatically resolves the Shell pool endpoint in the resource group, sets it for the run, and restores the environment variable afterwards.

```text
[shell-agent] Run 1/2 (Session [REDACTED]): $ cd /mnt/data && mkdir -p shell-demo && echo 'hello-from-shell-session' > shell-demo/note.txt && pwd
[shell-agent] Run 1/2 stdout:
/mnt/data
[shell-agent] Run 2/2 (Session [REDACTED]): $ cd /mnt/data && cat shell-demo/note.txt && pwd
[shell-agent] Run 2/2 stdout:
hello-from-shell-session
/mnt/data
```

```json
{"mode":"live","session":"[REDACTED]","runs":2,"cwd_explicit_each_time":true}
```

### Interpretation
- **File persistence and per-call shell process behavior**: `/mnt/data/shell-demo/note.txt` created in Run 1 is read back in Run 2 under the same `session_id`. Because each `execute_shell` call starts a fresh shell subprocess, every command explicitly specifies `cd /mnt/data && ...` rather than relying on working directory changes from earlier calls.
- **API contract notes**: The Shell preview service accepts `{shellCommand, timeoutInSeconds}` and reports process exit code `0` as the string `status="0"`. For nested file paths, pass the file basename in the URL route and the directory in the `path=` query parameter. ([Troubleshooting](troubleshooting-en.md))