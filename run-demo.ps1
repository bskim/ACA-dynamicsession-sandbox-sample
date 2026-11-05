[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('0', '1', '2', '3', '4', '5', '6', 'all', 'status')]
    [string]$Step,
    [switch]$Offline,
    [string]$ShellPoolName
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) {
    throw "Python venv not found at $py. Run: py -3.13 -m venv .venv"
}

function Get-DotEnvMap {
    $envFile = Join-Path $PSScriptRoot '.env'
    $map = @{}
    if (-not (Test-Path -LiteralPath $envFile)) {
        return $map
    }
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $val = $Matches[2].Trim()
            if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
                $val = $val.Substring(1, $val.Length - 2)
            }
            $map[$Matches[1]] = $val
        }
    }
    return $map
}

function Invoke-Checked([string]$Label, [scriptblock]$Command) {
    $global:LASTEXITCODE = 0
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE."
    }
}

$cfg = Get-DotEnvMap
$liveEnabled = (-not $Offline) -and (
    ($env:ENABLE_LIVE_AZURE -in @('true', '1', 'yes')) -or
    ($cfg['ENABLE_LIVE_AZURE'] -in @('true', '1', 'yes'))
)
$modeArgs = if ($liveEnabled) { @('--live') } else { @() }
$modeLabel = if ($liveEnabled) { 'LIVE Azure' } else { 'Offline Simulation' }

function Resolve-AzureContext {
    $sub = if ($env:AZURE_SUBSCRIPTION_ID) { $env:AZURE_SUBSCRIPTION_ID } else { $cfg['AZURE_SUBSCRIPTION_ID'] }
    if (-not $sub -or $sub -notmatch '^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$') {
        throw "Valid AZURE_SUBSCRIPTION_ID is required in .env or environment for live Azure operations."
    }
    $demoRg = if ($env:DYNAMIC_SESSIONS_RESOURCE_GROUP) {
        $env:DYNAMIC_SESSIONS_RESOURCE_GROUP
    } elseif ($cfg['DYNAMIC_SESSIONS_RESOURCE_GROUP']) {
        $cfg['DYNAMIC_SESSIONS_RESOURCE_GROUP']
    } elseif ($cfg['SANDBOX_RESOURCE_GROUP']) {
        $cfg['SANDBOX_RESOURCE_GROUP']
    } else {
        throw "Set DYNAMIC_SESSIONS_RESOURCE_GROUP or SANDBOX_RESOURCE_GROUP in .env."
    }
    if ($demoRg -imatch '^rg-foundry(?:-|$)') {
        throw "Refusing to operate on protected Foundry resource group: $demoRg"
    }
    return @{ SubscriptionId = $sub; ResourceGroup = $demoRg }
}

switch ($Step) {
    { $_ -in @('0', 'status') } {
        Write-Host "=== [Step 0] 로컬 환경 및 설정 상태 확인 (Mode: $modeLabel) ===" -ForegroundColor Cyan
        Invoke-Checked "Preflight check" { & (Join-Path $PSScriptRoot 'scripts\preflight.ps1') }
        if ($liveEnabled) {
            $ctx = Resolve-AzureContext
            Invoke-Checked "Azure resource list" {
                & az resource list --subscription $ctx.SubscriptionId -g $ctx.ResourceGroup --query '[].{Name:name, Type:type, Location:location}' -o table
            }
        }
    }
    '1' {
        Write-Host "`n=== [시나리오 1] Dynamic Sessions: Session ID 재사용(100 -> 101 -> 102) vs 다른 Session ID 격리 ($modeLabel) ===" -ForegroundColor Cyan
        if ($liveEnabled) {
            $suffix = (Get-Random -Minimum 100 -Maximum 999)
            $sessionA = "session-a-$suffix"
            $sessionB = "session-b-$suffix"
            Write-Host "`n[1-1] Session A ($sessionA) 최초 호출 (--step init): 메모리에 turn_counter=100, nonce 설정 및 파일 생성" -ForegroundColor Yellow
            Invoke-Checked "Scenario 1-1 (init Session A)" { & $py examples\dynamic_sessions\session_reuse.py --live --session-id $sessionA --step init }

            Write-Host "`n[1-2] Session A ($sessionA) 두 번째 호출 (--step reuse, 동일 ID): turn_counter가 101로 증가하고 파일 유지됨!" -ForegroundColor Green
            Invoke-Checked "Scenario 1-2 (reuse Session A #1)" { & $py examples\dynamic_sessions\session_reuse.py --live --session-id $sessionA --step reuse }

            Write-Host "`n[1-3] Session A ($sessionA) 세 번째 호출 (--step reuse, 동일 ID): turn_counter가 102로 계속 증가함!" -ForegroundColor Green
            Invoke-Checked "Scenario 1-3 (reuse Session A #2)" { & $py examples\dynamic_sessions\session_reuse.py --live --session-id $sessionA --step reuse }

            Write-Host "`n[1-4] Session B ($sessionB) 호출 (--step check, 다른 ID): 변수(False/null)와 파일(False)이 완전히 격리되어 보이지 않음!" -ForegroundColor Magenta
            Invoke-Checked "Scenario 1-4 (check Session B isolation)" { & $py examples\dynamic_sessions\session_reuse.py --live --session-id $sessionB --step check }
        } else {
            Invoke-Checked "Scenario 1 (session_reuse.py)" { & $py examples\dynamic_sessions\session_reuse.py }
        }
    }
    '2' {
        Write-Host "`n=== [시나리오 2] Dynamic Sessions: Foundry LLM 다중 턴 코딩 에이전트 (생성 -> 실패 관찰 -> 코드 수정 -> 호스트 검증 통과) ($modeLabel) ===" -ForegroundColor Cyan
        Write-Host "참고 파일: examples\dynamic_sessions\coding_agent.py (prompts 리스트에서 코딩 과제를 수정할 수 있습니다)" -ForegroundColor DarkGray
        Invoke-Checked "Scenario 2 (coding_agent.py)" { & $py examples\dynamic_sessions\coding_agent.py @modeArgs }
    }
    '3' {
        Write-Host "`n=== [시나리오 3] Dynamic Sessions: Shell 세션 풀 실행 ($modeLabel) ===" -ForegroundColor Cyan
        if ($liveEnabled) {
            $ctx = Resolve-AzureContext
            $shellEndpoint = $env:DYNAMIC_SESSIONS_SHELL_POOL_ENDPOINT
            if (-not $shellEndpoint) { $shellEndpoint = $cfg['DYNAMIC_SESSIONS_SHELL_POOL_ENDPOINT'] }
            if (-not $shellEndpoint) {
                $global:LASTEXITCODE = 0
                if ($ShellPoolName) {
                    $shellEndpoint = (& az resource show --subscription $ctx.SubscriptionId -g $ctx.ResourceGroup --resource-type Microsoft.App/sessionPools -n $ShellPoolName --api-version 2025-10-02-preview --query properties.poolManagementEndpoint -o tsv)
                } else {
                    $shellEndpoint = (& az resource list --subscription $ctx.SubscriptionId -g $ctx.ResourceGroup --resource-type Microsoft.App/sessionPools --query "[?contains(name, 'sh')].id | [0]" -o tsv)
                    if ($LASTEXITCODE -eq 0 -and $shellEndpoint) {
                        $shellEndpoint = (& az resource show --ids $shellEndpoint --api-version 2025-10-02-preview --query properties.poolManagementEndpoint -o tsv)
                    }
                }
                if ($LASTEXITCODE -ne 0 -or -not $shellEndpoint) {
                    throw "Failed to resolve Shell session pool endpoint in RG $($ctx.ResourceGroup). Pass -ShellPoolName <POOL_NAME> or set DYNAMIC_SESSIONS_SHELL_POOL_ENDPOINT."
                }
            }
            $oldEndpoint = $env:DYNAMIC_SESSIONS_POOL_ENDPOINT
            $env:DYNAMIC_SESSIONS_POOL_ENDPOINT = $shellEndpoint
            try {
                Invoke-Checked "Scenario 3 (shell_agent.py)" { & $py examples\dynamic_sessions\shell_agent.py --live }
            } finally {
                if ($null -eq $oldEndpoint) { Remove-Item Env:DYNAMIC_SESSIONS_POOL_ENDPOINT -ErrorAction SilentlyContinue }
                else { $env:DYNAMIC_SESSIONS_POOL_ENDPOINT = $oldEndpoint }
            }
        } else {
            Invoke-Checked "Scenario 3 (shell_agent.py)" { & $py examples\dynamic_sessions\shell_agent.py }
        }
    }
    '4' {
        Write-Host "`n=== [시나리오 4] ACA Sandbox: 외부 에이전트가 Sandbox 내 외부 CLI(git/jq/sqlite3/agent-tool) + RAM 도구 서버 호출 & Stop/Resume ($modeLabel) ===" -ForegroundColor Cyan
        Invoke-Checked "Scenario 4 (command_agent.py)" { & $py examples\sandbox\command_agent.py @modeArgs }
    }
    '5' {
        Write-Host "`n=== [시나리오 5] ACA Sandbox: Memory Snapshot vs. Disk Snapshot 비교 (설치 도구 보존 vs RAM 서버 보존 & 지연 시간) ($modeLabel) ===" -ForegroundColor Cyan
        Invoke-Checked "Scenario 5 (compare_snapshot_modes.py)" { & $py examples\sandbox\compare_snapshot_modes.py @modeArgs --iterations 1 --format markdown }
    }
    '6' {
        Write-Host "`n=== [시나리오 6] ACA Sandbox: 패키지 설치 + 프로세스 워밍업 상태의 명시적 Snapshot 생성 및 복제 ($modeLabel) ===" -ForegroundColor Cyan
        Invoke-Checked "Scenario 6 (snapshot_clone.py)" { & $py examples\sandbox\snapshot_clone.py @modeArgs }
    }
    'all' {
        foreach ($s in @('0', '1', '2', '3', '4', '5', '6')) {
            Invoke-Checked "Scenario $s" { & $PSCommandPath $s -Offline:$Offline -ShellPoolName $ShellPoolName }
        }
    }
}
