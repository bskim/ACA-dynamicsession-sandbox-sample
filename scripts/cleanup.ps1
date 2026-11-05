<#
.SYNOPSIS
Deletes only resources recorded in an explicitly supplied private run manifest.
.DESCRIPTION
Run manifests are JSON files under .internal/runs, version 1. sandbox_ids and
snapshot_ids are data-plane IDs created in that run; session_pool_ids, if any,
are full ARM IDs for pools created in that same run. Never include a pre-existing
resource. Dry-run is the default. Group, subscription and Foundry resources are
never deleted. Delete requests may complete asynchronously.
#>
[CmdletBinding(SupportsShouldProcess)]
param([Parameter(Mandatory)][string]$ManifestPath, [switch]$Execute)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $PSScriptRoot) '.internal\runs'))
$path = (Resolve-Path -LiteralPath $ManifestPath).Path
if (-not $path.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
    [IO.Path]::GetExtension($path) -ine '.json') { throw 'Manifest must be inside .internal\runs.' }
$m = Get-Content -Raw -LiteralPath $path | ConvertFrom-Json
if ($m.version -ne 1 -or $m.run_id -notmatch '^[a-f0-9]{32}$') { throw 'Invalid run manifest.' }
if ($m.subscription_id -notmatch '^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$') { throw 'Invalid subscription.' }
if ($m.resource_group -imatch '^rg-foundry(?:-|$)') { throw 'The existing Foundry resource group is protected.' }
if ($m.resource_group -notmatch '^[A-Za-z0-9._()\-]{1,90}$' -or $m.sandbox_group -notmatch '^(?![.-])[A-Za-z0-9-]{1,32}$' -or $m.region -notmatch '^[a-zA-Z0-9]+$') { throw 'Invalid sandbox scope.' }
foreach ($id in @($m.sandbox_ids) + @($m.snapshot_ids)) {
    if ($id -notmatch '^[A-Za-z0-9_-]+$') { throw 'Invalid data-plane resource ID.' }
}
$poolPattern = '^/subscriptions/([a-fA-F0-9-]{36})/resourceGroups/([A-Za-z0-9._()\-]{1,90})/providers/Microsoft\.App/sessionPools/([A-Za-z0-9_-]+)$'
foreach ($id in @($m.session_pool_ids)) {
    if ($id -notmatch $poolPattern) { throw 'Invalid session pool ID.' }
    if ($Matches[1] -ine $m.subscription_id -or $Matches[2] -ine $m.resource_group -or $Matches[2] -imatch '^rg-foundry(?:-|$)') { throw 'Invalid or protected session pool ID.' }
}
$base = "https://management.$($m.region).azuredevcompute.io/subscriptions/$($m.subscription_id)/resourceGroups/$($m.resource_group)/sandboxGroups/$($m.sandbox_group)"
$items = @()
foreach ($id in @($m.sandbox_ids)) { $items += @{ Name = "sandbox $id"; Uri = "$base/sandboxes/${id}?api-version=2026-02-01-preview" } }
foreach ($id in @($m.snapshot_ids)) { $items += @{ Name = "snapshot $id"; Uri = "$base/snapshots/${id}?api-version=2026-02-01-preview" } }
foreach ($id in @($m.session_pool_ids)) { $items += @{ Name = "session pool $id"; ResourceId = $id } }
foreach ($item in $items) {
    Write-Output "Selected: $($item.Name)"
    if ($Execute -and $PSCmdlet.ShouldProcess($item.Name, 'Delete run-owned resource')) {
        if ($item.Uri) {
            & az rest --method delete --uri $item.Uri --resource 'https://dynamicsessions.io/' --subscription $m.subscription_id --only-show-errors --output none
        } else {
            & az resource delete --ids $item.ResourceId --subscription $m.subscription_id --only-show-errors --output none
        }
        if ($LASTEXITCODE -ne 0) { throw "Deletion request failed: $($item.Name). Manifest retained for retry." }
    }
}
if (-not $Execute) { Write-Output 'Dry run. Supply -Execute to send deletion requests.' }

