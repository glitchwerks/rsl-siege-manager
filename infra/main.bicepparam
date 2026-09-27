using 'main.bicep'

// ── Development parameter file ────────────────────────────────────────────────
// Copy to main.prod.bicepparam for production and adjust values.
// Sensitive values (passwords, tokens, keys) MUST be set via environment
// variables or Azure CLI --parameters flags — never commit real secrets here.

param environment = 'dev'
param appPrefix = 'siege-web'
param location = 'westus'
param acrSku = 'Basic'
param imageTag = 'latest'

param postgresAdminUser = 'siegeadmin'
// Set via: --parameters postgresAdminPassword=$PG_ADMIN_PASSWORD
param postgresAdminPassword = ''

// Discord
param discordGuildId = '' // Your Discord server ID (non-secret, ok to fill in)
// Set via: --parameters discordToken=$DISCORD_TOKEN
param discordToken = ''
// Set via: --parameters discordBotApiKey=$DISCORD_BOT_API_KEY
param discordBotApiKey = ''
// Set via: --parameters botApiKey=$BOT_API_KEY
param botApiKey = ''
// Set via: --parameters botServiceToken=$BOT_SERVICE_TOKEN
param botServiceToken = ''

// Set via: --parameters slackAlertWebhookUrl=$SLACK_ALERT_WEBHOOK_URL
param slackAlertWebhookUrl = ''

param postgresGeoRedundantBackup = false

// ── External sidecar ──────────────────────────────────────────────────────────
// Default false: bundled bot Container App is provisioned as normal.
// Set true only when substituting an alternate Discord sidecar and also supply
// externalBotApiUrl pointing at its HTTP API.
param useExternalSidecar = false
// param externalBotApiUrl = 'https://your-sidecar.example.com'  // uncomment + set when useExternalSidecar = true

// ── ACR image retention ───────────────────────────────────────────────────────
// Release tags (v*) are never purged. SHA/commit tags beyond the keep count
// are deleted weekly. Untagged manifests older than 7 days are also removed.
param acrPurgeKeepCount = 10
param acrPurgeSchedule = '0 3 * * Sun'
