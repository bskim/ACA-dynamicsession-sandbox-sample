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
