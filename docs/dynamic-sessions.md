[English](./dynamic-sessions-en.md)

# Dynamic Sessions: 코딩 에이전트와 ID 재사용

![동일 식별자의 다중 턴 재사용, 격리와 만료](assets/dynamic-sessions-session-reuse.svg)

[Draw.io 원본](assets/dynamic-sessions-session-reuse.drawio) · [README와 설정](../README.md)

`PythonLTS`는 코드 인터프리터이며 로컬 에이전트가 모델 호출, 도구 정책 및 결과 전달을 관리합니다. 풀은 동일 `identifier`를 같은 활성 세션으로 라우팅합니다. A의 nonce·counter·파일이 다음 턴에 유지되는지 확인하고 B에서는 변수와 파일의 부재를 확인합니다. 만료 후 같은 ID로 빈 세션이 새로 할당될 수 있습니다. `state_lost`는 세션 만료를 감지했을 때 예제가 반환하는 상태값입니다. 식별자와 함께 사용자 소유권을 검증하세요.

```powershell
.\.venv\Scripts\python.exe examples\dynamic_sessions\session_reuse.py
.\.venv\Scripts\python.exe examples\dynamic_sessions\coding_agent.py
.\.venv\Scripts\python.exe examples\dynamic_sessions\shell_agent.py
```

기본은 fake 클라이언트입니다. 라이브는 README의 환경·RBAC 설정 후 `--live`와 `ENABLE_LIVE_AZURE=true`가 함께 필요합니다. `session_reuse.py --live --wait-for-ttl SECONDS`는 지정 초 동안 대기 후 실제 만료를 검사합니다. 예제의 `--session-id`는 명시 ID를 전달합니다. CLI 호출 사이의 세션 만료와 프로세스 장애에 대비해 별도 복구 방안을 마련하세요. 식별자를 비공개로 다루세요. `--wire-profile` 기본값은 `containerapp-1.3.0b5`이며 `learn-properties`는 선택 프로필입니다. 환경 설정에서 허용되는 기본 프로필과 실제 서비스 계약을 확인하고 실패 시 자동 재시도하지 마세요.

Shell 예제는 별도 Shell 풀을 사용하며, 각 실행이 독립된 셸 프로세스로 동작하므로 매번 `cd /mnt/data`와 필요한 환경 변수를 함께 지정합니다. 풀은 `infra/dynamic-sessions/main.bicep`, `scripts/setup-dynamic-sessions.ps1`로 구성하고 실제 반환된 endpoint를 사용합니다. 배포·실행 및 TTL 대기 중 비용이 발생할 수 있습니다.

[라우팅과 인증](https://learn.microsoft.com/ko-kr/azure/container-apps/sessions-usage) · [코드 인터프리터 API](https://learn.microsoft.com/ko-kr/azure/container-apps/sessions-code-interpreter) · [Shell](https://learn.microsoft.com/ko-kr/azure/container-apps/sessions-tutorial-shell)

## 실제 Azure 결과와 해석 (2026-11-05)

[배포·실행·정리 How-To](how-to-deploy-and-run.md)의 설정 후 `session_reuse.py --live`를 실행한 결과입니다. 식별자는 마스킹했습니다.

```json
{"mode":"live","session":"[REDACTED]","reuse":"passed","counter":42,"nonce_preserved":true,"original_sha256":"ba1a531f581d2e6094e978ed6f7aca7a8d92eeb62c6e7ad73ee692f7f18bc772","file_modified":true,"negative_control":"passed","expiry":"skipped: use --live --wait-for-ttl after choosing the pool cooldown"}
```

동일 활성 `identifier`로 RAM의 nonce와 counter, `/mnt/data`의 파일이 유지되고 counter가 42로 증가했습니다. 다른 ID의 음성 대조군은 변수와 파일의 부재를 확인했습니다. 수정된 파일 해시의 차이도 확인했습니다.

`session_reuse.py --live --wait-for-ttl 330` 결과:

```json
{"mode":"live","session":"[REDACTED]","reuse":"passed","counter":42,"nonce_preserved":true,"original_sha256":"ba1a531f581d2e6094e978ed6f7aca7a8d92eeb62c6e7ad73ee692f7f18bc772","file_modified":true,"negative_control":"passed","expiry":"state_lost"}
```

300초 cooldown 풀에서 해당 ID로 330초 동안 요청하지 않은 후 같은 ID로 조회하자 변수는 없었고 파일 GET은 404였습니다. 활성 세션이 만료되어 같은 문자열로 새 빈 세션이 할당됐습니다. 기본 실행은 TTL 단계를 건너뜁니다.

`coding_agent.py --live`와 `shell_agent.py --live` 결과:

```json
{"mode":"live","session":"[REDACTED]","creation":"passed","initial_test":"failed_as_expected","repair_test":"passed","model_turns":[2,2,2]}
{"mode":"live","session":"[REDACTED]","runs":2,"cwd_explicit_each_time":true}
```

실제 `gpt-5.4-mini` 모델이 생성 → 실패 테스트 → 수정/재테스트의 세 사용자 턴을 처리했고 각 턴의 도구 호출과 최종 응답이 `model_turns=[2,2,2]`로 관찰됐습니다. uncaught assertion이 원격 `Failed`가 된 뒤 수정 테스트가 통과했습니다. coding 예제 main 계측 wall은 약 61.9초, Shell 예제는 약 5.0초였습니다. 별도 호스트 marker 검사는 `model_turns`에 포함되지 않습니다.

Shell preview 서비스의 실제 요청 본문은 `{shellCommand, timeoutInSeconds}`만 허용했고 `status="0"`은 문자열 프로세스 종료 코드입니다(0 성공). Python 실행과는 응답 계약이 다릅니다. 각 Shell 실행은 cwd를 명시합니다. 중첩 파일은 basename을 URL route에 넣고 디렉터리를 `path=` query로 분리해야 했습니다. 자세한 오류와 Windows URL 취급은 [문제 해결](troubleshooting.md)을 보세요.

호스트에서 관측한 HTTP·Azure CLI credential 획득 왕복은 수 초일 수 있지만 원격 컨테이너의 `executionTimeInMilliseconds`는 생성/marker/수정에서 6~16ms였습니다(별도 uncaught assertion은 325ms). 예제 wall에는 모델·네트워크·정리가 포함되며 프로세스 초기화/선행 import는 제외됩니다.