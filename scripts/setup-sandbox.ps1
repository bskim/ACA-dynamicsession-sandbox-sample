[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$SubscriptionId,
    [Parameter(Mandatory)][string]$ResourceGroup,
    [Parameter(Mandatory)][string]$SandboxGroupName,
    [Parameter(Mandatory)][string]$Location,
    [string]$AssigneeObjectId,
    [ValidateSet('User', 'ServicePrincipal', 'Group')][string]$AssigneePrincipalType = 'ServicePrincipal',
    [switch]$Execute
)
$ErrorActionPreference = 'Stop'
if ($ResourceGroup -imatch '^rg-foundry(?:-|$)') { throw 'The existing Foundry resource group is protected.' }
if ($SandboxGroupName -cnotmatch '^(?![.-])[A-Za-z0-9-]{1,32}$') { throw 'Invalid sandbox group name.' }
if ($SubscriptionId -notmatch '^[0-9a-fA-F-]{36}$') { throw 'A subscription GUID is required.' }
$template = Join-Path (Split-Path -Parent $PSScriptRoot) 'infra\sandbox\main.bicep'
$scope = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.App/sandboxGroups/$SandboxGroupName"
Write-Output "Target: $scope"
Write-Output 'Role needed: Container Apps SandboxGroup Data Owner at the sandbox group scope.'
if (-not $Execute) { Write-Output 'Dry run. Supply -Execute to deploy; an existing resource group is required.'; return }
if ($env:ENABLE_PROVISIONING -ne 'true') { throw 'Set ENABLE_PROVISIONING=true explicitly before deployment.' }
if ($PSCmdlet.ShouldProcess($scope, 'Deploy sandbox group')) {
    & az deployment group create --subscription $SubscriptionId --resource-group $ResourceGroup --template-file $template --parameters "sandboxGroupName=$SandboxGroupName" "location=$Location" --only-show-errors --output none
    if ($LASTEXITCODE -ne 0) { throw 'Sandbox group deployment failed.' }
}
if ($AssigneeObjectId -and $PSCmdlet.ShouldProcess($scope, 'Assign SandboxGroup Data Owner')) {
    & az role assignment create --subscription $SubscriptionId --assignee-object-id $AssigneeObjectId --assignee-principal-type $AssigneePrincipalType --role 'Container Apps SandboxGroup Data Owner' --scope $scope --only-show-errors --output none
    if ($LASTEXITCODE -ne 0) { throw 'Role assignment failed.' }
}

