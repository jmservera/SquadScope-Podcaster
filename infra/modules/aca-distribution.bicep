// Provider distribution worker (#681): queue-triggered ACA Job that consumes
// distribution-jobs outbox identifiers and claims the durable outbox record.
// Provider mutation remains disabled by default until a canary explicitly turns
// DISTRIBUTION_WORKER_PROVIDER_MUTATION_ENABLED on. Actual media-bound human
// approval remains required even when this flag is enabled.
targetScope = 'resourceGroup'

@description('Azure region for the Container Apps Job.')
param location string

@description('Resource ID of the existing Container Apps managed environment.')
param containerAppsEnvId string

@description('Queue-triggered provider distribution Container Apps Job name.')
param distributionJobName string

@description('Resource ID of the user-assigned managed identity used by the distribution job.')
param jobIdentityResourceId string

@description('Client ID of the user-assigned managed identity (AZURE_CLIENT_ID for the runtime).')
param jobIdentityClientId string

@description('Existing Storage Account that holds artifacts and the distribution queue.')
param storageAccountName string

@description('Storage Queue carrying provider-distribution outbox identities only.')
param distributionQueueName string = 'distribution-jobs'

@description('Private blob container holding generated podcaster artifacts.')
param storageContainerName string = 'podcaster-artifacts'

@description('Distribution worker container image. Same image as synthesis; only the command differs.')
param distributionImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('Optional container registry login server for the image. When set, the job pulls with its managed identity.')
param containerRegistryServer string = ''

@description('Whether the worker may invoke mutating provider dispatch. Defaults false until canary.')
param providerMutationEnabled string = 'false'

@secure()
param spotifySessionCookieDc string = ''
@secure()
param spotifySessionCookieKey string = ''
param spotifyShowId string = ''
param spotifyVideoAllowLivePublish string = 'false'
param spotifyVideoPublishMode string = 'draft'
param videoYoutubeEnabled string = 'false'
param videoYoutubeCategoryId string = '28'
param videoYoutubePrivacy string = 'unlisted'
param videoYoutubePlaylistId string = ''
@secure()
param videoYoutubeClientId string = ''
@secure()
param videoYoutubeClientSecret string = ''
@secure()
param videoYoutubeRefreshToken string = ''

@description('vCPU allocated to a distribution worker replica.')
param jobCpu string = '1.0'

@description('Memory allocated to a distribution worker replica.')
param jobMemory string = '2.0Gi'

@description('ACA hard-kill timeout (seconds) for one distribution message.')
@minValue(60)
@maxValue(172800)
param replicaTimeoutSeconds int = 900

@description('Distribution queue receive visibility timeout (seconds). Kept above replicaTimeout so a live replica can delete with its original pop receipt after finalization.')
@minValue(60)
@maxValue(172800)
param distributionVisibilityTimeoutSeconds int = 960

@description('Queue length per replica that triggers scaling.')
@minValue(1)
param queueLengthPerReplica int = 1

@description('Maximum concurrent distribution worker replicas.')
@minValue(1)
@maxValue(10)
param maxExecutions int = 2

var storageDnsSuffix = environment().suffixes.storage
var hasContainerRegistry = !empty(containerRegistryServer)
var youtubeConfigured = videoYoutubeEnabled == 'true'
var spotifyConfigured = !empty(spotifySessionCookieDc) && !empty(spotifySessionCookieKey)
var youtubeSecrets = youtubeConfigured ? [
  { name: 'youtube-client-id', value: videoYoutubeClientId }
  { name: 'youtube-client-secret', value: videoYoutubeClientSecret }
  { name: 'youtube-refresh-token', value: videoYoutubeRefreshToken }
] : []
var spotifySecrets = spotifyConfigured ? [
  { name: 'spotify-sp-dc', value: spotifySessionCookieDc }
  { name: 'spotify-sp-key', value: spotifySessionCookieKey }
] : []
var youtubeEnvironment = youtubeConfigured ? [
  { name: 'VIDEO_YOUTUBE_CLIENT_ID', secretRef: 'youtube-client-id' }
  { name: 'VIDEO_YOUTUBE_CLIENT_SECRET', secretRef: 'youtube-client-secret' }
  { name: 'VIDEO_YOUTUBE_REFRESH_TOKEN', secretRef: 'youtube-refresh-token' }
] : []
var spotifyEnvironment = spotifyConfigured ? [
  { name: 'SP_DC', secretRef: 'spotify-sp-dc' }
  { name: 'SP_KEY', secretRef: 'spotify-sp-key' }
] : []

resource distributionJob 'Microsoft.App/jobs@2025-01-01' = {
  name: distributionJobName
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
      secrets: concat(youtubeSecrets, spotifySecrets)
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
              name: 'distribution-queue'
              type: 'azure-queue'
              metadata: {
                accountName: storageAccountName
                queueName: distributionQueueName
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
          name: 'distribution'
          image: distributionImage
          command: [
            'python'
            '-m'
            'podcaster.distribution_worker'
          ]
          args: [
            '--max-messages'
            '1'
            '--visibility-timeout'
            string(distributionVisibilityTimeoutSeconds)
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
              name: 'PODCASTER_DISTRIBUTION_QUEUE'
              value: distributionQueueName
            }
            {
              name: 'DISTRIBUTION_WORKER_PROVIDER_MUTATION_ENABLED'
              value: providerMutationEnabled
            }
            { name: 'VIDEO_YOUTUBE_ENABLED', value: videoYoutubeEnabled }
            { name: 'VIDEO_YOUTUBE_CATEGORY_ID', value: videoYoutubeCategoryId }
            { name: 'VIDEO_YOUTUBE_PRIVACY', value: videoYoutubePrivacy }
            { name: 'VIDEO_YOUTUBE_PLAYLIST_ID', value: videoYoutubePlaylistId }
            { name: 'VIDEO_SPOTIFY_UPLOAD_ENABLED', value: 'true' }
            { name: 'SPOTIFY_PUBLISH_ENABLED', value: 'false' }
            { name: 'SPOTIFY_SHOW_ID', value: spotifyShowId }
            { name: 'SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH', value: spotifyVideoAllowLivePublish }
            { name: 'SPOTIFY_VIDEO_PUBLISH_MODE', value: spotifyVideoPublishMode }
          ], youtubeEnvironment, spotifyEnvironment)
        }
      ]
    }
  }
}

output jobName string = distributionJob.name
