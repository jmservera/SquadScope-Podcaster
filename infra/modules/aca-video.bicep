// Video EDITOR / orchestrator (#324, scale-out #552/#565): a queue-triggered Azure Container
// Apps Job that consumes the video-jobs queue and drives the ffmpeg-backed video pipeline
// (podcaster.video.job_runner).
//
// PLANNED scale-out target (docs/scaleout-recorder-rfc.md): once the editor refactor (#563)
// lands, this job stops recording inline — it will plan, fan per-clip messages onto the
// video-clip-jobs queue (consumed by the recorder ACA Job, aca-recorder.bicep), block on the
// fan-in barrier, then run the unchanged download -> compose -> distribute -> cleanup path.
// Until #563 merges the runtime still records inline; this module ships the infra seam ahead of
// that (single-replica cap + clip queue env) so the topology is ready when the code lands.
//
// Reuses the synthesis container image (ffmpeg already baked in) but overrides the entrypoint
// command to run the video job runner instead of the audio synthesis runner. It also reuses the
// synthesis job's user-assigned managed identity, which already holds Storage Blob Data
// Contributor (granted at the storage-account scope in aca.bicep) and Storage Queue Data
// Contributor. Because the Blob role is account-scoped it automatically covers the new
// video-scratch container used for intermediate checkpoint/resume (#410) — no new role
// assignment is required.
//
// Capped at a single concurrent replica (maxExecutions = 1) so a redelivered video-jobs message
// can never spin up a second editor that double-publishes (RFC §6 single-publish).
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

@description('Storage Queue the editor fans per-clip recording messages onto for the recorder job (job_id + clip_index only).')
param videoClipQueueName string = 'video-clip-jobs'

@description('Private blob container holding generated podcaster artifacts.')
param storageContainerName string = 'podcaster-artifacts'

@description('Private blob container holding video pipeline intermediates for checkpoint/resume (#410).')
param videoScratchContainerName string = 'video-scratch'

@description('Video container image. Same image as synthesis (ffmpeg baked in); only the command differs.')
param videoImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('Optional container registry login server for the image. When set, the job pulls with its managed identity.')
param containerRegistryServer string = ''

@description('vCPU allocated to the video replica (4 cores needed for ffmpeg compose of 17+ segments).')
param jobCpu string = '4.0'

@description('Memory allocated to the video replica (video compose is ffmpeg-heavy).')
param jobMemory string = '8.0Gi'

@description('Replica timeout (seconds) sized for the editor: fan-in wait + ffmpeg compose + distribution. 90 min covers ~65 min typical run with headroom.')
@minValue(60)
@maxValue(172800)
param replicaTimeoutSeconds int = 5400

@description('video-jobs receive visibility timeout (seconds) the editor applies while it holds a job. Must be >= the editor worst-case runtime (fan-in wait + compose + publish) so the message is not redelivered to a second editor mid-run (RFC §8). Defaults to the replica timeout.')
@minValue(60)
@maxValue(172800)
param videoVisibilityTimeoutSeconds int = 5400

@description('Queue length per replica that triggers scaling (one message per episode).')
@minValue(1)
param queueLengthPerReplica int = 1

@description('Maximum concurrent video editor replicas. Capped at 1 so a redelivered video-jobs message cannot start a second editor that double-publishes (RFC §6 single-publish).')
@minValue(1)
@maxValue(1)
param maxExecutions int = 1

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

@description('Whether YouTube uploads are enabled for video distribution.')
param videoYoutubeEnabled string = 'false'

@description('Whether YouTube upload is a required delivery target (failure marks the job failed).')
param videoYoutubeRequired string = 'false'

@description('YouTube upload category id (default 28 = Science & Technology).')
param videoYoutubeCategoryId string = '28'

@description('YouTube upload privacy status (default unlisted draft).')
param videoYoutubePrivacy string = 'unlisted'

@secure()
@description('YouTube OAuth client ID for runtime token exchange.')
param videoYoutubeClientId string = ''

@secure()
@description('YouTube OAuth client secret for runtime token exchange.')
param videoYoutubeClientSecret string = ''

@secure()
@description('YouTube OAuth refresh token for runtime token exchange.')
param videoYoutubeRefreshToken string = ''

var storageDnsSuffix = environment().suffixes.storage
var hasContainerRegistry = !empty(containerRegistryServer)

// YouTube OAuth credentials are only wired when the upload feature is enabled.
// ACA rejects secrets with empty values, so emitting empty youtube-* secrets (and
// dangling secretRefs) when YouTube is disabled fails the deploy with
// ContainerAppSecretInvalid. Gate both the secret definitions and their env
// secretRefs on videoYoutubeEnabled so a disabled/unconfigured YouTube target
// omits them entirely. (Fixes deploy regression from #613.)
var youtubeConfigured = videoYoutubeEnabled == 'true'
var youtubeSecrets = youtubeConfigured ? [
  {
    name: 'youtube-client-id'
    value: videoYoutubeClientId
  }
  {
    name: 'youtube-client-secret'
    value: videoYoutubeClientSecret
  }
  {
    name: 'youtube-refresh-token'
    value: videoYoutubeRefreshToken
  }
] : []
var youtubeSecretEnv = youtubeConfigured ? [
  {
    name: 'VIDEO_YOUTUBE_CLIENT_ID'
    secretRef: 'youtube-client-id'
  }
  {
    name: 'VIDEO_YOUTUBE_CLIENT_SECRET'
    secretRef: 'youtube-client-secret'
  }
  {
    name: 'VIDEO_YOUTUBE_REFRESH_TOKEN'
    secretRef: 'youtube-refresh-token'
  }
] : []

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
      secrets: concat([
        {
          name: 'spotify-sp-dc'
          value: spotifySessionCookieDc
        }
        {
          name: 'spotify-sp-key'
          value: spotifySessionCookieKey
        }
      ], youtubeSecrets)
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
          env: concat([
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
              // Scratch container for video pipeline intermediates (#410). The job
              // checkpoints segment recordings, normalized clips, and the composed
              // video here under video-jobs/{job-id}/intermediates/ for resume.
              name: 'PODCASTER_VIDEO_SCRATCH_CONTAINER'
              value: videoScratchContainerName
            }
            {
              name: 'PODCASTER_VIDEO_QUEUE'
              value: videoQueueName
            }
            {
              // Queue the editor fans per-clip recording messages onto (#552/#565).
              name: 'PODCASTER_VIDEO_CLIP_QUEUE'
              value: videoClipQueueName
            }
            {
              // Visibility timeout the editor applies to its own video-jobs message while it
              // works, so the job is not redelivered to a second editor mid-run (RFC §8). The
              // editor refactor (#563) wires job_runner's receive call to honour this; until
              // then the value is inert (single-replica cap already prevents concurrent editors).
              name: 'PODCASTER_VIDEO_VISIBILITY_TIMEOUT'
              value: string(videoVisibilityTimeoutSeconds)
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
              name: 'VIDEO_YOUTUBE_ENABLED'
              value: videoYoutubeEnabled
            }
            {
              name: 'VIDEO_YOUTUBE_REQUIRED'
              value: videoYoutubeRequired
            }
            {
              name: 'VIDEO_YOUTUBE_CATEGORY_ID'
              value: videoYoutubeCategoryId
            }
            {
              name: 'VIDEO_YOUTUBE_PRIVACY'
              value: videoYoutubePrivacy
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
          ], youtubeSecretEnv)
        }
      ]
    }
  }
}

output jobName string = videoJob.name
output videoQueueName string = videoQueueName
