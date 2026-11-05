targetScope = 'resourceGroup'

@description('Globally appropriate session pool name.')
param poolName string
param location string = resourceGroup().location

@allowed([
  'PythonLTS'
  'Shell'
  'NodeLTS'
])
param containerType string = 'PythonLTS'

@minValue(1)
param cooldownPeriodInSeconds int = 300

@minValue(5)
@maxValue(600)
param maxConcurrentSessions int = 10

@minValue(0)
param readySessionInstances int = 0

resource pool 'Microsoft.App/sessionPools@2025-10-02-preview' = {
  name: poolName
  location: location
  properties: {
    poolManagementType: 'Dynamic'
    containerType: containerType
    dynamicPoolConfiguration: {
      lifecycleConfiguration: {
        lifecycleType: 'Timed'
        cooldownPeriodInSeconds: cooldownPeriodInSeconds
      }
    }
    scaleConfiguration: {
      maxConcurrentSessions: maxConcurrentSessions
      readySessionInstances: readySessionInstances
    }
    sessionNetworkConfiguration: {
      status: 'EgressDisabled'
    }
  }
}

output poolManagementEndpoint string = pool.properties.poolManagementEndpoint
output poolResourceId string = pool.id



