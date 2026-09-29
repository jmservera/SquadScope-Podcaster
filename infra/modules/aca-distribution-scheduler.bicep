targetScope = 'resourceGroup'

param location string
param containerAppsEnvId string
param schedulerJobName string
param jobIdentityResourceId string
param jobIdentityClientId string
param storageAccountName string
param distributionQueueName string = 'distribution-jobs'
param storageContainerName string = 'podcaster-artifacts'
param image string
param containerRegistryServer string = ''
param cronExpression string = '*/5 * * * *'

var storageDnsSuffix = environment().suffixes.storage
var hasContainerRegistry = !empty(containerRegistryServer)

resource schedulerJob 'Microsoft.App/jobs@2025-01-01' = {
  name: schedulerJobName
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
      triggerType: 'Schedule'
      replicaTimeout: 300
      replicaRetryLimit: 1
      registries: hasContainerRegistry ? [
        {
          server: containerRegistryServer
          identity: jobIdentityResourceId
        }
      ] : []
      scheduleTriggerConfig: {
        cronExpression: cronExpression
        parallelism: 1
        replicaCompletionCount: 1
      }
    }
    template: {
      containers: [
        {
          name: 'distribution-scheduler'
          image: image
          command: [
            'python'
            '-m'
            'podcaster.distribution_scheduler'
          ]
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
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
              name: 'PODCASTER_DISTRIBUTION_QUEUE'
              value: distributionQueueName
            }
          ]
        }
      ]
    }
  }
}

output jobName string = schedulerJob.name
