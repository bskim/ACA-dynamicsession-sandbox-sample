[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SubscriptionId,
    [Parameter(Mandatory = $true)][string]$ResourceGroupName,
    [Parameter(Mandatory = $true)][string]$PoolName,
    [Parameter(Mandatory = $true)][string]$Location,
    [ValidateSet('PythonLTS', 'Shell', 'NodeLTS')][string]$ContainerType = 'PythonLTS',
    [ValidateRange(1, 2147483647)][int]$CooldownPeriodInSeconds = 300,
    [ValidateRange(5, 600)][int]$MaxConcurrentSessions = 10,
    [ValidateRange(0, 2147483647)][int]$ReadySessionInstances = 0,
    [string]$ExecutorPrincipalObjectId,
    [ValidateSet('User', 'ServicePrincipal', 'Group')][string]$ExecutorPrincipalType = 'ServicePrincipal'
)
$ErrorActionPreference = 'Stop'
if ($ResourceGroupName -imatch '^rg-foundry(?:-|$)') { throw 'Foundry resource groups are protected.' }
if ($env:ENABLE_PROVISIONING -ne 'true') {
    throw 'Provisioning is disabled. Set ENABLE_PROVISIONING=true explicitly before running this script.'
}
if ($ReadySessionInstances -gt $MaxConcurrentSessions) { throw 'ReadySessionInstances cannot exceed MaxConcurrentSessions.' }
if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw 'Azure CLI is required.' }
$template = Join-Path $PSScriptRoot '..\infra\dynamic-sessions\main.bicep'
Write-Host 'Provisioning incurs Azure charges. No credentials are placed into the session pool.'
& az group create --subscription $SubscriptionId --name $ResourceGroupName --location $Location --output none
if ($LASTEXITCODE -ne 0) { throw 'Resource group provisioning failed.' }
& az deployment group create --subscription $SubscriptionId --resource-group $ResourceGroupName --template-file $template `
    --parameters "poolName=$PoolName" "location=$Location" "containerType=$ContainerType" `
    "cooldownPeriodInSeconds=$CooldownPeriodInSeconds" "maxConcurrentSessions=$MaxConcurrentSessions" "readySessionInstances=$ReadySessionInstances" --output none
if ($LASTEXITCODE -ne 0) { throw 'Session pool deployment failed.' }
$resourceId = (& az resource show --subscription $SubscriptionId --resource-group $ResourceGroupName --resource-type 'Microsoft.App/sessionPools' --name $PoolName --query id --output tsv)
if ($LASTEXITCODE -ne 0 -or -not $resourceId) { throw 'Could not resolve pool resource ID.' }
if ($ExecutorPrincipalObjectId) {
    & az role assignment create --subscription $SubscriptionId --assignee-object-id $ExecutorPrincipalObjectId `
        --assignee-principal-type $ExecutorPrincipalType --role 'Azure ContainerApps Session Executor' --scope $resourceId --output none
    if ($LASTEXITCODE -ne 0) { throw 'Session Executor role assignment failed. Check principal type and permission to assign roles.' }
    Write-Host 'Session Executor role assigned at pool scope; propagation may take time.'
} else {
    Write-Host 'Assign Azure ContainerApps Session Executor at pool scope to the runtime identity before use.'
    Write-Host 'Rerun with -ExecutorPrincipalObjectId and -ExecutorPrincipalType User for a signed-in user.'
}
Write-Host 'Retrieve poolManagementEndpoint from the deployment output or session pool resource into your private environment.'

