// Scale-out video recorder (#552/#565): a queue-triggered Azure Container Apps Job that
// consumes the video-clip-jobs queue and records exactly ONE clip per message
// (podcaster.video.recorder). This is the fan-out half of the recorder/editor split
// described in docs/scaleout-recorder-rfc.md (§3, §7, §8).
//
// Reuses the synthesis container image (Playwright + ffmpeg already baked in) with a command
// override, and reuses the synthesis job's user-assigned managed identity, which already holds
// Storage Blob Data Contributor + Storage Queue Data Contributor at the storage-account scope.
// Because the Blob role is account-scoped it automatically covers the existing video-scratch
// container the recorder writes clips/manifests into — no new role assignment is required.
//
// Smaller box than the editor: each replica records a single clip (~one Chromium context
// ≈1.5 GB), so CPU 2.0 / mem 4Gi, and KEDA fans the queue out across up to 10 replicas,
// scaling to zero when idle.
//
// Identity-only data plane (Blob + Queue). No keys, tokens, or secrets logged. The queue body
// carries only (job_id, clip_index) — never secrets/PII — so this job needs no secrets at all.
targetScope = 'resourceGroup'

@description('Azure region for the Container Apps Job.')
param location string

@description('Resource ID of the existing Container Apps managed environment (shared with synthesis).')
param containerAppsEnvId string

@description('Queue-triggered recorder Container Apps Job name.')
param recorderJobName string

@description('Resource ID of the user-assigned managed identity used by the recorder (reused from synthesis).')
param jobIdentityResourceId string

@description('Client ID of the user-assigned managed identity (AZURE_CLIENT_ID for the runtime).')
param jobIdentityClientId string

@description('Existing Storage Account that holds artifacts and the clip queue.')
param storageAccountName string

@description('Storage Queue that carries per-clip recording messages (job_id + clip_index only; no secrets/PII).')
param videoClipQueueName string = 'video-clip-jobs'

@description('Private blob container holding generated podcaster artifacts.')
param storageContainerName string = 'podcaster-artifacts'

@description('Private blob container holding the per-clip scratch outputs the recorder writes (reused from #410).')
param videoScratchContainerName string = 'video-scratch'

@description('Recorder container image. Same image as synthesis (Playwright + ffmpeg baked in); only the command differs.')
param recorderImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('Optional container registry login server for the image. When set, the job pulls with its managed identity.')
param containerRegistryServer string = ''

@description('vCPU allocated to a recorder replica. One Chromium context ≈1.5 GB; 2 cores records a single clip comfortably.')
param jobCpu string = '2.0'

@description('Memory allocated to a recorder replica (one Chromium clip recording).')
param jobMemory string = '4.0Gi'

@description('Replica timeout (seconds) — the per-clip record budget. Kept >= the clip queue visibility timeout (equality is valid) so a replica is not killed before its received message can either be deleted or fall back to visible (RFC §8).')
@minValue(60)
@maxValue(172800)
param replicaTimeoutSeconds int = 900

@description('Clip queue receive visibility timeout (seconds) the recorder applies to each received message. Must be <= replicaTimeout so a slow clip is not double-delivered mid-flight (RFC §8).')
@minValue(30)
@maxValue(172800)
param clipVisibilityTimeoutSeconds int = 900

@description('Queue length per replica that triggers scaling (one replica per pending clip).')
@minValue(1)
param queueLengthPerReplica int = 1

@description('Maximum concurrent recorder replicas. Fans recording out across up to 10 boxes; scales to zero when the queue drains (RFC §7).')
@minValue(1)
@maxValue(10)
param maxExecutions int = 10

var storageDnsSuffix = environment().suffixes.storage
var hasContainerRegistry = !empty(containerRegistryServer)

resource recorderJob 'Microsoft.App/jobs@2025-01-01' = {
  name: recorderJobName
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
              name: 'video-clip-queue'
              type: 'azure-queue'
              metadata: {
                accountName: storageAccountName
                queueName: videoClipQueueName
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
          name: 'recorder'
          image: recorderImage
          // Override the synthesis image's default entrypoint to run the clip recorder.
          command: [
            'python'
            '-m'
            'podcaster.video.recorder'
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
              // Per-clip scratch outputs (.webm + manifest.json) under
              // video-jobs/{job-id}/clips/ — the editor's fan-in barrier reads these.
              name: 'PODCASTER_VIDEO_SCRATCH_CONTAINER'
              value: videoScratchContainerName
            }
            {
              name: 'PODCASTER_VIDEO_CLIP_QUEUE'
              value: videoClipQueueName
            }
            {
              name: 'PODCASTER_CLIP_VISIBILITY_TIMEOUT'
              value: string(clipVisibilityTimeoutSeconds)
            }
            {
              name: 'AZURE_OPENAI_AUTH_MODE'
              value: 'managed_identity'
            }
          ]
        }
      ]
    }
  }
}

output jobName string = recorderJob.name
output videoClipQueueName string = videoClipQueueName
