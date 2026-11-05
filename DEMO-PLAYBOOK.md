[English](./DEMO-PLAYBOOK-en.md)

# 단계별 시나리오 실행 및 결과 해석 플레이북 (Step-by-Step Walkthrough)

이 문서는 저장소를 클론한 개발자가 **ACA Dynamic Sessions(코드·셸 실행)**와 **ACA Sandbox(에이전트 외부 도구 실행 및 Memory vs. Disk 스냅샷)**의 6가지 핵심 시나리오를 순서대로 실행하고, 각 출력 JSON과 표의 의미를 빠르게 확인·해석할 수 있도록 정리한 실행 가이드입니다.

[README](./README.md) · [아키텍처 상세](docs/architecture.md) · [배포·실행·정리 How-To](docs/how-to-deploy-and-run.md)

---

## 0. 전체 시나리오 요약 및 실행 소요 시간 (총 6개 시나리오)

루트의 `.\run-demo.ps1` 스크립트는 `.env`의 `ENABLE_LIVE_AZURE=true` 설정 여부에 따라 자동으로 실제 Azure 라이브 실행 또는 오프라인 시뮬레이션 모드로 동작합니다(`-Offline` 스위치를 추가하면 설정과 무관하게 오프라인 시뮬레이션으로 즉시 실행됩니다).

| 순서 | 실행 명령어 (`.\run-demo.ps1`) | 시나리오 핵심 검증 내용 | 라이브 실행 시간 (실측) |
| :---: | :--- | :--- | :---: |
| **Step 0** | **`.\run-demo.ps1 0`** | 로컬 환경(`preflight.ps1`) 및 `.env`에 지정된 구독/리소스 그룹 내 리소스 배포 상태 확인 | **약 2~3초** |
| **시나리오 1** | **`.\run-demo.ps1 1`** | **Dynamic Sessions**: `Session A` 3회 연속 호출(`100 → 101 → 102`, 파일 유지) vs. 다른 ID `Session B` 호출(`변수/파일 없음` 완전 격리) 비교 | **약 5~8초** |
| **시나리오 2** | **`.\run-demo.ps1 2`** | **Dynamic Sessions 코딩 에이전트**: `gpt-5.4-mini`가 세션 내 `/mnt/data/agent-demo/add.py`에 버그 코드(`a - b`) 작성 → `AssertionError` 실패 확인 → `a + b`로 수정 및 호스트 검증 통과 | **약 30~40초** |
| **시나리오 3** | **`.\run-demo.ps1 3`** | **Dynamic Sessions Shell 풀**: 리소스 그룹의 Shell 세션 풀 엔드포인트를 자동 연결해 셸 명령 2회 실행 및 원복 | **약 3~5초** |
| **시나리오 4** | **`.\run-demo.ps1 4`** | **ACA Sandbox 에이전트 툴체인**: 샌드박스 생성 + 외부 CLI(`git`/`jq`/`sqlite3`/`agent-tool`) `apt` 설치 + RAM 도구 서버 기동 → Turn 1 → `stop`/`resume` → 재설치 없이 Turn 2 패치·검증 | **약 45~65초** *(신규 VM 생성 + `apt` 설치 포함)* |
| **시나리오 5** | **`.\run-demo.ps1 5`** | **ACA Sandbox Memory vs. Disk 비교**: 샌드박스 2개 생성 및 도구 설치 후, 설치된 CLI 도구는 양쪽 모두 보존(`True`), RAM 도구 서버는 `Memory` 모드만 재기동 없이 유지됨을 표로 비교 | **약 75~100초** *(샌드박스 2개 생성 + `apt` 설치 포함)* |
| **시나리오 6** | **`.\run-demo.ps1 6`** | **ACA Sandbox 명시적 Snapshot & 복제**: 원본에 패키지 설치 + RAM 서버 워밍업 후 스냅샷 생성 → 새 샌드박스 복제(추가 설치·프로세스 재시작·캐시 재구축 없이 이어감) → 복제본 자체 Memory `stop`/`resume` 및 독립성 검증 | **약 45~60초** *(원본 생성 + `apt` 설치 + 스냅샷/복제 + stop/resume 포함)* |

### 아키텍처 다이어그램 및 구현 범위 참고 사항
1. **다이어그램 파일 (`docs/assets/`)**:
   - `docs/assets/overall-architecture.svg` (전체 아키텍처: 신뢰 호스트 vs. Azure 격리 실행 경계)
   - `docs/assets/dynamic-sessions-session-reuse.svg` (시나리오 1~2: `identifier` 라우팅 및 격리/TTL)
   - `docs/assets/sandbox-memory-vs-disk-snapshot.svg` (시나리오 4~6: `Memory` vs `Disk` vs `Snapshot Clone`)
2. **Dynamic Sessions (`PythonLTS`/`Shell` 내장 풀 vs. `Custom Container`)**:
   - 시나리오 1~3은 별도 컨테이너 레지스트리(ACR)나 이미지 빌드 없이 바로 실행할 수 있도록 플랫폼 내장 `PythonLTS` 및 `Shell` 풀로 세션 라우팅(`identifier` 기반 상태 재사용, 타 세션 격리, 쿨다운 만료)과 실행/파일 API를 검증합니다. 자체 이미지와 HTTP 실행 서버를 사용하는 `Custom Container` 풀로 확장할 때도 호스트 측 세션 ID 라우팅·소유권·정책 구조는 동일하게 사용할 수 있습니다.
3. **ACA Sandbox (에이전트 외부 도구 설치 및 상태 제어)**:
   - 시나리오 4~6은 외부 CLI 도구 설치, 로컬 디스크 파일 및 RAM 프로세스 상태 유지, `Memory` vs. `Disk` 중지/재개 비교, 명시적 스냅샷 복제와 원본-복제본 상태 독립성을 검증합니다.

---

## Step 0. 초기 환경 세팅 및 사전 점검

### A. 저장소 클론 및 가상환경(`.venv`) 구성

```powershell
# 1. 저장소 클론 및 Python 가상환경(.venv) 생성
git clone https://github.com/bskim/ACA-dynamicsession-sandbox-sample.git
Set-Location ACA-dynamicsession-sandbox-sample
py -3.13 -m venv .venv

# 2. 의존성 패키지 및 개발 모드 설치
.\.venv\Scripts\python.exe -m pip install -e ".[test]"

# 3. Azure CLI 로그인 및 워크스페이스 로컬 containerapp 익스텐션 준비
az login
$env:AZURE_EXTENSION_DIR = Join-Path (Get-Location) '.venv\az-extensions'
$env:AZURE_EXTENSION_USE_DYNAMIC_INSTALL = 'no'
az extension add --name containerapp --allow-preview true --yes

# 4. .env 파일 생성 및 사전 점검 (Azure 리소스 배포는 docs/how-to-deploy-and-run.md 참조)
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\scripts\preflight.ps1
```

### B. 환경 및 리소스 상태 확인

```powershell
.\run-demo.ps1 0
```

---

## 시나리오 1. Dynamic Sessions — Session ID(`identifier`) 재사용 vs. 다른 Session ID 호출 비교 (`session_reuse.py`)

### 1) 시나리오 개요
- 동일한 `session_id`로 여러 번 호출했을 때와 다른 `session_id`로 호출했을 때 결과가 어떻게 달라지는지 4단계로 비교합니다:
  1. `[1-1] Session A (--step init)`: `Session A`의 Python 메모리에 `turn_counter = 100`, 난수 `nonce`를 만들고 `/mnt/data/agent-demo/source.py` 파일을 생성합니다.
  2. `[1-2] Session A (--step reuse, 동일 ID 1차 재호출)`: 같은 `Session A` ID로 호출하면 메모리에 있던 `turn_counter`가 `101`로 증가하고 파일 내용이 그대로 읽힙니다.
  3. `[1-3] Session A (--step reuse, 동일 ID 2차 재호출)`: 다시 같은 `Session A` ID로 호출하면 `turn_counter`가 `102`로 계속 누적 증가합니다.
  4. `[1-4] Session B (--step check, 다른 ID 호출)`: 다른 식별자인 `Session B`로 호출하면 `variable_exists: false, turn_counter: null, file_exists: false`가 반환되어 `Session A`의 메모리/파일과 완전히 격리되어 있음을 확인합니다.

### 2) 실행 명령어

```powershell
# 4단계를 순서대로 실행 및 비교 출력
.\run-demo.ps1 1

# 또는 개별 CLI 호출로 직접 --session-id를 지정해 단계별 실행
.\.venv\Scripts\python.exe examples\dynamic_sessions\session_reuse.py --live --session-id my-session-a --step init
.\.venv\Scripts\python.exe examples\dynamic_sessions\session_reuse.py --live --session-id my-session-a --step reuse
.\.venv\Scripts\python.exe examples\dynamic_sessions\session_reuse.py --live --session-id my-session-a --step reuse
.\.venv\Scripts\python.exe examples\dynamic_sessions\session_reuse.py --live --session-id other-session-b --step check
```

### 3) 출력 결과 및 해석
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
- `turn_counter: 100 → 101 → 102` 및 `nonce` 일치: 같은 `identifier`로 요청하면 파일이나 DB에서 상태를 다시 읽지 않아도 동일 컨테이너의 Python 인터프리터 메모리가 그대로 유지됩니다.
- `Session B`의 `variable_exists: false`, `file_exists: false`: 다른 `identifier`는 별도의 Hyper-V 격리 컨테이너로 라우팅되므로 이전 세션의 메모리와 파일에 접근할 수 없습니다.

---

## 시나리오 2. Dynamic Sessions — Foundry LLM 다중 턴 코딩 에이전트 (`coding_agent.py`)

### 1) 시나리오 개요 및 코드 구조
- 원격 Dynamic Session 컨테이너 내부 파일(`/mnt/data/agent-demo/add.py`):
  - **Turn 1**: 모델이 의도적으로 버그가 있는 `def add(a, b): return a - b` 코드를 생성합니다.
  - **Turn 2**: 같은 세션에서 `assert add(2, 3) == 5`를 실행해 실제 `AssertionError` 트레이스백을 관찰합니다.
  - **Turn 3**: 모델이 `/mnt/data/agent-demo/add.py`를 `def add(a, b): return a + b`로 수정한 뒤 테스트를 통과(`TEST PASSED`)시키고, 호스트가 파일 AST와 `add()` 실행 결과를 독립 검증합니다.
- 로컬 호스트 스크립트(`examples/dynamic_sessions/coding_agent.py`):
  - `prompts` 리스트에서 에이전트에게 부여할 코딩 과제를 수정할 수 있으며, `src/aca_agent_sample/dynamic_sessions/coding_agent.py`의 도구 스키마에는 `session_id` 인자가 없어 모델이 대상 세션을 임의로 변경할 수 없습니다.

### 2) 실행 명령어
```powershell
.\run-demo.ps1 2
```

### 3) 출력 결과 및 해석
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

## 시나리오 3. Dynamic Sessions — Shell 세션 풀 명령 실행 (`shell_agent.py`)

### 1) 시나리오 개요
- `PythonLTS` 외에 일반 셸 명령어를 실행하는 `Shell` 세션 풀의 동작을 검증합니다.
- **Run 1**: `cd /mnt/data && mkdir -p shell-demo && echo 'hello-from-shell-session' > shell-demo/note.txt && pwd` 실행
- **Run 2**: 같은 세션에서 `cd /mnt/data && cat shell-demo/note.txt && pwd`를 실행해 Run 1에서 생성한 파일 내용을 읽습니다.
- `.\run-demo.ps1 3`은 리소스 그룹 내 Shell 세션 풀의 `poolManagementEndpoint`를 자동 조회해 연결하고 실행 후 원래 환경 변수로 복원합니다.

### 2) 실행 명령어
```powershell
.\run-demo.ps1 3
```

### 3) 출력 결과 및 해석
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
- 파일시스템(`/mnt/data/shell-demo/note.txt`)은 동일한 `session_id` 안에서 유지되지만, 각 호출은 새로운 셸 프로세스를 시작하므로 매번 `cd /mnt/data`를 명시합니다.

---

## 시나리오 4. ACA Sandbox — 외부 에이전트의 Sandbox 내 CLI 툴체인 + RAM 도구 서버 활용 및 Stop/Resume (`command_agent.py`)

### 1) 시나리오 개요
- **두뇌(호스트 오케스트레이터 + Foundry LLM)**와 **손발(샌드박스 내부 범용 CLI `git`/`jq`/`sqlite3`/`agent-tool` + 루프백 RAM 인덱스 서버)**을 분리하여, 샌드박스 안에 자격 증명을 주입하지 않고 에이전트 변경 수명 주기와 샌드박스 이미지/스냅샷 수명 주기를 분리합니다. ([3단계 상세 설명](docs/sandbox.md#외부-에이전트의-도구-노출과-호출-3단계))

### 2) 실행 명령어
```powershell
.\run-demo.ps1 4
```

### 3) 출력 결과 및 해석
- `"provision_count": 1` & `"installed_tool_preserved": true`: 샌드박스 생성 직후 1회만 `git`, `jq`, `sqlite3`, `agent-tool`을 설치했고, `stop` → `resume` 이후 재설치 없이 그대로 도구를 재사용했습니다.
- `"tool_server_oracle": { "mode": "Memory", "disk_preserved": true, "memory_preserved": true, "worker_restarted": false, "passed": true }`: `Memory` 모드로 중지·재개했기 때문에 백그라운드 루프백 RAM 인덱스 서버가 재기동 없이 살아 있으며 `in_memory_counter`도 `3 → 4`로 연속 증가했습니다.
- `"verification": { "config": {"audit": true, "retries": 3, "service": "demo"}, "events": "initialized,patched", "passed": true }`: Turn 2에서 모델이 설치된 CLI 도구들을 파이프라인으로 조합해 설정 패치와 SQLite 감사 기록 검증까지 완료했습니다.

---

## 시나리오 5. ACA Sandbox — `Memory Snapshot` vs. `Disk Snapshot` 비교 (`compare_snapshot_modes.py`)

### 1) 시나리오 개요
- ACA Sandbox의 수명 주기 보존 모드인 `Memory` 모드와 `Disk` 모드의 상태 보존 차이와 단계별 지연 시간을 비교합니다.

### 2) 실행 명령어
```powershell
.\run-demo.ps1 5
```

### 3) 출력 결과 및 해석

```markdown
| Mode | Iteration | Disk SHA-256 preserved | worker_boot_nonce preserved | in_memory_counter continuous | buffer_checksum preserved | Memory preserved | Worker restarted | Oracle passed | Installed tool preserved |
| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- |
| Memory | 1 | True | True | True | True | True | False | True | True |
| Disk | 1 | True | False | False | False | False | True | True | True |
```

1. **`Memory` 모드의 핵심 강점 — 기동 중인 프로세스와 메모리 상태의 생존 (`Memory preserved = True`, `Worker restarted = False`)**:
   - 플랫폼 재개 시간(`resume_to_running_ms`)은 두 모드 모두 약 1.2~1.4초 수준이며, `Memory` 모드는 RAM 페이지 전체를 저장·복원하므로 `stop_to_stopped_ms`가 더 길거나 네트워크 편차에 따라 단일 프로브 시간이 비슷하게 관측될 수 있습니다.
   - `Memory` 모드를 선택하는 핵심 이유는 단순 재개 속도가 아니라, 중지 전에 띄워둔 백그라운드 프로세스(인메모리 인덱스 서버, 언어 서버, 로컬 캐시, REPL 세션)가 죽지 않고 그대로 살아 있어서(`Worker restarted = False`) 난수 `nonce`·메모리 버퍼·요청 카운터(`in_memory_counter continuous = True`)를 재구축 없이 그대로 이어서 사용할 수 있다는 점입니다.
2. **`Disk` 모드에서도 설치한 외부 CLI 도구(`Installed tool preserved = True`)와 파일(`Disk SHA-256 preserved = True`)은 그대로 보존**:
   - `Disk` 모드로 중지하더라도 루트 파일시스템에 설치해 둔 `git`, `jq`, `sqlite3`, `agent-tool`과 작업 파일은 그대로 보존되므로 패키지 재설치는 필요 없으며, VM이 새로 부팅되면서 RAM 프로세스만 재기동(`Worker restarted = True`)됩니다.
3. **모드 선택 기준**:
   - 메모리에 살아있는 프로세스/세션/인덱스 상태의 연속성이 필요할 때 → **`Memory` 모드**
   - 설치된 CLI 도구와 디스크 파일만 유지되면 되고, 빠른 정지(`stop`)나 추가 Data Disk 볼륨 연결이 필요할 때 → **`Disk` 모드** (Data Disk 연결 시 `Memory` 모드 미지원)

---

## 시나리오 6. ACA Sandbox — '패키지 설치 + 필수 프로세스 기동' 상태의 명시적 Snapshot 생성 및 복제 (`snapshot_clone.py`)

### 1) 시나리오 개요 (컨테이너 이미지 vs. ACA Sandbox 스냅샷)
- 일반 컨테이너 이미지(Dockerfile)는 디스크(파일시스템)만 저장하므로 컨테이너를 새로 띄울 때마다 백그라운드 프로세스(언어 서버, MCP 도구 서버, 로컬 인덱스/캐시 데몬 등)를 처음부터 기동하고 메모리에 인덱스를 워밍업해야 합니다.
- 시나리오 6은 ACA Sandbox에서 **① 외부 패키지(`git`, `jq`, `sqlite3`, `agent-tool`) 설치 + ② 필수 백그라운드 프로세스(`state_worker.py` RAM 인덱스 서버) 기동 및 워밍업**까지 마친 상태에서 명시적 스냅샷(`create_snapshot`)을 찍고 이를 복제(`clone`)하는 패턴을 검증합니다:
  1. **추가 패키지 재설치 생략 (`clone_provision_count: 0`)**: 복제된 샌드박스에서 `apt-get` 설치를 다시 수행하지 않고 이미 설치된 CLI 도구를 바로 실행합니다.
  2. **프로세스 재시작 및 캐시 재구축 생략 (`clone_ram_preserved_from_snapshot: true`, `clone_worker_restarted: false`)**: 스냅샷으로부터 새 샌드박스를 복제·복원하고 첫 요청을 연결하는 데는 수 초의 플랫폼 복원 시간이 소요되지만, 복원된 직후에는 백그라운드 프로세스를 새로 시작하거나 인메모리 인덱스 캐시를 다시 구축할 필요 없이 이미 살아 있는 RAM 상태(`counter: 2 → 3`)를 그대로 이어갑니다.
  3. **복제본 자체의 Memory Stop/Resume 가능 (`clone_memory_stop_resume_passed: true`)**: 복제된 샌드박스도 독립된 마이크로 VM이므로, 이후 자체적으로 `Memory` 모드 `stop` → `resume`(`counter: 3 → 4`)을 수행하며 자기만의 메모리 상태를 이어갈 수 있습니다.
  4. **완전한 상태 분기/격리 (`isolation_after_clone: true`)**: 복제 이후 복제본의 파일·DB 수정과 RAM 카운터 증가(`4`)는 원본 샌드박스(`source_after_independent_probe: 3`)에 영향을 주지 않습니다.

### 2) 실행 명령어
```powershell
.\run-demo.ps1 6
```

### 3) 출력 결과 및 해석
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
- `counter_progression` (`2 → 3 → 4`, 원본은 `3`): 백그라운드 도구 서버(`state_worker.py`)는 상태 확인(`probe`)이나 캐시 조회 요청을 받을 때마다 RAM의 `in_memory_counter`를 `1`씩 증가시킵니다. 원본에서 카운터가 `2`일 때 스냅샷을 찍었는데, 복제본 생성 직후 서버를 새로 시작(`start`)하지 않고 `probe`만 호출했는데도 `1`로 리셋되지 않고 `3`이 반환되었으며, 복제본을 `Memory` 모드로 `stop` → `resume`한 뒤 다시 호출하면 `4`로 이어집니다.

---

## Step 마지막. 실습 종료 후 리소스 정리

실습을 모두 마친 뒤 라이브 리소스를 정리할 때는 아래 스크립트를 실행합니다. 모든 조회·삭제 단계마다 `$LASTEXITCODE`를 확인하여 실패 시 즉시 중단하며(실패·미완료 시 복구를 위해 `.env`의 리소스 설정을 유지함), 리소스 그룹 삭제 완료(`exists == false`)까지 확인된 경우에만 마지막에 `.env` 설정을 초기화합니다:

```powershell
$ErrorActionPreference = 'Stop'

# 1. .env에서 구독 ID와 테스트 리소스 그룹을 읽어 대상 확인 및 보호 그룹 가드 검증
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

# 삭제 전 데이터 평면(샌드박스/스냅샷) 및 ARM 리소스 목록을 확인합니다.
if ($sandboxGroup -and $sandboxGroup -notmatch '[<>]') {
    foreach ($collection in @('sandboxes', 'snapshots')) {
        $url = "https://management.$location.azuredevcompute.io/subscriptions/$sub/resourceGroups/$rg/sandboxGroups/$sandboxGroup/$collection"
        az rest --method get --url $url --url-parameters api-version=2026-02-01-preview --resource https://dynamicsessions.io/ --only-show-errors -o json
        if ($LASTEXITCODE -ne 0) { throw "Unable to list $collection; stop and inspect before deleting RG." }
    }
}
az resource list --subscription $sub -g $rg --query '[].{Name:name, Type:type, Location:location}' -o table
if ($LASTEXITCODE -ne 0) { throw "Resource lookup failed for $rg; aborting without modifying .env." }

# 2. 테스트 리소스 그룹 삭제 요청 및 실제 삭제 완료 확인 (--subscription 명시)
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

# 3. 삭제 완료가 확인된 후에만 로컬 .env의 ENABLE_LIVE_AZURE 및 임시 리소스 항목을 기본값으로 복원
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
