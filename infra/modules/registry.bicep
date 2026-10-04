@description('Azure region for all resources')
param location string

@description('Environment name (dev, prod)')
param environment string

@description('Short prefix for resource naming')
param appPrefix string

@description('SKU for the container registry (Basic, Standard, Premium)')
param acrSku string = 'Basic'

@description('Override the generated ACR name (must be globally unique, 5-50 alphanumeric chars). Leave empty to use the default: appPrefix + "acr" + environment.')
param acrNameOverride string = ''

@description('Number of recent non-release (SHA/commit) tags to keep per repository during purge. Older tags beyond this count are deleted.')
@minValue(1)
param purgeKeepCount int = 10

@description('Cron schedule for the weekly purge task (UTC). Default: Sunday 03:00 UTC.')
param purgeSchedule string = '0 3 * * Sun'

@description('Enable legacy tag-based cleanup. Production uses deployment-aware retention instead.')
param purgeEnabled bool = true

// ACR names must be alphanumeric only, 5-50 chars. Using a fixed, predictable
// name (e.g. siegeacrdev / siegeacrprod) so the GitHub Actions workflow can
// reference it without reading Bicep output at deploy time.
// acrNameOverride lets the dev environment supply a pre-checked globally-unique
// name (e.g. 'siegewebacr') without changing the default convention for prod.
var sanitizedName = empty(acrNameOverride) ? '${appPrefix}acr${environment}' : acrNameOverride

// ── acr purge command ────────────────────────────────────────────────────────
//
// Filter logic:
//   --filter 'repo:^[a-f0-9]{40}$'  — matches 40-char lowercase hex SHA tags
//                                      (the full Git commit SHA used by CI).
//                                      Does NOT match v* release tags.
//   --keep N                        — retains the N most-recently-pushed SHA
//                                      tags per repo; deletes older ones.
//   --untagged --ago 7d             — also deletes untagged manifests older
//                                      than 7 days (dangling layers, etc.).
//
// Release tags (v1.0.0, v2.3.1 …) are never matched by the SHA filter, so
// they accumulate without bound. This is intentional: release images are
// treated as permanent artifacts.
//
// Note: acr purge is currently in public preview (mcr.microsoft.com/acr/acr-cli:0.17).
var purgeCmd = 'acr purge --filter \'siege-api:^[a-f0-9]{40}$\' --filter \'siege-bot:^[a-f0-9]{40}$\' --filter \'siege-frontend:^[a-f0-9]{40}$\' --untagged --ago 7d --keep ${purgeKeepCount}'

resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: sanitizedName
  location: location
  tags: {
    project: appPrefix
    environment: environment
  }
  sku: {
    name: acrSku
  }
  properties: {
    adminUserEnabled: true
  }
}

// ── Scheduled ACR Task: weekly image retention purge ─────────────────────────
//
// The task runs acr purge inside ACR itself — no Container App or Function
// needed. Auth is handled by the task's system-assigned managed identity, which
// is automatically granted AcrDelete on the parent registry by Azure when the
// identity type is SystemAssigned.
//
// Schedule: weekly, Sunday 03:00 UTC (cron: "0 3 * * Sun").
// Platform: linux (required for ACR task runners).
//
// First-run note: this task is deployed but NOT triggered automatically.
// After the first infra deploy, run once on-demand to clear the existing
// backlog:
//   az acr task run --name weekly-purge --registry <acrName>
//
// Microsoft.ContainerRegistry/registries/tasks has never shipped a GA apiVersion;
// the latest available is 2025-03-01-preview. We intentionally stay on
// 2019-06-01-preview because preview-to-preview churn buys nothing on a stable
// surface (admin/anon-pull settings only). Re-evaluate if Microsoft promotes
// the tasks resource to GA. Audit reference: issue #249 (2026-05-07).
resource purgeTask 'Microsoft.ContainerRegistry/registries/tasks@2019-06-01-preview' = {
  name: 'weekly-purge'
  parent: registry
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    platform: {
      os: 'Linux'
    }
    agentConfiguration: {
      cpu: 2
    }
    step: {
      type: 'EncodedTask'
      encodedTaskContent: base64('version: v1.1.0\nsteps:\n  - cmd: ${purgeCmd}\n    disableWorkingDirectoryOverride: true\n    timeout: 3600\n')
    }
    trigger: {
      timerTriggers: [
        {
          name: 'weekly-sunday-0300-utc'
          schedule: purgeSchedule
          status: 'Enabled'
        }
      ]
    }
    isSystemTask: false
    status: purgeEnabled ? 'Enabled' : 'Disabled'
    timeout: 3600
  }
}

output registryId string = registry.id
output registryName string = registry.name
output loginServer string = registry.properties.loginServer

@secure()
output acrUsername string = registry.listCredentials().username

@secure()
output acrPassword string = registry.listCredentials().passwords[0].value
