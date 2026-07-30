@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Environment name: dev or prod')
@allowed(['dev', 'prod'])
param environment string = 'dev'

@description('Short prefix used in resource names')
param appPrefix string = 'siege'

@description('Container Registry SKU (Basic for dev, Standard for prod)')
param acrSku string = 'Basic'

@description('Override the generated ACR name. Leave empty to use the default convention (appPrefix + "acr" + environment).')
param acrNameOverride string = ''

@description('Number of recent SHA/commit-tagged images to keep per repo during the weekly purge. Release tags (v*) are never purged.')
@minValue(1)
param acrPurgeKeepCount int = 10

@description('Cron schedule (UTC) for the weekly ACR purge task. Default: Sunday 03:00 UTC.')
param acrPurgeSchedule string = '0 3 * * Sun'

@description('Image tag to deploy')
param imageTag string = 'latest'

@description('PostgreSQL administrator username')
param postgresAdminUser string = 'siegeadmin'

@description('PostgreSQL administrator password')
@secure()
param postgresAdminPassword string

@description('Discord bot token')
@secure()
param discordToken string

@description('Discord guild (server) ID')
param discordGuildId string

@description('API key used by backend to call the bot HTTP API')
@secure()
param discordBotApiKey string

@description('API key the bot validates on inbound HTTP requests')
@secure()
param botApiKey string

@description('Secret key for signing JWT session cookies')
@secure()
param sessionSecret string

@description('Discord OAuth2 application client ID')
@secure()
param discordClientId string

@description('Discord OAuth2 application client secret')
@secure()
param discordClientSecret string

@description('Discord OAuth2 redirect URI (full callback URL)')
param discordRedirectUri string

@description('Enable geo-redundant PostgreSQL backup (set true for prod)')
param postgresGeoRedundantBackup bool = false

// ── PostgreSQL sizing ─────────────────────────────────────────────────────────
// Dev default: Burstable B1ms — cheap shared vCores, adequate for testing.
// Prod recommendation: GeneralPurpose Standard_D2ds_v5 — dedicated 2 vCores,
// required for zone-redundant HA and consistent query performance.

@description('PostgreSQL SKU name (Standard_B1ms for dev, Standard_D2ds_v5 for prod)')
param postgresSku string = 'Standard_B1ms'

@description('PostgreSQL SKU tier (Burstable | GeneralPurpose | MemoryOptimized)')
@allowed(['Burstable', 'GeneralPurpose', 'MemoryOptimized'])
param postgresSkuTier string = 'Burstable'

@description('PostgreSQL storage size in GB')
param postgresStorageGB int = 32

@description('PostgreSQL backup retention in days (7–35)')
@minValue(7)
@maxValue(35)
param postgresBackupRetentionDays int = 7

@description('PostgreSQL high availability mode (Disabled | SameZone | ZoneRedundant)')
@allowed(['Disabled', 'SameZone', 'ZoneRedundant'])
param postgresHighAvailability string = 'Disabled'

// ── Key Vault ─────────────────────────────────────────────────────────────────

@description('Key Vault soft-delete retention in days (7 for dev, 90 for prod)')
@minValue(7)
@maxValue(90)
param kvSoftDeleteRetentionDays int = 7

// ── Log Analytics ─────────────────────────────────────────────────────────────

@description('Log Analytics retention in days (30 for dev, 90 for prod)')
param logRetentionDays int = 30

// ── Container App sizing ──────────────────────────────────────────────────────
// These parameters let each environment pick appropriate CPU/memory without
// forking the module. Prod uses larger allocations because the API runs
// Playwright (headless Chromium) for image generation.

@description('API Container App CPU (e.g. "0.5" dev, "1.0" prod)')
param apiCpu string = '0.5'

@description('API Container App memory (e.g. "1Gi" dev, "2Gi" prod)')
param apiMemory string = '1Gi'

@description('API Container App max replicas')
param apiMaxReplicas int = 3

@description('Frontend Container App CPU')
param frontendCpu string = '0.25'

@description('Frontend Container App memory')
param frontendMemory string = '0.5Gi'

@description('Bot Container App CPU')
param botCpu string = '0.25'

@description('Bot Container App memory')
param botMemory string = '0.5Gi'

@description('Minimum replicas for the API app (0 = scale to zero when idle)')
param apiMinReplicas int = 1

@description('Minimum replicas for the frontend app (0 = scale to zero when idle)')
param frontendMinReplicas int = 1

@description('Public-facing URL for canonical/og tags (e.g. https://rslsiege.com). Leave empty for dev.')
param publicUrl string = ''

@description('When true, the bundled bot Container App is not provisioned. Operators running an alternate Discord sidecar (e.g. mom-bot) must set this to true to avoid the Discord singleton-token conflict. Default false preserves existing behavior.')
param useExternalSidecar bool = false

@description('HTTP API URL of the external Discord sidecar. Required when useExternalSidecar is true (e.g. https://my-bot.example.com). Ignored when useExternalSidecar is false.')
param externalBotApiUrl string = ''

@description('Custom hostname to bind to the frontend (e.g. rslsiege.com). Leave empty to skip custom domain binding.')
param customDomainHostname string = ''

@description('Discord role snowflake ID for Day 1 attackers (used by day-role-sync contract v1.1). Leave empty to omit DISCORD_DAY_1_ROLE_ID env var.')
param discordDay1RoleId string = ''

@description('Discord role snowflake ID for Day 2 attackers (used by day-role-sync contract v1.1). Leave empty to omit DISCORD_DAY_2_ROLE_ID env var.')
param discordDay2RoleId string = ''

// ── Cloudflare Origin Certificate ────────────────────────────────────────────
//
// Two-phase deployment process:
//
//   Phase 1 — First deploy (enableCustomDomain = false, default):
//     - Key Vault, UAMI, and role assignment are created.
//     - No cert resource or domain binding is created on the Container App.
//     - User generates the Cloudflare Origin Cert, converts to PFX, and
//       uploads to Key Vault as a secret named 'cloudflare-origin-cert'.
//
//   Phase 2 — Second deploy (enableCustomDomain = true):
//     - Container Apps environment imports the PFX from Key Vault.
//     - Frontend Container App is bound with SniEnabled + the imported cert.
//     - Cloudflare SSL mode is set to Full (strict) and proxy is turned ON.
//
// This flag is the deployment gate. It defaults to false so a bare infrastructure
// deploy never fails waiting for a cert that hasn't been uploaded yet.

@description('When true, imports the Cloudflare Origin Cert from Key Vault and binds it to the frontend. Set false on first deploy, true after the PFX has been uploaded to KV.')
param enableCustomDomain bool = false

@description('Versionless Key Vault secret URL for the Cloudflare Origin Cert PFX (e.g. https://<vault>.vault.azure.net/secrets/cloudflare-origin-cert). Required when enableCustomDomain is true.')
param kvCertSecretUrl string = ''

// ── Custom-domain preconditions (Bicep `assert` — experimental feature, ──────
//    gated via bicepconfig.json). Surfaces "Assertion failed: <name>" errors at compile time when
//    enableCustomDomain=true but required params are missing or malformed.
//    Assert names are descriptive so the failed-name itself is the error message.

assert kvCertSecretUrlProvided = !enableCustomDomain || !empty(kvCertSecretUrl)

assert kvCertSecretUrlFormat = !enableCustomDomain || contains(kvCertSecretUrl, '.vault.azure.net/secrets/')

assert customDomainHostnameProvided = !enableCustomDomain || !empty(customDomainHostname)

// When useExternalSidecar = true, an explicit external URL must be supplied so
// the backend's DISCORD_BOT_API_URL is not left as an empty string.
assert externalBotApiUrlProvided = !useExternalSidecar || !empty(externalBotApiUrl)

// When useExternalSidecar = true in non-dev environments, the external URL must
// use HTTPS to prevent the BOT_API_KEY Bearer token from being sent in plaintext.
// Dev is exempt to allow http://localhost URLs during local development.
assert externalBotApiUrlIsHttps = !useExternalSidecar || environment == 'dev' || startsWith(externalBotApiUrl, 'https://')

// ── Monitoring ────────────────────────────────────────────────────────────────

@description('Email address that receives alert notifications from the monitoring action group (dev and prod use the same address in v1).')
param alertEmail string

// ── Modules ──────────────────────────────────────────────────────────────────

module logAnalytics 'modules/log-analytics.bicep' = {
  name: 'logAnalytics'
  params: {
    location: location
    environment: environment
    appPrefix: appPrefix
    retentionInDays: logRetentionDays
  }
}

module registry 'modules/registry.bicep' = {
  name: 'registry'
  params: {
    location: location
    environment: environment
    appPrefix: appPrefix
    acrSku: acrSku
    acrNameOverride: acrNameOverride
    purgeKeepCount: acrPurgeKeepCount
    purgeSchedule: acrPurgeSchedule
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    location: location
    environment: environment
    appPrefix: appPrefix
    adminUsername: postgresAdminUser
    adminPassword: postgresAdminPassword
    geoRedundantBackup: postgresGeoRedundantBackup
    postgresSku: postgresSku
    postgresSkuTier: postgresSkuTier
    storageSizeGB: postgresStorageGB
    backupRetentionDays: postgresBackupRetentionDays
    highAvailabilityMode: postgresHighAvailability
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyVault'
  params: {
    location: location
    environment: environment
    appPrefix: appPrefix
    databaseUrl: 'postgresql+asyncpg://${postgresAdminUser}:${postgresAdminPassword}@${postgres.outputs.serverFqdn}/siege'
    discordToken: discordToken
    discordGuildId: discordGuildId
    discordBotApiKey: discordBotApiKey
    botApiKey: botApiKey
    sessionSecret: sessionSecret
    discordClientId: discordClientId
    discordClientSecret: discordClientSecret
    softDeleteRetentionDays: kvSoftDeleteRetentionDays
  }
}

// Application Insights — workspace-based mode links telemetry to the same
// Log Analytics workspace used for container logs, giving one unified query
// surface. This module is always deployed; the connection string is threaded
// into all three Container Apps as APPLICATIONINSIGHTS_CONNECTION_STRING.
module appInsights 'modules/app-insights.bicep' = {
  name: 'appInsights'
  params: {
    location: location
    environment: environment
    appPrefix: appPrefix
    logAnalyticsWorkspaceId: logAnalytics.outputs.resourceId
  }
}

// Monitoring — action group + alert rules + Application Insights workbook.
// Workbook template lives at infra/modules/workbook.template.json (Phase 3b, #246).
// See issue #246 for design rationale.
module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    location: location
    environment: environment
    appPrefix: appPrefix
    appInsightsId: appInsights.outputs.appInsightsId
    appInsightsName: appInsights.outputs.appInsightsName
    alertEmail: alertEmail
    tags: {
      project: appPrefix
      environment: environment
    }
  }
}

// ── User-assigned managed identity for cert import ───────────────────────────
//
// The UAMI and its KV role assignment are always deployed (not gated on
// enableCustomDomain) so that the identity is ready the moment the user
// uploads the PFX. Deploying the identity only on Phase 2 would mean the
// role assignment wasn't in place, causing the import to fail.

module certIdentity 'modules/cert-identity.bicep' = {
  name: 'certIdentity'
  params: {
    location: location
    environment: environment
    appPrefix: appPrefix
    vaultName: keyVault.outputs.vaultName
  }
}

module containerEnv 'modules/container-env.bicep' = {
  name: 'containerEnv'
  params: {
    location: location
    environment: environment
    appPrefix: appPrefix
    logAnalyticsWorkspaceId: logAnalytics.outputs.workspaceId
    // Pass the UAMI so the environment can authenticate to KV during cert import.
    certIdentityId: certIdentity.outputs.identityId
  }
}

module containerApps 'modules/container-apps.bicep' = {
  name: 'containerApps'
  params: {
    location: location
    environment: environment
    appPrefix: appPrefix
    containerAppsEnvironmentId: containerEnv.outputs.environmentId
    acrLoginServer: registry.outputs.loginServer
    imageTag: imageTag
    keyVaultUri: keyVault.outputs.vaultUri
    discordGuildId: discordGuildId
    discordRedirectUri: discordRedirectUri
    acrUsername: registry.outputs.acrUsername
    acrPassword: registry.outputs.acrPassword
    appInsightsConnectionString: appInsights.outputs.connectionString
    apiCpu: apiCpu
    apiMemory: apiMemory
    apiMaxReplicas: apiMaxReplicas
    frontendCpu: frontendCpu
    frontendMemory: frontendMemory
    botCpu: botCpu
    botMemory: botMemory
    apiMinReplicas: apiMinReplicas
    frontendMinReplicas: frontendMinReplicas
    publicUrl: publicUrl
    containerAppsEnvironmentName: containerEnv.outputs.environmentName
    customDomainHostname: customDomainHostname
    enableCustomDomain: enableCustomDomain
    certIdentityId: certIdentity.outputs.identityId
    kvCertSecretUrl: kvCertSecretUrl
    useExternalSidecar: useExternalSidecar
    externalBotApiUrl: externalBotApiUrl
    discordDay1RoleId: discordDay1RoleId
    discordDay2RoleId: discordDay2RoleId
  }
}

// ── Key Vault role assignments ────────────────────────────────────────────────
//
// Role assignments are delegated to a dedicated module so that the vault name
// and principal IDs arrive as plain string parameters rather than as module
// outputs. Bicep BCP120 forbids using module outputs as the `name` of a
// resource or as the target of an `existing` scope, because those values are
// only known at runtime. Inside kv-role-assignments.bicep the vault name is a
// regular parameter string, so `existing` resolves at compile time and
// `guid(keyVault.id, ...)` is valid.

module kvRoleAssignments 'modules/kv-role-assignments.bicep' = {
  name: 'kvRoleAssignments'
  params: {
    vaultName: keyVault.outputs.vaultName
    apiPrincipalId: containerApps.outputs.apiAppPrincipalId
    frontendPrincipalId: containerApps.outputs.frontendAppPrincipalId
    botPrincipalId: containerApps.outputs.botAppPrincipalId
    useExternalSidecar: useExternalSidecar
  }
}

// ── Outputs ──────────────────────────────────────────────────────────────────

output registryLoginServer string = registry.outputs.loginServer
output postgresServerFqdn string = postgres.outputs.serverFqdn
output keyVaultName string = keyVault.outputs.vaultName
output appInsightsName string = appInsights.outputs.appInsightsName
output containerAppsEnvironmentName string = containerEnv.outputs.environmentName
output frontendFqdn string = containerApps.outputs.frontendAppFqdn
output apiAppName string = containerApps.outputs.apiAppName
output botAppName string = containerApps.outputs.botAppName
output certIdentityId string = certIdentity.outputs.identityId
