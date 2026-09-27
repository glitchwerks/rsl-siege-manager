# Deploying to Azure

This is the managed, hands-off path. Azure handles TLS, scaling, secret rotation, automated backups, and container orchestration for you. If you don't want an Azure subscription, see [Self-Host on Any VPS](Self-Host-on-Any-VPS) for the portable path — any VPS or Docker host via Docker Compose + Caddy, no Azure account required.

**What you'll end up with after following this guide:**

- Three Azure Container Apps (API, frontend, bot) running behind Azure's built-in ingress
- PostgreSQL Flexible Server with automated backups
- Azure Key Vault holding every secret, injected into containers at runtime via managed identity (no secrets in environment variable files)
- Azure Container Registry storing your images
- A GitHub Actions pipeline that deploys new images to dev on every push to `main` and promotes to prod when you push a `v*` tag

**Estimated time to first working deploy:** 30–60 minutes.

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Fork the repo and configure GitHub](#2-fork-the-repo-and-configure-github)
3. [Register a Discord application](#3-register-a-discord-application)
4. [Create the resource group](#4-create-the-resource-group)
5. [Provision infrastructure via Bicep](#5-provision-infrastructure-via-bicep)
6. [Push the initial images](#6-push-the-initial-images)
7. [Wire up GitHub Actions for continuous deployment](#7-wire-up-github-actions-for-continuous-deployment)
8. [DNS and custom domain](#8-dns-and-custom-domain)
9. [Smoke test](#9-smoke-test)
10. [Day-two operations](#10-day-two-operations)
11. [Tear down](#11-tear-down)

---

## 1. Prerequisites

### Tools

| Tool | Install | Version check |
|---|---|---|
| Azure CLI | [learn.microsoft.com/cli/azure/install-azure-cli](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) | `az --version` |
| Bicep CLI | `az bicep install` (installs as an az extension) | `az bicep version` |
| Docker Desktop | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) | `docker --version` |
| Git | [git-scm.com](https://git-scm.com/) | `git --version` |
| PowerShell 7+ | [github.com/PowerShell/PowerShell](https://github.com/PowerShell/PowerShell) | `$PSVersionTable.PSVersion` |

### Azure account

- An active Azure subscription. Free tier works for evaluation; the prod Bicep params use `Standard_B1ms` PostgreSQL and Basic-tier Container Apps, which cost roughly $30–$60/month depending on region.
- Your account needs **Contributor** (or Owner) on the subscription, or at minimum on the resource group you'll create.

### Discord

- A Discord server (guild) where you're an admin.
- You'll register a Discord application for OAuth2 login in Step 3.

---

## 2. Fork the repo and configure GitHub

1. Fork `glitchwerks/rsl-siege-manager` on GitHub (or clone it into your own org).
2. The deploy pipeline reads from two GitHub Environments (`dev` and `prod`). You'll populate them with secrets and variables in Step 7 — for now, just note that they need to exist.

---

## 3. Register a Discord application

The app uses Discord OAuth2 for user login. You need a Discord application with a bot user.

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) and click **New Application**.
2. Give it a name (e.g. "RSL Siege Manager").
3. Under **OAuth2 → General**, note the **Client ID** and generate a **Client Secret**. Save both — they become `DISCORD_CLIENT_ID` and `DISCORD_CLIENT_SECRET` in Bicep and in GitHub Secrets.
4. Under **OAuth2 → Redirects**, add your callback URL. For the prod deployment this will be:
   ```
   https://<your-frontend-fqdn>/api/auth/callback
   ```
   You won't have the FQDN yet (it's assigned after Bicep runs), so use a placeholder for now and come back to update it after Step 5. For local testing use `http://localhost:8000/api/auth/callback`.
5. Under **Bot**, click **Add Bot**. Copy the **Bot Token** — this becomes `DISCORD_TOKEN`.
6. Under **Bot**, enable the **Server Members Intent** (required for the bot to read the member list).
7. Invite the bot to your Discord server via **OAuth2 → URL Generator**: select scopes `bot` and `applications.commands`, grant it at minimum the **Send Messages** and **Read Message History** permissions, and open the generated URL.
8. Copy your Discord **Server ID** (right-click your server in Discord → **Copy Server ID** — requires Developer Mode enabled under User Settings → Advanced). This becomes `DISCORD_GUILD_ID`.

---

## 4. Create the resource group

Log in and set your subscription:

```powershell
az login
az account set --subscription "<your-subscription-id-or-name>"
```

Create the production resource group. All examples in this guide use `siege-web-prod` in `westus` — adjust the name and region to suit your preference:

```powershell
az group create --name siege-web-prod --location westus
```

> **Dev environment:** substitute `siege-web-dev` and `westus` (or any region). Dev and prod can live in the same subscription in separate resource groups.

---

## 5. Provision infrastructure via Bicep

### What gets created

A single `az deployment group create` call provisions all resources in one pass:

| Resource | Notes |
|---|---|
| Azure Container Registry | Stores your Docker images |
| PostgreSQL Flexible Server | Primary database; automated backups enabled |
| Azure Key Vault | Holds all secrets; Container Apps read them via managed identity |
| Container Apps Environment | Shared networking layer for all three apps |
| Container Apps (API, frontend, bot) | Three separately-scaled services |
| Log Analytics Workspace | Centralised log storage |
| Application Insights | Metrics, traces, and request telemetry |
| Key Vault role assignments | Grants each Container App's managed identity `Key Vault Secrets User` automatically — no manual step needed |

### Prepare your secrets

The Bicep template accepts secrets as parameters at deploy time — they are written directly into Key Vault and never stored on disk. Prepare values for each parameter listed below before running the deploy command.

| Parameter | What it is | How to get it |
|---|---|---|
| `postgresAdminPassword` | PostgreSQL `siegeadmin` user password | Generate using the PowerShell snippet below |
| `discordToken` | Discord bot token | Discord Developer Portal → your app → Bot → Reset Token |
| `discordGuildId` | Discord server ID | Right-click your server → Copy Server ID (requires Developer Mode) |
| `discordBotApiKey` | Shared key: backend → bot HTTP calls | Generate same as above |
| `botApiKey` | Shared key: bot validates inbound calls | **Must equal `discordBotApiKey`** (see note below) |
| `sessionSecret` | Signs JWT session cookies | Generate same as above |
| `discordClientId` | Discord OAuth2 app client ID | Discord Developer Portal → your app → OAuth2 |
| `discordClientSecret` | Discord OAuth2 app client secret | Discord Developer Portal → your app → OAuth2 |
| `discordRedirectUri` | Full OAuth2 callback URL | `https://<frontend-fqdn>/api/auth/callback` — use a placeholder on first deploy, update after you have the FQDN |
| `botServiceToken` | Bot-to-backend bearer token | Supply from `BOT_SERVICE_TOKEN`; store it as an environment-scoped secret in hosted deployments |
| `discordRequiredRole` | Discord role required to log in (optional) | Defaults to `Clan Deputies` if omitted — set this to whatever officer/manager role your clan uses |

To generate a random secret for `postgresAdminPassword`, `discordBotApiKey`,
`botApiKey`, `botServiceToken`, and `sessionSecret`:

```powershell
[Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }))
```

> **`discordBotApiKey` and `botApiKey` must always hold the same value.** They are two names for the same shared secret: the backend uses `discordBotApiKey` (stored as `discord-bot-api-key` in Key Vault) to authenticate requests it sends to the bot; the bot uses `botApiKey` (stored as `bot-api-key`) to validate those requests on arrival. If they diverge, every backend → bot call returns 401.

Store your secrets in `.env.deploy.prod` (this file is gitignored — never commit it):

```powershell
# Copy the template
Copy-Item .env.deploy.example .env.deploy.prod
# Open in your editor and fill in every value
notepad .env.deploy.prod
```

`.env.deploy.prod` contents:

```
PG_ADMIN_PASSWORD=<generated>
DISCORD_TOKEN=<from discord developer portal>
DISCORD_BOT_API_KEY=<generated — same value as BOT_API_KEY>
BOT_API_KEY=<generated — same value as DISCORD_BOT_API_KEY>
BOT_SERVICE_TOKEN=<independently generated bot-to-backend bearer token>
DISCORD_GUILD_ID=<your server id>
```

> **Note:** `SESSION_SECRET`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, and `DISCORD_REDIRECT_URI` are not in `.env.deploy.example` (it predates OAuth2 support). Pass them directly on the command line or add them to your `.env.deploy.prod` file and set them as environment variables before running the deploy.

> **`discordRequiredRole`** is optional — if you omit it the Bicep template defaults to `Clan Deputies`. Most self-hosters will need to change this. Set it to the exact (case-sensitive) name of the Discord role your clan uses for siege managers or officers, for example: `--parameters discordRequiredRole="Raid Officers"`

### Run the deploy

```powershell
# From the repo root — load secrets from file
Get-Content .env.deploy.prod |
    Where-Object { $_ -notmatch '^\s*#' -and $_ -match '=' } |
    ForEach-Object {
        $parts = $_ -split '=', 2
        [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim())
    }

# Deploy (run from repo root)
az deployment group create `
    --resource-group siege-web-prod `
    --template-file infra/main.bicep `
    --parameters infra/main.prod.bicepparam `
    --parameters postgresAdminPassword="$env:PG_ADMIN_PASSWORD" `
    --parameters discordToken="$env:DISCORD_TOKEN" `
    --parameters discordBotApiKey="$env:DISCORD_BOT_API_KEY" `
    --parameters botApiKey="$env:BOT_API_KEY" `
    --parameters botServiceToken="$env:BOT_SERVICE_TOKEN" `
    --parameters discordGuildId="$env:DISCORD_GUILD_ID" `
    --parameters sessionSecret="$env:SESSION_SECRET" `
    --parameters discordClientId="$env:DISCORD_CLIENT_ID" `
    --parameters discordClientSecret="$env:DISCORD_CLIENT_SECRET" `
    --parameters discordRedirectUri="$env:DISCORD_REDIRECT_URI"
```

For bash/Linux/macOS:

```bash
source .env.deploy.prod  # or export each variable manually

az deployment group create \
    --resource-group siege-web-prod \
    --template-file infra/main.bicep \
    --parameters infra/main.prod.bicepparam \
    --parameters postgresAdminPassword="$PG_ADMIN_PASSWORD" \
    --parameters discordToken="$DISCORD_TOKEN" \
    --parameters discordBotApiKey="$DISCORD_BOT_API_KEY" \
    --parameters botApiKey="$BOT_API_KEY" \
    --parameters botServiceToken="$BOT_SERVICE_TOKEN" \
    --parameters discordGuildId="$DISCORD_GUILD_ID" \
    --parameters sessionSecret="$SESSION_SECRET" \
    --parameters discordClientId="$DISCORD_CLIENT_ID" \
    --parameters discordClientSecret="$DISCORD_CLIENT_SECRET" \
    --parameters discordRedirectUri="$DISCORD_REDIRECT_URI"
```

The deployment takes 5–15 minutes on a cold resource group (PostgreSQL provisioning dominates).

### Verify the resources were created

```powershell
az resource list --resource-group siege-web-prod --output table
```

You should see entries for: `Microsoft.ContainerRegistry/registries`, `Microsoft.DBforPostgreSQL/flexibleServers`, `Microsoft.KeyVault/vaults`, `Microsoft.App/managedEnvironments`, three `Microsoft.App/containerApps`, `Microsoft.OperationalInsights/workspaces`, and `Microsoft.Insights/components`.

### Get the frontend FQDN

```powershell
az containerapp show `
    --name siege-web-frontend-prod `
    --resource-group siege-web-prod `
    --query "properties.configuration.ingress.fqdn" `
    --output tsv
```

Note this value — it's your app's public URL (e.g. `siege-web-frontend-prod.wonderfulrock-12345678.westus.azurecontainerapps.io`). Go back to the Discord Developer Portal now and add `https://<fqdn>/api/auth/callback` as a redirect URI, then re-run the Bicep deployment with `--parameters discordRedirectUri="https://<fqdn>/api/auth/callback"` to write the correct value into Key Vault.

---

## 6. Push the initial images

The Bicep deployment creates Container Apps with `imageTag=latest` but no images in ACR yet. Use `scripts/bootstrap-images.ps1` to build and push all three:

```powershell
.\scripts\bootstrap-images.ps1 -Env prod -EnvFile .env.deploy.prod
```

This script:
1. Looks up the ACR login server from the `siege-web-prod` resource group
2. Logs Docker in to ACR via `az acr login`
3. Builds `siege-api`, `siege-frontend`, and `siege-bot` from their respective directories
4. Pushes each image tagged with the current Git SHA and as `:latest`
5. Prints the `az deployment group create` command to deploy the new image tag (runs it automatically if `.env.deploy.prod` is present)

After the script completes, verify all three Container Apps are running:

```powershell
az containerapp list `
    --resource-group siege-web-prod `
    --query "[].{name:name, status:properties.runningStatus}" `
    --output table
```

Expected `status` for all three: `Running`.

---

## 7. Wire up GitHub Actions for continuous deployment

The repo ships with two deploy workflows:

| Workflow | File | Trigger |
|---|---|---|
| Application deploy | `.github/workflows/deploy.yml` | Push to `main` (→ dev) or push a `v*` tag (→ prod) |
| Infrastructure deploy | `.github/workflows/infra-deploy.yml` | Manual `workflow_dispatch` only |

### Create a service principal

The workflows authenticate to Azure using a service principal stored as `AZURE_CREDENTIALS`. Create one with Contributor access on both resource groups:

```powershell
az ad sp create-for-rbac `
    --name "siege-web-github-actions" `
    --role Contributor `
    --scopes "/subscriptions/<subscription-id>/resourceGroups/siege-web-prod" `
    --sdk-auth
```

> **Note:** `--sdk-auth` is deprecated in favor of [workload identity federation](https://learn.microsoft.com/azure/developer/github/connect-from-azure-openid-connect) for production deployments, but remains functional and is simpler for initial setup. Consider migrating once your deployment is stable.

Copy the full JSON output — that's the value of `AZURE_CREDENTIALS`.

> Add `"/subscriptions/<subscription-id>/resourceGroups/siege-web-dev"` as a second `--scopes` entry if you're also setting up the dev environment.

### Configure GitHub Environments

In your forked repo, go to **Settings → Environments** and create two environments: `dev` and `prod`.

For each environment, add the following **Secrets** and **Variables**:

#### `prod` environment

**Secrets** (Settings → Environments → prod → Secrets):

| Secret name | Value |
|---|---|
| `AZURE_CREDENTIALS` | Full JSON from the `az ad sp create-for-rbac` output above |
| `POSTGRES_ADMIN_PASSWORD` | Your `PG_ADMIN_PASSWORD` value |
| `DISCORD_TOKEN` | Your bot token |
| `BOT_API_KEY` | Your shared bot API key |
| `SESSION_SECRET` | Your session secret |
| `DISCORD_CLIENT_ID` | Your Discord app client ID |
| `DISCORD_CLIENT_SECRET` | Your Discord app client secret |

**Variables** (Settings → Environments → prod → Variables):

| Variable name | Value |
|---|---|
| `ACR_NAME` | `siegeacrprod` (as provisioned by Bicep) |
| `ACR_LOGIN_SERVER` | `siegeacrprod.azurecr.io` |
| `RESOURCE_GROUP` | `siege-web-prod` |
| `DISCORD_GUILD_ID` | Your Discord server ID |
| `DISCORD_REDIRECT_URI` | `https://<frontend-fqdn>/api/auth/callback` |

Repeat for `dev`, substituting `siege-web-dev`, `siegeacrdev`, `siegeacrdev.azurecr.io`, and the dev FQDN.

### Deploy model

- **Every push to `main`** triggers CI, then builds and pushes new images, then deploys to dev automatically.
- **Pushing a `v*` tag** (e.g. `git tag v1.0.0 && git push origin v1.0.0`) promotes the already-built image from the tagged commit to prod — no rebuild, no separate approval gate. Tag creation is the approval step.
- **Manual dispatch** of `deploy.yml` lets you choose environment and optionally specify an image SHA to redeploy.
- **Infrastructure changes** (editing `infra/`) are deployed manually via `infra-deploy.yml` → workflow dispatch. This workflow runs four CI gates (lint → build → ARM validate → what-if) before any resources change.

---

## 8. DNS and custom domain

Azure Container Apps provide a public FQDN automatically (e.g. `siege-web-frontend-prod.wonderfulrock-12345678.westus.azurecontainerapps.io`). If that's acceptable, skip this section.

Custom domain binding — including the managed TLS certificate — is declared in Bicep and survives every infrastructure re-deployment. You do not need to bind the domain or provision the certificate manually.

### How the Bicep-managed binding works

The `customDomainHostname` parameter (bare hostname, no `https://`) triggers a two-phase provisioning sequence inside `infra/modules/container-apps.bicep`:

1. **Phase 1** — registers the hostname on the frontend Container App with `bindingType: 'Disabled'`. This tells Azure which hostname to validate without yet serving TLS for it.
2. **Phase 2** — provisions an `Azure-managed certificate` (`Microsoft.App/managedEnvironments/managedCertificates`) via CNAME validation, then rebinds the frontend with `bindingType: 'SniEnabled'` and the issued certificate ID.

Both phases run in a single `az deployment group create` call. On subsequent re-deployments the hostname is already registered and the cert already issued, so both phases are no-ops — the binding is preserved automatically.

### Prerequisites before enabling the custom domain parameter

**Step 1 — get your Container App FQDN** (if you haven't already):

```powershell
az containerapp show `
    --name siege-web-frontend-prod `
    --resource-group siege-web-prod `
    --query "properties.configuration.ingress.fqdn" `
    --output tsv
```

**Step 2 — configure DNS at your registrar / DNS provider:**

Add a CNAME record for your domain pointing to the Container App FQDN:

| Type | Host | Value |
|---|---|---|
| `CNAME` | `siege.yourclan.com` (or `@` for an apex domain) | `siege-web-frontend-prod.wonderfulrock-12345678.westus.azurecontainerapps.io` |

> **Apex domains:** most DNS providers do not support a bare CNAME on the apex (`@`). Use a CNAME-flattening provider (e.g. Cloudflare) or a subdomain instead.

> **Cloudflare users:** set the CNAME record to **DNS-only (grey cloud)** during the first deploy. Azure performs CNAME validation to issue the certificate, and Cloudflare's proxy intercepts that validation. Once the certificate has been issued and the binding shows `SniEnabled`, you can re-enable the proxy (orange cloud).

Allow 5–15 minutes for DNS to propagate before deploying.

### Enable the parameter in Bicep

Set both parameters in `infra/main.prod.bicepparam`:

```bicep
param publicUrl           = 'https://siege.yourclan.com'  // sets VITE_PUBLIC_URL (canonical/og tags)
param customDomainHostname = 'siege.yourclan.com'         // bare hostname — no https://
```

Then run the infrastructure deploy (via the `infra-deploy.yml` workflow or `az deployment group create` directly). The managed certificate is provisioned and the binding is activated in one pass.

Setting `customDomainHostname` also automatically configures the backend's `ALLOWED_ORIGINS` env var to `https://<your-domain>`, so CORS is handled without any manual steps.

### After the custom domain is active

Update `discordRedirectUri` to use the new domain. This is a plain Bicep parameter (not a Key Vault secret), so update it in your `.bicepparam` file and redeploy:

1. In `infra/main.prod.bicepparam`, set the new value:

   ```bicep
   param discordRedirectUri = 'https://siege.yourclan.com/api/auth/callback'
   ```

2. Redeploy infrastructure (either via the `infra-deploy.yml` workflow or directly):

   ```powershell
   az deployment group create `
       --resource-group siege-web-prod `
       --template-file infra/main.bicep `
       --parameters infra/main.prod.bicepparam
   ```

   Or pass it as a one-off override without editing the file:

   ```powershell
   az deployment group create `
       --resource-group siege-web-prod `
       --template-file infra/main.bicep `
       --parameters infra/main.prod.bicepparam `
       --parameters discordRedirectUri="https://siege.yourclan.com/api/auth/callback"
   ```

Also update the redirect URI in the Discord Developer Portal (your app → OAuth2 → Redirects) to match.

---

## 9. Smoke test

Run through this checklist after the first deploy (and after any infra change):

- [ ] `GET https://<frontend-fqdn>/api/health` returns `{"status":"ok","db":"connected"}`
- [ ] The frontend loads at `https://<frontend-fqdn>/`
- [ ] Clicking **Sign in with Discord** completes the OAuth flow and lands you back on the app as an authenticated user
- [ ] The members list shows your Discord clan members (requires bot to be connected to your guild)
- [ ] You can create a new siege
- [ ] You can assign a member to a building position
- [ ] All three Container Apps show `Running` status:
  ```powershell
  az containerapp list `
      --resource-group siege-web-prod `
      --query "[].{name:name, status:properties.runningStatus}" `
      --output table
  ```
- [ ] No errors in the API logs in the last 15 minutes:
  ```powershell
  az containerapp logs show `
      --name siege-web-api-prod `
      --resource-group siege-web-prod `
      --tail 50
  ```
- [ ] PostgreSQL automated backups are enabled:
  ```powershell
  az postgres flexible-server show `
      --resource-group siege-web-prod `
      --name <db-server-name> `
      --query "backup" `
      --output json
  ```

---

## 10. Day-two operations

For ongoing operations (log queries, secret rotation, rollbacks, database restores, scaling), see [RUNBOOK.md in the main repo](https://github.com/glitchwerks/rsl-siege-manager/blob/main/docs/RUNBOOK.md). That document assumes your environment is already deployed and uses `siege-web-prod` and the actual resource names from your deployment.

### Quick reference: update a secret

Key Vault secrets are injected into Container Apps at revision creation time. To rotate a secret:

```powershell
# 1. Update the value in Key Vault
az keyvault secret set `
    --vault-name <vault-name> `
    --name discord-token `
    --value "$env:NEW_DISCORD_TOKEN"

# 2. Force a new revision so the app picks up the change
az containerapp update `
    --name siege-web-bot-prod `
    --resource-group siege-web-prod `
    --revision-suffix "secret-rotate-$(Get-Date -Format 'yyyyMMdd')"
```

### Quick reference: deploy infra changes

Any change to `infra/` should be deployed via the `infra-deploy.yml` workflow (not `az` CLI directly, so the CI gates run):

1. Push your Bicep change to a branch and open a PR — this triggers the infra CI gates automatically.
2. After the PR merges to `main`, go to **Actions → Infra Deploy → Run workflow**, select `prod`, and confirm.

---

## 11. Tear down

> This deletes **all resources** in the resource group, including the database. Take a backup first if the data matters.
>
> The Key Vault has a 90-day soft-delete retention in prod. If you need to reuse the same resource group name after deletion, purge the vault first:
> ```powershell
> az keyvault purge --name <vault-name>
> ```

```powershell
az group delete --name siege-web-prod --yes --no-wait
```

---

## All environment variables — origin reference

This table covers every secret and config value the deployed app uses, where it lives, and which service consumes it.

| Name | Stored as | Key Vault secret name | Consumed by | Origin |
|---|---|---|---|---|
| PostgreSQL admin password | Key Vault secret | `database-url` (full connection string) | `siege-api`, `siege-bot` | Bicep `postgresAdminPassword` param |
| Discord bot token | Key Vault secret | `discord-token` | `siege-bot` | Discord Developer Portal → Bot |
| Discord guild ID | Container App env var (plain) | — | `siege-api`, `siege-bot` | Discord server → Copy Server ID |
| Bot API key (backend side) | Key Vault secret | `discord-bot-api-key` | `siege-api` | Self-generated |
| Bot API key (bot side) | Key Vault secret | `bot-api-key` | `siege-bot` | Same value as above |
| Session secret | Key Vault secret | `session-secret` | `siege-api` | Self-generated |
| Discord OAuth2 client ID | Key Vault secret | `discord-client-id` | `siege-api` | Discord Developer Portal → OAuth2 |
| Discord OAuth2 client secret | Key Vault secret | `discord-client-secret` | `siege-api` | Discord Developer Portal → OAuth2 |
| Discord redirect URI | Container App env var (plain) | — | `siege-api` | Your frontend FQDN + `/api/auth/callback` |
| Required Discord role | Container App env var (plain) | — | `siege-api` | Bicep `discordRequiredRole` param (default: `Clan Deputies`) |
| App Insights connection string | Container App env var (plain) | — | `siege-api`, `siege-bot`, `siege-frontend` | Bicep output — set automatically |
