// VNet infrastructure for ACA ↔ Storage private connectivity (#225).
// Creates VNet, subnets (ACA infra + private endpoints), private endpoints
// for blob and queue, and the associated Private DNS zones.
targetScope = 'resourceGroup'

@description('Azure region for network resources.')
param location string

@description('VNet name.')
param vnetName string

@description('VNet address prefix.')
param vnetAddressPrefix string = '10.0.0.0/16'

@description('Subnet name for Container Apps Environment infrastructure.')
param acaSubnetName string = 'snet-aca'

@description('Address prefix for the ACA infrastructure subnet (minimum /23).')
param acaSubnetAddressPrefix string = '10.0.0.0/23'

@description('Subnet name for private endpoints.')
param peSubnetName string = 'snet-private-endpoints'

@description('Address prefix for the private endpoints subnet.')
param peSubnetAddressPrefix string = '10.0.2.0/24'

@description('Resource ID of the storage account to create private endpoints for.')
param storageAccountId string

@description('Name of the storage account (used for private endpoint naming).')
param storageAccountName string

// ---------- VNet + Subnets ----------

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
    subnets: [
      {
        name: acaSubnetName
        properties: {
          addressPrefix: acaSubnetAddressPrefix
          // No delegations — consumption-plan ACA does not support subnet delegations
          // (ManagedEnvironmentV1SubnetDelegationNotAllowed)
        }
      }
      {
        name: peSubnetName
        properties: {
          addressPrefix: peSubnetAddressPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

// ---------- Private DNS Zones ----------

resource blobDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.blob.${environment().suffixes.storage}'
  location: 'global'
}

resource queueDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.queue.${environment().suffixes.storage}'
  location: 'global'
}

resource blobDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  name: '${vnetName}-blob-link'
  parent: blobDnsZone
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}

resource queueDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  name: '${vnetName}-queue-link'
  parent: queueDnsZone
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}

// ---------- Private Endpoints ----------

resource blobPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: '${storageAccountName}-blob-pe'
  location: location
  properties: {
    subnet: {
      id: '${vnet.id}/subnets/${peSubnetName}'
    }
    privateLinkServiceConnections: [
      {
        name: '${storageAccountName}-blob'
        properties: {
          privateLinkServiceId: storageAccountId
          groupIds: [
            'blob'
          ]
        }
      }
    ]
  }
}

resource queuePrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: '${storageAccountName}-queue-pe'
  location: location
  properties: {
    subnet: {
      id: '${vnet.id}/subnets/${peSubnetName}'
    }
    privateLinkServiceConnections: [
      {
        name: '${storageAccountName}-queue'
        properties: {
          privateLinkServiceId: storageAccountId
          groupIds: [
            'queue'
          ]
        }
      }
    ]
  }
}

resource blobDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  name: 'blob-dns-zone-group'
  parent: blobPrivateEndpoint
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-blob'
        properties: {
          privateDnsZoneId: blobDnsZone.id
        }
      }
    ]
  }
}

resource queueDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  name: 'queue-dns-zone-group'
  parent: queuePrivateEndpoint
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-queue'
        properties: {
          privateDnsZoneId: queueDnsZone.id
        }
      }
    ]
  }
}

// ---------- Outputs ----------

output vnetId string = vnet.id
output vnetName string = vnet.name
output acaSubnetId string = '${vnet.id}/subnets/${acaSubnetName}'
output peSubnetId string = '${vnet.id}/subnets/${peSubnetName}'
