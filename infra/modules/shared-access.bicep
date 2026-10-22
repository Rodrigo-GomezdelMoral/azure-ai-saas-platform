param registryName string
param foundryAccountName string
param searchServiceName string
param registryGrants grant[]
param foundryGrants grant[]
param searchGrants grant[]

type grant = { principalId: string, role: string, principalType: 'ServicePrincipal' | 'User' }

var roles = {
  acrPush: '8311e382-0749-4cb8-b61a-304f252e45ec', acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  cognitiveServicesUser: 'a97b65f3-24c7-4388-baec-2e87135dc908', openAIUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  searchServiceContributor: '7ca78c08-252a-4471-8644-bb5ff32d4ba0', searchIndexDataContributor: '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
}

resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' existing = { name: registryName }
resource foundry 'Microsoft.CognitiveServices/accounts@2026-07-01' existing = { name: foundryAccountName }
resource search 'Microsoft.Search/searchServices@2025-05-01' existing = { name: searchServiceName }

resource registryAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for item in registryGrants: {
  name: guid(registry.id, item.principalId, roles[item.role]), scope: registry
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles[item.role]), principalId: item.principalId, principalType: item.principalType }
}]

resource foundryAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for item in foundryGrants: {
  name: guid(foundry.id, item.principalId, roles[item.role]), scope: foundry
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles[item.role]), principalId: item.principalId, principalType: item.principalType }
}]

resource searchAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for item in searchGrants: {
  name: guid(search.id, item.principalId, roles[item.role]), scope: search
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles[item.role]), principalId: item.principalId, principalType: item.principalType }
}]
