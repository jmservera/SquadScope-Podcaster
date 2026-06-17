// Management UI — static React SPA served by nginx (#264).
// Runs in the same Container Apps Environment as the API and synthesis job.
// Scales to zero when idle; external ingress on port 8080.
targetScope = 'resourceGroup'

@description('Azure region for the Container Apps App.')
param location string

@description('Container Apps managed environment resource ID.')
param containerAppsEnvId string

@description('ACA UI App name.')
param uiAppName string

@description('UI container image.')
param uiImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Optional container registry login server.')
param containerRegistryServer string = ''

@description('Optional managed identity resource ID for registry pull.')
param identityId string = ''

@description('MSAL client ID for Azure Entra ID authentication.')
param msalClientId string = ''

@description('MSAL authority URL.')
param msalAuthority string = ''

@description('API base URL that the UI calls (the API ACA FQDN).')
param apiBaseUrl string = ''

@secure()
@description('Simple auth username (#273).')
param uiAuthUsername string = ''

@secure()
@description('Simple auth password (#273).')
param uiAuthPassword string = ''

@secure()
@description('HMAC secret for UI auth JWTs (#273).')
param uiAuthSecret string = ''

@description('vCPU allocated to the UI app.')
param appCpu string = '0.25'

@description('Memory allocated to the UI app.')
param appMemory string = '0.5Gi'

@description('Minimum replicas (0 = scale to zero).')
@minValue(0)
param minReplicas int = 0

@description('Maximum replicas.')
@minValue(1)
param maxReplicas int = 2

var hasContainerRegistry = !empty(containerRegistryServer)
var hasIdentity = !empty(identityId)

resource uiApp 'Microsoft.App/containerApps@2025-01-01' = {
  name: uiAppName
  location: location
  identity: hasIdentity ? {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  } : {
    type: 'None'
  }
  properties: {
    environmentId: containerAppsEnvId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
      }
      registries: hasContainerRegistry && hasIdentity ? [
        {
          server: containerRegistryServer
          identity: identityId
        }
      ] : []
      secrets: [
        {
          name: 'ui-auth-username'
          value: uiAuthUsername
        }
        {
          name: 'ui-auth-password'
          value: uiAuthPassword
        }
        {
          name: 'ui-auth-secret'
          value: uiAuthSecret
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'ui'
          image: uiImage
          resources: {
            cpu: json(appCpu)
            memory: appMemory
          }
          env: [
            {
              name: 'VITE_MSAL_CLIENT_ID'
              value: msalClientId
            }
            {
              name: 'VITE_MSAL_AUTHORITY'
              value: msalAuthority
            }
            {
              name: 'VITE_API_BASE_URL'
              value: apiBaseUrl
            }
            {
              name: 'UI_AUTH_USERNAME'
              secretRef: 'ui-auth-username'
            }
            {
              name: 'UI_AUTH_PASSWORD'
              secretRef: 'ui-auth-password'
            }
            {
              name: 'UI_AUTH_SECRET'
              secretRef: 'ui-auth-secret'
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
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

@description('FQDN of the UI app.')
output uiAppFqdn string = uiApp.properties.configuration.ingress.fqdn
