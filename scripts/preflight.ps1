[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
$extensionDir = Join-Path $root '.venv\az-extensions'

function Write-Check([string]$Name, [bool]$Available) {
    $state = if ($Available) { 'OK' } else { 'MISSING' }
    Write-Output ("{0}: {1}" -f $Name, $state)
}

Write-Check 'Python on PATH' ($null -ne (Get-Command python -ErrorAction SilentlyContinue))
Write-Check '.venv Python' (Test-Path -LiteralPath $venvPython -PathType Leaf)
$az = Get-Command az -ErrorAction SilentlyContinue
Write-Check 'Azure CLI' ($null -ne $az)
$containerappInstalled = $false
if ($az -and (Test-Path -LiteralPath $extensionDir -PathType Container)) {
    $oldDir = $env:AZURE_EXTENSION_DIR
    $oldDynamic = $env:AZURE_EXTENSION_USE_DYNAMIC_INSTALL
    try {
        $env:AZURE_EXTENSION_DIR = $extensionDir
        $env:AZURE_EXTENSION_USE_DYNAMIC_INSTALL = 'no'
        $json = & $az.Source extension list --output json 2>$null
        if ($LASTEXITCODE -eq 0) {
            $extensions = $json | ConvertFrom-Json
            $containerappInstalled = @($extensions | Where-Object { $_.name -eq 'containerapp' }).Count -gt 0
        }
    } catch { $containerappInstalled = $false }
    finally {
        $env:AZURE_EXTENSION_DIR = $oldDir
        $env:AZURE_EXTENSION_USE_DYNAMIC_INSTALL = $oldDynamic
    }
}
Write-Check 'Local containerapp extension' $containerappInstalled

$values = @{}
$envPath = Join-Path $root '.env'
if (Test-Path -LiteralPath $envPath -PathType Leaf) {
    foreach ($line in Get-Content -LiteralPath $envPath) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $values[$Matches[1]] = $Matches[2].Trim('"', "'")
        }
    }
}
$keys = @('AZURE_SUBSCRIPTION_ID', 'AZURE_TENANT_ID', 'FOUNDRY_OPENAI_BASE_URL', 'FOUNDRY_MODEL_DEPLOYMENT',
          'DYNAMIC_SESSIONS_RESOURCE_GROUP', 'DYNAMIC_SESSIONS_POOL_ENDPOINT', 'SANDBOX_RESOURCE_GROUP',
          'SANDBOX_GROUP_NAME', 'ENABLE_LIVE_AZURE', 'ENABLE_PROVISIONING')
foreach ($key in $keys) {
    $processValue = [Environment]::GetEnvironmentVariable($key, 'Process')
    $value = if ($null -ne $processValue) { $processValue } else { $values[$key] }
    $state = if ([string]::IsNullOrWhiteSpace($value)) { 'NOT SET' }
             elseif ($value -match '<[^<>]+>|\$\{[^}]+\}') { 'PLACEHOLDER' }
             else { 'SET' }
    Write-Output ("{0}: {1}" -f $key, $state)
}
