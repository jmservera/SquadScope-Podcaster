// Azure Container Registry private endpoint (#598). Split into its own module so
// it can run after both the network module (which owns the VNet, private-endpoint
// subnet and the privatelink.azurecr.io DNS zone) and the registry exist, without
// creating a dependency cycle (network -> acr -> aca -> network).
targetScope = 'resourceGroup'

@description('Azure region for the private endpoint.')
param location string

@description('Resource ID of the private-endpoint subnet (from the network module).')
param peSubnetId string

@description('Resource ID of the container registry.')
param registryId string

@description('Name of the container registry (used for private-endpoint naming).')
param registryName string

@description('Resource ID of the privatelink.azurecr.io private DNS zone (from the network module).')
param acrDnsZoneId string

// ACR private endpoints use the 'registry' group id. A single privatelink.azurecr.io
// DNS zone group resolves both the registry FQDN and its regional data endpoints.
resource acrPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: '${registryName}-acr-pe'
  location: location
  properties: {
    subnet: {
      id: peSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${registryName}-registry'
        properties: {
          privateLinkServiceId: registryId
          groupIds: [
            'registry'
          ]
        }
      }
    ]
  }
}

resource acrDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  name: 'acr-dns-zone-group'
  parent: acrPrivateEndpoint
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-azurecr'
        properties: {
          privateDnsZoneId: acrDnsZoneId
        }
      }
    ]
  }
}
