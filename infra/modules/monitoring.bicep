// monitoring.bicep — Slack/email action group, alert router, and scheduled query rules.
//
// API versions confirmed stable GA as of 2026-04-29:
//   Microsoft.Insights/actionGroups         → 2023-01-01
//   Microsoft.Insights/scheduledQueryRules  → 2023-12-01 (upgraded from plan's 2023-03-15-preview)
//
// Scope: resource group (default).
// This module is invoked from main.bicep after the appInsights module so the
// App Insights resource ID is available as an input.

@description('Azure region for all resources')
param location string

@description('Environment name (dev or prod)')
@allowed(['dev', 'prod'])
param environment string

@description('Short prefix used in resource names')
param appPrefix string

@description('Resource ID of the Application Insights instance to scope alerts against')
param appInsightsId string

@description('Name of the Application Insights instance (used in the workbook serializedData substitution)')
param appInsightsName string

@description('Email address that receives alert notifications')
param alertEmail string

@description('Name of the Key Vault that stores the Slack incoming-webhook URL')
param keyVaultName string

@description('Key Vault data-plane URI, including the trailing slash')
param keyVaultUri string

@description('Resource tags to apply to all monitoring resources')
param tags object = {
  project: appPrefix
  environment: environment
}

// ── Action Group ─────────────────────────────────────────────────────────────
//
// Slack is the primary operational destination. Email remains enabled as an
// independent fallback receiver. The Logic App callback URI and Slack webhook
// are both secret-bearing values and are never emitted as deployment outputs.

var slackWebhookSecretName = 'slack-alert-webhook-url'
var azureAlertsPortalUrl = '${az.environment().portal}/#view/Microsoft_Azure_Monitoring/AzureMonitoringBrowseBlade/~/alerts'
var keyVaultAudience = 'https://${substring(az.environment().suffixes.keyvaultDns, 1)}'

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: keyVaultName
}

resource slackAlertWorkflow 'Microsoft.Logic/workflows@2019-05-01' = {
  name: '${appPrefix}-alert-slack-${environment}'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        alertEnvironment: {
          type: 'String'
          defaultValue: environment
        }
        keyVaultSecretUri: {
          type: 'String'
          defaultValue: '${keyVaultUri}secrets/${slackWebhookSecretName}?api-version=7.4'
        }
        fallbackInvestigationUrl: {
          type: 'String'
          defaultValue: azureAlertsPortalUrl
        }
        portalBaseUrl: {
          type: 'String'
          defaultValue: az.environment().portal
        }
      }
      triggers: {
        azure_monitor_common_alert: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            method: 'POST'
            schema: {
              type: 'object'
              required: [
                'schemaId'
                'data'
              ]
              properties: {
                schemaId: {
                  type: 'string'
                }
                data: {
                  type: 'object'
                  required: [
                    'essentials'
                  ]
                  properties: {
                    essentials: {
                      type: 'object'
                      required: [
                        'alertRule'
                        'severity'
                        'monitorCondition'
                        'alertTargetIDs'
                        'firedDateTime'
                      ]
                      properties: {
                        alertRule: {
                          type: 'string'
                        }
                        severity: {
                          type: 'string'
                        }
                        monitorCondition: {
                          type: 'string'
                        }
                        alertTargetIDs: {
                          type: 'array'
                          minItems: 1
                          items: {
                            type: 'string'
                          }
                        }
                        firedDateTime: {
                          type: 'string'
                        }
                        resolvedDateTime: {
                          type: [
                            'string'
                            'null'
                          ]
                        }
                        investigationLink: {
                          type: [
                            'string'
                            'null'
                          ]
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
      actions: {
        Validate_common_alert: {
          type: 'If'
          expression: '''@and(equals(triggerBody()?['schemaId'], 'azureMonitorCommonAlertSchema'), or(equals(triggerBody()?['data']?['essentials']?['monitorCondition'], 'Fired'), equals(triggerBody()?['data']?['essentials']?['monitorCondition'], 'Resolved')), not(empty(triggerBody()?['data']?['essentials']?['alertRule'])), not(empty(triggerBody()?['data']?['essentials']?['severity'])), not(empty(triggerBody()?['data']?['essentials']?['alertTargetIDs'])), not(empty(triggerBody()?['data']?['essentials']?['firedDateTime'])))'''
          actions: {
            Get_slack_webhook: {
              type: 'Http'
              inputs: {
                method: 'GET'
                uri: '''@parameters('keyVaultSecretUri')'''
                authentication: {
                  type: 'ManagedServiceIdentity'
                  audience: keyVaultAudience
                }
              }
              runtimeConfiguration: {
                secureData: {
                  properties: [
                    'inputs'
                    'outputs'
                  ]
                }
              }
            }
            Post_sanitized_alert_to_slack: {
              type: 'Http'
              runAfter: {
                Get_slack_webhook: [
                  'Succeeded'
                ]
              }
              inputs: {
                method: 'POST'
                uri: '''@body('Get_slack_webhook')?['value']'''
                headers: {
                  'Content-Type': 'application/json'
                }
                body: {
                  text: '''@concat(if(equals(triggerBody()?['data']?['essentials']?['monitorCondition'], 'Resolved'), '✅ RESOLVED', '🚨 FIRED'), ' | ', toUpper(parameters('alertEnvironment')), ' | ', take(string(triggerBody()?['data']?['essentials']?['alertRule']), 200), decodeUriComponent('%0A'), 'Severity: ', take(string(triggerBody()?['data']?['essentials']?['severity']), 20), ' | State: ', triggerBody()?['data']?['essentials']?['monitorCondition'], decodeUriComponent('%0A'), 'Resource: ', take(string(first(triggerBody()?['data']?['essentials']?['alertTargetIDs'])), 500), decodeUriComponent('%0A'), 'Time: ', if(equals(triggerBody()?['data']?['essentials']?['monitorCondition'], 'Resolved'), coalesce(triggerBody()?['data']?['essentials']?['resolvedDateTime'], triggerBody()?['data']?['essentials']?['firedDateTime']), triggerBody()?['data']?['essentials']?['firedDateTime']), decodeUriComponent('%0A'), 'Investigate: ', if(and(startsWith(coalesce(triggerBody()?['data']?['essentials']?['investigationLink'], ''), concat(parameters('portalBaseUrl'), '/')), not(contains(coalesce(triggerBody()?['data']?['essentials']?['investigationLink'], ''), decodeUriComponent('%0A'))), not(contains(coalesce(triggerBody()?['data']?['essentials']?['investigationLink'], ''), decodeUriComponent('%0D')))), triggerBody()?['data']?['essentials']?['investigationLink'], parameters('fallbackInvestigationUrl')))'''
                }
              }
              runtimeConfiguration: {
                secureData: {
                  properties: [
                    'inputs'
                    'outputs'
                  ]
                }
              }
            }
            Respond_accepted: {
              type: 'Response'
              kind: 'Http'
              runAfter: {
                Post_sanitized_alert_to_slack: [
                  'Succeeded'
                ]
              }
              inputs: {
                statusCode: 202
                body: {
                  status: 'forwarded'
                }
              }
            }
            Respond_delivery_failed: {
              type: 'Response'
              kind: 'Http'
              runAfter: {
                Post_sanitized_alert_to_slack: [
                  'Failed'
                  'Skipped'
                  'TimedOut'
                ]
              }
              inputs: {
                statusCode: 502
                body: {
                  status: 'delivery_failed'
                }
              }
            }
          }
          else: {
            actions: {
              Respond_bad_request: {
                type: 'Response'
                kind: 'Http'
                inputs: {
                  statusCode: 400
                  body: {
                    status: 'rejected'
                  }
                }
              }
            }
          }
        }
      }
      outputs: {}
    }
    parameters: {}
  }
}

// Built-in Key Vault Secrets User role. The Logic App reads only the named
// webhook secret at runtime through its system-assigned identity.
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource slackWebhookSecret 'Microsoft.KeyVault/vaults/secrets@2024-11-01' existing = {
  parent: keyVault
  name: slackWebhookSecretName
}

resource slackAlertKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(slackWebhookSecret.id, slackAlertWorkflow.name, kvSecretsUserRoleId)
  scope: slackWebhookSecret
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: slackAlertWorkflow.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

var slackAlertCallbackUrl = listCallbackUrl('${slackAlertWorkflow.id}/triggers/azure_monitor_common_alert', '2019-05-01').value

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'siege-app-health-${environment}'
  location: 'Global'    // Action groups are always global resources
  tags: tags
  properties: {
    groupShortName: 'siege-${environment}'   // ≤12 chars: 'siege-dev' = 9, 'siege-prod' = 10
    enabled: true
    emailReceivers: [
      {
        name: 'On-Call Email'
        emailAddress: alertEmail
        useCommonAlertSchema: true  // structured fields in the email body — easier to parse
      }
    ]
    armRoleReceivers: []
    azureAppPushReceivers: []
    azureFunctionReceivers: []
    eventHubReceivers: []
    itsmReceivers: []
    logicAppReceivers: [
      {
        name: 'Slack Infrastructure Alerts'
        resourceId: slackAlertWorkflow.id
        callbackUrl: slackAlertCallbackUrl
        useCommonAlertSchema: true
      }
    ]
    smsReceivers: []
    voiceReceivers: []
    webhookReceivers: []
  }
}

// ── Alert rules ───────────────────────────────────────────────────────────────
//
// Each alert is a log-search alert scoped to the App Insights resource.
// Common settings:
//   - evaluationFrequency PT1M — check every minute
//   - severity 2 (warning) or 3 (informational)
//   - Most rules remain stateless (autoMitigate false) with a 15-minute action
//     mute. The latency rule is stateful and has no mute duration; Azure rejects
//     autoMitigate true combined with muteActionsDuration.
//
// All KQL queries are written to return zero rows in the steady state and ≥1
// row when the condition is breached, so `operator: GreaterThan, threshold: 0`
// works uniformly across all five rules.
//
// NOTE: windowSize must be >= evaluationFrequency per Azure validation rules.
// Both are ISO 8601 duration strings.

// ── Alert 1: API 5xx rate > 1% over 5 min ────────────────────────────────────
// Severity 2 — page-worthy; the API is returning errors to real users.
// Min-traffic floor (total >= 20) prevents false alarms during idle windows.

resource alert5xxRate 'Microsoft.Insights/scheduledQueryRules@2026-03-01' = {
  name: '${appPrefix}-alert-5xx-rate-${environment}'
  location: location
  tags: tags
  properties: {
    displayName: '[${environment}] siege-api — 5xx error rate > 1%'
    description: 'Fires when more than 1% of API requests return HTTP 5xx over a 5-minute window. Floor: ≥20 total requests (suppresses low-traffic noise).'
    enabled: true
    severity: 2
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    scopes: [appInsightsId]
    autoMitigate: false
    muteActionsDuration: 'PT15M'
    criteria: {
      allOf: [
        {
          query: '''
requests
| where timestamp > ago(5m)
| where cloud_RoleName == "siege-api"
| summarize total = count(), errors = countif(toint(resultCode) >= 500)
| where total >= 20  // suppress alert when traffic is too low to be statistically meaningful
| extend errorRate = todouble(errors) / total
| where errorRate > 0.01
| project errorRate, errors, total
'''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [actionGroup.id]
    }
  }
}

// ── Alert 2: API request latency p95 > 3s ────────────────────────────────────
// Severity 3 — warning; latency is degraded but requests are still succeeding.
// Min-traffic floor (sampleCount >= 20) avoids false alarms from a single slow call.
// #263 review (2026-10-02): 10 prod instances formed two short episodes in
// the last 30 days, none acknowledged. Both returned to normal in the next
// 5-minute bucket. Resolve the condition automatically and notify once per
// episode; human acknowledgement remains an independent workflow state.

resource alertLatencyP95 'Microsoft.Insights/scheduledQueryRules@2026-03-01' = {
  name: '${appPrefix}-alert-latency-p95-${environment}'
  location: location
  tags: tags
  properties: {
    displayName: '[${environment}] siege-api — p95 latency > 3s'
    description: 'Fires when the 95th-percentile request duration exceeds 3000ms over a 5-minute window. Floor: ≥20 requests.'
    enabled: true
    severity: 3
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    scopes: [appInsightsId]
    autoMitigate: true
    criteria: {
      allOf: [
        {
          query: '''
requests
| where timestamp > ago(5m)
| where cloud_RoleName == "siege-api"
| summarize p95 = percentile(duration, 95), sampleCount = count()
| where sampleCount >= 20
| where p95 > 3000  // duration is milliseconds in App Insights
| project p95, sampleCount
'''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [actionGroup.id]
    }
  }
}

// ── Alert 3: Bot restart ──────────────────────────────────────────────────────
// Severity 2 — page-worthy; the bot holds a Discord WebSocket — any restart is
// notable. Window PT1M / frequency PT1M → a single restart pages immediately.
//
// Startup string ground-truthed against dev telemetry (2026-04-29, Phase 5 early):
//   traces | where cloud_RoleName == "siege-bot" | order by timestamp desc | take 50
// Actual unique startup string emitted by bot/app/telemetry.py on each boot:
//   "FastAPI HTTP sidecar instrumented for OpenTelemetry tracing."
// Count > 0 in the 1-minute window is sufficient to confirm a restart occurred.

resource alertBotRestart 'Microsoft.Insights/scheduledQueryRules@2026-03-01' = {
  name: '${appPrefix}-alert-bot-restart-${environment}'
  location: location
  tags: tags
  properties: {
    displayName: '[${environment}] siege-bot — unexpected restart detected'
    description: 'Fires when the bot\'s OTel startup trace is observed in a 1-minute window, indicating the bot process restarted. Startup string confirmed against dev telemetry 2026-04-29.'
    enabled: true
    severity: 2
    evaluationFrequency: 'PT1M'
    windowSize: 'PT1M'
    scopes: [appInsightsId]
    autoMitigate: false
    muteActionsDuration: 'PT15M'
    criteria: {
      allOf: [
        {
          // Startup string confirmed from dev traces 2026-04-29 (Phase 5 early ground-truth).
          // bot/app/telemetry.py emits this once per boot — unique and reliable.
          query: '''
traces
| where cloud_RoleName == "siege-bot"
| where message has "FastAPI HTTP sidecar instrumented"
| summarize Count = count()
| where Count > 0
'''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [actionGroup.id]
    }
  }
}

// ── Alert 4: DB connection error ─────────────────────────────────────────────
// Severity 2 — page-worthy; any failed PostgreSQL dependency means the backend
// cannot reach the database and all data-path requests will 500.
//
// Previously deferred (PR #246) pending #257. DB dependency spans confirmed in
// App Insights as of 2026-04-30 (PR #265, commit 9a11733): type == "postgresql",
// Pattern A (no span duplication). Blocker removed — alert wired here.

resource alertDbConnectionError 'Microsoft.Insights/scheduledQueryRules@2026-03-01' = {
  name: '${appPrefix}-alert-db-connection-error-${environment}'
  location: location
  tags: tags
  properties: {
    displayName: '[${environment}] siege-api — DB connection error'
    description: 'Fires when any PostgreSQL dependency call from siege-api fails in a 5-minute window. DB spans confirmed in App Insights 2026-04-30 (PR #265, Pattern A).'
    enabled: true
    severity: 2
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    scopes: [appInsightsId]
    autoMitigate: false
    muteActionsDuration: 'PT15M'
    criteria: {
      allOf: [
        {
          query: '''
dependencies
| where cloud_RoleName == "siege-api"
| where type == "postgresql"
| where success == false
| summarize FailureCount = count()
| where FailureCount > 0
'''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [actionGroup.id]
    }
  }
}

// ── Alert 5: Image generation > 10s ──────────────────────────────────────────
// Severity 3 — warning; slow renders degrade UX but requests still complete.
//
// Route confirmed: POST /api/sieges/{siege_id}/generate-images at backend/app/api/images.py:28.
// Predicate `name has "generate-images"` ground-truthed against dev requests table
// (2026-04-29, Phase 5 early): the route exists and the predicate matches correctly.
// Tile is empty only due to absence of traffic, not a KQL issue.
//
// This alert queries the `requests` table filtered by operation_Name matching
// the image generation route, rather than relying on a `customEvents` row.

resource alertImageGenSlow 'Microsoft.Insights/scheduledQueryRules@2026-03-01' = {
  name: '${appPrefix}-alert-image-gen-slow-${environment}'
  location: location
  tags: tags
  properties: {
    displayName: '[${environment}] siege-api — image generation > 10s'
    description: 'Fires when any request to the generate-images route takes longer than 10 seconds in a 5-minute window. Route confirmed at backend/app/api/images.py:28; predicate verified against dev telemetry 2026-04-29.'
    enabled: true
    severity: 3
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    scopes: [appInsightsId]
    autoMitigate: false
    muteActionsDuration: 'PT15M'
    criteria: {
      allOf: [
        {
          // Route name confirmed from dev requests table 2026-04-29.
          // `name has "generate-images"` matches POST /api/sieges/{siege_id}/generate-images.
          query: '''
requests
| where timestamp > ago(5m)
| where cloud_RoleName == "siege-api"
| where name has "generate-images"
| where duration > 10000  // duration is milliseconds in App Insights
| summarize slowRenders = count(), maxMs = max(duration)
| where slowRenders > 0
| project slowRenders, maxMs
'''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [actionGroup.id]
    }
  }
}

// ── Workbook ──────────────────────────────────────────────────────────────────
//
// Load the Gallery Template JSON and rewrite the three embedded dev-environment
// IDs so the same file deploys identically to both dev and prod:
//
//   1. Subscription ID  — the `fallbackResourceIds` array in the JSON contains
//      the dev subscription GUID; replaced with the current deployment's
//      subscription ID via subscription().subscriptionId.
//
//   2. Resource-group name — hard-coded "siege-web-dev" in the JSON; replaced
//      with the actual RG name at deploy time via resourceGroup().name.
//
//   3. App Insights resource name — hard-coded "siege-web-ai-dev-e2xv2wolzinjg";
//      replaced with the appInsightsName param so prod uses its own resource name.
//
// This is the documented Microsoft pattern for environment-portable workbook
// templates (chained replace() calls on the raw JSON string).

var workbookRaw = string(loadJsonContent('workbook.template.json'))
var workbookWithSub = replace(workbookRaw, '213aa1f8-32d1-4ffe-8f4d-6e60f1cd9dc0', subscription().subscriptionId)
var workbookWithRg = replace(workbookWithSub, 'siege-web-dev', resourceGroup().name)
var serializedWorkbook = replace(workbookWithRg, 'siege-web-ai-dev-e2xv2wolzinjg', appInsightsName)

// `name` must be a stable GUID — Microsoft.Insights/workbooks rejects non-GUID names.
// guid(resourceGroup().id, 'siege-app-health') produces the same GUID on every
// re-deploy within a given RG, so re-deploys update rather than create new resources.

resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: guid(resourceGroup().id, 'siege-app-health')
  location: location
  kind: 'shared'
  tags: tags
  properties: {
    displayName: 'Siege App Health'
    category: 'workbook'
    sourceId: appInsightsId
    serializedData: serializedWorkbook
    version: 'Notebook/1.0'
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────

@description('Resource ID of the deployed action group')
output actionGroupId string = actionGroup.id

@description('Name of the deployed action group')
output actionGroupName string = actionGroup.name

@description('Resource ID of the deployed workbook')
output workbookId string = workbook.id
