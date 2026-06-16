// Podcaster infrastructure — ACA-only + Storage + OpenAI (TTS)
// Migrated from Function App to Azure Container Apps (#109).
targetScope = 'resourceGroup'

@description('Azure region for all resources (eastus2 required for gpt-4o-mini-tts + ACA).')
param location string = 'eastus2'

@description('Globally unique Storage Account name. Defaults to a deterministic safe name based on the resource group.')
@minLength(3)
@maxLength(24)
param storageAccountName string = 'podcaster${uniqueString(resourceGroup().id)}'

@description('Base name used to derive related resource names.')
@minLength(2)
@maxLength(35)
param baseName string = 'podcaster-${uniqueString(resourceGroup().id)}'

@description('Application Insights name.')
param appInsightsName string = '${baseName}-appi'

@description('Log Analytics workspace name.')
param logAnalyticsName string = '${baseName}-law'

@secure()
@description('Podcaster API key used by ACA for auth. Do not print this value.')
param podcasterApiKey string

@description('Private blob container used for generated podcaster artifacts.')
param storageContainerName string = 'podcaster-artifacts'

@description('Blob prefixes (relative to the artifacts container) holding auto-generated outputs that are safe to auto-expire.')
param autoExpireArtifactPrefixes array = [
  'jobs/'
  'bakeoff/'
]

@description('Days after which generated podcaster artifacts are auto-deleted by the Storage lifecycle policy.')
@minValue(1)
@maxValue(365)
param artifactRetentionDays int = 7

@description('Optional object ID of the GitHub Actions deployment service principal. When provided, it receives Storage Blob Data Contributor on the storage account for OIDC uploads.')
param deploymentPrincipalObjectId string = ''

@description('Azure OpenAI (Cognitive Services) account name. Must be globally unique within its subdomain.')
@minLength(2)
@maxLength(63)
param openAiAccountName string = '${baseName}-openai'

@description('Azure OpenAI account SKU.')
param openAiSkuName string = 'S0'

@description('OpenAI TTS model to deploy (provides the fable/alloy voices selected in #4).')
param ttsModelName string = 'gpt-4o-mini-tts'

@description('Version of the OpenAI TTS model deployment.')
param ttsModelVersion string = '2025-03-20'

@description('Deployment (alias) name for the TTS model.')
param ttsDeploymentName string = 'tts'

@description('SKU tier for the TTS model deployment. gpt-4o-mini-tts requires GlobalStandard.')
param ttsModelSkuName string = 'GlobalStandard'

@description('Provisioned capacity for the TTS model deployment.')
param ttsModelCapacity int = 1

@description('OpenAI chat model used to write the two-voice Claracle script (#60).')
param chatModelName string = 'gpt-4o-mini'

@description('Version of the OpenAI chat model deployment.')
param chatModelVersion string = '2024-07-18'

@description('Deployment (alias) name for the chat model.')
param chatDeploymentName string = 'chat'

@description('SKU tier for the chat model deployment.')
param chatModelSkuName string = 'GlobalStandard'

@description('Provisioned capacity for the chat model deployment.')
param chatModelCapacity int = 10

@description('TTS voice for host A of the Claracle conversation.')
param ttsVoiceHostA string = 'fable'

@description('TTS voice for host B of the Claracle conversation.')
param ttsVoiceHostB string = 'alloy'

@description('Container Apps managed environment name.')
param containerAppsEnvName string = '${baseName}-cae'

@description('Queue-triggered synthesis Container Apps Job name.')
param synthesisJobName string = '${baseName}-synth'

@description('User-assigned managed identity used by the synthesis job.')
param synthesisJobIdentityName string = '${baseName}-synthesis-id'

@description('Storage Queue carrying synthesis messages (job_id only; no secrets/PII).')
param synthesisQueueName string = 'synthesis-jobs'

@description('Synthesis container image (ffmpeg baked in, built by #77).')
param synthesisImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('Optional container registry login server for the synthesis image.')
param containerRegistryServer string = ''

@description('Deploy the HTTP API app for /api/generate (#131).')
param deployApiApp bool = true

@description('HTTP API container image (#131).')
param apiImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('API app name.')
param apiAppName string = '${baseName}-api'

@description('Spotify show ID for auto-publish (#182). Empty disables publishing.')
param spotifyShowId string = ''

@description('Whether Spotify publishing is enabled for runtime orchestration.')
param spotifyPublishEnabled string = 'false'

@secure()
@description('Spotify session cookie SP_DC for runtime publication.')
param spotifySessionCookieDc string = ''

@secure()
@description('Spotify session cookie SP_KEY for runtime publication.')
param spotifySessionCookieKey string = ''

@description('Whether reviewed jobs should auto-publish after synthesis.')
param podcastAutoPublish string = 'false'

@description('Deploy an Azure Container Registry for synthesis/API images (#129).')
param deployAcr bool = true

@description('ACR name (alphanumeric only). Defaults to a deterministic name based on the RG.')
@minLength(5)
@maxLength(50)
param acrName string = 'podcaster${uniqueString(resourceGroup().id)}'

var hasDeploymentPrincipalObjectId = !empty(deploymentPrincipalObjectId)
var openAiCustomSubDomain = toLower(openAiAccountName)
var openAiEndpoint = 'https://${openAiCustomSubDomain}.openai.azure.com/'
var acrLoginServer = deployAcr ? '${toLower(acrName)}.azurecr.io' : containerRegistryServer

// VNet + private endpoints for ACA ↔ Storage connectivity (#225).
module network 'modules/network.bicep' = {
  name: 'vnet-private-endpoints'
  params: {
    location: location
    vnetName: '${baseName}-vnet'
    storageAccountId: storage.id
    storageAccountName: storage.name
  }
  dependsOn: [
    storage
  ]
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Deny'
    }
    publicNetworkAccess: 'Disabled'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  name: 'default'
  parent: storage
}

resource artifactContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: storageContainerName
  parent: blobService
  properties: {
    publicAccess: 'None'
  }
}

resource lifecyclePolicy 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  name: 'default'
  parent: storage
  properties: {
    policy: {
      rules: [
        {
          name: 'expire-artifacts'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: [
                'blockBlob'
              ]
              prefixMatch: [for prefix in autoExpireArtifactPrefixes: '${storageContainerName}/${prefix}']
            }
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: artifactRetentionDays
                }
              }
            }
          }
        }
      ]
    }
  }
  dependsOn: [
    artifactContainer
  ]
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
  }
}

resource deploymentBlobDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (hasDeploymentPrincipalObjectId) {
  name: guid(storage.id, deploymentPrincipalObjectId, 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  scope: storage
  properties: {
    principalId: deploymentPrincipalObjectId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalType: 'ServicePrincipal'
  }
}

// Production audio synthesis runner (ACA-only architecture, #109): a queue-triggered ACA Job
// that owns ffmpeg + heavy synthesis. Always deployed as the primary compute resource.
module aca 'modules/aca.bicep' = {
  name: 'audio-synthesis-job'
  params: {
    location: location
    containerAppsEnvName: containerAppsEnvName
    synthesisJobName: synthesisJobName
    jobIdentityName: synthesisJobIdentityName
    storageAccountName: storage.name
    logAnalyticsWorkspaceName: workspace.name
    synthesisQueueName: synthesisQueueName
    storageContainerName: storageContainerName
    synthesisImage: synthesisImage
    containerRegistryServer: acrLoginServer
    openAiEndpoint: openAiEndpoint
    ttsDeploymentName: ttsDeploymentName
    chatDeploymentName: chatDeploymentName
    ttsVoiceHostA: ttsVoiceHostA
    ttsVoiceHostB: ttsVoiceHostB
    podcasterApiKey: podcasterApiKey
    spotifyPublishEnabled: spotifyPublishEnabled
    spotifyShowId: spotifyShowId
    spotifySessionCookieDc: spotifySessionCookieDc
    spotifySessionCookieKey: spotifySessionCookieKey
    podcastAutoPublish: podcastAutoPublish
    infrastructureSubnetId: network.outputs.acaSubnetId
  }
  dependsOn: [
    artifactContainer
  ]
}

// Production TTS provider (OpenAI, voices fable+alloy) — always deployed in ACA-only architecture.
module openAi 'modules/openai.bicep' = {
  name: 'openai-tts'
  params: {
    location: location
    openAiAccountName: openAiAccountName
    openAiCustomSubDomain: openAiCustomSubDomain
    openAiSkuName: openAiSkuName
    ttsModelName: ttsModelName
    ttsModelVersion: ttsModelVersion
    ttsDeploymentName: ttsDeploymentName
    ttsModelSkuName: ttsModelSkuName
    ttsModelCapacity: ttsModelCapacity
    chatModelName: chatModelName
    chatModelVersion: chatModelVersion
    chatDeploymentName: chatDeploymentName
    chatModelSkuName: chatModelSkuName
    chatModelCapacity: chatModelCapacity
    synthesisJobPrincipalId: aca.outputs.jobIdentityPrincipalId
    audioJobPrincipalId: ''
  }
}

// HTTP API front door for /api/generate (#131) — validates requests, stages artifacts,
// enqueues synthesis. Gated behind deployApiApp until the container registry is approved (#129).
module api 'modules/api.bicep' = if (deployApiApp) {
  name: 'http-api-app'
  params: {
    location: location
    containerAppsEnvId: aca.outputs.environmentId
    apiAppName: apiAppName
    identityId: aca.outputs.jobIdentityResourceId
    identityClientId: aca.outputs.jobIdentityClientId
    storageAccountName: storage.name
    storageContainerName: storageContainerName
    synthesisQueueName: synthesisQueueName
    podcasterApiKey: podcasterApiKey
    apiImage: apiImage
    containerRegistryServer: acrLoginServer
    openAiEndpoint: openAiEndpoint
    chatDeploymentName: chatDeploymentName
    spotifyPublishEnabled: spotifyPublishEnabled
    spotifyShowId: spotifyShowId
    spotifySessionCookieDc: spotifySessionCookieDc
    spotifySessionCookieKey: spotifySessionCookieKey
    podcastAutoPublish: podcastAutoPublish
  }
  dependsOn: [
    artifactContainer
  ]
}

// Azure Container Registry for synthesis + API images (#129). Operator approved ACR.
module acr 'modules/acr.bicep' = if (deployAcr) {
  name: 'container-registry'
  params: {
    location: location
    registryName: acrName
    synthesisPullPrincipalId: aca.outputs.jobIdentityPrincipalId
    pushPrincipalId: deploymentPrincipalObjectId
  }
}

output storageAccountName string = storage.name
output storageContainerName string = storageContainerName
output artifactRetentionDays int = artifactRetentionDays
output openAiEndpoint string = openAiEndpoint
output openAiAccountName string = openAiAccountName
output ttsDeploymentName string = ttsDeploymentName
output chatDeploymentName string = chatDeploymentName
output synthesisJobName string = aca.outputs.jobName
output containerAppsEnvName string = aca.outputs.environmentName
output synthesisQueueName string = aca.outputs.queueName
output synthesisJobIdentityClientId string = aca.outputs.jobIdentityClientId
output apiAppFqdn string = deployApiApp ? api!.outputs.apiAppFqdn : ''
output acrLoginServer string = deployAcr ? acr!.outputs.loginServer : ''
