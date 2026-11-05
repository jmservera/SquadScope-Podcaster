// Separate post-custody stage: deploy only after the owner has created this named secret.
targetScope = 'resourceGroup'

@allowed([
  'post-owner-secret'
])
@description('Explicit post-custody activation; no default RBAC bootstrap.')
param activation string

@description('Existing dedicated recovery vault name, from recovery-bootstrap.bicep.')
param vaultName string = 'podrv${uniqueString(resourceGroup().id, 'recovery')}'

@description('Exact principal ID from recovery-bootstrap output; verify against the existing API recovery identity before deployment.')
param recoveryPrincipalId string

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: vaultName
}

resource operatorSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'video-recovery-operator-key'
}

var secretsUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')

resource exactSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (activation == 'post-owner-secret') {
  scope: operatorSecret
  name: guid(operatorSecret.id, recoveryPrincipalId, secretsUserRole)
  properties: {
    roleDefinitionId: secretsUserRole
    principalId: recoveryPrincipalId
    principalType: 'ServicePrincipal'
  }
}
