param location string
param tags object
param suffix string
param logAnalyticsWorkspaceId string
param publisherPrincipalIds string[]
param operatorObjectId string

var metricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${suffix}'
  location: location
  tags: tags
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: logAnalyticsWorkspaceId, IngestionMode: 'LogAnalytics', DisableLocalAuth: true }
}

resource publishers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (principal, i) in concat(publisherPrincipalIds, [operatorObjectId]): {
  name: guid(insights.id, principal, metricsPublisher)
  scope: insights
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', metricsPublisher), principalId: principal
    principalType: i < length(publisherPrincipalIds) ? 'ServicePrincipal' : 'User'
  }
}]

output connectionString string = insights.properties.ConnectionString
