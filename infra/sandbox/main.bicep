@description('Sandbox group name; setup-sandbox.ps1 also checks the ARM name pattern.')
@minLength(1)
@maxLength(32)
param sandboxGroupName string

param location string = resourceGroup().location
param tags object = {}

#disable-next-line BCP081
resource sandboxGroup 'Microsoft.App/sandboxGroups@2026-07-01' = {
  name: sandboxGroupName
  location: location
  tags: tags
  properties: {}
}

output sandboxGroupId string = sandboxGroup.id

