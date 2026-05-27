@description('Azure region for all resources')
param location string

@description('Environment name (dev, prod)')
param environment string

@description('Short prefix for resource naming')
param appPrefix string

@description('Container Apps Environment resource ID')
param containerAppsEnvironmentId string

@description('Container Apps Environment name — required for the BYO certificate child resource')
param containerAppsEnvironmentName string

@description('Custom hostname to bind to the frontend (e.g. rslsiege.com). Leave empty to skip custom domain setup.')
param customDomainHostname string = ''

@description('When true, binds the Cloudflare Origin Cert from Key Vault to the frontend. Set false on first deploy (before the PFX is uploaded to KV) to avoid a chicken-and-egg error.')
param enableCustomDomain bool = false

@description('Resource ID of the user-assigned managed identity used to authenticate to Key Vault for cert import. Required when enableCustomDomain is true.')
param certIdentityId string = ''

@description('Key Vault secret URL for the Cloudflare Origin Cert PFX (e.g. https://vault.vault.azure.net/secrets/cloudflare-origin-cert). Required when enableCustomDomain is true.')
param kvCertSecretUrl string = ''

@description('Container registry login server (e.g. myregistry.azurecr.io)')
param acrLoginServer string

@description('Image tag to deploy')
param imageTag string = 'latest'

@description('Key Vault URI for secret references')
param keyVaultUri string

@description('Discord guild ID (non-secret)')
param discordGuildId string

@description('Discord OAuth2 redirect URI (non-secret; public URL visible in browser address bar)')
param discordRedirectUri string

@description('ACR admin username for image pull authentication')
param acrUsername string

@description('ACR admin password for image pull authentication')
@secure()
param acrPassword string

// Application Insights connection string is the modern replacement for the
// classic instrumentation key. It encodes both the endpoint URL and the
// resource identity, which means the SDK works correctly in sovereign clouds
// and future endpoint migrations without code changes.
@description('Application Insights connection string for telemetry (pass empty string to disable)')
param appInsightsConnectionString string = ''

@description('Public-facing URL injected as VITE_PUBLIC_URL into the frontend container (e.g. https://rslsiege.com). Leave empty to omit the variable.')
param publicUrl string = ''

// ── CPU / memory sizing ───────────────────────────────────────────────────────
// Container Apps allocates CPU and memory in fixed pairs:
//   0.25 vCPU / 0.5 Gi  — fine for static content or very low traffic
//   0.5  vCPU / 1.0 Gi  — suitable for most API workloads (dev)
//   1.0  vCPU / 2.0 Gi  — production API with Playwright (image generation)
//   2.0  vCPU / 4.0 Gi  — high-traffic or CPU-intensive workloads
// Playwright (used by siege-api for image generation) is the most memory-
// hungry component, so the API gets a larger allocation.

@description('CPU cores for the API app (e.g. "0.5" for dev, "1.0" for prod)')
param apiCpu string = '0.5'

@description('Memory for the API app (e.g. "1Gi" for dev, "2Gi" for prod)')
param apiMemory string = '1Gi'

@description('Max replicas for the API app')
param apiMaxReplicas int = 3

@description('Minimum replicas for the API app (0 = scale to zero when idle)')
param apiMinReplicas int = 1

@description('Minimum replicas for the frontend app (0 = scale to zero when idle)')
param frontendMinReplicas int = 1

@description('CPU cores for the frontend app (e.g. "0.25")')
param frontendCpu string = '0.25'

@description('Memory for the frontend app (e.g. "0.5Gi")')
param frontendMemory string = '0.5Gi'

@description('CPU cores for the bot app (e.g. "0.25")')
param botCpu string = '0.25'

@description('Memory for the bot app (e.g. "0.5Gi")')
param botMemory string = '0.5Gi'

@description('When true, the bundled bot Container App is not provisioned. Use when substituting an external Discord sidecar (e.g. mom-bot). Default false preserves existing behavior.')
param useExternalSidecar bool = false

@description('URL of the alternate sidecar HTTP API. Required when useExternalSidecar is true (e.g. https://my-bot.example.com). Ignored when useExternalSidecar is false.')
param externalBotApiUrl string = ''

@description('Discord role snowflake ID for Day 1 attackers (day-role-sync contract v1.1). When non-empty, injects DISCORD_DAY_1_ROLE_ID into the backend container. Leave empty to omit the env var (required for prod until day-role-sync rollout).')
param discordDay1RoleId string = ''

@description('Discord role snowflake ID for Day 2 attackers (day-role-sync contract v1.1). When non-empty, injects DISCORD_DAY_2_ROLE_ID into the backend container. Leave empty to omit the env var.')
param discordDay2RoleId string = ''

var apiAppName = '${appPrefix}-api-${environment}'
var frontendAppName = '${appPrefix}-frontend-${environment}'
var botAppName = '${appPrefix}-bot-${environment}'

// Whether a custom domain binding should actually be applied. Both conditions
// must be true: the hostname must be non-empty AND the enableCustomDomain flag
// must be set. This supports the two-phase deploy:
//   Phase 1 (enableCustomDomain = false): infrastructure deploys, KV + UAMI
//            are created, user uploads PFX to KV.
//   Phase 2 (enableCustomDomain = true):  cert resource is imported from KV
//            and bound to the frontend. No DNS validation needed — CF handles
//            all public resolution; Azure only talks to KV.
var bindCert = enableCustomDomain && !empty(customDomainHostname)

// Stable, predictable certificate resource name derived from the hostname.
// Dots are replaced with hyphens to satisfy Azure resource name rules.
// The name is capped at 60 chars to avoid ARM validation errors.
var certResourceName = bindCert ? take('${replace(customDomainHostname, '.', '-')}-origin-cert', 60) : 'placeholder-cert'

// ── siege-api ────────────────────────────────────────────────────────────────

resource apiApp 'Microsoft.App/containerApps@2025-07-01' = {
  name: apiAppName
  location: location
  tags: { project: appPrefix, environment: environment }
  identity: {
    // System-assigned managed identity lets the app authenticate to Key Vault
    // and ACR without storing any credentials — Azure handles the token lifecycle.
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: containerAppsEnvironmentId
    configuration: {
      ingress: {
        // Internal-only ingress: the API is not directly reachable from the
        // internet. Traffic arrives via the frontend Nginx proxy (/api/* prefix).
        external: false
        targetPort: 8000
        transport: 'http'
        allowInsecure: true // Nginx proxies plain HTTP internally; without this Azure redirects to HTTPS (301), causing browsers to downgrade POST → GET → 405
      }
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
        {
          // Key Vault reference — value is fetched at runtime using the
          // app's managed identity. No secret value is stored in the Bicep
          // template or the Container Apps configuration plane.
          name: 'database-url'
          keyVaultUrl: '${keyVaultUri}secrets/database-url'
          identity: 'system'
        }
        {
          name: 'discord-bot-api-key'
          keyVaultUrl: '${keyVaultUri}secrets/discord-bot-api-key'
          identity: 'system'
        }
        {
          name: 'session-secret'
          keyVaultUrl: '${keyVaultUri}secrets/session-secret'
          identity: 'system'
        }
        {
          name: 'discord-client-id'
          keyVaultUrl: '${keyVaultUri}secrets/discord-client-id'
          identity: 'system'
        }
        {
          name: 'discord-client-secret'
          keyVaultUrl: '${keyVaultUri}secrets/discord-client-secret'
          identity: 'system'
        }
        {
          // Separate KV secret for the inbound bot→backend trust boundary.
          // bot-service-token backs BOT_SERVICE_TOKEN (verifies requests FROM the
          // bot TO the backend). Splitting it off discord-bot-api-key gives each
          // direction its own rotation surface — see issue #443.
          name: 'bot-service-token'
          keyVaultUrl: '${keyVaultUri}secrets/bot-service-token'
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'siege-api'
          image: '${acrLoginServer}/siege-api:${imageTag}'
          resources: {
            cpu: json(apiCpu)
            memory: apiMemory
          }
          env: concat(
            [
              { name: 'DATABASE_URL', secretRef: 'database-url' }
              { name: 'DISCORD_BOT_API_KEY', secretRef: 'discord-bot-api-key' }
              // When useExternalSidecar is true, point at the operator-supplied URL;
              // otherwise use the bundled bot's internal Container Apps service name.
              { name: 'DISCORD_BOT_API_URL', value: useExternalSidecar ? externalBotApiUrl : 'http://${botAppName}' }
              { name: 'DISCORD_GUILD_ID', value: discordGuildId }
              { name: 'ENVIRONMENT', value: environment }
              { name: 'SESSION_SECRET', secretRef: 'session-secret' }
              { name: 'DISCORD_CLIENT_ID', secretRef: 'discord-client-id' }
              { name: 'DISCORD_CLIENT_SECRET', secretRef: 'discord-client-secret' }
              { name: 'DISCORD_REDIRECT_URI', value: discordRedirectUri }
              { name: 'BOT_SERVICE_TOKEN', secretRef: 'bot-service-token' }
              // When a custom domain is configured, allow CORS from that origin so
              // the browser can reach /api/* from the custom domain frontend.
              // Falls back to localhost:5173 for dev deployments without a custom domain.
              { name: 'ALLOWED_ORIGINS', value: !empty(customDomainHostname) ? 'https://${customDomainHostname}' : 'http://localhost:5173' }
            ],
            // Only inject the Application Insights connection string and
            // OTEL_SERVICE_NAME when a connection string has been provided.
            // OTEL_SERVICE_NAME is what the Azure Monitor distro uses to set
            // the service.name resource attribute, which App Insights surfaces
            // as cloud_RoleName.  Without it every span lands under the
            // synthetic "unknown_service" node and the Application Map cannot
            // render.  The frontend container (nginx, no SDK) does not need
            // either variable.
            empty(appInsightsConnectionString) ? [] : [
              { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
              { name: 'OTEL_SERVICE_NAME', value: 'siege-api' }
            ],
            // Day-role sync contract v1.1: inject role snowflake IDs only when
            // non-empty. Omitting the env var entirely (rather than setting it to
            // "") is required because pydantic-settings parses these as int | None
            // and an empty string would fail validation. Prod leaves both empty
            // until the day-role-sync feature is rolled out (see issue #463).
            empty(discordDay1RoleId) ? [] : [
              { name: 'DISCORD_DAY_1_ROLE_ID', value: discordDay1RoleId }
            ],
            empty(discordDay2RoleId) ? [] : [
              { name: 'DISCORD_DAY_2_ROLE_ID', value: discordDay2RoleId }
            ]
          )
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/api/health'
                port: 8000
              }
              initialDelaySeconds: 15
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: apiMinReplicas
        maxReplicas: apiMaxReplicas
      }
    }
  }
}

// ── Cloudflare Origin Certificate (BYO from Key Vault) ────────────────────────
//
// DESIGN: Why BYO cert instead of Azure-managed cert?
//
// Azure managed certificates (Microsoft.App/managedEnvironments/managedCertificates)
// use CNAME validation by DigiCert — Azure must be able to resolve your hostname
// directly. This fails for two reasons in this topology:
//
//   1. rslsiege.com is an apex domain. CNAME records at the zone apex (naked domain)
//      are not allowed in standard DNS (RFC 1912). Azure's validator cannot find the
//      required CNAME record.
//   2. Cloudflare proxy is permanently ON for DDoS protection and WAF. Even if a
//      CNAME existed, Cloudflare's proxy intercepts the validation request — DigiCert
//      sees Cloudflare IPs, not Azure's, so validation fails.
//
// Solution: Cloudflare Origin Certificates (free, 15-year validity). Generated in
// the Cloudflare dashboard, converted to PFX via scripts/generate-origin-pfx.ps1,
// and uploaded to Key Vault as a secret. Azure never talks to a public CA —
// no validation needed. Cloudflare acts as the public CA to browsers (its own
// trusted cert); the origin cert is only used for the Cloudflare → Azure leg.
//
// NETWORK SECURITY NOTE: Key Vault is accessible from public networks with RBAC.
// This is acceptable because:
//   - Access requires valid Azure AD token from the UAMI (not just network access)
//   - No PII/PHI/financial data is stored in this vault (only infra secrets)
//   - Private endpoint would require VNet integration ($$$) without meaningful security gain
// Revisit if: compliance requirements change (PCI, HIPAA), or threat model includes
// nation-state actors with Azure AD compromise capability.
//
// PHASE GATE (enableCustomDomain param):
// The cert resource is only created when enableCustomDomain = true. On first
// deploy (flag = false), the KV and UAMI are provisioned but no cert binding
// occurs. After the user uploads the PFX to KV, a second deploy with the flag
// set to true completes the binding. This avoids an ARM deployment failure that
// would occur if the KV cert reference pointed at a non-existent secret.

// API VERSION NOTE: Using preview `2024-08-02-preview` because the environment-level
// user-assigned identity block is not present in the GA API `2024-03-01`. The preview
// surface is scoped to this resource only; all other managed-env operations stay on GA.
// MIGRATION: revert to GA once the identity block ships in a GA API version.
// Track: https://aka.ms/azure-rest-api-specs (Microsoft.App API changelog).
resource containerAppsEnv 'Microsoft.App/managedEnvironments@2024-08-02-preview' existing = {
  name: containerAppsEnvironmentName
}

// API VERSION NOTE: Using preview `2024-08-02-preview` because `certificateKeyVaultProperties`
// is not present in the GA API `2024-03-01`. This property is the KV-import path that lets
// Container Apps pull the PFX directly from Key Vault without staging it through Bicep.
// The preview surface is scoped to this resource only; all other resources stay on GA.
// MIGRATION: revert to GA once certificateKeyVaultProperties ships in a GA API version.
// Track: https://aka.ms/azure-rest-api-specs (Microsoft.App API changelog).
//
// BYO certificate resource: Container Apps imports the PFX from Key Vault using
// the environment's user-assigned managed identity. The certificateKeyVaultProperties
// block takes:
//   identity   — the resource ID of the UAMI (or "System" for system-assigned)
//   keyVaultUrl — the versioned or versionless URL of the KV secret holding the PFX
//
// This resource is only created when both enableCustomDomain=true and a non-empty
// customDomainHostname are provided. Bicep conditional resources are declared
// with an `if` expression on the resource statement.
resource originCert 'Microsoft.App/managedEnvironments/certificates@2024-08-02-preview' = if (bindCert) {
  parent: containerAppsEnv
  name: certResourceName
  location: location
  properties: {
    // certificateKeyVaultProperties is the KV-import path — the cert bytes
    // are never stored in the Bicep template or in Container Apps configuration.
    certificateKeyVaultProperties: {
      identity: certIdentityId
      keyVaultUrl: kvCertSecretUrl
    }
  }
}

// ── siege-frontend ────────────────────────────────────────────────────────────
//
// When bindCert is true, the frontend binds the origin cert with SniEnabled.
// When false, no customDomains block is emitted and the app is reachable only
// via its default *.azurecontainerapps.io FQDN.
//
// certificateId format for BYO (unmanaged) certs:
//   /subscriptions/{sub}/resourceGroups/{rg}/providers/
//   Microsoft.App/managedEnvironments/{env}/certificates/{name}
// This is the .id property of the originCert resource — Bicep resolves it
// symbolically, which also creates an implicit dependsOn.

resource frontendApp 'Microsoft.App/containerApps@2025-07-01' = {
  name: frontendAppName
  location: location
  tags: { project: appPrefix, environment: environment }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: containerAppsEnvironmentId
    configuration: {
      ingress: {
        // Public ingress: the frontend is the only internet-facing entry point.
        external: true
        targetPort: 80
        transport: 'http'
        // When bindCert is true, attach the origin cert and enable SNI.
        // Bicep infers the dependency on originCert through the symbolic reference
        // to originCert.id — no explicit dependsOn needed.
        customDomains: bindCert
          ? [
              {
                name: customDomainHostname
                bindingType: 'SniEnabled'
                certificateId: originCert.id
              }
            ]
          : []
      }
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'siege-frontend'
          image: '${acrLoginServer}/siege-frontend:${imageTag}'
          resources: {
            cpu: json(frontendCpu)
            memory: frontendMemory
          }
          env: concat(
            [
              { name: 'VITE_API_URL', value: '' } // nginx proxies /api/* to siege-api internally
              { name: 'API_UPSTREAM', value: 'http://${apiAppName}' }
            ],
            empty(appInsightsConnectionString) ? [] : [
              { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            ],
            empty(publicUrl) ? [] : [
              { name: 'VITE_PUBLIC_URL', value: publicUrl }
            ]
          )
        }
      ]
      scale: {
        minReplicas: frontendMinReplicas
        maxReplicas: 2
      }
    }
  }
}

// ── siege-bot ─────────────────────────────────────────────────────────────────
//
// Conditionally provisioned: skipped when useExternalSidecar = true so the
// operator can run their own Discord sidecar without the bundled bot racing for
// the same DISCORD_TOKEN. The Discord singleton-token constraint means both
// cannot coexist — enforcement is at the infrastructure layer, not documentation.

resource botApp 'Microsoft.App/containerApps@2025-07-01' = if (!useExternalSidecar) {
  name: botAppName
  location: location
  tags: { project: appPrefix, environment: environment }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: containerAppsEnvironmentId
    configuration: {
      ingress: {
        // Bot has no public ingress at all — it is only reachable from
        // siege-api within the same Container Apps environment via its
        // internal service name (http://siege-bot-<env>).
        external: false
        targetPort: 8001
        transport: 'http'
        allowInsecure: true // Same as API: internal HTTP calls from siege-api must not be redirected to HTTPS
      }
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
        {
          name: 'discord-token'
          keyVaultUrl: '${keyVaultUri}secrets/discord-token'
          identity: 'system'
        }
        {
          name: 'database-url'
          keyVaultUrl: '${keyVaultUri}secrets/database-url'
          identity: 'system'
        }
        {
          name: 'bot-api-key'
          keyVaultUrl: '${keyVaultUri}secrets/bot-api-key'
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'siege-bot'
          image: '${acrLoginServer}/siege-bot:${imageTag}'
          resources: {
            cpu: json(botCpu)
            memory: botMemory
          }
          env: concat(
            [
              { name: 'DISCORD_TOKEN', secretRef: 'discord-token' }
              { name: 'DATABASE_URL', secretRef: 'database-url' }
              { name: 'BOT_API_KEY', secretRef: 'bot-api-key' }
              { name: 'DISCORD_GUILD_ID', value: discordGuildId }
              { name: 'ENVIRONMENT', value: environment }
            ],
            // Inject App Insights connection string and OTEL_SERVICE_NAME for
            // the bot sidecar when telemetry is enabled.  See the API comment
            // block above for the rationale on OTEL_SERVICE_NAME.
            empty(appInsightsConnectionString) ? [] : [
              { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
              { name: 'OTEL_SERVICE_NAME', value: 'siege-bot' }
            ]
          )
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/api/health'
                port: 8001
              }
              initialDelaySeconds: 20
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        // Bot maintains a single replica — it holds a stateful Discord WebSocket
        // connection and multiple replicas would create duplicate event handlers.
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

output apiAppName string = apiApp.name
output apiAppPrincipalId string = apiApp.identity.principalId
output frontendAppName string = frontendApp.name
output frontendAppFqdn string = frontendApp.properties.configuration.ingress.fqdn
output frontendAppPrincipalId string = frontendApp.identity.principalId
// Safe-nav (`?.`) returns null if botApp isn't created (useExternalSidecar=true); `?? ''` falls back to an empty string so downstream consumers don't get null.
output botAppName string = botApp.?name ?? ''
output botAppPrincipalId string = botApp.?identity.?principalId ?? ''
output useExternalSidecar bool = useExternalSidecar
