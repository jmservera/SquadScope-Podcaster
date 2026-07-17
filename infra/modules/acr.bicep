// Azure Container Registry for synthesis + API container images (#129).
// Basic SKU keeps costs low (~$5/mo); the managed identity pull is configured
// in the ACA modules via the registry server output.
//
// Private-by-default in VNet mode (#598): when deployVnet=true the registry
// disables its public endpoint and is reached over a private endpoint (wired in
// acr-private-endpoint.bicep). Private endpoints and Disabled public access are
// Premium-only features, so VNet mode forces the Premium SKU regardless of the
// requested skuName. This is a deliberate cost trade-off (~$50/mo Premium vs
// ~$5/mo Basic) accepted only on the opt-in production hardening path; the
// default (deployVnet=false, local dev/test) keeps Basic + public access.
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

@description('When true (VNet mode), disable the ACR public endpoint and force the Premium SKU so a private endpoint can be attached (#598).')
param deployVnet bool = false

// Private endpoints + Disabled public network access require the Premium SKU, so
// VNet mode overrides the requested SKU. Non-VNet deployments keep skuName.
var effectiveSkuName = deployVnet ? 'Premium' : skuName

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
    name: effectiveSkuName
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: deployVnet ? 'Disabled' : 'Enabled'
    policies: {
      retentionPolicy: {
        status: 'disabled'
      }
    }
  }
}

resource synthesisPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (hasSynthesisPull) {
  name: guid(subscription().subscriptionId, resourceGroup().id, registryName, synthesisPullPrincipalId, acrPullRoleId)
  scope: registry
  properties: {
    principalId: synthesisPullPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalType: 'ServicePrincipal'
  }
}

resource pushRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (hasPush) {
  name: guid(subscription().subscriptionId, resourceGroup().id, registryName, pushPrincipalId, acrPushRoleId)
  scope: registry
  properties: {
    principalId: pushPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPushRoleId)
    principalType: 'ServicePrincipal'
  }
}

output loginServer string = registry.properties.loginServer
output registryName string = registry.name
output registryId string = registry.id
