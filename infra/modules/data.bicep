param common object
param database string
param gatewayPrincipalId string
param functionPrincipalId string
param onboardPrincipalId string
param cicdPrincipalId string
param operatorObjectId string

var dataContributor = '00000000-0000-0000-0000-000000000002'
var dataReader = '00000000-0000-0000-0000-000000000001'
var cosmosOperator = '230815da-be43-4aae-9cb4-875f7bd000aa'
var busSender = '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
var busReceiver = '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
var busOwner = '090c5cfd-751d-490a-894a-3ce6f1109419'
var eventSender = 'd5a91429-5739-47e2-a06b-3470a27159e7'
var redisUsers = [{ name: 'gateway', id: gatewayPrincipalId }, { name: 'onboarding', id: onboardPrincipalId }, { name: 'operator', id: operatorObjectId }]
var cosmosGrants = [
  { id: gatewayPrincipalId, role: dataContributor }, { id: functionPrincipalId, role: dataContributor }, { id: onboardPrincipalId, role: dataContributor }
  { id: operatorObjectId, role: dataContributor }, { id: cicdPrincipalId, role: dataReader }
]
var operators = [{ id: onboardPrincipalId, type: 'ServicePrincipal' }, { id: operatorObjectId, type: 'User' }]
var busGrants = [{ id: gatewayPrincipalId, role: busSender }, { id: functionPrincipalId, role: busOwner }, { id: onboardPrincipalId, role: busReceiver }]
var containers = [
  { name: 'tenants', paths: ['/id'], kind: 'Hash' }, { name: 'credentials', paths: ['/tenantId'], kind: 'Hash' }, { name: 'usage', paths: ['/tenantId', '/month'], kind: 'MultiHash' }
]
var allMetrics = [{ category: 'AllMetrics', enabled: true }]
var allLogs = { workspaceId: common.workspaceId, logs: [{ categoryGroup: 'allLogs', enabled: true }], metrics: allMetrics }

resource redis 'Microsoft.Cache/redisEnterprise@2025-07-01' = {
  name: 'redis-${common.suffix}', location: common.location, tags: common.tags, sku: { name: 'Balanced_B0' }
  properties: { highAvailability: 'Enabled', minimumTlsVersion: '1.2', publicNetworkAccess: 'Enabled' }
}

resource redisDatabase 'Microsoft.Cache/redisEnterprise/databases@2025-07-01' = {
  parent: redis, name: 'default'
  properties: {
    clientProtocol: 'Encrypted', port: 10000, clusteringPolicy: 'EnterpriseCluster', evictionPolicy: 'NoEviction', accessKeysAuthentication: 'Disabled'
    modules: [{ name: 'RediSearch' }]
  }
}

@batchSize(1)
resource redisAccess 'Microsoft.Cache/redisEnterprise/databases/accessPolicyAssignments@2025-07-01' = [for user in redisUsers: {
  parent: redisDatabase, name: user.name, properties: { accessPolicyName: 'default', user: { objectId: user.id } }
}]

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2025-10-15' = {
  name: 'cosmos-${common.suffix}', location: common.location, tags: common.tags, kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard', capabilities: [{ name: 'EnableServerless' }], disableLocalAuth: true, enableFreeTier: false, minimalTlsVersion: 'Tls12'
    publicNetworkAccess: 'Enabled', consistencyPolicy: { defaultConsistencyLevel: 'Session' }, locations: [{ locationName: common.location, failoverPriority: 0, isZoneRedundant: false }]
  }
}

resource cosmosDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2025-10-15' = { parent: cosmos, name: database, properties: { resource: { id: database } } }

@batchSize(1)
resource cosmosContainers 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-10-15' = [for item in containers: {
  parent: cosmosDatabase, name: item.name
  properties: { resource: { id: item.name, partitionKey: { paths: item.paths, kind: item.kind, version: 2 } } }
}]

@batchSize(1)
resource cosmosAccess 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-10-15' = [for item in cosmosGrants: {
  parent: cosmos, name: guid(cosmos.id, item.id, item.role)
  properties: { roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${item.role}', principalId: item.id, scope: cosmos.id }
}]

resource cosmosControlPlane 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for item in operators: {
  name: guid(cosmos.id, item.id, cosmosOperator), scope: cosmos
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cosmosOperator), principalId: item.id, principalType: item.type }
}]

resource bus 'Microsoft.ServiceBus/namespaces@2026-01-01' = {
  #disable-next-line BCP334
  name: 'sb-${common.suffix}', location: common.location, tags: common.tags, sku: { name: 'Basic', tier: 'Basic' }
  properties: { disableLocalAuth: true, minimumTlsVersion: '1.2', publicNetworkAccess: 'Enabled' }
}

resource queues 'Microsoft.ServiceBus/namespaces/queues@2026-01-01' = [for name in ['usage', 'onboarding']: {
  parent: bus, name: name
  properties: { maxDeliveryCount: 10, lockDuration: 'PT1M', deadLetteringOnMessageExpiration: true, defaultMessageTimeToLive: 'P14D' }
}]

resource busAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for item in busGrants: {
  name: guid(bus.id, item.id, item.role), scope: bus
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', item.role), principalId: item.id, principalType: 'ServicePrincipal' }
}]

resource topic 'Microsoft.EventGrid/topics@2025-02-15' = {
  name: 'evgt-${common.suffix}', location: common.location, tags: common.tags
  properties: { inputSchema: 'CloudEventSchemaV1_0', disableLocalAuth: true, publicNetworkAccess: 'Enabled', minimumTlsVersionAllowed: '1.2' }
}

resource topicAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for item in concat(operators, [{ id: gatewayPrincipalId, type: 'ServicePrincipal' }]): {
  name: guid(topic.id, item.id, eventSender), scope: topic
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', eventSender), principalId: item.id, principalType: item.type }
}]

resource redisDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-workspace', scope: redis, properties: { workspaceId: common.workspaceId, metrics: allMetrics }
}

resource redisDatabaseDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-workspace', scope: redisDatabase, properties: { workspaceId: common.workspaceId, logs: [{ category: 'ConnectionEvents', enabled: true }] }
}

resource cosmosDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-workspace', scope: cosmos
  properties: { workspaceId: common.workspaceId, logs: [{ category: 'DataPlaneRequests', enabled: true }], metrics: [{ category: 'Requests', enabled: true }] }
}

resource busDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-workspace', scope: bus, properties: { workspaceId: common.workspaceId, logs: [{ category: 'OperationalLogs', enabled: true }], metrics: allMetrics }
}

resource topicDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = { name: 'to-workspace', scope: topic, properties: allLogs }

output redisHost string = redis.properties.hostName
output cosmosEndpoint string = cosmos.properties.documentEndpoint
output cosmosAccountId string = cosmos.id
output servicebusNamespace string = '${bus.name}.servicebus.windows.net'
output servicebusId string = bus.id
output eventgridEndpoint string = topic.properties.endpoint
