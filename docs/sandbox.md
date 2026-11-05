[English](./sandbox-en.md)

# ACA Sandbox: 명령 실행과 수명 주기

![Sandbox Memory와 Disk 모드 및 named snapshot clone](assets/sandbox-memory-vs-disk-snapshot.svg)

[Draw.io 원본](assets/sandbox-memory-vs-disk-snapshot.drawio) · [README와 설정](../README.md) · [실험 세부 사항](snapshot-comparison.md)

SandboxGroup은 관리 경계이고 각 sandbox는 독립된 마이크로 VM입니다. `infra/sandbox/main.bicep`와 `scripts/setup-sandbox.ps1`로 group을 구성합니다. setup은 기본 dry run이며 실제 배포에 `-Execute`와 PowerShell `ENABLE_PROVISIONING=true`가 필요합니다. Python SDK 경로는 별도 `aca` CLI 없이 동작합니다. 배포/명령/스냅샷은 과금될 수 있습니다.

```powershell
.\.venv\Scripts\python.exe examples\sandbox\command_agent.py
.\.venv\Scripts\python.exe examples\sandbox\compare_snapshot_modes.py --iterations 3 --format markdown
.\.venv\Scripts\python.exe examples\sandbox\snapshot_clone.py
```

기본은 오프라인 시뮬레이션입니다. 라이브 호출은 `.env`와 RBAC를 준비하고 `ENABLE_LIVE_AZURE=true`, `--live`를 함께 지정합니다. command agent는 실제 에이전트 툴체인과 루프백 도구 서버를 사용하며 두 작업 턴 사이에 같은 sandbox를 stop/resume합니다. sandbox 내부에 자격 증명을 전달하지 않습니다. 모델이 제안한 임의 명령을 실행하는 개발 예제이므로 서비스 노출 시 추가 보안 통제가 필요합니다.

| 동작 | Memory | Disk |
| --- | --- | --- |
| stop/resume 후 | 루트 디스크와 RAM 프로세스·변수 유지 | 루트 디스크 유지, VM·프로세스 재시작 |
| Data Disk 볼륨 | 연결 시 미지원 | 지원되는 모드 |
| 측정 | Running, 첫 exec, 워커 준비 단계 분리 | 같은 구간; 워커 준비에 bootstrap 포함 |

Memory 모드에서도 외부 연결·토큰의 유효성은 재검증해야 합니다. 명시적 named snapshot은 별도 group/region 범위 리소스이며 새 sandbox clone에 사용됩니다. 예제는 원본을 변경해 clone의 파일이 독립적인지 검사합니다. 기존 sandbox의 resume와 혼동하지 마세요. 예제는 자신이 만든 sandbox·snapshot을 정리하려 하지만 실패 시 비공개 manifest로 복구해야 합니다. [공식 Sandbox 개요](https://learn.microsoft.com/ko-kr/azure/container-apps/sandboxes-overview) · [수명 주기와 상태](https://learn.microsoft.com/ko-kr/azure/container-apps/sandboxes-snapshots-state-management).

라이브 서비스에서 기본 `ubuntu` 이미지에는 `/workspace`가 없어 create 후 `/`에서 `mkdir -p /workspace`로 초기화했습니다. 동기 snapshot 생성 뒤 GET 응답에 `status`가 생략되기도 했습니다. 이 경우 요청한 snapshot ID와 일치하는 GET 존재로 완료를 확인하며, `Creating`이나 실패 status를 완료로 취급하지 않습니다. 일시적 404는 관찰 GET만 제한 시간 내 반복하고 snapshot 생성 POST는 자동 재전송하지 않습니다. 이러한 preview 계약은 버전별로 달라질 수 있습니다.

## 외부 에이전트의 도구 노출과 호출: 3단계

이 예제의 두뇌는 **신뢰된 호스트의 정책 오케스트레이터와 원격 Foundry 모델**이고, 손발은 sandbox 마이크로 VM에 설치된 CLI와 루프백 도구 서버입니다. 모델 자체는 호스트에서 실행되지 않습니다. 오케스트레이터를 게스트 안에 두면 추론 토큰이나 Azure 자격 증명을 게스트에 주입해야 할 수 있으며, 비신뢰 코드나 프롬프트 인젝션으로 토큰을 탈취당할 위험이 커집니다. 호스트가 자격 증명을 관리하고 게스트에는 주입하지 않습니다. 다음은 `run_toolchain_demo`의 흐름입니다. 일반 `run` 경로가 자동으로 도구를 설치하는 것은 아닙니다.

```text
Host: provision_agent_tools + fixture installation
  -> guest: git / jq / sqlite3 + agent-tool / repo-audit + state_worker.py
Host: prompt catalog + run_sandbox_command schema -> model tool call
  -> SandboxToolPolicy.validate -> _Executor (host-owned sandbox_id)
  -> AcaSandboxAdapter.exec -> sandbox.exec -> executeShellCommand
  -> guest CLI -> optional loopback state_worker.py /audit
  -> ToolExecutionResult -> role="tool" -> model
```

### 에이전트와 sandbox 변경 수명 주기 분리

에이전트 프롬프트, 오케스트레이션 로직, 모델 버전, 비즈니스 규칙까지 게스트 이미지나 스냅샷에 함께 굽는다면 작은 에이전트 변경에도 이미지·스냅샷 갱신과 재배포가 필요해지는 강한 결합이 생깁니다. 반면 이 샘플은 호스트 에이전트와 게스트의 범용 CLI·리포지토리 감사/인덱스 도구를 분리합니다.

1. **에이전트 업데이트 독립성:** 도구 인터페이스와 호환성이 유지되면 모델 교체(예: `gpt-5.4-mini` → `gpt-5.4`), 프롬프트나 호스트 로직 수정, 코드 리뷰·보안 감사·패치 역할 추가에 sandbox 이미지나 스냅샷을 다시 만들 필요가 없습니다. 모델 교체 시 tool calling 지원과 권한은 확인해야 합니다.
2. **sandbox 재사용성:** 무거운 CLI 패키지와 기본 서버 프로그램이 설치된 골든 스냅샷을 재사용해 서로 다른 에이전트 용도의 sandbox를 clone하거나 기존 인스턴스를 stop/resume할 수 있습니다. 재개는 같은 sandbox이고 clone은 별도 sandbox입니다. 서버의 RAM·프로세스 연속성은 Memory/Disk 모드와 clone의 차이를 따르며, 스냅샷에서 실행 중인 서버가 자동으로 복원된다고 가정하지 마세요.

실제 `snapshot_clone.py`는 도구가 설치된 원본의 named snapshot으로 새 clone을 만들어 `clone_provision_count=0`을 확인합니다. 이 예제는 여러 전문 에이전트를 연결하지 않으며, `run_toolchain_demo`도 새 sandbox에 직접 도구를 설치합니다. 다양한 에이전트가 골든 스냅샷을 사용하려면 별도 연결·라우팅·소유권·권한 제어가 필요합니다. 게스트 도구, 이미지 설정이나 도구 계약이 변경되면 호환성 확인과 스냅샷 갱신이 필요할 수 있습니다.

### 1단계 — 게스트 도구 계층 준비

sandbox 생성과 `/workspace` 초기화 후 호스트가 SDK의 `sandbox.exec(...)`를 통해 게스트 Ubuntu VM에 도구를 준비합니다.

- **시스템 CLI**: `provision_agent_tools`가 `apt-get update` 후 `apt-get install -y --no-install-recommends git jq sqlite3`를 실행합니다. 실행 파일은 각각 `/usr/bin/git`, `/usr/bin/jq`, `/usr/bin/sqlite3`입니다.
- **전용 CLI**: 같은 함수가 `agent_toolkit.py`를 `/usr/local/bin/agent-tool`로 설치하고 `/usr/local/bin/repo-audit`와 `/usr/local/bin/agent-toolkit` 심볼릭 링크를 만듭니다. `agent-tool --versions`, `repo-audit init`, `repo-audit analyze`, `repo-audit verify`, `agent-tool cached-audit`를 사용할 수 있습니다.
- **루프백 인메모리 서버**: 호스트가 별도 fixture 설치 명령으로 `/workspace/demo/state_worker.py`를 복사합니다. 첫 작업 턴에서 프로젝트를 초기화한 후 모델이 `python3 /workspace/demo/state_worker.py start`로 서버를 시작합니다. `agent-tool cached-audit`는 외부 포트를 열지 않고 게스트 내부에서 `POST http://127.0.0.1:18765/audit`로 RAM 인덱스 캐시를 조회합니다. 설치 자체가 서버를 시작하지는 않습니다.

### 2단계 — 모델에 게이트웨이와 도구 목록 제공

`SandboxToolPolicy.schemas`는 CLI별 함수가 아니라 단일 게이트웨이 `run_sandbox_command(command, working_directory)`의 스키마를 제공합니다. 작업 프롬프트는 `git`, `jq`, `sqlite3`, `repo-audit`, `agent-tool cached-audit` 등 설치된 도구와 사용법을 안내합니다. 이는 범용 셸 게이트웨이와 프롬프트 카탈로그를 결합한 패턴으로, 특정 상용 에이전트의 내부 구현이 같다는 주장은 아닙니다. 모델은 도구를 직접 발견하거나 호출하는 것이 아니라 안내된 명령을 `&&`, `|`, 리다이렉션 등으로 조합해 하나 이상의 게이트웨이 호출을 제안합니다.

다음은 실제 함수 스키마의 JSON 예시입니다(`working_directory`는 선택 사항).

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

### 3단계 — 호스트 검증과 데이터 평면 실행

`SandboxToolPolicy.validate`는 함수명, JSON 구조, 길이와 인자를 검사하고 `sandbox_id`, `endpoint`, `token` 같은 라우팅·권한 필드와 추가 필드를 거부합니다. 기본 작업 디렉터리는 `/workspace`이며 프롬프트는 모든 스크래치 파일에 이 경로를 사용하도록 지시합니다. 정책은 명령 문자열에 문자 그대로 포함된 `/tmp`, `/var/tmp`, `mktemp`를 거부합니다. **그러나 다른 절대 작업 디렉터리도 허용하고 경로 우회나 셸 내부 동작을 완전히 차단하지 않습니다.** `/workspace` 강제나 파일시스템 격리를 보장하는 검증으로 취급하지 마세요.

`_Executor`는 모델이 선택한 ID가 아니라 자신이 보유한 `sandbox_id`를 `AcaSandboxAdapter.exec`에 전달합니다. 어댑터는 자신이 등록한 sandbox만 조회하여 `sandbox.exec(...)`를 실행합니다. 라이브 SDK는 ACA Sandbox 데이터 평면에 `POST https://management.<region>.azuredevcompute.io/.../sandboxes/<id>/executeShellCommand?api-version=2026-02-01-preview`를 보냅니다. 이는 경로를 줄여 쓴 설명이며 엔드포인트·인증과 ID는 호스트가 관리합니다. 오프라인 모드는 이 호출을 시뮬레이션합니다.

게스트의 `exit_code`, `stdout`, `stderr`는 `ToolExecutionResult`로 정규화되고, 출력 길이 제한을 거쳐 `status`, `error_kind`와 함께 `role="tool"` 메시지로 모델에 전달됩니다. 호스트 전용 메타데이터는 모델에 전달하지 않습니다. 이 예제는 임의 셸 명령을 실행할 수 있으므로 추가 egress·자원 제한과 독립된 보안 통제가 필요합니다.

## 설치된 에이전트 도구와 RAM 도구 서버

`provision_agent_tools`는 샌드박스 **내부에서만** `apt-get update`와 `apt-get install -y --no-install-recommends git jq sqlite3`를 수행하고 `/usr/local/bin/agent-tool`, `repo-audit`, `agent-toolkit` 및 전용 설정을 설치합니다. 호스트 도구 설치나 Foundry 자격 증명 전달은 없습니다. Ubuntu 패키지 저장소에 접근할 수 있는 아웃바운드 네트워크와 이미지 내 Python 3가 필요하며 설치 시간·비용은 별도입니다. 동일 sandbox에 프로비저닝은 한 번만 수행합니다.

1. **Turn 1**: Foundry 모델이 설치 도구 버전을 확인하고 `repo-audit init`으로 Git 리포지토리·JSON 설정·SQLite 감사 DB를 구성합니다. `git`, `jq`, `sqlite3` 및 셸 파이프라인으로 분석하고, 설치된 `state_worker.py`를 구동해 리포지토리 인덱스를 RAM에 캐시합니다. `agent-tool cached-audit`로 루프백 도구 서버를 실제 호출합니다.
2. **호스트 stop → resume**: 같은 sandbox를 재개합니다. 설치 파일·패키지 버전·설정의 SHA-256 지문과 실행 성공을 비교하며, 서버 nonce·카운터·RAM 버퍼·시작 지문도 검사합니다. 재설치나 서버 재기동은 없습니다.
3. **Turn 2**: 모델이 기존 CLI와 도구 서버를 그대로 호출한 뒤 `jq`로 설정을 패치하고 SQLite에 `patched`를 기록합니다. `repo-audit verify`가 `verification.json`을 생성하며 호스트도 독립적으로 검증합니다. 알려진 명령 오류는 모델이 수정할 수 있지만 결과가 불확실한 실행은 자동 재전송하지 않습니다.

```powershell
.\.venv\Scripts\python.exe examples\sandbox\command_agent.py --live
.\.venv\Scripts\python.exe examples\sandbox\compare_snapshot_modes.py --live --iterations 3 --output-dir .internal\results\live-snapshot-compare
.\.venv\Scripts\python.exe examples\sandbox\snapshot_clone.py --live
```

| 검증 | Memory | Disk |
| --- | --- | --- |
| `installed_tool_preserved` | `true`: CLI·패키지·설정 재사용 | `true`: CLI·패키지·설정 재사용 |
| `disk_preserved` | `true` | `true` |
| `memory_preserved` | `true`: 인덱스 서버 재기동 없음 | `false`: 기존 서버 없음 |
| `worker_restarted` | `false` | `true`: 설치된 서버 프로그램만 다시 실행 |

명시적 snapshot/clone 예제도 실제 툴체인과 샘플 프로젝트를 원본에 설치합니다. clone은 프로비저닝 0회로 설치된 CLI를 즉시 사용해 프로젝트를 패치하고 검증합니다. 원본 설정과 clone의 스냅샷 시점 아티팩트가 서로 독립적인지도 검사합니다. 이는 clone의 RAM 연속성에 대한 주장이 아닙니다. 기본 오프라인 모드는 같은 계약을 흉내 내며 실제 apt나 바이너리를 실행하지 않습니다.


### 실제 라이브 결과 (2026-11-05)

최종 gpt-5.4-mini 실행은 Turn 1 도구 호출 1회, Turn 2 2회(호출마다 여러 CLI 포함)로 성공했고 실패 명령은 없었습니다. `installed_tool_preserved=true`, Memory 서버 `memory_preserved=true`/`worker_restarted=false`를 확인했습니다. 명시적 clone도 재설치 0회와 원본 설정 독립성 검증에 성공했습니다. Memory/Disk는 각각 3회 전부 통과했습니다. sandbox·snapshot·임시 그룹/리소스·생성 역할을 모두 삭제했고 `.env`는 변경되지 않았습니다. 단위 테스트는 317건 통과했습니다. 리소스 정리가 실행 요금 0원을 뜻하지는 않습니다.


`command_agent.py --live`의 마스킹된 요약 (리소스 식별자와 관찰 상세 제외):

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

`snapshot_clone.py --live`의 마스킹된 요약:

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

이 결과는 새 clone에서 설치된 CLI 재사용 및 파일 상태 독립성을 보여 주며, 원본 서버의 RAM 연속성을 의미하지 않습니다. 재현 절차는 [배포·실행·정리 How-To](how-to-deploy-and-run.md)를 참조하세요.
