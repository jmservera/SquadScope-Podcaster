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

@description('Optional object ID of the GitHub Actions deployment service principal. When provided, it receives Storage Blob Data Contributor on the storage account for OIDC package uploads.')
param deploymentPrincipalObjectId string = ''

var hostingPlanName = '${functionAppName}-plan'
var hasDeploymentPrincipalObjectId = !empty(deploymentPrincipalObjectId)
var storageDnsSuffix = environment().suffixes.storage

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
      defaultAction: 'Deny'
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
      appSettings: [
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
      ]
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

output endpoint string = 'https://${functionApp.properties.defaultHostName}/api/generate'
output functionAppName string = functionApp.name
output storageAccountName string = storage.name
output storageContainerName string = storageContainerName
output packageContainerName string = packageContainerName
