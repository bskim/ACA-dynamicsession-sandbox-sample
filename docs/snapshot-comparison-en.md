[한국어](snapshot-comparison.md)

# Memory / Disk state comparison

![Memory and Disk resume and explicit snapshot cloning](assets/sandbox-memory-vs-disk-snapshot.svg)

[Draw.io source](assets/sandbox-memory-vs-disk-snapshot.drawio) · [README](../README-en.md) · [Sandbox guide](sandbox-en.md)

`examples\sandbox\fixtures\state_worker.py` is a standard-library-only Python
loopback HTTP worker supporting `start`, `probe`, `increment`, and `stop`.
The Linux defaults are `/workspace/demo` and `127.0.0.1:18765`.
No public port or external connection is required.

## Offline execution

```powershell
.\.venv\Scripts\python.exe examples\sandbox\compare_snapshot_modes.py --iterations 3 --format markdown
.\.venv\Scripts\python.exe examples\sandbox\compare_snapshot_modes.py --format json
.\.venv\Scripts\python.exe examples\sandbox\compare_snapshot_modes.py --format csv
.\.venv\Scripts\python.exe -m pytest tests\unit
```

The CLI defaults to offline; `--live` with `ENABLE_LIVE_AZURE=true` uses Azure. Fake latency is host simulator execution time; JSON/CSV identify it with `simulated`. Each repetition uses a
fresh sandbox with identical settings, alternating mode order.

## Evidence and acceptance

- **Installed tools**: actually run `/usr/local/bin/agent-tool --versions` before and after resume. git/jq/sqlite3 binaries and versions, custom CLI and configuration fingerprints must match for `installed_tool_preserved=true`. Neither mode reinstalls tools. A sentinel alone, missing tools or failed tool execution cannot pass.
- **Tool server**: index the sample Git/JSON/SQLite project once in RAM. Probe calls the loopback server API; index and nonce/counter state are never reconstructed from a saved RAM-state file.

- Disk evidence is fixed `sentinel.txt` content and its SHA-256. Initial writes
  complete `flush()` and `os.fsync()`. Restart never overwrites an existing file.
- RAM nonce, a random 64 KiB buffer/checksum, process-start fingerprint, and counter
  exist only in daemon memory. No PID file, state file, or daemon log persists
  them. Observations return to the host and are never supplied as restore inputs.
- Every successful loopback request increments the counter. The first `start`
  response has counter 1.
- **Memory**: without restarting, disk hash, nonce, buffer checksum, and process
  fingerprint must match, and the counter must increase. PID equality alone is
  never sufficient.
- **Disk**: before bootstrap the disk hash must match and the old daemon must not
  respond. Explicit `start` must return a fresh nonce/fingerprint and counter 1.
  Recreating a missing sentinel during bootstrap cannot hide failed pre-bootstrap
  disk evidence.
- A separate post-resume file write/read is verified. Named snapshot/clone APIs
  are not used in this experiment.

## Timing boundaries and failures

| Metric | Interval |
| --- | --- |
| `stop_to_stopped_ms` | Before stop → confirmed Stopped/Suspended |
| `resume_to_running_ms` | Before resume → confirmed Running |
| `first_exec_probe_ms` | Immediately after Running → first exec response |
| `worker_ready_ms` | After first exec → RAM/disk probe and Disk bootstrap response |

Provisioning/fixture transfer are excluded. Both modes use equal stopped hold
times. Statistics use successful samples: mean/p50 (median)/p95 (nearest-rank)/
min/max. Total/success/failure counts and per-stage exclusions are always shown;
JSON/CSV retain failed raw samples and partial timings. Execution commands are
never automatically resent. Cleanup targets only this run's owned sandboxes.
Delete failures fail the sample and retain adapter ownership IDs for recovery.

## Live Azure execution

Prepare the README Sandbox configuration and RBAC, and first verify that the image has `python3`. Live execution incurs costs. Raw observations include nonce and file hashes; keep them private.

```powershell
.\.venv\Scripts\python.exe examples\sandbox\compare_snapshot_modes.py --live --iterations 3 --format json --output-dir .internal\results\comparison
```

The default is one iteration per mode, and `--iterations` alternates the Memory/Disk ordering. `--format` supports text, json, markdown, and csv; `--output-dir` saves JSON/CSV/Markdown files. This CLI has no warm-up, mode-selection, or hold-time arguments. Failed samples are reported; check the private recovery manifest if cleanup fails.

## Before connecting Azure

An explicitly enabled `AcaSandboxAdapter` can be injected into
`SandboxLifecycleComparator(adapter).run(...)`. Follow the existing configuration/
authentication contracts and provide a private recovery manifest. Keep image/CPU/memory/region identical, and verify `python3` availability in the `ubuntu` image outside measurement. The module installs git/jq/sqlite3, custom CLIs and configuration before measurement; Ubuntu package repository access is required.

The comparator reads back mode policy and disabled auto-delete. Do not attach
volumes for Memory comparison; attachments with unverified backing types are also
conservatively rejected. Host observations include SDK polling and network time. The observed live results and their scope are provided below. Store raw observations
under private `.internal\results\`.


## Live toolchain and index-server results (2026-11-05)

All six real Azure samples passed (`simulated=false`). git/jq/sqlite3, custom CLI and configuration were executable with an unchanged fingerprint after resume in both modes. Each initial index had three project files. Memory kept nonce, buffer checksum and process identity with counter 5→6; Disk had no server before bootstrap and restarted it with a new identity and counter 1. No tools were reinstalled after resume. Installation is excluded from lifecycle timing.

| Mode | Iteration | Installed tools | Disk | Memory | Restarted | Stop ms | Resume ms | First exec ms | Server ready ms |
| --- | ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| Memory | 1 | true | true | true | false | 9106.398 | 1348.855 | 297.033 | 399.850 |
| Disk | 1 | true | true | false | true | 6922.497 | 1452.435 | 213.394 | 936.704 |
| Disk | 2 | true | true | false | true | 6802.469 | 1325.619 | 214.688 | 891.404 |
| Memory | 2 | true | true | true | false | 8900.459 | 1087.710 | 252.034 | 412.084 |
| Memory | 3 | true | true | true | false | 9503.373 | 1284.911 | 224.748 | 451.825 |
| Disk | 3 | true | true | false | true | 6790.159 | 1437.641 | 205.635 | 987.945 |

The three observations per mode are client-side measurements that include SDK polling.


Stage aggregates (ms; p95 is nearest-rank):

| Mode | Samples | Success | Failure | Stage | Mean ms | p50 ms | p95 ms | Excluded |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| Memory | 3 | 3 | 0 | stop_to_stopped_ms | 9170.077 | 9106.398 | 9503.373 | 0 |
| Memory | 3 | 3 | 0 | resume_to_running_ms | 1240.492 | 1284.911 | 1348.855 | 0 |
| Memory | 3 | 3 | 0 | first_exec_probe_ms | 257.938 | 252.034 | 297.033 | 0 |
| Memory | 3 | 3 | 0 | worker_ready_ms | 421.253 | 412.084 | 451.825 | 0 |
| Disk | 3 | 3 | 0 | stop_to_stopped_ms | 6838.375 | 6802.469 | 6922.497 | 0 |
| Disk | 3 | 3 | 0 | resume_to_running_ms | 1405.231 | 1437.641 | 1452.435 | 0 |
| Disk | 3 | 3 | 0 | first_exec_probe_ms | 211.239 | 213.394 | 214.688 | 0 |
| Disk | 3 | 3 | 0 | worker_ready_ms | 938.684 | 936.704 | 987.945 | 0 |

## Architecture interpretation

- **Installed tools and disk**: `installed_tool_preserved=true` and `disk_preserved=true` in both modes because `git`, `jq`, `sqlite3`, `agent-tool`, and their configuration reside on the preserved root filesystem. Run the installed tools after resume without reinstalling them to verify their preservation.
- **Memory**: `memory_preserved=true`, `worker_restarted=false`. The existing loopback server and RAM index, nonce, buffer checksum, process fingerprint and advancing counter demonstrate continuity; mean `worker_ready_ms` is 421.253 ms.
- **Disk**: `memory_preserved=false`, `worker_restarted=true`. The VM/process RAM and cached index do not persist; after `resume`, the host confirms that the process has exited (`worker_alive=False`) and explicitly restarts the worker to rebuild the index from retained project files. Mean `worker_ready_ms` is 938.684 ms. Assess continuity using the process fingerprint and RAM state alongside the PID.
- `worker_ready_ms` measures the additional interval after first exec; Disk includes detecting and restarting the server. Both modes reach `resume_to_running_ms` in roughly 1.2–1.4 seconds, and because Memory mode saves and restores RAM pages, `stop_to_stopped_ms` can take longer and single-call probe latency can vary with network jitter. The primary reason to choose Memory mode is therefore process and in-memory state continuity (`memory_preserved=true`, `worker_restarted=false`) rather than raw resume speed. With three samples per mode, p95 is the maximum of those values. Provisioning was excluded, no Data Disk was attached, and all six new sandboxes passed and were cleaned up.
