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

@description('Private blob container used for video pipeline intermediates (segment recordings, normalized clips, composed video) for checkpoint/resume (#410).')
param videoScratchContainerName string = 'video-scratch'

@description('Blob prefixes (relative to the artifacts container) holding auto-generated outputs that are safe to auto-expire.')
param autoExpireArtifactPrefixes array = [
  'jobs/'
  'bakeoff/'
]

@description('Days after which generated podcaster artifacts are auto-deleted by the Storage lifecycle policy.')
@minValue(1)
@maxValue(365)
param artifactRetentionDays int = 7

@description('Days after which video pipeline intermediates in the scratch container are auto-deleted (#410).')
@minValue(1)
@maxValue(365)
param videoScratchRetentionDays int = 7

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

@description('Queue-triggered video Container Apps Job name (#324).')
param videoJobName string = '${baseName}-video'

@description('Queue-triggered scale-out recorder Container Apps Job name (#552/#565).')
param videoRecorderJobName string = '${baseName}-recorder'

@description('User-assigned managed identity used by the synthesis job.')
param synthesisJobIdentityName string = '${baseName}-synthesis-id'

@description('Storage Queue carrying synthesis messages (job_id only; no secrets/PII).')
param synthesisQueueName string = 'synthesis-jobs'

@description('Storage Queue carrying video-generation messages (job_id only; no secrets/PII).')
param videoQueueName string = 'video-jobs'

@description('Storage Queue carrying per-clip recording messages (job_id + clip_index only; no secrets/PII).')
param videoClipQueueName string = 'video-clip-jobs'

@description('Whether the synthesis job enqueues a video-generation message after publishing audio (#324).')
param videoGenerationEnabled string = 'true'

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

@description('Spotify web player client ID (#302). Defaults to public Spotify web player ID.')
param spotifyClientId string = '05a1371ee5194c27860b3ff3ff3979d2'

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

@description('Deploy the Management UI app (#264).')
param deployUiApp bool = true

@description('UI container image (#264).')
param uiImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('UI app name.')
param uiAppName string = '${baseName}-ui'

@description('MSAL client ID for Azure Entra ID authentication.')
param msalClientId string = ''

@description('MSAL authority URL for Azure Entra ID authentication.')
param msalAuthority string = ''

@secure()
@description('Simple auth username for the UI when MSAL/Entra is not available (#273).')
param uiAuthUsername string = ''

@secure()
@description('Simple auth password for the UI when MSAL/Entra is not available (#273).')
param uiAuthPassword string = ''

@secure()
@description('HMAC secret for signing UI auth JWTs (#273).')
param uiAuthSecret string = ''

@description('Deploy VNet + private endpoints for ACA ↔ Storage connectivity. Requires environment recreation if enabling on an existing deployment (VNet integration is a create-time-only setting).')
param deployVnet bool = false

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
// NOTE: VNet integration is a create-time-only setting for Container Apps environments.
// Enabling deployVnet on an existing environment requires recreation
// (az containerapp env delete + redeploy). For new deployments, set deployVnet=true.
module network 'modules/network.bicep' = if (deployVnet) {
  name: 'vnet-private-endpoints'
  params: {
    location: location
    vnetName: '${baseName}-vnet'
    storageAccountId: storage.id
    storageAccountName: storage.name
  }
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
      defaultAction: deployVnet ? 'Deny' : 'Allow'
    }
    publicNetworkAccess: deployVnet ? 'Disabled' : 'Enabled'
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

// Scratch container for video pipeline intermediates (#410): segment recordings,
// normalized clips, and the composed video are checkpointed here under
// video-jobs/{job-id}/intermediates/ so a crashed job can resume and local disk
// only holds the file currently being processed. Auto-expired after 7 days.
resource videoScratchContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: videoScratchContainerName
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
        {
          // Auto-delete video pipeline intermediates after 7 days (#410). The job
          // deletes its own scratch blobs on successful publish; this policy is the
          // safety net for interrupted/abandoned jobs so scratch never accumulates.
          // The video-jobs/ prefix also covers the scale-out per-clip scratch
          // (video-jobs/{job_id}/clips/** and clipset.json, #552/#565), so abandoned
          // fan-out clips are reaped by the same backstop — no extra rule needed.
          name: 'expire-video-scratch'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: [
                'blockBlob'
              ]
              prefixMatch: [
                '${videoScratchContainerName}/video-jobs/'
              ]
            }
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: videoScratchRetentionDays
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
    videoScratchContainer
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
    videoQueueName: videoQueueName
    videoClipQueueName: videoClipQueueName
    videoGenerationEnabled: videoGenerationEnabled
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
    spotifyClientId: spotifyClientId
    spotifySessionCookieDc: spotifySessionCookieDc
    spotifySessionCookieKey: spotifySessionCookieKey
    podcastAutoPublish: podcastAutoPublish
    deployVnet: deployVnet
    infrastructureSubnetId: deployVnet ? network.outputs.acaSubnetId : ''
  }
  dependsOn: [
    artifactContainer
  ]
}

// Video generation runner (#324): a queue-triggered ACA Job consuming the video-jobs queue.
// Reuses the synthesis image (ffmpeg baked in) with a command override that runs the video job
// runner, and reuses the synthesis managed identity (already has Blob + Queue RBAC).
module acaVideo 'modules/aca-video.bicep' = {
  name: 'video-generation-job'
  params: {
    location: location
    containerAppsEnvId: aca.outputs.environmentId
    videoJobName: videoJobName
    jobIdentityResourceId: aca.outputs.jobIdentityResourceId
    jobIdentityClientId: aca.outputs.jobIdentityClientId
    storageAccountName: storage.name
    videoQueueName: aca.outputs.videoQueueName
    videoClipQueueName: aca.outputs.videoClipQueueName
    storageContainerName: storageContainerName
    videoScratchContainerName: videoScratchContainerName
    videoImage: synthesisImage
    containerRegistryServer: acrLoginServer
    openAiEndpoint: openAiEndpoint
    chatDeploymentName: chatDeploymentName
    spotifySessionCookieDc: spotifySessionCookieDc
    spotifySessionCookieKey: spotifySessionCookieKey
    spotifyShowId: spotifyShowId
  }
  dependsOn: [
    artifactContainer
    videoScratchContainer
  ]
}

// Scale-out video recorder (#552/#565): a queue-triggered ACA Job consuming the video-clip-jobs
// queue. Records exactly one clip per message and writes it to the shared video-scratch container.
// Reuses the synthesis image + managed identity (Blob + Queue RBAC already account-scoped) and
// fans out across up to 10 replicas, scaling to zero when the clip queue drains.
module acaRecorder 'modules/aca-recorder.bicep' = {
  name: 'video-recorder-job'
  params: {
    location: location
    containerAppsEnvId: aca.outputs.environmentId
    recorderJobName: videoRecorderJobName
    jobIdentityResourceId: aca.outputs.jobIdentityResourceId
    jobIdentityClientId: aca.outputs.jobIdentityClientId
    storageAccountName: storage.name
    videoClipQueueName: aca.outputs.videoClipQueueName
    storageContainerName: storageContainerName
    videoScratchContainerName: videoScratchContainerName
    recorderImage: synthesisImage
    containerRegistryServer: acrLoginServer
  }
  dependsOn: [
    artifactContainer
    videoScratchContainer
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
    spotifyClientId: spotifyClientId
    spotifySessionCookieDc: spotifySessionCookieDc
    spotifySessionCookieKey: spotifySessionCookieKey
    podcastAutoPublish: podcastAutoPublish
    uiAuthUsername: uiAuthUsername
    uiAuthPassword: uiAuthPassword
    uiAuthSecret: uiAuthSecret
  }
  dependsOn: [
    artifactContainer
  ]
}

// Management UI — static React SPA served by nginx (#264).
// Gated behind deployUiApp. Runs in the same Container Apps Environment.
module ui 'modules/ui.bicep' = if (deployUiApp) {
  name: 'management-ui-app'
  params: {
    location: location
    containerAppsEnvId: aca.outputs.environmentId
    uiAppName: uiAppName
    uiImage: uiImage
    containerRegistryServer: acrLoginServer
    identityId: aca.outputs.jobIdentityResourceId
    msalClientId: msalClientId
    msalAuthority: msalAuthority
    apiBaseUrl: deployApiApp ? 'https://${api!.outputs.apiAppFqdn}' : ''
  }
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
output videoScratchContainerName string = videoScratchContainerName
output artifactRetentionDays int = artifactRetentionDays
output openAiEndpoint string = openAiEndpoint
output openAiAccountName string = openAiAccountName
output ttsDeploymentName string = ttsDeploymentName
output chatDeploymentName string = chatDeploymentName
output synthesisJobName string = aca.outputs.jobName
output containerAppsEnvName string = aca.outputs.environmentName
output synthesisQueueName string = aca.outputs.queueName
output videoQueueName string = aca.outputs.videoQueueName
output videoJobName string = acaVideo.outputs.jobName
output videoRecorderJobName string = acaRecorder.outputs.jobName
output videoClipQueueName string = aca.outputs.videoClipQueueName
output synthesisJobIdentityClientId string = aca.outputs.jobIdentityClientId
output apiAppFqdn string = deployApiApp ? api!.outputs.apiAppFqdn : ''
output uiAppFqdn string = deployUiApp ? ui!.outputs.uiAppFqdn : ''
output acrLoginServer string = deployAcr ? acr!.outputs.loginServer : ''
