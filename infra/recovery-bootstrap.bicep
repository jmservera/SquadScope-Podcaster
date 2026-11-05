// Isolated first stage: deploy only private recovery vault, PE/DNS, and unattached API identity.
targetScope = 'resourceGroup'

@description('Podcaster region; must match the existing private VNet.')
param location string = 'eastus2'

@description('Existing Podcaster VNet name; no VNet or subnet properties are redeployed.')
param existingVnetName string = 'podcaster-${uniqueString(resourceGroup().id)}-vnet'

@description('Dedicated API recovery identity name; not attached by this template.')
param recoveryIdentityName string = 'podcaster-${uniqueString(resourceGroup().id)}-api-recovery-id'

var vaultName = 'podrv${uniqueString(resourceGroup().id, 'recovery')}'

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' existing = {
  name: existingVnetName
}

resource peSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' existing = {
  parent: vnet
  name: 'snet-private-endpoints'
}

resource recoveryIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: recoveryIdentityName
  location: location
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  properties: {
    tenantId: tenant().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      bypass: 'None'
      defaultAction: 'Deny'
    }
  }
}

resource vaultDns 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
}

resource vaultDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: vaultDns
  name: '${existingVnetName}-recovery-vault'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}

resource vaultPe 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: '${vaultName}-pe'
  location: location
  properties: {
    subnet: {
      id: peSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: '${vaultName}-vault'
        properties: {
          privateLinkServiceId: vault.id
          groupIds: [
            'vault'
          ]
        }
      }
    ]
  }
}

resource vaultPeDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: vaultPe
  name: 'vault-dns'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'vault'
        properties: {
          privateDnsZoneId: vaultDns.id
        }
      }
    ]
  }
}

output vaultName string = vault.name
output identityResourceId string = recoveryIdentity.id
output identityClientId string = recoveryIdentity.properties.clientId
output identityPrincipalId string = recoveryIdentity.properties.principalId
