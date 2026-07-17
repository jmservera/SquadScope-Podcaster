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
          delegations: [
            {
              name: 'Microsoft.App.environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
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

// Azure OpenAI private DNS zone (#598). The private endpoint + zone group are
// created in a separate module (openai-private-endpoint.bicep) that runs after
// the OpenAI account exists, so the zone lives here but is consumed there.
resource openAiDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.openai.azure.com'
  location: 'global'
}

// Azure Container Registry private DNS zone (#598). The private endpoint + zone
// group are created in acr-private-endpoint.bicep after the registry exists; the
// zone lives here so it shares the VNet link lifecycle. A single
// privatelink.azurecr.io zone resolves both the registry FQDN and its regional
// data endpoints.
resource acrDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.azurecr.io'
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

resource openAiDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  name: '${vnetName}-openai-link'
  parent: openAiDnsZone
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}

resource acrDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  name: '${vnetName}-acr-link'
  parent: acrDnsZone
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
output openAiDnsZoneId string = openAiDnsZone.id
output acrDnsZoneId string = acrDnsZone.id
