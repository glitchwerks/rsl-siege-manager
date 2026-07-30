@description('Azure region for all resources')
param location string

@description('Environment name (dev, prod)')
param environment string

@description('Short prefix for resource naming')
param appPrefix string

@description('Resource ID of the Log Analytics workspace to route Container Apps Environment logs to')
param logAnalyticsWorkspaceId string

@description('Resource ID of the user-assigned managed identity used to import certs from Key Vault. Leave empty to omit (identity block will be SystemAssigned).')
param certIdentityId string = ''

var envName = '${appPrefix}-cae-${environment}-${uniqueString(resourceGroup().id)}'

// The Container Apps environment needs an identity so it can authenticate to
// Key Vault and import the BYO certificate. We use a user-assigned identity
// rather than system-assigned so the role assignment can exist in the same
// Bicep deployment (system-assigned principal IDs are only known after the
// resource is first created).
//
// When certIdentityId is empty (dev with no cert), we fall back to a no-op
// identity block (None). This keeps the resource declaration unconditional and
// avoids the Bicep limitation around conditional identity blocks.

// API VERSION NOTE: Using preview `2024-08-02-preview` because the environment-level
// user-assigned identity block is not present in the GA API `2024-03-01`. The preview
// surface is scoped to this resource only; all other managed-env operations stay on GA.
// MIGRATION: revert to GA once the identity block ships in a GA API version.
// Track: https://aka.ms/azure-rest-api-specs (Microsoft.App API changelog).
resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-08-02-preview' = {
  name: envName
  location: location
  tags: {
    project: appPrefix
    environment: environment
  }
  identity: empty(certIdentityId)
    ? {
        type: 'None'
      }
    : {
        type: 'UserAssigned'
        userAssignedIdentities: {
          '${certIdentityId}': {}
        }
      }
  properties: {
    appLogsConfiguration: {
      destination: 'azure-monitor'
    }
  }
}

// Routes CAE console + system logs to Log Analytics via the platform resource-log
// pipeline (diagnostic settings) instead of the legacy shared-key custom-log
// ingest. Destination tables lose the _CL suffix: ContainerAppConsoleLogs /
// ContainerAppSystemLogs. See issue #521.
resource caeDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'cae-logs-to-law'
  scope: containerAppsEnvironment
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      { category: 'ContainerAppConsoleLogs', enabled: true }
      { category: 'ContainerAppSystemLogs', enabled: true }
    ]
  }
}

output environmentId string = containerAppsEnvironment.id
output environmentName string = containerAppsEnvironment.name
output defaultDomain string = containerAppsEnvironment.properties.defaultDomain
