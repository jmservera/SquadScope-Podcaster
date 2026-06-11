// HOTFIX: isolate deploy/CI changes (branch squad/47-isolate-deploy-ci)
targetScope = 'resourceGroup'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Globally unique Storage Account name. Defaults to a deterministic safe name based on the resource group.')
@minLength(3)
@maxLength(24)
param storageAccountName string = 'podcaster${uniqueString(resourceGroup().id)}'

@description('Globally unique Function App name. Defaults to a deterministic safe name based on the resource group.')
@minLength(2)
@maxLength(35)
param functionAppName string = 'podcaster-${uniqueString(resourceGroup().id)}'

@description('Application Insights name.')
param appInsightsName string = '${functionAppName}-appi'

@description('Log Analytics workspace name.')
param logAnalyticsName string = '${functionAppName}-law'

@secure()
@description('Podcaster API key stored as a Function App setting. Do not print this value.')
param podcasterApiKey string

@description('Artifact base URL used by local/dev fallback when Azure Blob Storage is not configured.')
param artifactBaseUrl string = 'https://example.invalid/podcaster-stub'

@description('Private blob container used for generated podcaster artifacts.')
param storageContainerName string = 'podcaster-artifacts'

@description('Private blob container used by GitHub Actions to stage Function App run-from-package ZIPs.')
param packageContainerName string = 'function-packages'

@description('Blob prefixes (relative to the artifacts container) holding auto-generated outputs that are safe to auto-expire. Operator review artifacts under "review/" are intentionally omitted so the editorial review gate inputs (#93) are retained until sign-off.')
param autoExpireArtifactPrefixes array = [
  'jobs/'
  'bakeoff/'
]

@description('Days after which generated podcaster artifacts are auto-deleted by the Storage lifecycle policy. Matches the documented 7-day manifest expiry (expires_at / retention.cleanup_after).')
@minValue(1)
@maxValue(365)
param artifactRetentionDays int = 7

@description('Days after which stale run-from-package deploy ZIPs are auto-deleted by the Storage lifecycle policy. Old packages are not needed once a newer deploy supersedes them.')
@minValue(1)
@maxValue(365)
param packageRetentionDays int = 7

@description('Optional object ID of the GitHub Actions deployment service principal. When provided, it receives Storage Blob Data Contributor on the storage account for OIDC package uploads.')
param deploymentPrincipalObjectId string = ''

@description('Opt-in switch for the production Azure OpenAI TTS infrastructure (#30). Defaults to false so the storage + Function App deploy stays green in regions where the selected TTS model is unavailable. Set to true only in a region/SKU that supports the configured ttsModelName/chatModelName.')
param deployOpenAi bool = false

@description('Azure OpenAI (Cognitive Services) account name. Must be globally unique within its subdomain. Defaults follow the existing Function App naming convention.')
@minLength(2)
@maxLength(63)
param openAiAccountName string = '${functionAppName}-openai'

@description('Azure OpenAI account SKU.')
param openAiSkuName string = 'S0'

@description('OpenAI TTS model to deploy (provides the fable/alloy voices selected in #4). Availability is region-specific; pick a region that supports this model when deployOpenAi=true.')
param ttsModelName string = 'gpt-4o-mini-tts'

@description('Version of the OpenAI TTS model deployment.')
param ttsModelVersion string = '2025-03-20'

@description('Deployment (alias) name used by the Function App to reference the TTS model.')
param ttsDeploymentName string = 'tts'

@description('Provisioned capacity (thousands of tokens / requests per minute) for the TTS model deployment.')
param ttsModelCapacity int = 1

@description('OpenAI chat model used to write the two-voice Claracle script in the production generate path (#60).')
param chatModelName string = 'gpt-4o-mini'

@description('Version of the OpenAI chat model deployment.')
param chatModelVersion string = '2024-07-18'

@description('Deployment (alias) name used by the Function App to reference the chat model.')
param chatDeploymentName string = 'chat'

@description('Provisioned capacity (thousands of tokens per minute) for the chat model deployment.')
param chatModelCapacity int = 10

@description('TTS voice for host A of the Claracle conversation.')
param ttsVoiceHostA string = 'fable'

@description('TTS voice for host B of the Claracle conversation.')
param ttsVoiceHostB string = 'alloy'

@description('Opt-in switch for the production audio synthesis Azure Container Apps Job (#76, ADR 0001 Option C). Defaults to false so deploy stays a no-op until the operator approves the new Azure spend (#67). When true, provisions an ACA environment, a queue-triggered synthesis Job, the synthesis Storage Queue, and identity-only role assignments.')
param deployAudioJob bool = false

@description('Container Apps managed environment name for the synthesis job.')
param containerAppsEnvName string = '${functionAppName}-cae'

@description('Queue-triggered synthesis Container Apps Job name.')
param synthesisJobName string = '${functionAppName}-synthesis'

@description('User-assigned managed identity used by the synthesis job for identity-only data-plane access.')
param synthesisJobIdentityName string = '${functionAppName}-synthesis-id'

@description('Storage Queue carrying synthesis messages (job_id only; no secrets/PII).')
param synthesisQueueName string = 'synthesis-jobs'

@description('Synthesis container image (ffmpeg baked in, built by #77). Placeholder until the registry/image are approved.')
param synthesisImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('Optional container registry login server for the synthesis image (#77). Empty until approved.')
param containerRegistryServer string = ''

var hostingPlanName = '${functionAppName}-plan'
var hasDeploymentPrincipalObjectId = !empty(deploymentPrincipalObjectId)
var storageDnsSuffix = environment().suffixes.storage
var openAiCustomSubDomain = toLower(openAiAccountName)
// Deterministic Azure OpenAI endpoint derived from the custom subdomain so the Function App
// settings do not depend on the conditional OpenAI module (avoids a circular dependency).
var openAiEndpoint = deployOpenAi ? 'https://${openAiCustomSubDomain}.openai.azure.com/' : ''
// Function App reaches Azure OpenAI with its managed identity (Cognitive Services OpenAI User);
// account keys are never written to Function App settings, logs, or deployment outputs.
var openAiAppSettings = deployOpenAi ? [
  {
    name: 'AZURE_OPENAI_ENDPOINT'
    value: openAiEndpoint
  }
  {
    name: 'AZURE_OPENAI_TTS_DEPLOYMENT'
    value: ttsDeploymentName
  }
  {
    name: 'AZURE_OPENAI_CHAT_DEPLOYMENT'
    value: chatDeploymentName
  }
  {
    name: 'AZURE_OPENAI_TTS_VOICE_HOST_A'
    value: ttsVoiceHostA
  }
  {
    name: 'AZURE_OPENAI_TTS_VOICE_HOST_B'
    value: ttsVoiceHostB
  }
  {
    name: 'AZURE_OPENAI_AUTH_MODE'
    value: 'managed_identity'
  }
] : []

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
    publicNetworkAccess: 'Enabled'
    supportsHttpsTrafficOnly: true
  }
}

resource artifactContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${storage.name}/default/${storageContainerName}'
  properties: {
    publicAccess: 'None'
  }
}

resource packageContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${storage.name}/default/${packageContainerName}'
  properties: {
    publicAccess: 'None'
  }
}

// Auto-delete expired artifacts and stale deploy packages so the documented manifest
// retention contract (expires_at / cleanup_after) is enforced by storage, not just declared.
// The artifacts rule only targets auto-generated output prefixes (jobs/, bakeoff/). Operator
// review artifacts under "review/" are intentionally excluded (#93): Azure lifecycle filters
// cannot express exclusions, so the review gate inputs are protected by omitting their prefix.
resource lifecyclePolicy 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  name: '${storage.name}/default'
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
          name: 'expire-deploy-packages'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: [
                'blockBlob'
              ]
              prefixMatch: [
                '${packageContainerName}/'
              ]
            }
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: packageRetentionDays
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
    packageContainer
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

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: hostingPlanName
  location: location
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  kind: 'functionapp'
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      appSettings: concat([
        {
          name: 'AzureWebJobsStorage__blobServiceUri'
          value: 'https://${storage.name}.blob.${storageDnsSuffix}'
        }
        {
          name: 'AzureWebJobsStorage__queueServiceUri'
          value: 'https://${storage.name}.queue.${storageDnsSuffix}'
        }
        {
          name: 'AzureWebJobsStorage__tableServiceUri'
          value: 'https://${storage.name}.table.${storageDnsSuffix}'
        }
        {
          name: 'AzureWebJobsStorage__credential'
          value: 'managedidentity'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'AzureWebJobsFeatureFlags'
          value: 'EnableWorkerIndexing'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'PODCASTER_API_KEY'
          value: podcasterApiKey
        }
        {
          name: 'PODCASTER_ARTIFACT_BASE_URL'
          value: artifactBaseUrl
        }
        {
          name: 'PODCASTER_STORAGE_ACCOUNT_URL'
          value: 'https://${storage.name}.blob.${storageDnsSuffix}'
        }
        {
          name: 'PODCASTER_STORAGE_CONTAINER'
          value: storageContainerName
        }
      ], openAiAppSettings)
    }
  }
}

resource blobDataOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(functionApp.id, storage.id, 'Storage Blob Data Owner')
  scope: storage
  properties: {
    principalId: functionApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
    principalType: 'ServicePrincipal'
  }
  dependsOn: [
    artifactContainer
  ]
}

resource queueDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(functionApp.id, storage.id, 'Storage Queue Data Contributor')
  scope: storage
  properties: {
    principalId: functionApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
    principalType: 'ServicePrincipal'
  }
}

resource tableDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(functionApp.id, storage.id, 'Storage Table Data Contributor')
  scope: storage
  properties: {
    principalId: functionApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')
    principalType: 'ServicePrincipal'
  }
}

resource deploymentBlobDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (hasDeploymentPrincipalObjectId) {
  name: guid(storage.id, deploymentPrincipalObjectId, 'Deployment Storage Blob Data Contributor')
  scope: storage
  properties: {
    principalId: deploymentPrincipalObjectId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalType: 'ServicePrincipal'
  }
  dependsOn: [
    packageContainer
  ]
}

// Production TTS provider (OpenAI, voices fable+alloy) selected in #4. Opt-in via deployOpenAi
// so the core storage + Function App deploy stays green where the TTS model is unavailable.
module openAi 'modules/openai.bicep' = if (deployOpenAi) {
  name: 'openai-tts'
  params: {
    location: location
    openAiAccountName: openAiAccountName
    openAiCustomSubDomain: openAiCustomSubDomain
    openAiSkuName: openAiSkuName
    ttsModelName: ttsModelName
    ttsModelVersion: ttsModelVersion
    ttsDeploymentName: ttsDeploymentName
    ttsModelCapacity: ttsModelCapacity
    chatModelName: chatModelName
    chatModelVersion: chatModelVersion
    chatDeploymentName: chatDeploymentName
    chatModelCapacity: chatModelCapacity
    functionAppPrincipalId: functionApp.identity.principalId
    // Grant the synthesis job's identity Cognitive Services OpenAI User too, when both opt-ins are on.
    audioJobPrincipalId: deployAudioJob ? aca!.outputs.jobIdentityPrincipalId : ''
  }
}

// Production audio synthesis runner (ADR 0001, Option C): a queue-triggered ACA Job that owns
// ffmpeg + heavy synthesis. Opt-in via deployAudioJob so deploy stays a no-op until the operator
// approves the new Azure spend (#67). Kept in the deploy/infra lane, separate from squad upgrade.
module aca 'modules/aca.bicep' = if (deployAudioJob) {
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
    containerRegistryServer: containerRegistryServer
    openAiEndpoint: openAiEndpoint
    ttsDeploymentName: deployOpenAi ? ttsDeploymentName : ''
    chatDeploymentName: deployOpenAi ? chatDeploymentName : ''
    ttsVoiceHostA: ttsVoiceHostA
    ttsVoiceHostB: ttsVoiceHostB
  }
  dependsOn: [
    artifactContainer
  ]
}

output endpoint string = 'https://${functionApp.properties.defaultHostName}/api/generate'
output functionAppName string = functionApp.name
output functionAppPrincipalId string = functionApp.identity.principalId
output artifactContainerResourceId string = artifactContainer.id
output storageAccountName string = storage.name
output storageContainerName string = storageContainerName
output packageContainerName string = packageContainerName
output artifactRetentionDays int = artifactRetentionDays
output packageRetentionDays int = packageRetentionDays
output openAiDeployed bool = deployOpenAi
output openAiEndpoint string = openAiEndpoint
output openAiAccountName string = deployOpenAi ? openAiAccountName : ''
output ttsDeploymentName string = deployOpenAi ? ttsDeploymentName : ''
output chatDeploymentName string = deployOpenAi ? chatDeploymentName : ''
output audioJobDeployed bool = deployAudioJob
output synthesisJobName string = deployAudioJob ? aca!.outputs.jobName : ''
output containerAppsEnvName string = deployAudioJob ? aca!.outputs.environmentName : ''
output synthesisQueueName string = deployAudioJob ? aca!.outputs.queueName : ''
output synthesisJobIdentityClientId string = deployAudioJob ? aca!.outputs.jobIdentityClientId : ''
