using 'main.bicep'

// ── Development parameter file ────────────────────────────────────────────────
//
// Deploy command:
//
//   az deployment group create \
//     --resource-group siege-web-dev \
//     --template-file infra/main.bicep \
//     --parameters infra/main.dev.bicepparam \
//     --parameters postgresAdminPassword="$PG_ADMIN_PASSWORD" \
//     --parameters discordToken="$DISCORD_TOKEN" \
//     --parameters discordBotApiKey="$DISCORD_BOT_API_KEY" \
//     --parameters botApiKey="$BOT_API_KEY" \
//     --parameters discordGuildId="$DISCORD_GUILD_ID" \
//     --parameters sessionSecret="$SESSION_SECRET" \
//     --parameters discordClientId="$DISCORD_CLIENT_ID" \
//     --parameters discordClientSecret="$DISCORD_CLIENT_SECRET"
//     --parameters discordRedirectUri="$DISCORD_REDIRECT_URI"
//
// NEVER commit real secrets here. All @secure() params must be supplied at
// deploy time via environment variables or CLI --parameters flags.
//
// Resource group convention:
//   dev  → siege-web-dev
//   prod → siege-web-prod
// ─────────────────────────────────────────────────────────────────────────────

param environment = 'dev'
param appPrefix = 'siege-web'
param location = 'westus'

// ── Container Registry ────────────────────────────────────────────────────────
// Basic SKU is sufficient for dev: lower throughput limits and no geo-
// replication, but all required features (push/pull, webhooks) are present.
param acrSku = 'Basic'

// The default generated name 'siegeacrdev' is already taken globally (ACR names
// are unique across all Azure tenants). 'siegewebacr' was confirmed available at
// provisioning time — verify with:
//   az acr check-name --name siegewebacr
param acrNameOverride = 'siegewebacr'

param imageTag = 'latest'

// ── PostgreSQL ────────────────────────────────────────────────────────────────
// Burstable B1ms: 1 vCore (burstable), 2 GiB RAM — sufficient for dev/testing
// workloads. Saves ~$15/month vs B2s. Not eligible for HA; no geo-redundant
// backup. Switch to B2s or GeneralPurpose if load-testing is needed.
param postgresSku = 'Standard_B1ms'
param postgresSkuTier = 'Burstable'
param postgresStorageGB = 32
param postgresBackupRetentionDays = 7
param postgresGeoRedundantBackup = false
param postgresHighAvailability = 'Disabled'

param postgresAdminUser = 'siegeadmin'
// Supply at deploy time: --parameters postgresAdminPassword="$PG_ADMIN_PASSWORD"
param postgresAdminPassword = ''

// ── Discord secrets ───────────────────────────────────────────────────────────
param discordGuildId = '' // Your Discord server ID (non-secret; ok to fill in)
// Supply at deploy time:
param discordToken = ''
param discordBotApiKey = ''
param botApiKey = ''

// ── OAuth2 secrets ────────────────────────────────────────────────────────────
// Supply at deploy time:
param sessionSecret = ''
param discordClientId = ''
param discordClientSecret = ''

// ── OAuth2 config (non-secret) ────────────────────────────────────────────────
// The redirect URI is a public URL (visible in the browser address bar during
// the OAuth flow). It is config, not a secret — supply inline or via vars.*.
param discordRedirectUri = ''

// ── Key Vault ─────────────────────────────────────────────────────────────────
// 7-day soft-delete retention is the minimum allowed by Azure and enables fast
// teardown of the dev environment without waiting out a long purge window.
// Do not use 7 days in production — accidental deletion is harder to recover.
param kvSoftDeleteRetentionDays = 7

// ── Log Analytics ─────────────────────────────────────────────────────────────
// 30 days is sufficient for dev debugging. Reduces data ingestion cost while
// still covering a reasonable debugging window for active development.
param logRetentionDays = 30

// ── Container App sizing ──────────────────────────────────────────────────────
// Minimum viable allocations for dev — keeps Container Apps costs low while
// the environment is mostly idle between test sessions.
//
// siege-api runs Playwright (headless Chromium) for image generation.
// 0.5 vCPU / 1 GiB is tight; increase to 1.0/2Gi if browser launch timeouts
// are observed during dev testing.
param apiCpu = '0.5'
param apiMemory = '1Gi'
param apiMaxReplicas = 2

// Frontend is static Nginx — minimal resources needed.
param frontendCpu = '0.25'
param frontendMemory = '0.5Gi'

// Bot holds one Discord WebSocket — always 1 replica; 0.25/0.5Gi is enough
// for the connection and lightweight event handling in dev.
param botCpu = '0.25'
param botMemory = '0.5Gi'

// ── Replica scaling ────────────────────────────────────────────────────────────
// Scale API and frontend to zero when dev is idle. Saves ~$40/month when the
// environment is not being actively used. Trade-off: expect a 5–10s cold start
// after an idle period while the Container App spins up a new replica.
// Bot is excluded — it holds a Discord WebSocket and must stay warm.
param apiMinReplicas = 0
param frontendMinReplicas = 0

// ── Monitoring ────────────────────────────────────────────────────────────────
// Alert email recipient for action group. Same address for dev and prod in v1.
// Confirmed by user: cmb_dev@outlook.com (2026-04-29, Issue #246).
param alertEmail = 'cmb_dev@outlook.com'

// ── External sidecar ──────────────────────────────────────────────────────────
// Default false: bundled bot Container App is provisioned as normal.
// Set true only when substituting an alternate Discord sidecar and also supply
// externalBotApiUrl pointing at its HTTP API.
param useExternalSidecar = false
// param externalBotApiUrl = 'https://your-sidecar.example.com'  // uncomment + set when useExternalSidecar = true

// ── Day-role sync ─────────────────────────────────────────────────────────────
// Snowflake IDs for the Day 1 and Day 2 attacker Discord roles.
// These are injected as DISCORD_DAY_1_ROLE_ID / DISCORD_DAY_2_ROLE_ID into
// the backend container (day-role-sync contract v1.1). See issue #463.
param discordDay1RoleId = '1385267221206405171'
param discordDay2RoleId = '1385267473099653170'

// ── ACR image retention ───────────────────────────────────────────────────────
// Dev currently has ~364 manifests (127/126/111 across api/bot/frontend).
// Release tags (v*) are preserved forever. SHA/commit tags beyond the last 10
// per repo are deleted weekly. Untagged manifests older than 7 days are removed.
// After the first deploy, run once on-demand to clear the existing backlog:
//   az acr task run --name weekly-purge --registry siegewebacr
param acrPurgeKeepCount = 10
param acrPurgeSchedule = '0 3 * * Sun'
