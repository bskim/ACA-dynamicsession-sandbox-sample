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

기본은 오프라인 시뮬레이션입니다. 라이브 호출은 `.env`와 RBAC를 준비하고 `ENABLE_LIVE_AZURE=true`, `--live`를 함께 지정합니다. command agent는 Foundry 도구 호출로 원격 명령을 실행하며 명령 사이 stop/resume합니다. sandbox 내부에 자격 증명을 전달하지 않습니다. 모델이 제안한 임의 명령을 실행하는 개발 예제이므로 서비스 노출 시 추가 보안 통제가 필요합니다.

| 동작 | Memory | Disk |
| --- | --- | --- |
| stop/resume 후 | 루트 디스크와 RAM 프로세스·변수 유지 | 루트 디스크 유지, VM·프로세스 재시작 |
| Data Disk 볼륨 | 연결 시 미지원 | 지원되는 모드 |
| 측정 | Running, 첫 exec, 워커 준비 단계 분리 | 같은 구간; 워커 준비에 bootstrap 포함 |

Memory 모드에서도 외부 연결·토큰의 유효성은 재검증해야 합니다. 명시적 named snapshot은 별도 group/region 범위 리소스이며 새 sandbox clone에 사용됩니다. 예제는 원본을 변경해 clone의 파일이 독립적인지 검사합니다. 기존 sandbox의 resume와 혼동하지 마세요. 예제는 자신이 만든 sandbox·snapshot을 정리하려 하지만 실패 시 비공개 manifest로 복구해야 합니다. [공식 Sandbox 개요](https://learn.microsoft.com/ko-kr/azure/container-apps/sandboxes-overview) · [수명 주기와 상태](https://learn.microsoft.com/ko-kr/azure/container-apps/sandboxes-snapshots-state-management).

## 실제 Azure 결과와 해석 (2026-11-05)

[배포·실행·정리 How-To](how-to-deploy-and-run.md)에서 실행 절차를 확인하세요. `command_agent.py --live`에서 `gpt-5.4-mini`가 `/workspace/demo/hello.sh`를 만들고 실행하고 `Hello from demo script` 출력을 확인하는 도구 호출 3회가 모두 성공했습니다. 실제 예제 main 계측 wall은 약 49.2초였습니다. 이 값에는 모델·호스트·네트워크와 정리가 포함되지만 프로세스 시작/선행 import는 제외됩니다.

| 단계 | 횟수 | 호출별 클라이언트 관측 ms |
| --- | ---: | --- |
| create | 1 | 3811.842 |
| stop | 3 | 6980.071, 7054.588, 7149.089 |
| resume | 2 | 1544.888, 1257.362 |
| delete | 1 | 1322.698 |

create 후 원격 명령으로 파일 생성 → 실행 → 출력 검증을 수행했으며, 명령 사이 같은 sandbox를 stop/resume하고 종료 시 삭제했습니다. 위 수치는 단계별 호출이며 전부 순수 서비스 내부 처리 시간이나 SLA가 아닙니다.

`snapshot_clone.py --live` 결과 (ID·manifest 경로 제외):

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

명시적 `create_snapshot`으로 group/region 범위 스냅샷을 만들고 `snapshot_id`로 **새 sandbox**를 생성합니다. 원본과 clone이 다른 sandbox이고 원본 변경 후 clone은 스냅샷 시점의 해시를 유지했습니다. 이는 파일 독립성의 증거이며 clone의 RAM 연속성을 검증한 것은 아닙니다. 자동 일시 중지 정책의 stop/resume은 같은 sandbox를 재개하는 별개 작업입니다.

라이브 서비스에서 기본 `ubuntu` 이미지에는 `/workspace`가 없어 create 후 `/`에서 `mkdir -p /workspace`로 초기화했습니다. 동기 snapshot 생성 뒤 GET 응답에 `status`가 생략되기도 했습니다. 이 경우 요청한 snapshot ID와 일치하는 GET 존재로 완료를 확인하며, `Creating`이나 실패 status를 완료로 취급하지 않습니다. 일시적 404는 관찰 GET만 제한 시간 내 반복하고 snapshot 생성 POST는 자동 재전송하지 않습니다. 이러한 preview 계약은 버전별로 달라질 수 있습니다.