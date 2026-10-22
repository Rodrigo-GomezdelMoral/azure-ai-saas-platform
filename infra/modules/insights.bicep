param common object
param publisherPrincipalIds string[]
param operatorObjectId string

var metricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${common.suffix}', location: common.location, tags: common.tags, kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: common.workspaceId, IngestionMode: 'LogAnalytics', DisableLocalAuth: true }
}

resource publishes 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (principal, i) in concat(publisherPrincipalIds, [operatorObjectId]): {
  name: guid(insights.id, principal, metricsPublisher), scope: insights
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', metricsPublisher), principalId: principal
    principalType: i < length(publisherPrincipalIds) ? 'ServicePrincipal' : 'User'
  }
}]

output connectionString string = insights.properties.ConnectionString
output id string = insights.id
