// Production TTS provider (OpenAI) selected in #4: voices fable (host A) + alloy (host B).
// Deployed conditionally from main.bicep via `module openAi = if (deployOpenAi)`. Keeping the
// resources in a dedicated module lets them stay unconditional here, so static analysis
// (Checkov) can read their hardened configuration instead of skipping a conditional resource.
targetScope = 'resourceGroup'

@description('Azure region for the OpenAI account.')
param location string

@description('Azure OpenAI (Cognitive Services) account name.')
@minLength(2)
@maxLength(63)
param openAiAccountName string

@description('Custom subdomain required for Entra ID (managed identity) token auth.')
param openAiCustomSubDomain string

@description('Azure OpenAI account SKU.')
param openAiSkuName string = 'S0'

@description('OpenAI TTS model that provides the fable/alloy voices.')
param ttsModelName string

@description('Version of the OpenAI TTS model deployment.')
param ttsModelVersion string

@description('Deployment (alias) name for the TTS model.')
param ttsDeploymentName string

@description('SKU tier for the TTS model deployment.')
param ttsModelSkuName string = 'GlobalStandard'

@description('Provisioned capacity for the TTS model deployment.')
param ttsModelCapacity int = 1

@description('OpenAI chat model used to write the two-voice Claracle script.')
param chatModelName string

@description('Version of the OpenAI chat model deployment.')
param chatModelVersion string

@description('Deployment (alias) name for the chat model.')
param chatDeploymentName string

@description('SKU tier for the chat model deployment.')
param chatModelSkuName string = 'GlobalStandard'

@description('Provisioned capacity for the chat model deployment.')
param chatModelCapacity int = 10

@description('Synthesis job managed-identity principal that receives Cognitive Services OpenAI User.')
param synthesisJobPrincipalId string

@description('Optional synthesis job managed-identity principal that also receives Cognitive Services OpenAI User (#76). Empty when the audio job is not deployed.')
param audioJobPrincipalId string = ''

@description('Set to true to restore a soft-deleted account with the same name. Set to false for normal operation.')
param restoreAccount bool = false

@description('When true (VNet mode), the account is private-by-default: public network access is disabled and reached only via the private endpoint created in modules/openai-private-endpoint.bicep. When false (local dev/test), the public endpoint stays enabled for convenience. See #598.')
param deployVnet bool = false

var hasAudioJobPrincipal = !empty(audioJobPrincipalId)

resource openAiAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: openAiAccountName
  location: location
  kind: 'OpenAI'
  sku: {
    name: openAiSkuName
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    // customSubDomainName is required for Entra ID (managed identity) token auth.
    customSubDomainName: openAiCustomSubDomain
    restore: restoreAccount
    // ACA job authenticates with its managed identity only; account keys are disabled.
    disableLocalAuth: true
    // Private-by-default in VNet mode (#598): the ACA synthesis job runs inside the VNet
    // and reaches this account over the private endpoint (see
    // modules/openai-private-endpoint.bicep; network.bicep owns the VNet + DNS zone).
    // Public access stays enabled only for local dev/test (deployVnet=false).
    publicNetworkAccess: deployVnet ? 'Disabled' : 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: deployVnet ? 'Deny' : 'Allow'
    }
  }
}

resource ttsDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openAiAccount
  name: ttsDeploymentName
  sku: {
    name: ttsModelSkuName
    capacity: ttsModelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: ttsModelName
      version: ttsModelVersion
    }
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openAiAccount
  name: chatDeploymentName
  sku: {
    name: chatModelSkuName
    capacity: chatModelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: chatModelName
      version: chatModelVersion
    }
  }
  // Azure rejects parallel deployments on the same account; serialize after TTS.
  dependsOn: [
    ttsDeployment
  ]
}

// Synthesis job reaches Azure OpenAI with its managed identity instead of an account key.
resource openAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAiAccount.id, synthesisJobPrincipalId, '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
  scope: openAiAccount
  properties: {
    principalId: synthesisJobPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalType: 'ServicePrincipal'
  }
}

// The synthesis ACA Job (#76) also reaches Azure OpenAI TTS with its own managed identity.
resource audioJobOpenAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (hasAudioJobPrincipal) {
  name: guid(openAiAccount.id, audioJobPrincipalId, '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
  scope: openAiAccount
  properties: {
    principalId: audioJobPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalType: 'ServicePrincipal'
  }
}

output accountName string = openAiAccount.name
output accountId string = openAiAccount.id
output endpoint string = openAiAccount.properties.endpoint
output ttsDeploymentName string = ttsDeployment.name
output chatDeploymentName string = chatDeployment.name
