param common object
param identityId string
param identityClientId string
param identityPrincipalId string
param appInsightsConnectionString string
param servicebusNamespace string
param cosmosEndpoint string
param database string
param usageQueue string

var blobOwner = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var storageName = replace('st${common.suffix}fn', '-', '')
var settings = {
  AzureWebJobsStorage__accountName: storageName, AzureWebJobsStorage__credential: 'managedidentity', AzureWebJobsStorage__clientId: identityClientId
  APPLICATIONINSIGHTS_CONNECTION_STRING: appInsightsConnectionString, APPLICATIONINSIGHTS_AUTHENTICATION_STRING: 'Authorization=AAD;ClientId=${identityClientId}'
  ServiceBusConnection__fullyQualifiedNamespace: servicebusNamespace, ServiceBusConnection__credential: 'managedidentity', ServiceBusConnection__clientId: identityClientId
  AZURE_CLIENT_ID: identityClientId, COSMOS_ENDPOINT: cosmosEndpoint, COSMOS_DATABASE: database, USAGE_QUEUE: usageQueue
}

resource storage 'Microsoft.Storage/storageAccounts@2026-06-01' = {
  name: storageName, location: common.location, tags: common.tags, kind: 'StorageV2', sku: { name: 'Standard_LRS' }
  properties: { allowSharedKeyAccess: false, allowBlobPublicAccess: false, defaultToOAuthAuthentication: true, minimumTlsVersion: 'TLS1_2', supportsHttpsTrafficOnly: true }
}

resource deployments 'Microsoft.Storage/storageAccounts/blobServices/containers@2026-06-01' = { name: '${storage.name}/default/deployments', properties: {} }

resource hostStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, identityPrincipalId, blobOwner), scope: storage
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobOwner), principalId: identityPrincipalId, principalType: 'ServicePrincipal' }
}

resource plan 'Microsoft.Web/serverfarms@2025-03-01' = {
  name: 'plan-${common.suffix}', location: common.location, tags: common.tags, kind: 'functionapp', sku: { tier: 'FlexConsumption', name: 'FC1' }, properties: { reserved: true }
}

resource app 'Microsoft.Web/sites@2025-03-01' = {
  name: 'func-${common.suffix}', location: common.location, tags: common.tags, kind: 'functionapp,linux'
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  dependsOn: [hostStorage, deployments]
  properties: {
    serverFarmId: plan.id, httpsOnly: true, siteConfig: { appSettings: map(items(settings), item => { name: item.key, value: item.value }) }
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer', value: '${storage.properties.primaryEndpoints.blob}deployments'
          authentication: { type: 'UserAssignedIdentity', userAssignedIdentityResourceId: identityId }
        }
      }
      runtime: { name: 'python', version: '3.12' }, scaleAndConcurrency: { maximumInstanceCount: 40, instanceMemoryMB: 2048 }
    }
  }
}

resource appDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-workspace', scope: app
  properties: { workspaceId: common.workspaceId, logs: [{ category: 'FunctionAppLogs', enabled: true }], metrics: [{ category: 'AllMetrics', enabled: true }] }
}

resource storageDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-workspace', scope: storage, properties: { workspaceId: common.workspaceId, metrics: [{ category: 'Transaction', enabled: true }] }
}

output functionAppName string = app.name
