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
@description('Kubernetes minor version, the AKS default for the region when the cluster was designed.')
param kubernetesVersion string = '1.36'
@description('Sizes of the single system node, which also hosts the starter and business gateways at rest, and of the spot and enterprise nodes.')
param nodeSizes object = { system: 'Standard_D4as_v5', user: 'Standard_D2as_v5' }
@description('First day of the month the budget starts.')
param budgetStartDate string = utcNow('yyyy-MM-01')

var suffix = 'aisaas-prod-swc'
var tags = { workload: 'aisaas', env: 'prod', owner: 'portfolio', managedBy: 'bicep', repo: 'azure-ai-saas-platform', tenant: 'shared' }
var common = { location: location, tags: tags, suffix: suffix, workspaceId: landingZone.logAnalyticsWorkspaceId }
var identityNames = ['id-saasgw', 'id-saasfn', 'id-saasonboard']
var managedIdentityContributor = 'e40ec5ca-96e0-45a2-b4ff-59039f2c2b59'
var costManagementReader = '72fafb9e-0641-4937-9268-a91bfd8191a3'
var database = 'saas'

resource identities 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = [for name in identityNames: { name: name, location: location, tags: tags }]

resource onboardFederatesTenants 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(identities[0].id, identities[2].id, managedIdentityContributor), scope: identities[0]
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', managedIdentityContributor), principalId: identities[2].properties.principalId, principalType: 'ServicePrincipal' }
}

resource operatorFederatesTenants 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(identities[0].id, operatorObjectId, managedIdentityContributor), scope: identities[0]
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', managedIdentityContributor), principalId: operatorObjectId, principalType: 'User' }
}

resource chargebackReadsCosts 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, cicdPrincipalId, costManagementReader)
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', costManagementReader), principalId: cicdPrincipalId, principalType: 'ServicePrincipal' }
}

module insights 'modules/insights.bicep' = {
  name: 'insights'
  params: {
    common: common, operatorObjectId: operatorObjectId
    publisherPrincipalIds: [identities[0].properties.principalId, identities[1].properties.principalId, identities[2].properties.principalId]
  }
}

module vault 'modules/vault.bicep' = {
  name: 'vault'
  params: { common: common, createMode: vaultCreateMode, keyName: 'credential-signing', signerPrincipalId: identities[2].properties.principalId, operatorObjectId: operatorObjectId }
}

module cluster 'modules/cluster.bicep' = {
  name: 'cluster'
  params: { common: common, kubernetesVersion: kubernetesVersion, nodeSizes: nodeSizes, cicdPrincipalId: cicdPrincipalId, operatorObjectId: operatorObjectId }
}

module data 'modules/data.bicep' = {
  name: 'data'
  params: {
    common: common, database: database, cicdPrincipalId: cicdPrincipalId, operatorObjectId: operatorObjectId, gatewayPrincipalId: identities[0].properties.principalId
    functionPrincipalId: identities[1].properties.principalId, onboardPrincipalId: identities[2].properties.principalId
  }
}

module functions 'modules/functions.bicep' = {
  name: 'functions'
  params: {
    common: common, identityId: identities[1].id, identityClientId: identities[1].properties.clientId, identityPrincipalId: identities[1].properties.principalId, database: database, usageQueue: 'usage'
    appInsightsConnectionString: insights.outputs.connectionString, servicebusNamespace: data.outputs.servicebusNamespace, cosmosEndpoint: data.outputs.cosmosEndpoint
  }
}

module alerts 'modules/alerts.bicep' = {
  name: 'alerts'
  params: {
    common: common, actionGroupId: landingZone.actionGroupId, appInsightsId: insights.outputs.id, servicebusId: data.outputs.servicebusId, clusterId: cluster.outputs.clusterId
    budgetStartDate: budgetStartDate
  }
}

module federation 'modules/federation.bicep' = { name: 'federation', params: { issuer: cluster.outputs.oidcIssuerUrl }, dependsOn: [identities] }

module sharedAccess 'modules/shared-access.bicep' = {
  name: 'shared-access'
  scope: resourceGroup(landingZone.sharedResourceGroupName)
  params: {
    registryName: landingZone.registryName, foundryAccountName: landingZone.foundryAccountName, searchServiceName: landingZone.searchServiceName
    registryGrants: [
      { principalId: cicdPrincipalId, role: 'acrPush', principalType: 'ServicePrincipal' }, { principalId: cluster.outputs.kubeletPrincipalId, role: 'acrPull', principalType: 'ServicePrincipal' }
    ]
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
output clusterName string = cluster.outputs.clusterName
output oidcIssuerUrl string = cluster.outputs.oidcIssuerUrl
output redisHost string = data.outputs.redisHost
output cosmosEndpoint string = data.outputs.cosmosEndpoint
output servicebusNamespace string = data.outputs.servicebusNamespace
output eventgridEndpoint string = data.outputs.eventgridEndpoint
output functionAppName string = functions.outputs.functionAppName
output registryLoginServer string = landingZone.registryLoginServer
output foundryEndpoint string = landingZone.foundryEndpoint
output searchEndpoint string = landingZone.searchEndpoint
output embeddingDeployment string = landingZone.embeddingDeploymentName
