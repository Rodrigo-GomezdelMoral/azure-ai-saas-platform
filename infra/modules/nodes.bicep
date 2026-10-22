targetScope = 'resourceGroup'

@description('Principals that read the cost of the nodes AKS bills in this resource group: the CI principal and the operator.')
param principalIds string[]
param role string

resource costReaders 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (id, i) in principalIds: {
  name: guid(resourceGroup().id, id, role)
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role), principalId: id, principalType: i == 0 ? 'ServicePrincipal' : 'User' }
}]
