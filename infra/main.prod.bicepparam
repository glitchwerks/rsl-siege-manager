using 'main.bicep'

// ── Production parameter file ─────────────────────────────────────────────────
//
// Deploy command (Infra Deploy workflow handles this automatically):
//
//   az deployment group create \
//     --resource-group siege-web-prod \
//     --template-file infra/main.bicep \
//     --parameters infra/main.prod.bicepparam \
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
// deploy time via environment variables or a CI/CD secret store.
//
// Resource group convention:
//   dev  → siege-web-dev
//   prod → siege-web-prod
// ─────────────────────────────────────────────────────────────────────────────

param environment = 'prod'
param appPrefix = 'siege-web'
param location = 'westus'

// ── Container Registry ────────────────────────────────────────────────────────
// Standard SKU enables geo-replication, content trust, and higher throughput
// pull limits compared to Basic. Required for production workloads.
param acrSku = 'Standard'

// Prod ACR is already deployed as siegeacrprod — override keeps the existing
// registry rather than creating a new hyphenated name.
param acrNameOverride = 'siegeacrprod'

param imageTag = 'latest'

// ── PostgreSQL ────────────────────────────────────────────────────────────────
// Burstable B1ms: 1 vCore (burstable), 2 GiB RAM — sufficient for a clan-sized
// user base (<100 concurrent users). Upgrade to D2ds_v5 (GeneralPurpose) if
// query latency becomes an issue under real load. HA can be re-enabled at any
// time without data loss.
param postgresSku = 'Standard_B1ms'
param postgresSkuTier = 'Burstable'
param postgresStorageGB = 32
param postgresBackupRetentionDays = 7   // increase to 35 for maximum retention
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
// 90-day soft-delete retention gives the maximum recovery window for secrets
// accidentally deleted or overwritten. The dev value of 7 days enables fast
// teardown of test environments but is not appropriate for production.
param kvSoftDeleteRetentionDays = 90

// ── Log Analytics ─────────────────────────────────────────────────────────────
// 30 days covers the typical incident investigation window and avoids premium
// data retention charges. Increase to 90 if longer trend analysis is needed.
param logRetentionDays = 30

// ── Container App sizing ──────────────────────────────────────────────────────
// siege-api runs Playwright (headless Chromium) for image generation.
// 0.5/1Gi is tight for Playwright — monitor cold start times and upgrade to
// 1.0/2Gi if image generation times out under real load.
param apiCpu = '0.5'
param apiMemory = '1Gi'
param apiMaxReplicas = 3

// Frontend is static content served by Nginx — 0.25/0.5Gi is sufficient.
param frontendCpu = '0.25'
param frontendMemory = '0.5Gi'

// Bot holds a single Discord WebSocket connection — always 1 replica.
// 0.25/0.5Gi is sufficient for connection management and lightweight event handling.
param botCpu = '0.25'
param botMemory = '0.5Gi'

// ── Custom domain ─────────────────────────────────────────────────────────────
// Injected as VITE_PUBLIC_URL into the frontend container for canonical/og tags.
param publicUrl = 'https://rslsiege.com'

// The bare hostname (no https://) for the custom domain binding.
param customDomainHostname = 'rslsiege.com'

// ── Cloudflare Origin Certificate — two-phase deploy gate ─────────────────────
//
// PHASE 1 (first deploy, before PFX is in KV):
//   Set enableCustomDomain = false (current setting).
//   The Key Vault and user-assigned managed identity are created.
//   No certificate resource or domain binding is created — the deploy succeeds
//   without a cert in KV.
//
// PHASE 2 (after PFX is uploaded to KV):
//   1. Upload the PFX to Key Vault: follow RUNBOOK.md Section 9.
//   2. Set enableCustomDomain = true here.
//   3. Set kvCertSecretUrl to the versionless secret URL, e.g.:
//        https://<vault-name>.vault.azure.net/secrets/cloudflare-origin-cert
//   4. Trigger the Infra Deploy workflow (manual dispatch).
//   5. Set Cloudflare SSL/TLS mode to "Full (strict)" and turn the proxy ON.
//
// See docs/RUNBOOK.md "Custom Domain — Cloudflare Origin Cert Rotation" for
// the full step-by-step guide.

param enableCustomDomain = true

// Set to the versionless secret URL after uploading the PFX to Key Vault.
// Versionless URL lets KV serve the latest version automatically on rotation.
// Example: 'https://siege-web-kv-prod-abc123.vault.azure.net/secrets/cloudflare-origin-cert'
param kvCertSecretUrl = 'https://siege-web-kv-prod-yf3fl2.vault.azure.net/secrets/cloudflare-origin-cert'

// ── Replica scaling ────────────────────────────────────────────────────────────
// API stays warm in prod — Playwright cold starts on a scaled-to-zero replica
// are too slow for an acceptable user experience.
// Frontend is Nginx only; a 2–3s cold start is acceptable in prod.
param apiMinReplicas = 1
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
// Prod day-role IDs deferred until prod rollout of day-role-sync. See siege-web#463.
// Set these to the production Discord role snowflake IDs and re-deploy infra when ready.
param discordDay1RoleId = ''
param discordDay2RoleId = ''

// ── ACR image retention ───────────────────────────────────────────────────────
// Prod currently has ~155 manifests (51/52/52 across api/bot/frontend).
// Release tags (v*) are preserved forever. SHA/commit tags beyond the last 10
// per repo are deleted weekly. Untagged manifests older than 7 days are removed.
// After the first deploy, run once on-demand to clear the existing backlog:
//   az acr task run --name weekly-purge --registry siegeacrprod
param acrPurgeKeepCount = 10
param acrPurgeSchedule = '0 3 * * Sun'
