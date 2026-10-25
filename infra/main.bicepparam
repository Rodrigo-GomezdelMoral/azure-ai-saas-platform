using './main.bicep'

param landingZone = {
  sharedResourceGroupName: readEnvironmentVariable('SHARED_RESOURCE_GROUP_NAME')
  registryName: readEnvironmentVariable('REGISTRY_NAME')
  registryLoginServer: readEnvironmentVariable('REGISTRY_LOGIN_SERVER')
  logAnalyticsWorkspaceId: readEnvironmentVariable('LOG_ANALYTICS_WORKSPACE_ID')
  actionGroupId: readEnvironmentVariable('ACTION_GROUP_ID')
  foundryAccountName: readEnvironmentVariable('FOUNDRY_ACCOUNT_NAME')
  foundryEndpoint: readEnvironmentVariable('FOUNDRY_ENDPOINT')
  searchServiceName: readEnvironmentVariable('SEARCH_SERVICE_NAME')
  searchEndpoint: readEnvironmentVariable('SEARCH_ENDPOINT')
  embeddingDeploymentName: readEnvironmentVariable('EMBEDDING_DEPLOYMENT_NAME')
}
param cicdPrincipalId = readEnvironmentVariable('CICD_PRINCIPAL_ID')
param operatorObjectId = readEnvironmentVariable('OPERATOR_OBJECT_ID')
param vaultCreateMode = readEnvironmentVariable('VAULT_CREATE_MODE', 'default')
param nodeGroupExists = bool(readEnvironmentVariable('NODE_GROUP_EXISTS', 'false'))
