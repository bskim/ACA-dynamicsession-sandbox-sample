[English](./architecture-en.md)

# 아키텍처와 신뢰 경계

![신뢰된 호스트와 격리된 실행 경로](assets/overall-architecture.svg)

[편집 가능한 Draw.io 원본](assets/overall-architecture.drawio) · [README](../README.md) · [배포·실행·정리 How-To](how-to-deploy-and-run.md)

사용자와 로컬 CLI의 오케스트레이터·도구 정책은 신뢰 영역에 있습니다. Foundry OpenAI v1 배포 모델은 코드를 제안하지만 endpoint, 자격 증명, 사용자 소유권이나 세션을 선택하지 않습니다. 정책은 허용 도구·인자·횟수·시간·출력을 제한합니다. 모델 출력과 도구 결과는 비신뢰 데이터이며 호스트 subprocess로 실행하지 않습니다. 각각의 서비스에 적절한 Entra 토큰을 호스트에서 취득하고 샌드박스에 주입하지 않습니다.

## 왜 두뇌와 실행 환경을 분리하나요?

이 샘플의 두뇌는 신뢰된 호스트의 정책 오케스트레이터와 원격 Foundry 모델입니다. 호스트가 추론·Azure 토큰을 관리하고 모델의 결정을 검증합니다. 오케스트레이터까지 sandbox에서 실행하면 추론 토큰이나 Azure 자격 증명을 게스트에 주입해야 할 수 있고, 비신뢰 코드 실행이나 프롬프트 인젝션으로 탈취될 위험이 커집니다. 자격 증명은 호스트에서 관리하며 격리된 마이크로 VM에는 도구 실행에 필요한 CLI와 루프백 인메모리 서버를 둡니다.

호스트는 설치된 도구를 각각 모델 함수로 등록하지 않고, 프롬프트의 도구 목록과 단일 `run_sandbox_command` 게이트웨이를 제공합니다. 모델이 제안한 셸 명령은 호스트 정책 검증을 거쳐 호스트가 소유한 sandbox로 전달되고, 결과는 다시 모델에 반환됩니다. 정책의 문자열 검사는 완전한 명령 격리가 아니므로 자격 증명 미주입, egress와 자원 제한 등 추가 방어가 필요합니다. [3단계 상세 흐름](sandbox.md#외부-에이전트의-도구-노출과-호출-3단계)

### 에이전트와 sandbox 변경 수명 주기 분리

프롬프트, 오케스트레이션 로직, 모델 버전과 비즈니스 규칙까지 이미지나 스냅샷에 함께 굽는 설계라면 이들을 변경할 때 이미지·스냅샷의 갱신과 재배포가 필요해져 강하게 결합됩니다. 이 샘플은 에이전트 구성과 범용 게스트 도구(`git`, `jq`, `sqlite3`, 리포지토리 감사·인덱스)를 분리합니다. 도구 인터페이스와 호환성이 유지되는 한 모델 교체(예: `gpt-5.4-mini` → `gpt-5.4`), 프롬프트나 호스트 로직 변경, 코드 리뷰·보안 감사·패치 같은 역할 추가는 sandbox 이미지나 스냅샷 재생성 없이 가능합니다. 대상 모델의 tool calling 지원과 접근 권한은 별도 확인해야 합니다.

설치된 CLI와 서버 프로그램을 포함한 골든 스냅샷은 서로 다른 에이전트가 사용할 sandbox clone의 기반이 될 수 있고, 기존 sandbox는 stop/resume할 수 있습니다. 공개 clone 예제의 `clone_provision_count=0`은 새 clone에서 설치된 도구를 재사용했음을 보여 줍니다. 여러 에이전트를 연결하려면 별도 라우팅·소유권·보안 제어가 필요하며, 도구·게스트 설정·인터페이스가 바뀌면 스냅샷을 갱신해야 할 수 있습니다.

## 대안 A / B

A는 `Microsoft.App/sessionPools`의 PythonLTS 세션으로 요청 `identifier`를 라우팅합니다. 동일 활성 ID의 인터프리터와 파일은 재사용되지만 유휴 만료 후 보장되지 않습니다. Shell은 별도 선택 풀입니다. B는 `Microsoft.App/sandboxGroups`의 격리된 마이크로 VM을 만들고 SDK로 exec·stop·resume·삭제를 제어합니다. 샌드박스 내부에 에이전트 CLI 툴체인(`git`, `jq`, `sqlite3`, `agent-tool`·`repo-audit` 등 커스텀 CLI)과 루프백 리포지토리 인덱스 도구 서버(`state_worker.py`)를 설치·제어합니다. Memory는 RAM과 루트 디스크를 보존하고, Disk는 설치 도구와 루트 디스크를 보존하지만 서버 프로세스는 재시작합니다. named snapshot에서 새 clone을 만드는 것은 기존 인스턴스 재개와 다릅니다. [A 가이드](dynamic-sessions.md) · [B 가이드](sandbox.md) · [상태 오라클](snapshot-comparison.md)

## 운영 경계

서비스로 공개하려면 사용자 인증과 세션 소유권, egress, 예산·자원 제한, 감사·데이터 보존 정책을 별도로 구현해야 합니다. 세션 ID와 함께 소유권을 검증하세요. Sandbox 메모리 스냅샷에도 민감한 RAM이 포함될 수 있으므로 외부 시스템 상태와 연결을 재검증하세요. 정리 시 기존 Foundry나 다른 사용자의 리소스를 삭제하지 마세요.

다이어그램은 Draw.io 32.6.1에서 내보냈으며 해당 프로그램의 Azure 아이콘 라이브러리 그래픽을 내장합니다. Container Apps에 전용 아이콘이 없는 경우 Container Apps Environment 상위 아이콘과 명시적 라벨을 사용합니다. [Azure 공식 아이콘 출처와 이용 조건](https://learn.microsoft.com/ko-kr/azure/architecture/icons/).
