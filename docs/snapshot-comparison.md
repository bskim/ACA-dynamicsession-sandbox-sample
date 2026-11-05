[English](snapshot-comparison-en.md)

# Memory / Disk 상태 비교

![Memory와 Disk 재개 및 명시적 스냅샷 복제](assets/sandbox-memory-vs-disk-snapshot.svg)

[Draw.io 원본](assets/sandbox-memory-vs-disk-snapshot.drawio) · [README](../README.md) · [Sandbox 가이드](sandbox.md)

`examples\sandbox\fixtures\state_worker.py`는 Python 표준 라이브러리만 사용하는
루프백 HTTP 워커다. `start`, `probe`, `increment`, `stop`을 지원한다.
기본 Linux 경로는 `/workspace/demo`, 포트는 `127.0.0.1:18765`다.
공개 포트나 외부 연결은 필요하지 않다.

## 오프라인 실행

```powershell
.\.venv\Scripts\python.exe examples\sandbox\compare_snapshot_modes.py --iterations 3 --format markdown
.\.venv\Scripts\python.exe examples\sandbox\compare_snapshot_modes.py --format json
.\.venv\Scripts\python.exe examples\sandbox\compare_snapshot_modes.py --format csv
.\.venv\Scripts\python.exe -m pytest tests\unit
```

CLI는 기본적으로 오프라인이며 `--live`와 `ENABLE_LIVE_AZURE=true`로 실제 Azure를 사용한다. fake의 지연 시간은 호스트 시뮬레이터 실행 시간이며,
Azure 성능 수치가 아니다. JSON/CSV의 `simulated` 필드로 구분한다.
각 반복은 같은 설정의 새 샌드박스를 사용하며, 모드 순서는 교차한다.

## 증거와 합격 조건

- 디스크 증거는 `sentinel.txt`의 고정 내용과 SHA-256이다. 최초 쓰기는
  `flush()`와 `os.fsync()`를 완료한다. 재시작은 기존 파일을 덮어쓰지 않는다.
- RAM nonce, 64 KiB 난수 버퍼/체크섬, 프로세스 시작 지문과 카운터는 데몬의
  메모리에만 존재한다. PID 파일, 상태 파일, 데몬 로그로 저장하지 않는다.
  관찰 결과는 호스트에 반환하며 복원 입력으로 보내지 않는다.
- 모든 정상 루프백 요청은 카운터를 증가시킨다. `start`의 최초 응답은 1이다.
- **Memory**: 재시작 없이 디스크 해시, nonce, 버퍼 체크섬, 프로세스 시작 지문이
  같고 카운터가 증가해야 한다. PID 일치만으로는 합격하지 않는다.
- **Disk**: bootstrap 전 디스크 해시가 같고 기존 데몬이 응답하지 않아야 한다.
  명시적 `start` 후 새 nonce/시작 지문과 카운터 1을 확인한다.
  bootstrap이 누락된 sentinel을 새로 만들어도 사전 관찰 실패를 숨기지 않는다.
- 재개 후 별도 파일의 쓰기/읽기도 검증한다. named snapshot/clone API는
  이 실험에서 사용하지 않는다.

## 측정 경계와 실패

| 지표 | 측정 구간 |
| --- | --- |
| `stop_to_stopped_ms` | stop 직전 → Stopped/Suspended 확인 |
| `resume_to_running_ms` | resume 직전 → Running 확인 |
| `first_exec_probe_ms` | Running 확인 직후 → 첫 exec 응답 |
| `worker_ready_ms` | 첫 exec 이후 → RAM/disk probe 및 Disk bootstrap 응답 |

프로비저닝·fixture 전송은 측정에서 제외한다. 중지 유지 시간은 동일하다.
통계는 성공 표본의 평균/p50(중앙값)/p95(nearest-rank)/min/max를 계산한다.
전체·성공·실패 수와 단계별 제외 수를 항상 표시하고, 실패 원시 표본과 부분
측정값도 JSON/CSV에 보존한다. 실행 명령은 자동 재전송하지 않는다.
실패 시에도 이번 실행 소유 샌드박스만 정리하며 삭제 실패는 실패 표본으로
남기고 어댑터의 소유 ID를 보존한다.

## 실제 Azure 연결

README의 Sandbox 설정과 RBAC를 준비하고, 이미지에서 `python3`의 존재를 먼저 확인한다. 라이브 실행은 비용을 발생시킨다. raw 관찰에는 nonce와 파일 해시가 포함되므로 비공개로 보관한다.

```powershell
.\.venv\Scripts\python.exe examples\sandbox\compare_snapshot_modes.py --live --iterations 3 --format json --output-dir .internal\results\comparison
```

기본 반복은 각 모드 1회이며, `--iterations`마다 Memory/Disk 순서를 교차한다. `--format`은 text, json, markdown, csv를 지원하고 `--output-dir`을 지정하면 JSON/CSV/Markdown 세 파일을 저장한다. 이 CLI에는 warm-up, 모드 선택 또는 hold-time 인자가 없다. 실패 표본도 보고하며 삭제 실패 시 private recovery manifest를 확인한다.

## 실제 Azure 연결 전 점검

`SandboxLifecycleComparator(adapter).run(...)`에 명시적으로 활성화된
`AcaSandboxAdapter`를 주입할 수 있다. 기존 설정/인증 계약을 따르고 private
manifest를 지정한다. 동일 이미지/CPU/메모리/리전 설정을 유지해야 한다.
기본 `ubuntu` 이미지에 `python3`가 있다고 가정하지 않는다. 이미지 준비와
Python 설치는 측정 전에 별도 검증한다. 현재 모듈은 패키지 설치를 하지 않는다.

모드 정책과 auto-delete 비활성화를 재조회하며, Memory 비교에는 볼륨을
연결하지 않는다. backing type을 증명할 수 없는 볼륨도 보수적으로 거부한다.
SDK polling 간격·네트워크를 포함한 호스트 관측값이므로 플랫폼 고유 지연이나
sub-second SLO로 해석하지 않는다. 실험 결과와 적용 범위는 아래에 제시한다.
원시 관찰값은 공개 문서가 아닌 `.internal\results\`에 보관한다.


## 실제 Azure 독립 표본 전체 (2026-11-05)

[배포·실행·정리 How-To](how-to-deploy-and-run.md)를 준비한 후 `compare_snapshot_modes.py --live --iterations 3`으로 실행했습니다. `simulated=false`, 모드별 각 3개 신규 sandbox, 총 6개이며 전부 oracle 통과 및 cleanup 성공입니다. 모드 순서는 회차마다 교차했습니다. 프로비저닝과 fixture 설치는 4개 단계에서 제외했고 Data Disk는 연결하지 않았습니다.

| 모드 | 회차 | stop_to_stopped_ms | resume_to_running_ms | first_exec_probe_ms | worker_ready_ms | 디스크 SHA | RAM / 재시작 |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Memory | 1 | 7803.891 | 1212.690 | 235.225 | 294.778 | True | nonce 동일, counter 5→6, 재시작 없음 |
| Disk | 1 | 6629.338 | 1263.399 | 223.615 | 999.466 | True | worker 없음, 새 nonce, counter 1 |
| Disk | 2 | 6518.513 | 1361.731 | 207.791 | 673.276 | True | worker 없음, 새 nonce, counter 1 |
| Memory | 2 | 9626.962 | 857.958 | 209.762 | 273.803 | True | nonce 동일, counter 5→6, 재시작 없음 |
| Memory | 3 | 7366.401 | 1572.143 | 215.680 | 280.312 | True | nonce 동일, counter 5→6, 재시작 없음 |
| Disk | 3 | 6516.680 | 1246.546 | 213.401 | 685.342 | True | worker 없음, 새 nonce, counter 1 |

단계별 성공 표본 집계 (ms; p95는 nearest-rank):

| 모드 | 표본 | 성공 | 실패 | 단계 | 평균 ms | p50 ms | p95 ms | 제외 |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| Memory | 3 | 3 | 0 | stop_to_stopped_ms | 8265.751 | 7803.891 | 9626.962 | 0 |
| Memory | 3 | 3 | 0 | resume_to_running_ms | 1214.264 | 1212.690 | 1572.143 | 0 |
| Memory | 3 | 3 | 0 | first_exec_probe_ms | 220.222 | 215.680 | 235.225 | 0 |
| Memory | 3 | 3 | 0 | worker_ready_ms | 282.964 | 280.312 | 294.778 | 0 |
| Disk | 3 | 3 | 0 | stop_to_stopped_ms | 6554.844 | 6518.513 | 6629.338 | 0 |
| Disk | 3 | 3 | 0 | resume_to_running_ms | 1290.558 | 1263.399 | 1361.731 | 0 |
| Disk | 3 | 3 | 0 | first_exec_probe_ms | 214.936 | 213.401 | 223.615 | 0 |
| Disk | 3 | 3 | 0 | worker_ready_ms | 786.028 | 685.342 | 999.466 | 0 |

표본이 모드별 단 3개이므로 p95는 사실상 해당 세 값의 최댓값입니다. 결과는 이 리전/네트워크/이미지/SDK/부하와 작은 워커의 클라이언트 관측값이며 SDK polling 간격을 포함합니다. SLA나 일반적인 모드 간 우열·비용으로 확대하지 마세요. 양 모드에 실패·제외 표본은 없었습니다.

## 오라클과 지연의 아키텍처 해석

- **디스크**: `/workspace/demo/sentinel.txt` SHA-256 `9259d694616fd8a2a2cb5cbdd63b47c9577d61eff2f1f5bdb958426031d96574`가 모든 전후 표본에서 동일했습니다. 이 파일의 바이트 보존을 뜻하며 모든 디스크·외부 시스템의 트랜잭션 보장은 아닙니다.
- **Memory**: 재개 후 bootstrap 없이 기존 루프백 소켓이 응답했습니다. `worker_boot_nonce`, RAM 버퍼 체크섬과 프로세스 시작 지문이 동일했고 counter는 5→6으로 증가했습니다. RAM·실행 문맥 연속성의 다중 증거이며 `worker_ready_ms` 평균은 282.964ms입니다.
- **Disk**: 재개 직후 bootstrap 전 probe는 sentinel이 있지만 `worker_alive=false`였고 nonce·counter·checksum·fingerprint는 null이었습니다. 워커 재시작 후 새 nonce/지문 및 counter=1이 관찰됐고 `worker_ready_ms` 평균은 786.028ms입니다. 우연히 PID가 재사용되어도 RAM 연속성 증거가 아닙니다.
- `stop_to_stopped_ms`: stop 요청 시작부터 SDK polling과 최종 GET으로 Stopped/Suspended 확인까지; 네트워크·poll 비용이 포함됩니다. 이 표본의 Memory 값이 길어도 일반 우열이 아닙니다.
- `resume_to_running_ms`: resume 요청 시작부터 SDK 및 추가 GET의 Running 확인까지. Running 자체가 워크로드 준비를 의미하지는 않습니다.
- `first_exec_probe_ms`: Running 확인 직후 첫 `echo ACA_EXEC_READY` 성공까지; 실행 API·네트워크 경로의 준비를 관측합니다.
- `worker_ready_ms`: 첫 exec probe **이후** worker probe 완료까지의 추가 구간입니다. Memory는 기존 워커 응답을, Disk는 워커 부재 관찰 후 명시적 재시작까지 포함합니다. 전체 resume 시간이 아닙니다.