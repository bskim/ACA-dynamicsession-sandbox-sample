[한국어](./how-to-deploy-and-run.md)

# Deployment, execution and cleanup How-To (Windows PowerShell)

This guide deploys PythonLTS, optional Shell, and Sandbox in a **dedicated test resource group** and removes them afterwards. Deployment, execution, waiting and Foundry inference may incur charges. Never modify or delete existing Foundry or other users' resources. Replace `<YOUR_...>` yourself; keep actual values, tokens and results private. The paths are independent: skip unneeded pools/groups and their variables and roles.

Six examples, TTL and three samples per mode were verified in real Azure on 2026-11-05. Fresh clone/venv installation, interactive sign-in, actual setup-wrapper deployment and all CLI live dispatches were not rerun then. The validated scope was subscription deployment referencing the Bicep sources and direct live execution of example files. APIs are preview and behavior depends on region, permissions and versions.

## Step 1: Prepare

You need Python 3.13 or newer, Git, Azure CLI, Bicep, an existing tool-calling Foundry deployment, and provisioning and role-assignment permissions. The runtime user also needs Foundry inference permissions. Select an installed Python version.

```powershell
git clone '<YOUR_PUBLIC_REPOSITORY_URL>'
Set-Location ACA-dynamicsession-sandbox-sample
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
az login
az account set --subscription '<YOUR_SUBSCRIPTION_ID>'
az account show --query '{subscription:id,state:state,tenant:tenantId}' -o json
az ad signed-in-user show --query id -o tsv

# Set in each new PowerShell; do not modify global CLI extensions.
$env:AZURE_EXTENSION_DIR = Join-Path (Get-Location) '.venv\az-extensions'
$env:AZURE_EXTENSION_USE_DYNAMIC_INSTALL = 'no'
az extension add --name containerapp --allow-preview true --yes
az extension list --query '[].{name:name,version:version}' -o json
az containerapp sessionpool create --help
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

The recorded environment was Python 3.14.7 / Azure CLI 2.91.0 / local containerapp extension 1.3.0b5 / sandbox SDK 0.1.0b4. The extension-install command can select a newer preview; verify its version and wire contract rather than assuming this run validates later versions. Verify Bicep availability with `az bicep version` and install it with `az bicep install` if missing.

Set your own `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`, `AZURE_LOCATION`, `FOUNDRY_OPENAI_BASE_URL=https://<YOUR_FOUNDRY_ACCOUNT>.openai.azure.com/openai/v1/`, existing `FOUNDRY_MODEL_DEPLOYMENT`, `FOUNDRY_API_MODE=chat_completions`, and `FOUNDRY_TOKEN_SCOPE=https://ai.azure.com/.default` in `.env`. The OpenAI inference URL is not the project endpoint. Replace or clear remaining `<...>` placeholders **including unused fields**. Never store secret keys or access tokens. `.env` is Git-ignored, not encrypted. Verify the local account and tenant; process environment variables override `.env`.

```powershell
.\.venv\Scripts\aca-agent-sample.exe preflight config-check
.\.venv\Scripts\python.exe -m pytest tests\unit -q
```

## Step 2: Azure infrastructure and RBAC

Use a new dedicated group. Stop if it already exists or ownership is uncertain. The provisioner needs `Microsoft.Authorization/roleAssignments/write`. Check regional [quota](https://sandboxes.azure.com/docs/sandboxes/limits), [session pools](https://learn.microsoft.com/en-us/azure/container-apps/session-pool), provider registration and regional availability.

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

Before the recorded run, `SessionPools` was `0/50` and `SandboxCores` was `0/300`; these are not your subscription limits. Do not attach a Data Disk volume for the Memory comparison.

### Option A: Direct Bicep deployment

```powershell
az deployment group create --subscription $sub -g $rg --name python-pool --template-file .\infra\dynamic-sessions\main.bicep --parameters "poolName=$pythonPool" "location=$location" containerType=PythonLTS readySessionInstances=0 maxConcurrentSessions=10 cooldownPeriodInSeconds=300 --query properties.provisioningState -o tsv
if ($LASTEXITCODE -ne 0) { throw 'Python pool deployment failed.' }
# Optional: separate pool for the Shell example
az deployment group create --subscription $sub -g $rg --name shell-pool --template-file .\infra\dynamic-sessions\main.bicep --parameters "poolName=$shellPool" "location=$location" containerType=Shell readySessionInstances=0 maxConcurrentSessions=5 cooldownPeriodInSeconds=300 --query properties.provisioningState -o tsv
if ($LASTEXITCODE -ne 0) { throw 'Shell pool deployment failed.' }
az deployment group create --subscription $sub -g $rg --name sandbox-group --template-file .\infra\sandbox\main.bicep --parameters "sandboxGroupName=$sandboxGroup" "location=$location" --query properties.provisioningState -o tsv
if ($LASTEXITCODE -ne 0) { throw 'Sandbox group deployment failed.' }
az resource list --subscription $sub -g $rg --query '[].{name:name,type:type,location:location}' -o json
```

Pool `maxConcurrentSessions` must be **5–600** and differs from preallocated `readySessionInstances`. Too many concurrent sessions may cause 429. The `Microsoft.App/sandboxGroups@2026-07-01` template contains `properties: {}`, required in the live deployment. A local `BCP081` type warning is separate from service acceptance. This configuration does not require a Managed Environment.

### Option B: Setup scripts (instead of A)

Run after creating the dedicated RG above. The Sandbox script is a dry run unless `-Execute` is specified. The scripts also assign the roles below, so do not repeat the role-creation commands from option A. Still perform scope/endpoint lookup and propagation checks in the next section.

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

### Data-plane roles and endpoints

Create role assignments for option A only. Role definition IDs are public built-in role IDs, not user identifiers. For a service principal, use its correct object ID and principal type instead of `User`.

```powershell
$pythonScope = "/subscriptions/$sub/resourceGroups/$rg/providers/Microsoft.App/sessionPools/$pythonPool"
$shellScope = "/subscriptions/$sub/resourceGroups/$rg/providers/Microsoft.App/sessionPools/$shellPool"
$sandboxScope = "/subscriptions/$sub/resourceGroups/$rg/providers/Microsoft.App/sandboxGroups/$sandboxGroup"
# Option A only: Azure ContainerApps Session Executor
foreach ($scope in @($pythonScope, $shellScope)) {
    az role assignment create --subscription $sub --assignee-object-id $userObjectId --assignee-principal-type User --role '0fb8eba5-a2bb-4abe-b1c1-49dfad359bb0' --scope $scope --output none
    if ($LASTEXITCODE -ne 0) { throw 'Session role assignment failed.' }
}
# Option A only: Container Apps SandboxGroup Data Owner
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

### Existing Foundry inference permission

The account-scoped `Cognitive Services OpenAI User` role is separate from the Session/Sandbox roles. Have the account administrator verify the correct runtime identity's existing or inherited inference permission. The recorded live run reused existing permission; it did not alter Foundry. The optional block below makes no assignment by default. Only enable it if the role is missing and an authorized administrator approves. Keep this account scope separate from the dedicated cleanup `$rg`; never delete the existing Foundry group. A role listing does not prove inference propagation: the model-backed live examples verify actual access.

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

### Map deployment outputs into `.env`

First verify the Sandbox endpoint matches your actual region. Preserve existing file content and replace only specified keys. Keep `DYNAMIC_SESSIONS_API_VERSION=2025-10-02-preview`, `DYNAMIC_SESSIONS_WIRE_PROFILE=containerapp-1.3.0b5`, Sandbox data-plane `2026-02-01-preview`, ARM `2026-07-01`, and both data-plane scopes `https://dynamicsessions.io/.default`. These differ from the Foundry scope.

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


If no active session ID exists, **after** mapping `.env`, generate an unpredictable ID and explicitly run the example once as follows, then perform the GET polling below. Investigate failures rather than automatically repeating requests with unknown outcomes. Keep the generated ID and example console output private.

```powershell
$existingSessionIdentifier = [guid]::NewGuid().ToString('N')
.\.venv\Scripts\python.exe .\examples\dynamic_sessions\session_reuse.py --live --session-id $existingSessionIdentifier
if ($LASTEXITCODE -ne 0) { throw 'Live session check failed; inspect the error before explicitly trying again.' }
```

### Verify data-plane propagation

A role listing or successful token issuance does not prove data-plane propagation. Use the following GET only for an **existing live** test session identifier. If there is none, explicitly try a single live run in step 3 and check success; do not automatically repeat an indeterminate POST. A new or expired identifier can produce 404 independently of RBAC. Check Sandbox access with the read-only list GET below or successful actual create/exec. Stop and investigate authentication/ID for 401/404 and other errors; poll only 403 within a deadline. The permissions needed to observe propagation may differ by configuration.

```powershell
# Use an existing, still-active identifier you own (after mapping .env below).
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

Windows `az.cmd` can interpret URL `&` as a command separator, so separate query parameters with `--url-parameters`. Do not automatically widen permissions on authentication errors.

## Step 3: Run offline and live scenarios

Run from the repository root. Offline uses fake backends only and incurs no Azure charges. Unit tests and installation still use local resources.

```powershell
$py = '.\.venv\Scripts\python.exe'
& $py .\examples\dynamic_sessions\session_reuse.py
& $py .\examples\dynamic_sessions\coding_agent.py
& $py .\examples\dynamic_sessions\shell_agent.py
& $py .\examples\sandbox\command_agent.py
& $py .\examples\sandbox\compare_snapshot_modes.py --iterations 3 --output-dir .internal\results\offline-snapshot-compare
& $py .\examples\sandbox\snapshot_clone.py
```

Live execution requires **both** `ENABLE_LIVE_AZURE=true` in `.env` and `--live`. Keep raw results and manifests private; never share session identifiers or nonces. The TTL experiment waits 330 seconds for a pool with a 300-second cooldown and can incur extra charges. Only Shell temporarily uses its separate endpoint.

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

The installed CLI can run the same examples. Apply the same endpoint override above to the Shell command. The recorded live evidence is from direct example-file execution, not every live CLI dispatch.

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

| What to check | Details |
| --- | --- |
| `reuse=passed`, counter 42, isolation `passed`; TTL experiment `state_lost` | [Dynamic Sessions](dynamic-sessions-en.md) |
| Coding creation, expected failure and repaired test pass; Shell explicit cwd twice | [Dynamic Sessions](dynamic-sessions-en.md) |
| Command agent's three tool calls and stop/resume; clone hash and isolation | [Sandbox](sandbox-en.md) |
| Both modes preserve files, Memory retains RAM, Disk restarts worker; stage latency | [Comparison experiment](snapshot-comparison-en.md) |

## Step 4: Cleanup and configuration restoration

Examples attempt to clean up their own sandboxes/snapshots. On failure, select that run's private manifest and inspect the dry run before deleting. Setup-created resources are not automatically included in a manifest. Accepted deletion requests are not the same as confirmed deletion.

```powershell
.\scripts\cleanup.ps1 -ManifestPath '.internal\runs\<YOUR_RUN_MANIFEST>.json'
.\scripts\cleanup.ps1 -ManifestPath '.internal\runs\<YOUR_RUN_MANIFEST>.json' -Execute -Confirm:$false
```

For a Session-only deployment, skip the Sandbox list loop below; do not query an undeployed group. Check the data-plane sandbox/snapshot lists before deleting the group. The following **permanently deletes the entire group created and owned for this exercise**. Never target the existing Foundry group. Review the actual `$rg` and `$sub` targets again before execution.

```powershell
# Data-plane lists before RG deletion; inspect private output for remaining IDs.
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

List roles at subscription scope and filter rather than querying a deleted resource scope. Session/sandbox quota is **shared subscription-wide**, so usage can remain nonzero due to other workloads. In the recorded run, resources and role assignments were zero and `SessionPools` returned to `0/50`, `SandboxCores` to `0/300`. Those are not targets or limits for every environment.

Keep the original Foundry settings and clear only temporary fields. If the file previously held other resource settings, restore your private original instead, then set both opt-ins to false. Remove process overrides too.

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

**Zero remaining resources does not mean zero charges.** Cost Management aggregation can be delayed; prior execution, snapshot and inference charges from a separate Foundry resource group can appear later. Check both cost scopes later. [Troubleshooting](troubleshooting-en.md) · [Security](../SECURITY-en.md) · [Sandbox costs](https://sandboxes.azure.com/docs/sandboxes/cost). The Sandbox site has no locale-specific path and some Korean Learn pages may display English.