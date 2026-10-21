targetScope = 'resourceGroup'

@export()
@description('The landing zone outputs this workload consumes, copied verbatim from azd env get-values of azure-ai-landing-zone.')
type landingZoneOutputs = {
  sharedResourceGroupName: string, registryName: string, registryLoginServer: string, logAnalyticsWorkspaceId: string, actionGroupId: string
  foundryAccountName: string, foundryEndpoint: string, searchServiceName: string, searchEndpoint: string, embeddingDeploymentName: string
}

@description('Region of every resource; defaults to the location of the existing resource group.')
param location string = resourceGroup().location
@description('Landing zone outputs; the shared registry, Foundry account, search service, workspace and action group are referenced as existing, never created here.')
param landingZone landingZoneOutputs
@description('Object id of the service principal behind the GitHub bootstrap app registration, which every workflow signs in as.')
param cicdPrincipalId string
@description('Object id of the engineer who onboards tenants and runs the load tests from a workstation.')
param operatorObjectId string
@description('default on the first deployment; recover after a destroy, because purge protection keeps the soft-deleted vault and its name.')
@allowed(['default', 'recover'])
param vaultCreateMode string = 'default'

var suffix = 'aisaas-prod-swc'
var tags = { workload: 'aisaas', env: 'prod', owner: 'portfolio', managedBy: 'bicep', repo: 'azure-ai-saas-platform', tenant: 'shared' }
var identityNames = ['id-saasgw', 'id-saasfn', 'id-saasonboard']
var managedIdentityContributor = 'e40ec5ca-96e0-45a2-b4ff-59039f2c2b59'
var costManagementReader = '72fafb9e-0641-4937-9268-a91bfd8191a3'

resource identities 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = [for name in identityNames: {
  name: name
  location: location
  tags: tags
}]

resource onboardFederatesTenants 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(identities[0].id, identities[2].id, managedIdentityContributor)
  scope: identities[0]
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', managedIdentityContributor), principalId: identities[2].properties.principalId, principalType: 'ServicePrincipal' }
}

resource operatorFederatesTenants 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(identities[0].id, operatorObjectId, managedIdentityContributor)
  scope: identities[0]
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', managedIdentityContributor), principalId: operatorObjectId, principalType: 'User' }
}

resource chargebackReadsCosts 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, cicdPrincipalId, costManagementReader)
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', costManagementReader), principalId: cicdPrincipalId, principalType: 'ServicePrincipal' }
}

module insights 'modules/insights.bicep' = {
  name: 'insights'
  params: {
    location: location, tags: tags, suffix: suffix, logAnalyticsWorkspaceId: landingZone.logAnalyticsWorkspaceId, operatorObjectId: operatorObjectId
    publisherPrincipalIds: [identities[0].properties.principalId, identities[1].properties.principalId, identities[2].properties.principalId]
  }
}

module vault 'modules/vault.bicep' = {
  name: 'vault'
  params: {
    location: location, tags: tags, suffix: suffix, logAnalyticsWorkspaceId: landingZone.logAnalyticsWorkspaceId, createMode: vaultCreateMode
    keyName: 'credential-signing', signerPrincipalId: identities[2].properties.principalId, operatorObjectId: operatorObjectId
  }
}

module sharedAccess 'modules/shared-access.bicep' = {
  name: 'shared-access'
  scope: resourceGroup(landingZone.sharedResourceGroupName)
  params: {
    registryName: landingZone.registryName, foundryAccountName: landingZone.foundryAccountName, searchServiceName: landingZone.searchServiceName
    registryGrants: [{ principalId: cicdPrincipalId, role: 'acrPush', principalType: 'ServicePrincipal' }]
    foundryGrants: [
      { principalId: identities[0].properties.principalId, role: 'cognitiveServicesUser', principalType: 'ServicePrincipal' }
      { principalId: identities[0].properties.principalId, role: 'openAIUser', principalType: 'ServicePrincipal' }
      { principalId: operatorObjectId, role: 'cognitiveServicesUser', principalType: 'User' }
    ]
    searchGrants: [
      { principalId: identities[2].properties.principalId, role: 'searchServiceContributor', principalType: 'ServicePrincipal' }
      { principalId: identities[2].properties.principalId, role: 'searchIndexDataContributor', principalType: 'ServicePrincipal' }
    ]
  }
}

output gatewayClientId string = identities[0].properties.clientId
output gatewayPrincipalId string = identities[0].properties.principalId
output functionClientId string = identities[1].properties.clientId
output onboardClientId string = identities[2].properties.clientId
output vaultUrl string = vault.outputs.vaultUrl
output appInsightsConnectionString string = insights.outputs.connectionString
