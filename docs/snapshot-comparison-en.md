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

The CLI defaults to offline; `--live` with `ENABLE_LIVE_AZURE=true` uses Azure. Fake latency is host simulator execution time, not an
Azure benchmark; JSON/CSV identify it with `simulated`. Each repetition uses a
fresh sandbox with identical settings, alternating mode order.

## Evidence and acceptance

- **v2 installed tools**: actually run `/usr/local/bin/agent-tool --versions` before and after resume. git/jq/sqlite3 binaries and versions, custom CLI and configuration fingerprints must match for `installed_tool_preserved=true`. Neither mode reinstalls tools. A sentinel alone, missing tools or failed tool execution cannot pass.
- **v2 tool server**: index the sample Git/JSON/SQLite project once in RAM. Probe calls the loopback server API; index and nonce/counter state are never reconstructed from a saved RAM-state file.

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
authentication contracts and provide a private recovery manifest. Keep image/CPU/
memory/region identical. Do not assume the default `ubuntu` image has `python3`.
Verify Python availability outside measurement. The module installs git/jq/sqlite3, custom CLIs and configuration before measurement; Ubuntu package repository access is required.

The comparator reads back mode policy and disabled auto-delete. Do not attach
volumes for Memory comparison; attachments with unverified backing types are also
conservatively rejected. Host observations include SDK polling and network time;
they are not intrinsic platform latency or a sub-second SLO. The observed live results and their scope are provided below. Store raw observations
under private `.internal\results\`, not public documentation.


## Historical v1 independent Azure live samples (2026-11-05)

These worker-only measurements predate installed-tool validation and must not be mixed with v2 timings.

After preparing the [deployment, execution and cleanup How-To](how-to-deploy-and-run-en.md), `compare_snapshot_modes.py --live --iterations 3` was run. All are `simulated=false`: three new sandboxes per mode, six total, all oracle-passed and cleaned up. Mode order alternated by iteration. Provisioning and fixture installation were outside the four stages; no Data Disk was attached.

| Mode | Iteration | stop_to_stopped_ms | resume_to_running_ms | first_exec_probe_ms | worker_ready_ms | Disk SHA | RAM / restart |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Memory | 1 | 7803.891 | 1212.690 | 235.225 | 294.778 | True | nonce same, counter 5→6, no restart |
| Disk | 1 | 6629.338 | 1263.399 | 223.615 | 999.466 | True | worker absent, new nonce, counter 1 |
| Disk | 2 | 6518.513 | 1361.731 | 207.791 | 673.276 | True | worker absent, new nonce, counter 1 |
| Memory | 2 | 9626.962 | 857.958 | 209.762 | 273.803 | True | nonce same, counter 5→6, no restart |
| Memory | 3 | 7366.401 | 1572.143 | 215.680 | 280.312 | True | nonce same, counter 5→6, no restart |
| Disk | 3 | 6516.680 | 1246.546 | 213.401 | 685.342 | True | worker absent, new nonce, counter 1 |

Successful samples aggregated by stage (ms; p95 is nearest-rank):

| Mode | Samples | Success | Failure | Stage | Mean ms | p50 ms | p95 ms | Excluded |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| Memory | 3 | 3 | 0 | stop_to_stopped_ms | 8265.751 | 7803.891 | 9626.962 | 0 |
| Memory | 3 | 3 | 0 | resume_to_running_ms | 1214.264 | 1212.690 | 1572.143 | 0 |
| Memory | 3 | 3 | 0 | first_exec_probe_ms | 220.222 | 215.680 | 235.225 | 0 |
| Memory | 3 | 3 | 0 | worker_ready_ms | 282.964 | 280.312 | 294.778 | 0 |
| Disk | 3 | 3 | 0 | stop_to_stopped_ms | 6554.844 | 6518.513 | 6629.338 | 0 |
| Disk | 3 | 3 | 0 | resume_to_running_ms | 1290.558 | 1263.399 | 1361.731 | 0 |
| Disk | 3 | 3 | 0 | first_exec_probe_ms | 214.936 | 213.401 | 223.615 | 0 |
| Disk | 3 | 3 | 0 | worker_ready_ms | 786.028 | 685.342 | 999.466 | 0 |

With only three samples per mode, p95 is effectively the maximum of those three values. Results are client observations for this region/network/image/SDK/load and small worker, including SDK polling intervals. Do not extrapolate them to an SLA or general performance/cost superiority. Both modes had zero failed or excluded samples.

## Oracle and latency architecture interpretation

- **Disk**: `/workspace/demo/sentinel.txt` SHA-256 `9259d694616fd8a2a2cb5cbdd63b47c9577d61eff2f1f5bdb958426031d96574` matched before and after in all samples. This proves bytes for this file, not transactional preservation of every disk or external system.
- **Memory**: after resume the existing loopback socket responded without bootstrap. `worker_boot_nonce`, RAM buffer checksum and process-start fingerprint matched and the counter continued 5→6. These jointly evidence RAM/execution-context continuity; mean `worker_ready_ms` was 282.964 ms.
- **Disk**: immediately after resume, before bootstrap, the probe found the sentinel but `worker_alive=false`, with null nonce/counter/checksum/fingerprint. An explicit worker restart then yielded a new nonce/fingerprint and counter=1; mean `worker_ready_ms` was 786.028 ms. A coincidentally reused PID does not prove RAM continuity.
- `stop_to_stopped_ms`: from stop request through SDK polling and final GET confirming Stopped/Suspended; includes network and polling. Memory was longer in this sample, not a general ranking.
- `resume_to_running_ms`: from resume request through SDK and additional GET confirming Running. Running alone does not mean workload ready.
- `first_exec_probe_ms`: from just after Running until first successful `echo ACA_EXEC_READY`; observes execution API/network-path readiness.
- `worker_ready_ms`: the additional interval **after** the first exec probe until worker probe completion. Memory checks the existing worker; Disk includes observing the missing worker and explicitly restarting it. This is not total resume time.

## v2 live toolchain and index-server results (2026-11-05)

All six real Azure samples passed (`simulated=false`). git/jq/sqlite3, custom CLI and configuration were executable with an unchanged fingerprint after resume in both modes. Each initial index had three project files. Memory kept nonce, buffer checksum and process identity with counter 5→6; Disk had no server before bootstrap and restarted it with a new identity and counter 1. No tools were reinstalled after resume. Installation is excluded from lifecycle timing.

| Mode | Iteration | Installed tools | Disk | Memory | Restarted | Stop ms | Resume ms | First exec ms | Server ready ms |
| --- | ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| Memory | 1 | true | true | true | false | 9106.398 | 1348.855 | 297.033 | 399.850 |
| Disk | 1 | true | true | false | true | 6922.497 | 1452.435 | 213.394 | 936.704 |
| Disk | 2 | true | true | false | true | 6802.469 | 1325.619 | 214.688 | 891.404 |
| Memory | 2 | true | true | true | false | 8900.459 | 1087.710 | 252.034 | 412.084 |
| Memory | 3 | true | true | true | false | 9503.373 | 1284.911 | 224.748 | 451.825 |
| Disk | 3 | true | true | false | true | 6790.159 | 1437.641 | 205.635 | 987.945 |

Only three observations per mode; these client-side measurements include SDK polling and are not an SLA or a general performance ranking.
