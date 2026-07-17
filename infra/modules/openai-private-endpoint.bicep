// Azure OpenAI private endpoint (#598). Split into its own module so it can run
// after both the network module (which owns the VNet, private-endpoint subnet and
// the privatelink.openai.azure.com DNS zone) and the OpenAI account exist, without
// creating a dependency cycle (network -> openAi -> aca -> network).
targetScope = 'resourceGroup'

@description('Azure region for the private endpoint.')
param location string

@description('Resource ID of the private-endpoint subnet (from the network module).')
param peSubnetId string

@description('Resource ID of the Azure OpenAI (Cognitive Services) account.')
param openAiAccountId string

@description('Name of the Azure OpenAI account (used for private-endpoint naming).')
param openAiAccountName string

@description('Resource ID of the privatelink.openai.azure.com private DNS zone (from the network module).')
param openAiDnsZoneId string

// Cognitive Services private endpoints use the 'account' group id.
resource openAiPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: '${openAiAccountName}-openai-pe'
  location: location
  properties: {
    subnet: {
      id: peSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${openAiAccountName}-account'
        properties: {
          privateLinkServiceId: openAiAccountId
          groupIds: [
            'account'
          ]
        }
      }
    ]
  }
}

resource openAiDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  name: 'openai-dns-zone-group'
  parent: openAiPrivateEndpoint
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-openai'
        properties: {
          privateDnsZoneId: openAiDnsZoneId
        }
      }
    ]
  }
}
