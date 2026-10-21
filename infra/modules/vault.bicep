param location string
param tags object
param suffix string
param logAnalyticsWorkspaceId string
param createMode string
param keyName string
param signerPrincipalId string
param operatorObjectId string

var cryptoUser = '12338af0-0e69-4776-bea7-57ae8d297424'

resource vault 'Microsoft.KeyVault/vaults@2026-05-15' = {
  name: 'kv-${suffix}'
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId, sku: { family: 'A', name: 'standard' }, createMode: createMode, publicNetworkAccess: 'Enabled'
    enableRbacAuthorization: true, enableSoftDelete: true, softDeleteRetentionInDays: 90, enablePurgeProtection: true
  }
}

resource signingKey 'Microsoft.KeyVault/vaults/keys@2026-05-15' = {
  parent: vault
  name: keyName
  tags: tags
  properties: { kty: 'RSA', keySize: 3072, keyOps: ['sign', 'verify'] }
}

resource signers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (principal, i) in [signerPrincipalId, operatorObjectId]: {
  name: guid(vault.id, principal, cryptoUser)
  scope: vault
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cryptoUser), principalId: principal, principalType: i == 0 ? 'ServicePrincipal' : 'User' }
}]

resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-workspace'
  scope: vault
  properties: { workspaceId: logAnalyticsWorkspaceId, logs: [{ category: 'AuditEvent', enabled: true }], metrics: [{ category: 'AllMetrics', enabled: true }] }
}

output vaultUrl string = vault.properties.vaultUri
output keyName string = signingKey.name
