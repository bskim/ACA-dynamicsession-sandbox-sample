[English](./how-to-deploy-and-run-en.md)

# 배포·실행·정리 How-To (Windows PowerShell)

이 가이드는 **전용 테스트 리소스 그룹**에서 PythonLTS, 선택 Shell, Sandbox를 배포하고 정리합니다. 실제 배포·실행·대기·Foundry 추론은 비용이 발생할 수 있습니다. 기존 Foundry나 다른 사용자의 리소스를 수정·삭제하지 마세요. `<YOUR_...>`는 사용자가 직접 교체하며 실제 값·토큰·결과는 비공개로 유지합니다. 각 경로는 독립적입니다. 불필요한 풀이나 그룹은 생략하고 해당 변수·역할도 생략하세요.

2026-11-05에 실제 Azure에서 예제 6종, TTL 및 각 모드 3표본을 검증했습니다. 새 clone/venv 설치, 대화형 로그인, setup wrapper 실배포와 모든 CLI 라이브 dispatch는 그때 다시 실행하지 않았습니다. Bicep 원본을 참조하는 subscription 배포와 예제 파일 직접 라이브 실행이 검증 범위입니다. API는 preview이며 지역·권한·버전에 따라 다릅니다.

## 1단계: 사전 준비

Python 3.13 이상, Git, Azure CLI, Bicep, 기존 tool-calling Foundry 배포 및 프로비저닝·역할 할당 권한이 필요합니다. 런타임 사용자는 Foundry 추론 권한도 필요합니다. Python 버전은 자신의 설치에 맞게 선택하세요.

```powershell
git clone '<YOUR_PUBLIC_REPOSITORY_URL>'
Set-Location ACA-dynamicsession-sandbox-sample
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
az login
az account set --subscription '<YOUR_SUBSCRIPTION_ID>'
az account show --query '{subscription:id,state:state,tenant:tenantId}' -o json
az ad signed-in-user show --query id -o tsv

# 새 PowerShell마다 설정; 전역 CLI 확장을 수정하지 않습니다.
$env:AZURE_EXTENSION_DIR = Join-Path (Get-Location) '.venv\az-extensions'
$env:AZURE_EXTENSION_USE_DYNAMIC_INSTALL = 'no'
az extension add --name containerapp --allow-preview true --yes
az extension list --query '[].{name:name,version:version}' -o json
az containerapp sessionpool create --help
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

기록된 환경은 Python 3.14.7 / Azure CLI 2.91.0 / 로컬 containerapp 확장 1.3.0b5 / sandbox SDK 0.1.0b4였습니다. 확장 설치 명령은 더 최신 preview를 선택할 수 있으므로 버전과 wire 계약을 확인하고 후속 버전까지 이번 검증이 적용된다고 가정하지 마세요. `az bicep version`으로 Bicep을 확인하고 없으면 `az bicep install`로 설치합니다.

`.env`에 자신의 `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`, `AZURE_LOCATION`, `FOUNDRY_OPENAI_BASE_URL=https://<YOUR_FOUNDRY_ACCOUNT>.openai.azure.com/openai/v1/`, 기존 `FOUNDRY_MODEL_DEPLOYMENT`, `FOUNDRY_API_MODE=chat_completions`, `FOUNDRY_TOKEN_SCOPE=https://ai.azure.com/.default`를 설정합니다. OpenAI 추론 URL은 project endpoint가 아닙니다. **미사용 항목을 포함해** 남은 `<...>` placeholder는 자신의 값으로 바꾸거나 비우세요. 비밀키와 액세스 토큰은 저장하지 않습니다. `.env`는 Git 무시 대상이지만 암호화되지 않습니다. 로컬 계정 및 테넌트를 확인하고 프로세스 환경 변수가 `.env`보다 우선함에 유의하세요.

```powershell
.\.venv\Scripts\aca-agent-sample.exe preflight config-check
.\.venv\Scripts\python.exe -m pytest tests\unit -q
```

## 2단계: Azure 인프라와 RBAC

반드시 새 전용 그룹을 사용하세요. 기존 그룹이 있거나 소유권이 불확실하면 중단합니다. 리소스 생성자에게 `Microsoft.Authorization/roleAssignments/write`가 필요합니다. 지역별 [할당량](https://sandboxes.azure.com/docs/sandboxes/limits) 및 [세션 풀](https://learn.microsoft.com/ko-kr/azure/container-apps/session-pool)을 확인하세요. 제공자 등록과 지역 가용성도 확인합니다.

```powershell
$sub = az account show --query id -o tsv
$userObjectId = az ad signed-in-user show --query id -o tsv
$location = '<YOUR_AZURE_REGION>'
$rg = '<YOUR_NEW_TEST_RESOURCE_GROUP>'
$pythonPool = '<YOUR_PYTHON_POOL>'
$shellPool = '<YOUR_SHELL_POOL>'
$sandboxGroup = '<YOUR_SANDBOX_GROUP>'
if (-not $sub -or -not $userObjectId -or $rg -match '[<>]' -or $rg -imatch '^rg-foundry(?:-|$)') { throw 'Check subscription, user and dedicated RG.' }
$exists = az group exists --subscription $sub --name $rg -o tsv
if ($LASTEXITCODE -ne 0 -or $exists -ne 'false') { throw 'RG must be new; stop and inspect.' }
az rest --method get --url "https://management.azure.com/subscriptions/$sub/providers/Microsoft.App/locations/$location/usages?api-version=2025-01-01" --query 'value[?contains(name.value, `Session`) || contains(name.value, `Sandbox`)].{name:name.value,current:currentValue,limit:limit}' -o json
if ($LASTEXITCODE -ne 0) { throw 'Quota query failed.' }
az group create --subscription $sub --name $rg --location $location --output none
if ($LASTEXITCODE -ne 0) { throw 'RG creation failed.' }
```

실측 전 할당량은 `SessionPools 0/50`, `SandboxCores 0/300`이었으며 사용자 구독의 제한을 의미하지 않습니다. 별도 Data Disk 볼륨은 Memory 비교에 연결하지 않습니다.

### 방법 A: Bicep 직접 배포

```powershell
az deployment group create --subscription $sub -g $rg --name python-pool --template-file .\infra\dynamic-sessions\main.bicep --parameters "poolName=$pythonPool" "location=$location" containerType=PythonLTS readySessionInstances=0 maxConcurrentSessions=10 cooldownPeriodInSeconds=300 --query properties.provisioningState -o tsv
if ($LASTEXITCODE -ne 0) { throw 'Python pool deployment failed.' }
# 선택: Shell 예제용 별도 풀
az deployment group create --subscription $sub -g $rg --name shell-pool --template-file .\infra\dynamic-sessions\main.bicep --parameters "poolName=$shellPool" "location=$location" containerType=Shell readySessionInstances=0 maxConcurrentSessions=5 cooldownPeriodInSeconds=300 --query properties.provisioningState -o tsv
if ($LASTEXITCODE -ne 0) { throw 'Shell pool deployment failed.' }
az deployment group create --subscription $sub -g $rg --name sandbox-group --template-file .\infra\sandbox\main.bicep --parameters "sandboxGroupName=$sandboxGroup" "location=$location" --query properties.provisioningState -o tsv
if ($LASTEXITCODE -ne 0) { throw 'Sandbox group deployment failed.' }
az resource list --subscription $sub -g $rg --query '[].{name:name,type:type,location:location}' -o json
```

풀의 `maxConcurrentSessions`는 **5~600**이며 미리 준비된 `readySessionInstances`와 다릅니다. 과도한 동시 세션은 429가 될 수 있습니다. `Microsoft.App/sandboxGroups@2026-07-01` 템플릿에는 실배포에 필요한 `properties: {}`가 들어 있습니다. `BCP081` 로컬 타입 경고와 서비스 수락은 별개입니다. Managed Environment는 이 구성에 필요하지 않습니다.

### 방법 B: setup 스크립트 (A 대신 선택)

위에서 전용 RG를 만든 후 실행합니다. Sandbox 스크립트는 기본 dry run이며 `-Execute`를 명시해야 합니다. 두 스크립트는 아래 역할도 부여하므로 A의 역할 생성 명령을 중복 실행할 필요는 없습니다. 다음 절의 scope와 endpoint 조회 및 전파 확인은 그대로 수행하세요.

```powershell
$oldProvisioning = $env:ENABLE_PROVISIONING
$env:ENABLE_PROVISIONING = 'true'
try {
    .\scripts\setup-dynamic-sessions.ps1 -SubscriptionId $sub -ResourceGroupName $rg -PoolName $pythonPool -Location $location -ContainerType PythonLTS -MaxConcurrentSessions 10 -ReadySessionInstances 0 -CooldownPeriodInSeconds 300 -ExecutorPrincipalObjectId $userObjectId -ExecutorPrincipalType User
    .\scripts\setup-dynamic-sessions.ps1 -SubscriptionId $sub -ResourceGroupName $rg -PoolName $shellPool -Location $location -ContainerType Shell -MaxConcurrentSessions 5 -ExecutorPrincipalObjectId $userObjectId -ExecutorPrincipalType User
    .\scripts\setup-sandbox.ps1 -SubscriptionId $sub -ResourceGroup $rg -SandboxGroupName $sandboxGroup -Location $location -AssigneeObjectId $userObjectId -AssigneePrincipalType User -Execute
} finally {
    if ($null -eq $oldProvisioning) { Remove-Item Env:ENABLE_PROVISIONING -ErrorAction SilentlyContinue }
    else { $env:ENABLE_PROVISIONING = $oldProvisioning }
}
```

### 데이터 평면 역할과 endpoint

방법 A에서만 역할을 생성합니다. 역할 정의 ID는 공개된 내장 역할 ID이며 사용자 식별자가 아닙니다. `User`가 아닌 서비스 주체는 올바른 object ID와 principal type으로 변경합니다.

```powershell
$pythonScope = "/subscriptions/$sub/resourceGroups/$rg/providers/Microsoft.App/sessionPools/$pythonPool"
$shellScope = "/subscriptions/$sub/resourceGroups/$rg/providers/Microsoft.App/sessionPools/$shellPool"
$sandboxScope = "/subscriptions/$sub/resourceGroups/$rg/providers/Microsoft.App/sandboxGroups/$sandboxGroup"
# 방법 A만 실행: Azure ContainerApps Session Executor
foreach ($scope in @($pythonScope, $shellScope)) {
    az role assignment create --subscription $sub --assignee-object-id $userObjectId --assignee-principal-type User --role '0fb8eba5-a2bb-4abe-b1c1-49dfad359bb0' --scope $scope --output none
    if ($LASTEXITCODE -ne 0) { throw 'Session role assignment failed.' }
}
# 방법 A만 실행: Container Apps SandboxGroup Data Owner
az role assignment create --subscription $sub --assignee-object-id $userObjectId --assignee-principal-type User --role 'c24cf47c-5077-412d-a19c-45202126392c' --scope $sandboxScope --output none
if ($LASTEXITCODE -ne 0) { throw 'Sandbox role assignment failed.' }
foreach ($scope in @($pythonScope, $shellScope, $sandboxScope)) {
    az role assignment list --scope $scope --query '[].{role:roleDefinitionName,principalType:principalType}' -o json
}
$pythonEndpoint = az resource show --ids $pythonScope --api-version 2025-10-02-preview --query properties.poolManagementEndpoint -o tsv
if ($LASTEXITCODE -ne 0 -or -not $pythonEndpoint) { throw 'Python endpoint unavailable.' }
$shellEndpoint = az resource show --ids $shellScope --api-version 2025-10-02-preview --query properties.poolManagementEndpoint -o tsv
if ($LASTEXITCODE -ne 0 -or -not $shellEndpoint) { throw 'Shell endpoint unavailable.' }
```

### 기존 Foundry 추론 권한

계정 범위의 `Cognitive Services OpenAI User`는 Session/Sandbox 역할과 별개입니다. 계정 관리자에게 올바른 실행 주체의 기존·상속 추론 권한을 확인받으세요. 기록된 라이브 실행은 기존 권한을 재사용했으며 Foundry를 변경하지 않았습니다. 아래 선택 블록은 기본적으로 역할을 생성하지 않습니다. 권한이 없고 권한 있는 관리자의 승인을 받은 경우에만 활성화하세요. 이 계정 scope를 정리용 전용 `$rg`와 혼동하지 말고 기존 Foundry 그룹은 삭제하지 마세요. 역할 목록만으로 추론 전파가 증명되지 않으며 모델을 사용하는 라이브 예제의 성공으로 실제 접근을 확인합니다.

```powershell
$foundryScope = '<YOUR_EXISTING_FOUNDRY_ACCOUNT_ARM_ID>'
if ($foundryScope -notmatch ('^/subscriptions/' + [regex]::Escape($sub) + '/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/[^/]+$')) { throw 'Check the existing Foundry account scope and subscription.' }
az role assignment list --subscription $sub --assignee $userObjectId --scope $foundryScope --include-inherited --query '[].roleDefinitionName' -o json
if ($LASTEXITCODE -ne 0) { throw 'Foundry role lookup failed.' }
$assignMissingFoundryRole = $false
if ($assignMissingFoundryRole) {
    az role assignment create --subscription $sub --assignee-object-id $userObjectId --assignee-principal-type User --role '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd' --scope $foundryScope --output none
    if ($LASTEXITCODE -ne 0) { throw 'Foundry inference role assignment failed.' }
}
```

### 배포값을 `.env`에 연결

먼저 지역별 Sandbox endpoint가 실제 지역과 맞는지 확인하세요. 이전 내용을 유지하면서 지정 키만 바꿉니다. `DYNAMIC_SESSIONS_API_VERSION=2025-10-02-preview`, `DYNAMIC_SESSIONS_WIRE_PROFILE=containerapp-1.3.0b5`, Sandbox data-plane `2026-02-01-preview`, ARM `2026-07-01`과 두 데이터 평면 scope `https://dynamicsessions.io/.default`를 유지합니다. Foundry scope와 다릅니다.

```powershell
$content = Get-Content .env -Raw
$values = @{
    AZURE_LOCATION = $location
    DYNAMIC_SESSIONS_RESOURCE_GROUP = $rg
    DYNAMIC_SESSIONS_POOL_ENDPOINT = $pythonEndpoint
    SANDBOX_RESOURCE_GROUP = $rg
    SANDBOX_GROUP_NAME = $sandboxGroup
    SANDBOX_ENDPOINT = "https://management.$location.azuredevcompute.io"
    ENABLE_LIVE_AZURE = 'true'
    ENABLE_PROVISIONING = 'true'
}
foreach ($key in $values.Keys) {
    $pattern = '(?m)^' + [regex]::Escape($key) + '=[^\r\n]*'
    if ($content -notmatch $pattern) { throw "Missing .env key: $key" }
    $replacement = "$key=$($values[$key])"
    $content = [regex]::Replace($content, $pattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $replacement })
}
[IO.File]::WriteAllText((Resolve-Path .env), $content, [Text.UTF8Encoding]::new($false))
```


활성 세션 ID가 아직 없다면 `.env` 연결 **후** 아래와 같이 추측하기 어려운 ID를 생성해 예제를 한 번 명시적으로 실행한 뒤 아래 GET 폴링을 수행할 수 있습니다. 실패 시 오류를 조사하고 결과가 불명인 요청을 자동 반복하지 마세요. 생성 ID와 예제의 콘솔 출력은 비공개로 다루세요.

```powershell
$existingSessionIdentifier = [guid]::NewGuid().ToString('N')
.\.venv\Scripts\python.exe .\examples\dynamic_sessions\session_reuse.py --live --session-id $existingSessionIdentifier
if ($LASTEXITCODE -ne 0) { throw 'Live session check failed; inspect the error before explicitly trying again.' }
```

### 데이터 평면 전파 확인

역할 목록과 토큰 발급만으로 데이터 평면 전파를 증명할 수 없습니다. 기존에 **살아 있는** 테스트 세션 ID가 있을 때만 아래 GET을 사용합니다. ID가 없으면 3단계의 단일 라이브 실행을 한 번 명시적으로 시도해 확인하고, 결과 불명인 POST는 자동 재시도하지 마세요. 새/만료 ID의 404는 RBAC 전파 실패와 다릅니다. Sandbox 권한은 아래 읽기 전용 목록 GET 또는 성공한 실제 create/exec로 확인합니다. 401/404 등은 즉시 중단해 인증·ID를 조사하며 403만 제한된 시간 동안 폴링합니다. 역할 전파 확인에 필요한 관측 권한은 구성에 따라 다를 수 있습니다.

```powershell
# 위에서 .env를 연결한 뒤 본인이 보유한 활성 identifier를 사용합니다.
if (-not $existingSessionIdentifier -or $existingSessionIdentifier -match '[<>]') { throw 'Set an existing active session ID first.' }
$deadline = (Get-Date).AddMinutes(3)
do {
    $result = az rest --method get --url "$pythonEndpoint/files" --url-parameters api-version=2025-10-02-preview "identifier=$existingSessionIdentifier" --resource https://dynamicsessions.io/ --only-show-errors -o none 2>&1
    if ($LASTEXITCODE -eq 0) { Write-Host 'Session data-plane GET succeeded'; break }
    if (($result | Out-String) -notmatch '(?i)403|Forbidden' -or (Get-Date) -ge $deadline) { throw 'Data-plane GET failed; inspect authentication, identifier and RBAC privately.' }
    Start-Sleep -Seconds 10
} while ($true)
$sandboxUrl = "https://management.$location.azuredevcompute.io/subscriptions/$sub/resourceGroups/$rg/sandboxGroups/$sandboxGroup/sandboxes"
$deadline = (Get-Date).AddMinutes(3)
do {
    $result = az rest --method get --url $sandboxUrl --url-parameters api-version=2026-02-01-preview --resource https://dynamicsessions.io/ --only-show-errors -o json 2>&1
    if ($LASTEXITCODE -eq 0) { Write-Output $result; break }
    if (($result | Out-String) -notmatch '(?i)403|Forbidden' -or (Get-Date) -ge $deadline) { throw 'Sandbox GET failed; inspect authentication and RBAC privately.' }
    Start-Sleep -Seconds 10
} while ($true)
```

Windows `az.cmd`에서 URL의 `&`가 명령 구분자로 해석될 수 있으므로 `--url-parameters`로 분리합니다. 인증 오류를 일괄 권한 확대로 해결하지 마세요.

## 3단계: 오프라인과 실제 시나리오 실행

저장소 루트에서 실행하세요. 오프라인은 모의 백엔드만 사용하며 Azure 과금이 없습니다. 단위 테스트와 설치는 별도로 로컬 자원을 사용합니다.

```powershell
$py = '.\.venv\Scripts\python.exe'
& $py .\examples\dynamic_sessions\session_reuse.py
& $py .\examples\dynamic_sessions\coding_agent.py
& $py .\examples\dynamic_sessions\shell_agent.py
& $py .\examples\sandbox\command_agent.py
& $py .\examples\sandbox\compare_snapshot_modes.py --iterations 3 --output-dir .internal\results\offline-snapshot-compare
& $py .\examples\sandbox\snapshot_clone.py
```

실제 실행은 `.env`의 `ENABLE_LIVE_AZURE=true` **및** `--live`가 필요합니다. 원시 결과와 manifest는 비공개입니다. 세션 식별자와 nonce를 공유하지 마세요. 아래 TTL은 300초 cooldown 풀에서 330초를 기다리며 비용이 추가될 수 있습니다. Shell만 별도 endpoint를 일시 사용합니다.

```powershell
& $py .\examples\dynamic_sessions\session_reuse.py --live
& $py .\examples\dynamic_sessions\session_reuse.py --live --wait-for-ttl 330
& $py .\examples\dynamic_sessions\coding_agent.py --live
$oldEndpoint = $env:DYNAMIC_SESSIONS_POOL_ENDPOINT
$env:DYNAMIC_SESSIONS_POOL_ENDPOINT = $shellEndpoint
try { & $py .\examples\dynamic_sessions\shell_agent.py --live }
finally {
    if ($null -eq $oldEndpoint) { Remove-Item Env:DYNAMIC_SESSIONS_POOL_ENDPOINT -ErrorAction SilentlyContinue }
    else { $env:DYNAMIC_SESSIONS_POOL_ENDPOINT = $oldEndpoint }
}
& $py .\examples\sandbox\command_agent.py --live
& $py .\examples\sandbox\compare_snapshot_modes.py --live --iterations 3 --output-dir .internal\results\live-snapshot-compare
& $py .\examples\sandbox\snapshot_clone.py --live
```

동일 예제는 설치된 CLI에서도 실행할 수 있습니다. Shell 명령에는 위와 같은 endpoint override를 적용하세요. 라이브 기록은 예제 파일 직접 실행을 검증했으며 모든 CLI 라이브 dispatch를 검증한 것은 아닙니다.

```powershell
.\.venv\Scripts\aca-agent-sample.exe dynamic-session reuse --live
.\.venv\Scripts\aca-agent-sample.exe dynamic-session coding-agent --live
$oldEndpoint = $env:DYNAMIC_SESSIONS_POOL_ENDPOINT
$env:DYNAMIC_SESSIONS_POOL_ENDPOINT = $shellEndpoint
try { .\.venv\Scripts\aca-agent-sample.exe dynamic-session shell --live }
finally {
    if ($null -eq $oldEndpoint) { Remove-Item Env:DYNAMIC_SESSIONS_POOL_ENDPOINT -ErrorAction SilentlyContinue }
    else { $env:DYNAMIC_SESSIONS_POOL_ENDPOINT = $oldEndpoint }
}
.\.venv\Scripts\aca-agent-sample.exe sandbox command-agent --live
.\.venv\Scripts\aca-agent-sample.exe sandbox compare-modes --live --iterations 3 --output-dir .internal\results\live-snapshot-compare
.\.venv\Scripts\aca-agent-sample.exe sandbox snapshot-clone --live
```

| 확인 포인트 | 상세 |
| --- | --- |
| `reuse=passed`, counter 42, 격리 `passed`; TTL 실험 `state_lost` | [Dynamic Sessions](dynamic-sessions.md) |
| coding 생성·예상 실패·수정 테스트 통과; Shell 2회 cwd 명시 | [Dynamic Sessions](dynamic-sessions.md) |
| command agent 3회 도구 호출과 stop/resume; clone 해시 및 독립성 | [Sandbox](sandbox.md) |
| 양 모드 파일 보존, Memory RAM 연속성, Disk 워커 재시작 및 단계별 지연 | [비교 실험](snapshot-comparison.md) |

## 4단계: 정리와 설정 복원

예제는 자기 소유 sandbox/snapshot을 정리하려고 시도합니다. 실패하면 해당 실행의 비공개 manifest를 선택하여 우선 dry run으로 확인한 후 삭제합니다. setup이 만든 리소스는 자동 manifest 대상이 아닙니다. 삭제 요청 수락과 실제 삭제 완료는 다릅니다.

```powershell
.\scripts\cleanup.ps1 -ManifestPath '.internal\runs\<YOUR_RUN_MANIFEST>.json'
.\scripts\cleanup.ps1 -ManifestPath '.internal\runs\<YOUR_RUN_MANIFEST>.json' -Execute -Confirm:$false
```

Session만 배포했다면 아래 Sandbox 목록 반복문은 생략하세요. 배포하지 않은 그룹을 조회하지 않습니다. Sandbox/snapshot 데이터 평면 목록도 삭제 전에 확인하세요. 다음 명령은 **이번 실습을 위해 새로 만들고 소유권을 확인한 그룹 전체를 영구 삭제**합니다. 기존 Foundry 그룹은 절대 사용하지 않습니다. `$rg`와 `$sub`의 실제 대상을 다시 검토한 뒤에만 실행하세요.

```powershell
# RG 삭제 전 데이터 평면 목록을 비공개로 확인합니다.
foreach ($collection in @('sandboxes','snapshots')) {
    $url = "https://management.$location.azuredevcompute.io/subscriptions/$sub/resourceGroups/$rg/sandboxGroups/$sandboxGroup/$collection"
    az rest --method get --url $url --url-parameters api-version=2026-02-01-preview --resource https://dynamicsessions.io/ --only-show-errors -o json
    if ($LASTEXITCODE -ne 0) { throw "Unable to list $collection; stop and inspect." }
}
if (-not $sub -or $sub -match '[<>]' -or -not $rg -or $rg -match '[<>]' -or $rg -imatch '^rg-foundry(?:-|$)') { throw 'Unsafe subscription or resource group.' }
az group delete --subscription $sub --name $rg --yes --no-wait --only-show-errors
if ($LASTEXITCODE -ne 0) { throw 'RG delete request failed.' }
$deadline = (Get-Date).AddMinutes(10)
do {
    $exists = az group exists --subscription $sub --name $rg -o tsv
    if ($LASTEXITCODE -ne 0) { throw 'RG existence check failed.' }
    if ($exists -eq 'false') { break }
    if ((Get-Date) -ge $deadline) { throw 'RG deletion not complete.' }
    Start-Sleep -Seconds 5
} while ($true)
az resource list --subscription $sub --query "[?resourceGroup=='$rg'].{type:type,name:name}" -o json
if ($LASTEXITCODE -ne 0) { throw 'Remaining resource lookup failed.' }
az role assignment list --subscription $sub --all --query "[?contains(scope, '$rg')].{scope:scope,role:roleDefinitionName}" -o json
if ($LASTEXITCODE -ne 0) { throw 'Remaining role lookup failed.' }
az rest --method get --url "https://management.azure.com/subscriptions/$sub/providers/Microsoft.App/locations/$location/usages?api-version=2025-01-01" --query 'value[?contains(name.value, `Session`) || contains(name.value, `Sandbox`)].{name:name.value,current:currentValue,limit:limit}' -o json
```

역할은 삭제된 scope에 직접 조회하지 않고 구독 목록에서 필터합니다. 세션/샌드박스 할당량은 **공유 구독의 전체 사용량**이므로 다른 작업이 있다면 0이 아닐 수 있습니다. 당시에는 리소스·역할 0건, `SessionPools 0/50`, `SandboxCores 0/300`으로 복귀했습니다. 이 값이 모든 환경의 목표나 한도를 뜻하지 않습니다.

기존 Foundry 설정은 유지하고 임시 필드만 비웁니다. 사전에 다른 리소스 설정을 사용했다면 아래 대신 자신의 비공개 원본을 복원한 뒤 두 opt-in을 false로 설정하세요. 기존 프로세스 override도 해제합니다.

```powershell
$content = Get-Content .env -Raw
foreach ($key in @('ENABLE_LIVE_AZURE','ENABLE_PROVISIONING')) {
    $content = [regex]::Replace($content, "(?m)^$key=[^\r\n]*", "$key=false")
}
foreach ($key in @('DYNAMIC_SESSIONS_RESOURCE_GROUP','DYNAMIC_SESSIONS_POOL_ENDPOINT','SANDBOX_RESOURCE_GROUP','SANDBOX_GROUP_NAME')) {
    $content = [regex]::Replace($content, "(?m)^$key=[^\r\n]*", "$key=")
}
[IO.File]::WriteAllText((Resolve-Path .env), $content, [Text.UTF8Encoding]::new($false))
Remove-Item Env:ENABLE_LIVE_AZURE, Env:ENABLE_PROVISIONING, Env:DYNAMIC_SESSIONS_POOL_ENDPOINT -ErrorAction SilentlyContinue
```

**잔여 리소스 0건은 청구 0원이 아닙니다.** Cost Management 집계는 지연될 수 있고 이전 실행·스냅샷 및 별도 Foundry 리소스 그룹의 추론 비용은 나중에 반영될 수 있습니다. 나중에 양 비용 범위를 확인하세요. [문제 해결](troubleshooting.md) · [보안](../SECURITY.md) · [Sandbox 비용](https://sandboxes.azure.com/docs/sandboxes/cost). 로케일이 없는 Sandbox 사이트와 일부 한국어 Learn 페이지는 영어로 표시될 수 있습니다.