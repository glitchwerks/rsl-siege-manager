// #250: capacity and platform health. All rules use the existing Action Group
// (Slack primary, email fallback); no synthetic load or image upload is needed.
param location string
@allowed(['dev', 'prod'])
param environment string
param appPrefix string
param actionGroupId string
param registryId string
@allowed(['Basic', 'Standard', 'Premium'])
param acrSku string
@description('Registry storage growth budget in GiB; zero uses the SKU included allowance. Not a hard capacity limit.')
@minValue(0)
param acrStorageAlertBudgetGiB int = 0
param postgresServerId string
param postgresMaxConnections int
param workspaceId string
param apiMinReplicas int
param useExternalSidecar bool

var tags = { project: appPrefix, environment: environment }
var gib = 1073741824
// Included storage is a billing allowance, not a hard registry capacity limit.
// An explicit budget decouples growth notifications from registry tier changes.
var acrIncludedGiB = acrSku == 'Basic' ? 10 : acrSku == 'Standard' ? 100 : 500
var acrBudgetGiB = acrStorageAlertBudgetGiB > 0 ? acrStorageAlertBudgetGiB : acrIncludedGiB
var apiName = '${appPrefix}-api-${environment}'
var botName = '${appPrefix}-bot-${environment}'
var appResourceBase = '${resourceGroup().id}/providers/Microsoft.App/containerApps'

var metricRules = [
  { name: 'acr-storage-80', resourceId: registryId, namespace: 'Microsoft.ContainerRegistry/registries', metric: 'StorageUsed', aggregation: 'Average', operator: 'GreaterThan', threshold: acrBudgetGiB * gib * 80 / 100, window: 'PT1H', frequency: 'PT1H', severity: 2 }
  { name: 'acr-storage-95', resourceId: registryId, namespace: 'Microsoft.ContainerRegistry/registries', metric: 'StorageUsed', aggregation: 'Average', operator: 'GreaterThan', threshold: acrBudgetGiB * gib * 95 / 100, window: 'PT1H', frequency: 'PT1H', severity: 1 }
  { name: 'pg-storage-80', resourceId: postgresServerId, namespace: 'Microsoft.DBforPostgreSQL/flexibleServers', metric: 'storage_percent', aggregation: 'Average', operator: 'GreaterThan', threshold: 80, window: 'PT5M', frequency: 'PT5M', severity: 2 }
  { name: 'pg-storage-90', resourceId: postgresServerId, namespace: 'Microsoft.DBforPostgreSQL/flexibleServers', metric: 'storage_percent', aggregation: 'Average', operator: 'GreaterThan', threshold: 90, window: 'PT5M', frequency: 'PT5M', severity: 1 }
  { name: 'pg-connections-80', resourceId: postgresServerId, namespace: 'Microsoft.DBforPostgreSQL/flexibleServers', metric: 'active_connections', aggregation: 'Average', operator: 'GreaterThanOrEqual', threshold: postgresMaxConnections * 80 / 100, window: 'PT5M', frequency: 'PT5M', severity: 2 }
  { name: 'pg-cpu-80', resourceId: postgresServerId, namespace: 'Microsoft.DBforPostgreSQL/flexibleServers', metric: 'cpu_percent', aggregation: 'Average', operator: 'GreaterThan', threshold: 80, window: 'PT15M', frequency: 'PT5M', severity: 2 }
]

resource capacityMetrics 'Microsoft.Insights/metricAlerts@2018-03-01' = [for rule in metricRules: {
  name: '${appPrefix}-alert-${rule.name}-${environment}'
  location: 'global'
  tags: tags
  properties: {
    description: startsWith(rule.name, 'acr-storage-')
      ? 'Registry storage exceeds a growth budget threshold of ${rule.threshold} bytes over ${rule.window}; budget ${acrBudgetGiB} GiB, included billing allowance ${acrIncludedGiB} GiB. Not a hard capacity limit; routes to Slack and fallback email.'
      : '${rule.metric} ${rule.operator} ${rule.threshold} over ${rule.window}; routes to Slack and fallback email.'
    enabled: true
    severity: rule.severity
    evaluationFrequency: rule.frequency
    windowSize: rule.window
    scopes: [rule.resourceId]
    autoMitigate: true
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [{
        name: rule.name
        criterionType: 'StaticThresholdCriterion'
        metricNamespace: rule.namespace
        metricName: rule.metric
        timeAggregation: rule.aggregation
        operator: rule.operator
        threshold: rule.threshold
        skipMetricValidation: false
      }]
    }
    actions: [{ actionGroupId: actionGroupId }]
  }
}]

// The dev API intentionally scales to zero. Only provision this rule when an
// application is configured to keep at least one replica alive.
var replicaRules = concat(
  apiMinReplicas > 0 ? [{ name: apiName, resourceId: '${appResourceBase}/${apiName}' }] : [],
  !useExternalSidecar ? [{ name: botName, resourceId: '${appResourceBase}/${botName}' }] : []
)

resource zeroReplicas 'Microsoft.Insights/metricAlerts@2018-03-01' = [for app in replicaRules: {
  name: '${appPrefix}-alert-zero-replicas-${replace(app.name, '${appPrefix}-', '')}'
  location: 'global'
  tags: tags
  properties: {
    description: 'A Container App configured with minReplicas >= 1 reported zero replicas for one minute.'
    enabled: true
    severity: 1
    evaluationFrequency: 'PT1M'
    windowSize: 'PT1M'
    scopes: [app.resourceId]
    autoMitigate: true
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [{
        name: 'zero-replicas'
        criterionType: 'StaticThresholdCriterion'
        metricNamespace: 'Microsoft.App/containerApps'
        metricName: 'Replicas'
        timeAggregation: 'Minimum'
        operator: 'LessThan'
        threshold: 1
        skipMetricValidation: false
      }]
    }
    actions: [{ actionGroupId: actionGroupId }]
  }
}]

// RestartCount is cumulative for each replica, not a five-minute rate. Count
// ContainerStarted system events by app instead. This catches crash loops and
// may also fire during rapid operator deployments; investigate before acting.
var crashLoopApps = concat([apiName], !useExternalSidecar ? [botName] : [])
resource crashLoop 'Microsoft.Insights/scheduledQueryRules@2026-03-01' = [for app in crashLoopApps: {
  name: '${appPrefix}-alert-crash-loop-${replace(app, '${appPrefix}-', '')}'
  location: location
  tags: tags
  properties: {
    displayName: '[${environment}] ${app} — >3 starts in 5m'
    description: 'More than three ContainerStarted events in five minutes. Check revision and system logs for crash-loop or rapid deployment.'
    enabled: true
    severity: 2
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    scopes: [workspaceId]
    autoMitigate: false
    muteActionsDuration: 'PT15M'
    criteria: {
      allOf: [{
        query: '''
ContainerAppSystemLogs
| where TimeGenerated > ago(5m)
| where ContainerAppName == "${app}"
| where Reason == "ContainerStarted"
| summarize Starts = count() by ContainerAppName
| where Starts > 3
'''
        timeAggregation: 'Count'
        operator: 'GreaterThan'
        threshold: 0
        failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
      }]
    }
    actions: { actionGroups: [actionGroupId] }
  }
}]

resource postgresResourceHealth 'Microsoft.Insights/activityLogAlerts@2020-10-01' = {
  name: '${appPrefix}-alert-pg-resource-health-${environment}'
  location: 'global'
  tags: tags
  properties: {
    description: 'PostgreSQL Flexible Server became Degraded or Unavailable.'
    enabled: true
    scopes: [postgresServerId]
    condition: { allOf: [
      { field: 'category', equals: 'ResourceHealth' }
      { anyOf: [
        { field: 'properties.currentHealthStatus', equals: 'Degraded' }
        { field: 'properties.currentHealthStatus', equals: 'Unavailable' }
      ] }
    ] }
    actions: { actionGroups: [{ actionGroupId: actionGroupId }] }
  }
}

// Both environments share a subscription: one subscription-scoped Service
// Health rule avoids duplicate alerts. Do not filter region, since global
// advisories may not report an impacted region at all.
resource serviceHealth 'Microsoft.Insights/activityLogAlerts@2020-10-01' = if (environment == 'prod') {
  name: '${appPrefix}-alert-service-health'
  location: 'global'
  tags: tags
  properties: {
    description: 'Azure Service Health for Container Registry, Container Apps, PostgreSQL flexible servers, or Key Vault.'
    enabled: true
    scopes: [subscription().id]
    condition: { allOf: [
      { field: 'category', equals: 'ServiceHealth' }
      { field: 'properties.impactedServices[*].ServiceName', containsAny: [
        'Azure Container Registry'
        'Azure Container Apps'
        'Azure Database for PostgreSQL flexible servers'
        'Key Vault'
      ] }
    ] }
    actions: { actionGroups: [{ actionGroupId: actionGroupId }] }
  }
}
