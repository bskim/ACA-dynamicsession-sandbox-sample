[English](./troubleshooting-en.md)

# 문제 해결

[README](../README.md) · [Dynamic Sessions](dynamic-sessions.md) · [Sandbox](sandbox.md)

| 증상 | 확인할 사항 |
| --- | --- |
| 설정 오류 또는 placeholder | `.env`의 사용하지 않는 `<...>` 값도 비우고 추론 URL의 `/openai/v1/`을 확인합니다. 프로세스 환경 변수는 `.env`보다 우선합니다. |
| 403 / 인증 오류 | 올바른 테넌트·구독, Foundry 추론 역할, pool의 Session Executor 또는 group의 SandboxGroup Data Owner와 역할 전파를 확인합니다. Contributor를 자동 부여하지 않습니다. |
| 동일 ID에 상태 없음 | 유휴 TTL이나 장애로 새 세션이 할당될 수 있습니다. `--wait-for-ttl`은 풀 cooldown보다 길게 지정하며, 영구 보관이 필요한 데이터는 외부 스토리지에 저장합니다. |
| Shell 또는 실행 API 오류 | 올바른 유형의 풀, 반환된 endpoint, API/wire profile 및 미리 보기 버전을 확인합니다. Shell 변수·cwd는 실행마다 설정합니다. |
| 배포 오류 | `ENABLE_PROVISIONING`은 setup 프로세스의 환경 변수입니다. Sandbox는 기존 그룹과 `-Execute`가 필요합니다. 리전·quota·RBAC를 확인하세요. |
| 비교 또는 clone 실패 | Disk는 기존 worker가 사라지고 bootstrap이 필요합니다. Memory에 Data Disk를 붙이지 마세요. 이미지의 `python3`, SDK, 정책·polling 및 삭제 실패 manifest를 확인하세요. |
| resume 이후 연결 끊김 | 외부 연결·임대·토큰을 재검증하고 재연결합니다. |

오프라인 실행은 모의 백엔드를 사용합니다. 진단 공유 전 endpoint, ID, 명령 출력, 토큰 및 원시 결과를 제거하세요. [Sandbox Python SDK](https://learn.microsoft.com/ko-kr/azure/container-apps/sandboxes-quickstart-python-sdk) · [Session pool](https://learn.microsoft.com/ko-kr/azure/container-apps/session-pool).

## 실제 preview 실행에서 확인한 사례 (2026-11-05)

[배포·실행·정리 How-To](how-to-deploy-and-run.md)도 참고하세요. 다음은 해당 preview 버전에서 관측한 사례입니다.

| 증상 | 확인과 해결 |
| --- | --- |
| Sandbox 배포 `ValidationForResourceFailed` | `Microsoft.App/sandboxGroups@2026-07-01` Bicep 리소스에는 `properties: {}`가 필요합니다. 상세 오류 메시지가 비어 있더라도 템플릿에 `properties: {}`가 포함되어 있는지 확인하세요. |
| `SessionPoolInvalidMaxConcurrentSessions` 또는 429 | 실제 허용 범위는 5~600입니다. 3은 PUT에서 거부됐고 5개 ceiling에 독립 세션이 겹치면 429가 발생했습니다. 세션 수·풀 설정·quota를 점검하고 필요하면 한도를 늘립니다.  |
| 중첩 파일 GET 401 | `agent-demo/source.py`를 통째로 route에 인코딩하지 않고 basename `source.py`를 route에, `path=agent-demo`를 query에 분리해 해결했습니다. 무조건 토큰이나 RBAC를 확대하지 마세요. |
| Shell `SessionExecutionPropertyNotSupported` | 실제 요청 본문은 `{shellCommand, timeoutInSeconds}`만 허용했습니다. Python용 `codeInputType`/`executionType`을 보내지 마세요. 응답 `status="0"`은 문자열 종료 코드이며 0만 성공입니다. |
| Windows `az.cmd` URL에서 `&` | 인용된 URL도 `&`가 명령 구분자로 처리될 수 있습니다. `--url-parameters api-version=2025-10-02-preview "identifier=$existingSessionIdentifier"`처럼 분리하세요. 올바른 CLI 구문으로 GET을 재확인했습니다. |
| Ubuntu cwd 오류 또는 snapshot timeout | 기본 이미지에 `/workspace`가 없어 `/`에서 `mkdir -p /workspace`로 준비했습니다. 동기 snapshot의 GET에는 `status`가 없을 수 있으므로 일치하는 ID의 존재를 확인합니다. Creating이나 실패 상태는 계속 확인합니다. 관찰 GET만 일시적인 404에 한해 재시도합니다. |

401/403/404의 원인은 다릅니다. GET 전파 폴링은 기존 활성 세션을 사용하고 결과가 불명인 실행·생성 POST는 자동 재시도하지 마세요.