// Video generation runner (#324): a queue-triggered Azure Container Apps Job that consumes
// the video-jobs queue and drives the ffmpeg-backed video pipeline (podcaster.video.job_runner).
//
// Reuses the synthesis container image (ffmpeg already baked in) but overrides the entrypoint
// command to run the video job runner instead of the audio synthesis runner. It also reuses the
// synthesis job's user-assigned managed identity, which already holds Storage Blob Data
// Contributor and Storage Queue Data Contributor on the storage account, so no new role
// assignments are required.
//
// Identity-only data plane (Blob + Queue). No keys, tokens, or secrets logged.
targetScope = 'resourceGroup'

@description('Azure region for the Container Apps Job.')
param location string

@description('Resource ID of the existing Container Apps managed environment (shared with synthesis).')
param containerAppsEnvId string

@description('Queue-triggered video Container Apps Job name.')
param videoJobName string

@description('Resource ID of the user-assigned managed identity used by the video job (reused from synthesis).')
param jobIdentityResourceId string

@description('Client ID of the user-assigned managed identity (AZURE_CLIENT_ID for the runtime).')
param jobIdentityClientId string

@description('Existing Storage Account that holds artifacts and the video queue.')
param storageAccountName string

@description('Storage Queue that carries video-generation messages (job_id only; no secrets/PII).')
param videoQueueName string = 'video-jobs'

@description('Private blob container holding generated podcaster artifacts.')
param storageContainerName string = 'podcaster-artifacts'

@description('Video container image. Same image as synthesis (ffmpeg baked in); only the command differs.')
param videoImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('Optional container registry login server for the image. When set, the job pulls with its managed identity.')
param containerRegistryServer string = ''

@description('vCPU allocated to the video replica (4 cores needed for ffmpeg compose of 17+ segments).')
param jobCpu string = '4.0'

@description('Memory allocated to the video replica (video compose is ffmpeg-heavy).')
param jobMemory string = '8.0Gi'

@description('Replica timeout (seconds) sized for full video segment generation + ffmpeg compose + distribution. 90 min covers ~65 min typical run with headroom.')
@minValue(60)
@maxValue(172800)
param replicaTimeoutSeconds int = 5400

@description('Queue length per replica that triggers scaling (one message per episode).')
@minValue(1)
param queueLengthPerReplica int = 1

@description('Maximum concurrent video replicas. Bursty weekly duty cycle; scales to zero when idle.')
@minValue(1)
param maxExecutions int = 2

@description('Azure OpenAI endpoint (empty when OpenAI is not deployed).')
param openAiEndpoint string = ''

@description('Azure OpenAI chat deployment (alias) name.')
param chatDeploymentName string = ''

@secure()
@description('Spotify session cookie SP_DC for video upload.')
param spotifySessionCookieDc string = ''

@secure()
@description('Spotify session cookie SP_KEY for video upload.')
param spotifySessionCookieKey string = ''

@description('Spotify show ID for video upload target.')
param spotifyShowId string = ''

var storageDnsSuffix = environment().suffixes.storage
var hasContainerRegistry = !empty(containerRegistryServer)

resource videoJob 'Microsoft.App/jobs@2025-01-01' = {
  name: videoJobName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${jobIdentityResourceId}': {}
    }
  }
  properties: {
    environmentId: containerAppsEnvId
    configuration: {
      triggerType: 'Event'
      replicaTimeout: replicaTimeoutSeconds
      replicaRetryLimit: 1
      secrets: [
        {
          name: 'spotify-sp-dc'
          value: spotifySessionCookieDc
        }
        {
          name: 'spotify-sp-key'
          value: spotifySessionCookieKey
        }
      ]
      registries: hasContainerRegistry ? [
        {
          server: containerRegistryServer
          identity: jobIdentityResourceId
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
              name: 'video-queue'
              type: 'azure-queue'
              metadata: {
                accountName: storageAccountName
                queueName: videoQueueName
                queueLength: string(queueLengthPerReplica)
              }
              identity: jobIdentityResourceId
            }
          ]
        }
      }
    }
    template: {
      containers: [
        {
          name: 'video'
          image: videoImage
          // Override the synthesis image's default entrypoint to run the video job runner.
          command: [
            'python'
            '-m'
            'podcaster.video.job_runner'
          ]
          resources: {
            cpu: json(jobCpu)
            memory: jobMemory
          }
          env: [
            {
              name: 'AZURE_CLIENT_ID'
              value: jobIdentityClientId
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
              name: 'PODCASTER_VIDEO_QUEUE'
              value: videoQueueName
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: openAiEndpoint
            }
            {
              name: 'AZURE_OPENAI_CHAT_DEPLOYMENT'
              value: chatDeploymentName
            }
            {
              name: 'AZURE_OPENAI_AUTH_MODE'
              value: 'managed_identity'
            }
            {
              name: 'VIDEO_BLOB_ARCHIVE_ENABLED'
              value: 'true'
            }
            {
              name: 'VIDEO_SPOTIFY_UPLOAD_ENABLED'
              value: 'true'
            }
            {
              name: 'SPOTIFY_SHOW_ID'
              value: spotifyShowId
            }
            {
              name: 'SP_DC'
              secretRef: 'spotify-sp-dc'
            }
            {
              name: 'SP_KEY'
              secretRef: 'spotify-sp-key'
            }
          ]
        }
      ]
    }
  }
}

output jobName string = videoJob.name
output videoQueueName string = videoQueueName
