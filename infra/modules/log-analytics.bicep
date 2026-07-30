@description('Azure region for all resources')
param location string

@description('Environment name (dev, prod)')
param environment string

@description('Short prefix for resource naming')
param appPrefix string

@description('Log retention in days (30 for dev, 90 for prod)')
param retentionInDays int = 30

var workspaceName = '${appPrefix}-logs-${environment}-${uniqueString(resourceGroup().id)}'

resource workspace 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name: workspaceName
  location: location
  tags: {
    project: appPrefix
    environment: environment
  }
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
  }
}

output workspaceId string = workspace.id
output workspaceName string = workspace.name

// Resource ID used to link Application Insights in workspace-based mode
output resourceId string = workspace.id
