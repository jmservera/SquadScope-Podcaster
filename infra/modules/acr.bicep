// Azure Container Registry for synthesis + API container images (#129).
// Basic SKU keeps costs low (~$5/mo); the managed identity pull is configured
// in the ACA modules via the registry server output.
targetScope = 'resourceGroup'

@description('Azure region for the container registry.')
param location string

@description('Globally unique ACR name (alphanumeric only, 5–50 chars).')
@minLength(5)
@maxLength(50)
param registryName string

@description('ACR SKU tier. Basic is sufficient for the weekly podcast duty cycle.')
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param skuName string = 'Basic'

@description('Principal ID of the synthesis job managed identity (granted AcrPull).')
param synthesisPullPrincipalId string = ''

@description('Principal ID of the GitHub Actions deployment SP (granted AcrPush for image publish).')
param pushPrincipalId string = ''

var hasSynthesisPull = !empty(synthesisPullPrincipalId)
var hasPush = !empty(pushPrincipalId)

// Built-in role: AcrPull (7f951dda-4ed3-4680-a7ca-43fe172d538d)
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
// Built-in role: AcrPush (8311e382-0749-4cb8-b61a-304f252e45ec)
var acrPushRoleId = '8311e382-0749-4cb8-b61a-304f252e45ec'

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: {
    name: skuName
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
    policies: {
      retentionPolicy: {
        status: 'disabled'
      }
    }
  }
}

resource synthesisPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (hasSynthesisPull) {
  name: guid(registry.id, synthesisPullPrincipalId, 'AcrPull')
  scope: registry
  properties: {
    principalId: synthesisPullPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalType: 'ServicePrincipal'
  }
}

resource pushRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (hasPush) {
  name: guid(registry.id, pushPrincipalId, 'AcrPush')
  scope: registry
  properties: {
    principalId: pushPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPushRoleId)
    principalType: 'ServicePrincipal'
  }
}

output loginServer string = registry.properties.loginServer
output registryName string = registry.name
