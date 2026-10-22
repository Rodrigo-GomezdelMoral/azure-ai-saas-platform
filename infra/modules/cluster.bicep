param common object
param kubernetesVersion string
param nodeSizes object
param cicdPrincipalId string
param operatorObjectId string

var clusterAdmin = 'b1ff04bb-8a4e-4dc4-8eb5-8693973ce19b'
var clusterUser = '4abbcc35-e782-43d8-92c5-2d3f1bd2253f'
var subjects = [{ id: cicdPrincipalId, type: 'ServicePrincipal' }, { id: operatorObjectId, type: 'User' }]
var grants = flatten(map(subjects, s => [{ id: s.id, type: s.type, role: clusterAdmin }, { id: s.id, type: s.type, role: clusterUser }]))
var streams = ['Microsoft-ContainerLogV2', 'Microsoft-KubeEvents', 'Microsoft-KubePodInventory', 'Microsoft-KubeNodeInventory', 'Microsoft-InsightsMetrics']
var quiet = ['kube-system', 'gatekeeper-system', 'aks-istio-system', 'app-routing-system']
var pool = {
  osType: 'Linux', osSKU: 'AzureLinux', type: 'VirtualMachineScaleSets', osDiskType: 'Managed', osDiskSizeGB: 32, maxPods: 60, enableAutoScaling: true, tags: common.tags
}
var surge = { upgradeSettings: { maxSurge: '1' } }

resource cluster 'Microsoft.ContainerService/managedClusters@2026-06-01' = {
  name: 'aks-${common.suffix}', location: common.location, tags: common.tags, sku: { name: 'Base', tier: 'Free' }, identity: { type: 'SystemAssigned' }
  properties: {
    kubernetesVersion: kubernetesVersion, dnsPrefix: 'aks-${common.suffix}', nodeResourceGroup: 'rg-${common.suffix}-nodes', disableLocalAccounts: true
    aadProfile: { managed: true, enableAzureRBAC: true }, oidcIssuerProfile: { enabled: true }, securityProfile: { workloadIdentity: { enabled: true } }
    workloadAutoScalerProfile: { keda: { enabled: true } }, autoUpgradeProfile: { upgradeChannel: 'patch', nodeOSUpgradeChannel: 'NodeImage' }
    networkProfile: {
      networkPlugin: 'azure', networkPluginMode: 'overlay', networkDataplane: 'cilium', networkPolicy: 'cilium', loadBalancerSku: 'standard', outboundType: 'loadBalancer'
    }
    ingressProfile: { gatewayAPI: { installation: 'Standard' }, webAppRouting: { gatewayAPIImplementations: { appRoutingIstio: { mode: 'Enabled' } } } }
    addonProfiles: {
      azureKeyvaultSecretsProvider: { enabled: true, config: { enableSecretRotation: 'true' } }
      omsagent: { enabled: true, config: { logAnalyticsWorkspaceResourceID: common.workspaceId, useAADAuth: 'true' } }
    }
    agentPoolProfiles: [
      union(pool, surge, { name: 'system', mode: 'System', vmSize: nodeSizes.system, count: 1, minCount: 1, maxCount: 1 })
      union(pool, { name: 'spot', mode: 'User', vmSize: nodeSizes.user, count: 0, minCount: 0, maxCount: 2, scaleSetPriority: 'Spot', scaleSetEvictionPolicy: 'Delete', spotMaxPrice: -1 })
      union(pool, surge, { name: 'enterprise', mode: 'User', vmSize: nodeSizes.user, count: 0, minCount: 0, maxCount: 1, nodeTaints: ['tier=enterprise:NoSchedule'], nodeLabels: { tier: 'enterprise' } })
    ]
  }
}

resource containerInsights 'Microsoft.Insights/dataCollectionRules@2024-03-11' = {
  name: 'dcr-${common.suffix}-containers', location: common.location, tags: common.tags, kind: 'Linux'
  properties: {
    dataSources: {
      extensions: [{
        name: 'ContainerInsightsExtension', extensionName: 'ContainerInsights', streams: streams
        extensionSettings: { dataCollectionSettings: { interval: '5m', namespaceFilteringMode: 'Exclude', namespaces: quiet, enableContainerLogV2: true } }
      }]
    }
    destinations: { logAnalytics: [{ workspaceResourceId: common.workspaceId, name: 'workspace' }] }, dataFlows: [{ streams: streams, destinations: ['workspace'] }]
  }
}

resource containerInsightsAssociation 'Microsoft.Insights/dataCollectionRuleAssociations@2024-03-11' = {
  name: 'ContainerInsightsExtension', scope: cluster, properties: { dataCollectionRuleId: containerInsights.id }
}

resource access 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for item in grants: {
  name: guid(cluster.id, item.id, item.role), scope: cluster
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', item.role), principalId: item.id, principalType: item.type }
}]

resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-workspace', scope: cluster
  properties: {
    workspaceId: common.workspaceId, metrics: [{ category: 'AllMetrics', enabled: true }]
    logs: [{ category: 'kube-audit-admin', enabled: true }, { category: 'cluster-autoscaler', enabled: true }, { category: 'guard', enabled: true }]
  }
}

output clusterName string = cluster.name
output clusterId string = cluster.id
output oidcIssuerUrl string = cluster.properties.oidcIssuerProfile.issuerURL
output kubeletPrincipalId string = cluster.properties.identityProfile.kubeletidentity.objectId
