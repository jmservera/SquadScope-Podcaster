// HTTP API front door for /api/generate (#131).
// A lightweight ACA App with external HTTP ingress that validates requests,
// stages artifacts, enqueues synthesis messages to the Storage Queue, and returns
// the stable 202 response. Runs in the same Container Apps Environment as the
// synthesis job. Scales to zero when idle.
targetScope = 'resourceGroup'

@description('Azure region for the Container Apps App.')
param location string

@description('Container Apps managed environment resource ID.')
param containerAppsEnvId string

@description('ACA API App name.')
param apiAppName string

@description('User-assigned managed identity resource ID for Storage access.')
param identityId string

@description('User-assigned managed identity client ID.')
param identityClientId string

@description('Existing Storage Account name (for artifact staging + queue).')
param storageAccountName string

@description('Storage container name for artifacts.')
param storageContainerName string = 'podcaster-artifacts'

@description('Storage Queue for synthesis messages.')
param synthesisQueueName string = 'synthesis-jobs'

@secure()
@description('Podcaster API key for auth.')
param podcasterApiKey string

@description('API container image.')
param apiImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Optional container registry login server for the API image.')
param containerRegistryServer string = ''

@description('vCPU allocated to the API app.')
param appCpu string = '0.25'

@description('Memory allocated to the API app.')
param appMemory string = '0.5Gi'

@description('Minimum replicas (0 = scale to zero).')
@minValue(0)
param minReplicas int = 0

@description('Maximum replicas.')
@minValue(1)
param maxReplicas int = 2

var storageDnsSuffix = environment().suffixes.storage
var hasContainerRegistry = !empty(containerRegistryServer)

resource apiApp 'Microsoft.App/containerApps@2025-01-01' = {
  name: apiAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    environmentId: containerAppsEnvId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
      }
      registries: hasContainerRegistry ? [
        {
          server: containerRegistryServer
          identity: identityId
        }
      ] : []
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImage
          resources: {
            cpu: json(appCpu)
            memory: appMemory
          }
          env: [
            {
              name: 'AZURE_CLIENT_ID'
              value: identityClientId
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
              name: 'PODCASTER_API_KEY'
              value: podcasterApiKey
            }
            {
              name: 'PODCASTER_API_PORT'
              value: '8000'
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-scaler'
            http: {
              metadata: {
                concurrentRequests: '10'
              }
            }
          }
        ]
      }
    }
  }
}

@description('FQDN of the API app (used as PODCASTER_ENDPOINT).')
output apiAppFqdn string = apiApp.properties.configuration.ingress.fqdn
