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

@description('Deployment (alias) name used by the Function App to reference the TTS model.')
param ttsDeploymentName string

@description('SKU tier for the TTS model deployment.')
param ttsModelSkuName string = 'GlobalStandard'

@description('Provisioned capacity for the TTS model deployment.')
param ttsModelCapacity int = 1

@description('OpenAI chat model used to write the two-voice Claracle script.')
param chatModelName string

@description('Version of the OpenAI chat model deployment.')
param chatModelVersion string

@description('Deployment (alias) name used by the Function App to reference the chat model.')
param chatDeploymentName string

@description('SKU tier for the chat model deployment.')
param chatModelSkuName string = 'GlobalStandard'

@description('Provisioned capacity for the chat model deployment.')
param chatModelCapacity int = 10

@description('Function App system-assigned principal that receives Cognitive Services OpenAI User.')
param functionAppPrincipalId string

@description('Optional synthesis job managed-identity principal that also receives Cognitive Services OpenAI User (#76). Empty when the audio job is not deployed.')
param audioJobPrincipalId string = ''

@description('Set to true to restore a soft-deleted account with the same name. Set to false for normal operation.')
param restoreAccount bool = false

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
    // Function App authenticates with its managed identity only; account keys are disabled.
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
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

// Function App reaches Azure OpenAI with its managed identity instead of an account key.
resource openAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAiAccount.id, functionAppPrincipalId, 'Cognitive Services OpenAI User')
  scope: openAiAccount
  properties: {
    principalId: functionAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalType: 'ServicePrincipal'
  }
}

// The synthesis ACA Job (#76) also reaches Azure OpenAI TTS with its own managed identity.
resource audioJobOpenAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (hasAudioJobPrincipal) {
  name: guid(openAiAccount.id, audioJobPrincipalId, 'Synthesis Job Cognitive Services OpenAI User')
  scope: openAiAccount
  properties: {
    principalId: audioJobPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalType: 'ServicePrincipal'
  }
}

output accountName string = openAiAccount.name
output endpoint string = openAiAccount.properties.endpoint
output ttsDeploymentName string = ttsDeployment.name
output chatDeploymentName string = chatDeployment.name
