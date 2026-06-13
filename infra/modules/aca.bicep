// Production audio synthesis runner (ADR 0001, Option C) for #67/#76.
// A queue-triggered Azure Container Apps Job runs the ffmpeg-backed synthesis pipeline
// (episode.py). The HTTP API front door (#131) will be a separate ACA App with ingress.
// Kept in a dedicated module so main.bicep stays readable while the resources here are
// unconditional for static analysis (Checkov), mirroring modules/openai.bicep.
//
// Identity-only data plane: the job authenticates to Storage (Blob + Queue) and Azure
// OpenAI TTS with a user-assigned managed identity. No keys or connection strings are
// written to the job, the queue messages, logs, or deployment outputs.
targetScope = 'resourceGroup'

@description('Azure region for the Container Apps environment and job.')
param location string

@description('Container Apps managed environment name.')
param containerAppsEnvName string

@description('Queue-triggered synthesis Container Apps Job name.')
param synthesisJobName string

@description('User-assigned managed identity used by the synthesis job for identity-only data-plane access.')
param jobIdentityName string

@description('Existing Storage Account that holds artifacts and the synthesis queue.')
param storageAccountName string

@description('Existing Log Analytics workspace name used for Container Apps environment logs.')
param logAnalyticsWorkspaceName string

@description('Storage Queue that carries synthesis messages (job_id only; no secrets/PII).')
param synthesisQueueName string = 'synthesis-jobs'

@description('Private blob container holding generated podcaster artifacts.')
param storageContainerName string = 'podcaster-artifacts'

@description('Synthesis container image (ffmpeg baked in, built by #77). Defaults to a benign placeholder so the template is valid before the registry/image are approved.')
param synthesisImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('Optional container registry login server for the synthesis image. When set, the job pulls with its managed identity. Leave empty until the registry is approved (#77).')
param containerRegistryServer string = ''

@description('vCPU allocated to the synthesis replica.')
param jobCpu string = '1.0'

@description('Memory allocated to the synthesis replica.')
param jobMemory string = '2.0Gi'

@description('Replica timeout (seconds) sized for a full episode synthesis + loudnorm + validate.')
@minValue(60)
@maxValue(172800)
param replicaTimeoutSeconds int = 1800

@description('Queue length per replica that triggers scaling (one message per episode).')
@minValue(1)
param queueLengthPerReplica int = 1

@description('Maximum concurrent synthesis replicas. Bursty weekly duty cycle; scales to zero when idle.')
@minValue(1)
param maxExecutions int = 3

@description('Azure OpenAI endpoint for TTS (empty when OpenAI is not deployed).')
param openAiEndpoint string = ''

@description('Azure OpenAI TTS deployment (alias) name.')
param ttsDeploymentName string = ''

@description('Azure OpenAI chat deployment (alias) name.')
param chatDeploymentName string = ''

@secure()
@description('Podcaster API key for auth.')
param podcasterApiKey string = ''

@description('TTS voice for host A.')
param ttsVoiceHostA string = 'fable'

@description('TTS voice for host B.')
param ttsVoiceHostB string = 'alloy'

@description('Spotify show ID for auto-publish (#182). Empty disables publishing in the container.')
param spotifyShowId string = ''

var storageDnsSuffix = environment().suffixes.storage
var hasContainerRegistry = !empty(containerRegistryServer)

// Built-in role definition IDs.
var blobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
// Storage Queue Data Contributor: lets the job's identity both read queue length (KEDA scaler)
// and process (peek/get/delete) synthesis messages. #80 (Hermes) reviews whether this can be
// tightened to Queue Data Message Processor + Reader.
var queueDataContributorRoleId = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'

resource jobIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: jobIdentityName
  location: location
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  name: 'default'
  parent: storage
}

resource artifactContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  name: storageContainerName
  parent: blobService
}

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  name: 'default'
  parent: storage
}

resource synthesisQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  name: synthesisQueueName
  parent: queueService
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource managedEnv 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: containerAppsEnvName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
  }
}

resource synthesisJob 'Microsoft.App/jobs@2025-01-01' = {
  name: synthesisJobName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${jobIdentity.id}': {}
    }
  }
  properties: {
    environmentId: managedEnv.id
    configuration: {
      triggerType: 'Event'
      replicaTimeout: replicaTimeoutSeconds
      replicaRetryLimit: 1
      registries: hasContainerRegistry ? [
        {
          server: containerRegistryServer
          identity: jobIdentity.id
        }
      ] : []
      eventTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
        scale: {
          minExecutions: 0
          maxExecutions: maxExecutions
          pollingInterval: 30
          rules: [
            {
              name: 'synthesis-queue'
              type: 'azure-queue'
              metadata: {
                accountName: storageAccountName
                queueName: synthesisQueueName
                queueLength: string(queueLengthPerReplica)
              }
              identity: jobIdentity.id
            }
          ]
        }
      }
    }
    template: {
      containers: [
        {
          name: 'synthesis'
          image: synthesisImage
          resources: {
            cpu: json(jobCpu)
            memory: jobMemory
          }
          env: [
            {
              name: 'AZURE_CLIENT_ID'
              value: jobIdentity.properties.clientId
            }
            {
              name: 'PODCASTER_STORAGE_ACCOUNT_URL'
              value: 'https://${storageAccountName}.blob.${storageDnsSuffix}'
            }
            {
              name: 'PODCASTER_STORAGE_QUEUE_URL'
              value: 'https://${storageAccountName}.queue.${storageDnsSuffix}'
            }
            {
              name: 'PODCASTER_STORAGE_CONTAINER'
              value: storageContainerName
            }
            {
              name: 'PODCASTER_SYNTHESIS_QUEUE'
              value: synthesisQueueName
            }
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
            {
              name: 'PODCASTER_API_KEY'
              value: podcasterApiKey
            }
            {
              name: 'SPOTIFY_SHOW_ID'
              value: spotifyShowId
            }
          ]
        }
      ]
    }
  }
}

// Storage Blob Data Contributor at account level — container-scoped RBAC is unreliable
// for data-plane calls via IMDS tokens in ACA. Account scope is still least-privilege
// relative to the subscription/RG.
resource jobBlobDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, jobIdentity.id, 'Synthesis Job Storage Blob Data Contributor')
  scope: storage
  properties: {
    principalId: jobIdentity.properties.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobDataContributorRoleId)
    principalType: 'ServicePrincipal'
  }
}

// Storage Queue Data Contributor at account level — same reasoning as blob above.
resource jobQueueDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, jobIdentity.id, 'Synthesis Job Storage Queue Data Contributor')
  scope: storage
  properties: {
    principalId: jobIdentity.properties.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', queueDataContributorRoleId)
    principalType: 'ServicePrincipal'
  }
}

output jobName string = synthesisJob.name
output environmentName string = managedEnv.name
output environmentId string = managedEnv.id
output queueName string = synthesisQueueName
output jobIdentityName string = jobIdentity.name
output jobIdentityPrincipalId string = jobIdentity.properties.principalId
output jobIdentityClientId string = jobIdentity.properties.clientId
output jobIdentityResourceId string = jobIdentity.id
