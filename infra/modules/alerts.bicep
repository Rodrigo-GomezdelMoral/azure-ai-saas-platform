param common object
param actionGroupId string
param appInsightsId string
param servicebusId string
param clusterId string
param budgetStartDate string

var insights = 'microsoft.insights/components'
var aks = 'Microsoft.ContainerService/managedClusters'
var serverErrors = [{ name: 'request/resultCode', operator: 'Include', values: ['500', '502', '503', '504'] }]
var rejections = [{ name: 'request/resultCode', operator: 'Include', values: ['429'] }]
var rules = [
  { name: 'gateway-5xx', about: 'Server errors answered by the gateway', scope: appInsightsId, space: insights, metric: 'requests/failed', agg: 'Count', threshold: 5, severity: 1, dims: serverErrors }
  { name: 'quota-rejections', about: 'Business signal: tenants hitting their plan ceiling', scope: appInsightsId, space: insights, metric: 'requests/failed', agg: 'Count', threshold: 50, severity: 3, dims: rejections }
  { name: 'servicebus-dead-letters', about: 'Usage or onboarding messages the consumers could not process', scope: servicebusId, space: 'Microsoft.ServiceBus/namespaces', metric: 'DeadletteredMessages', agg: 'Average', threshold: 0, severity: 2, dims: [] }
  { name: 'node-memory-pressure', about: 'Node working set above 85 percent', scope: clusterId, space: aks, metric: 'node_memory_working_set_percentage', agg: 'Average', threshold: 85, severity: 2, dims: [] }
  { name: 'node-cpu-pressure', about: 'Node CPU above 85 percent', scope: clusterId, space: aks, metric: 'node_cpu_usage_percentage', agg: 'Average', threshold: 85, severity: 2, dims: [] }
]
var notification = { enabled: true, operator: 'GreaterThanOrEqualTo', contactGroups: [actionGroupId] }

resource alerts 'Microsoft.Insights/metricAlerts@2026-01-01' = [for rule in rules: {
  name: 'alert-${rule.name}', location: 'global', tags: common.tags
  properties: {
    description: rule.about, severity: rule.severity, enabled: true, scopes: [rule.scope], evaluationFrequency: 'PT5M', windowSize: 'PT15M', actions: [{ actionGroupId: actionGroupId }]
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [{
        criterionType: 'StaticThresholdCriterion', name: 'signal', metricName: rule.metric, metricNamespace: rule.space, operator: 'GreaterThan', threshold: rule.threshold
        timeAggregation: rule.agg, dimensions: rule.dims
      }]
    }
  }
}]

resource budget 'Microsoft.Consumption/budgets@2026-06-01' = {
  name: 'budget-${common.suffix}'
  properties: {
    category: 'Cost', amount: 40, timeGrain: 'Monthly', timePeriod: { startDate: budgetStartDate }
    notifications: {
      actual50: union(notification, { threshold: 50, thresholdType: 'Actual' }), actual80: union(notification, { threshold: 80, thresholdType: 'Actual' })
      actual100: union(notification, { threshold: 100, thresholdType: 'Actual' }), forecast100: union(notification, { threshold: 100, thresholdType: 'Forecasted' })
    }
  }
}
