# Siege Assignment App — Operations Runbook

Practical reference for the on-call operator or maintainer. All Azure CLI commands assume
`az login` has been run and the correct subscription is active.

Replace placeholders throughout:

| Placeholder | Example value |
|---|---|
| `{RESOURCE_GROUP}` | `siege-prod-rg` |
| `{CONTAINER_APP_API}` | `siege-api` |
| `{CONTAINER_APP_FRONTEND}` | `siege-frontend` |
| `{CONTAINER_APP_BOT}` | `siege-bot` |
| `{ACR_NAME}` | `siegeacr` |
| `{KEY_VAULT_NAME}` | `siege-kv` |
| `{DB_SERVER_NAME}` | `siege-db` |
| `{DB_NAME}` | `siege` |
| `{LOG_ANALYTICS_WORKSPACE}` | `siege-logs` |
| `{ENVIRONMENT_NAME}` | `siege-env` |

---

## 1. Restarting Containers

### Restart a specific Azure Container App (force new revision)

Azure Container Apps do not have a "restart" button. The recommended restart equivalent is
to force a new revision with the same image:

```bash
# Restart siege-api
az containerapp update \
  --name {CONTAINER_APP_API} \
  --resource-group {RESOURCE_GROUP} \
  --revision-suffix "restart-$(date +%s)"

# Restart siege-frontend
az containerapp update \
  --name {CONTAINER_APP_FRONTEND} \
  --resource-group {RESOURCE_GROUP} \
  --revision-suffix "restart-$(date +%s)"

# Restart siege-bot
az containerapp update \
  --name {CONTAINER_APP_BOT} \
  --resource-group {RESOURCE_GROUP} \
  --revision-suffix "restart-$(date +%s)"
```

### Force a new deployment revision (redeploy latest image)

```bash
# Get the current image tag for siege-api
az containerapp show \
  --name {CONTAINER_APP_API} \
  --resource-group {RESOURCE_GROUP} \
  --query "properties.template.containers[0].image" \
  --output tsv

# Redeploy with the same (or new) image tag
az containerapp update \
  --name {CONTAINER_APP_API} \
  --resource-group {RESOURCE_GROUP} \
  --image {ACR_NAME}.azurecr.io/siege-api:latest
```

### Restart the local docker-compose stack

```bash
# Stop and remove containers, then bring everything back up
docker-compose down
docker-compose up --build

# Restart a single service without rebuilding
docker-compose restart backend

# Full rebuild of a single service
docker-compose up --build backend
```

---

## 2. Rolling Back a Bad Deployment

### Identify the previous good revision

```bash
# List all revisions for siege-api, sorted by creation time
az containerapp revision list \
  --name {CONTAINER_APP_API} \
  --resource-group {RESOURCE_GROUP} \
  --query "sort_by([].{Name:name, Created:properties.createdTime, Active:properties.active, Traffic:properties.trafficWeight}, &Created)" \
  --output table
```

Note the revision name of the last known-good revision (e.g., `siege-api--abc1234`).

### Route traffic back to a previous revision (traffic splitting)

```bash
# Send 100% of traffic to the previous good revision
az containerapp ingress traffic set \
  --name {CONTAINER_APP_API} \
  --resource-group {RESOURCE_GROUP} \
  --revision-weight {PREVIOUS_REVISION_NAME}=100
```

To confirm the traffic weight took effect:

```bash
az containerapp ingress traffic show \
  --name {CONTAINER_APP_API} \
  --resource-group {RESOURCE_GROUP}
```

### Redeploy a specific Docker image tag

```bash
# Deploy a specific tagged image (e.g., a prior build tag like 20260318-abcd123)
az containerapp update \
  --name {CONTAINER_APP_API} \
  --resource-group {RESOURCE_GROUP} \
  --image {ACR_NAME}.azurecr.io/siege-api:{PREVIOUS_TAG}
```

Find available image tags in ACR:

```bash
az acr repository show-tags \
  --name {ACR_NAME} \
  --repository siege-api \
  --orderby time_desc \
  --output table
```

### Roll back the database migration (Alembic downgrade)

> Only do this if the bad deployment included a schema migration. Rolling back the DB
> without rolling back the code (or vice versa) will cause errors.

First, exec into the backend container or run from a machine with DB access and the
`backend/` virtualenv activated:

```bash
# Downgrade one step
alembic downgrade -1

# Downgrade to a specific revision ID (find IDs in backend/alembic/versions/)
alembic downgrade {REVISION_ID}

# Check current head after downgrade
alembic current
```

Then redeploy the previous application image as shown above.

---

## 3. Restoring from Database Backup

Azure PostgreSQL Flexible Server performs automated backups. Point-in-time restore creates
a **new server** from the backup — it does not overwrite the existing server.

### Point-in-time restore

```bash
# Restore to a specific point in time (ISO 8601 UTC)
az postgres flexible-server restore \
  --resource-group {RESOURCE_GROUP} \
  --name {DB_SERVER_NAME}-restored \
  --source-server {DB_SERVER_NAME} \
  --restore-time "2026-03-18T14:00:00Z"
```

This creates a new server named `{DB_SERVER_NAME}-restored`. The operation takes
10–30 minutes depending on database size.

Monitor restore progress:

```bash
az postgres flexible-server show \
  --resource-group {RESOURCE_GROUP} \
  --name {DB_SERVER_NAME}-restored \
  --query "state" \
  --output tsv
# Expected final state: "Ready"
```

### Verify the restore was successful

Connect to the restored server and spot-check key tables:

```bash
# Connect via psql (install psql locally or use Azure Cloud Shell)
psql "host={DB_SERVER_NAME}-restored.postgres.database.azure.com \
      port=5432 \
      dbname={DB_NAME} \
      user={DB_ADMIN_USER} \
      sslmode=require"
```

```sql
-- Verify record counts look correct
SELECT COUNT(*) FROM members;
SELECT COUNT(*) FROM sieges;
SELECT COUNT(*) FROM positions;

-- Check the most recent siege
SELECT id, status, created_at FROM sieges ORDER BY created_at DESC LIMIT 5;

-- Check alembic version is at expected head
SELECT version_num FROM alembic_version;
```

### Reconnect the app to the restored database

1. Update the `database-url` secret in Key Vault to point to the restored server hostname:

```bash
az keyvault secret set \
  --vault-name {KEY_VAULT_NAME} \
  --name "database-url" \
  --value "postgresql+asyncpg://{USER}:{PASSWORD}@{DB_SERVER_NAME}-restored.postgres.database.azure.com/{DB_NAME}"
```

2. Force a new revision on each container app that reads the DB (see Section 4 for the
   full secret rotation + revision procedure):

```bash
az containerapp update \
  --name {CONTAINER_APP_API} \
  --resource-group {RESOURCE_GROUP} \
  --revision-suffix "db-restore-$(date +%s)"
```

3. Hit the health endpoint to confirm DB connectivity:

```bash
curl https://{FRONTEND_URL}/api/health
# Expected: {"status":"ok","db":"connected"}
```

---

## 4. Rotating Secrets

### Secrets inventory

| Secret name (Key Vault) | Used by | Purpose |
|---|---|---|
| `database-url` | `siege-api`, `siege-bot` | PostgreSQL connection string (asyncpg format) |
| `discord-token` | `siege-bot` | Bot login token for the Discord API |
| `discord-guild-id` | `siege-api`, `siege-bot` | Targets the correct Discord server |
| `discord-bot-api-key` | `siege-api` | Auth header sent from backend → bot HTTP sidecar |
| `bot-api-key` | `siege-bot` | Validates inbound requests on the bot HTTP API |

Note: `discord-bot-api-key` (the key the backend uses to call the bot) and `bot-api-key`
(the key the bot validates against) must always match. Rotate them together.

### Update a secret in Azure Key Vault

```bash
# Example: rotate the discord-token
az keyvault secret set \
  --vault-name {KEY_VAULT_NAME} \
  --name "discord-token" \
  --value "{NEW_TOKEN_VALUE}"
```

Confirm the new version is active:

```bash
az keyvault secret show \
  --vault-name {KEY_VAULT_NAME} \
  --name "discord-token" \
  --query "{Version:id, Updated:attributes.updated}" \
  --output table
```

### Force Container Apps to pick up updated Key Vault secrets

Container Apps cache secrets at revision creation time. A new revision must be created
to pick up the updated value.

```bash
# Force new revision for siege-api (picks up database-url, discord-guild-id, discord-bot-api-key)
az containerapp update \
  --name {CONTAINER_APP_API} \
  --resource-group {RESOURCE_GROUP} \
  --revision-suffix "secret-rotation-$(date +%s)"

# Force new revision for siege-bot (picks up discord-token, discord-guild-id, bot-api-key, database-url)
az containerapp update \
  --name {CONTAINER_APP_BOT} \
  --resource-group {RESOURCE_GROUP} \
  --revision-suffix "secret-rotation-$(date +%s)"
```

After the new revisions are active, verify the health endpoints (see Section 6) to confirm
the app reconnected correctly with the new secret values.

---

## 5. Viewing Logs

### Stream live logs from Azure Container Apps

```bash
# Stream logs from siege-api (most recent 20 lines + follow)
az containerapp logs show \
  --name {CONTAINER_APP_API} \
  --resource-group {RESOURCE_GROUP} \
  --follow \
  --tail 20

# Stream logs from siege-bot
az containerapp logs show \
  --name {CONTAINER_APP_BOT} \
  --resource-group {RESOURCE_GROUP} \
  --follow \
  --tail 20

# Stream from a specific revision
az containerapp logs show \
  --name {CONTAINER_APP_API} \
  --resource-group {RESOURCE_GROUP} \
  --revision {REVISION_NAME} \
  --follow
```

### Query logs in Log Analytics Workspace

Open the Azure portal → Log Analytics Workspaces → `{LOG_ANALYTICS_WORKSPACE}` → Logs,
or run queries via CLI:

```bash
# Errors from siege-api in the last hour
az monitor log-analytics query \
  --workspace {LOG_ANALYTICS_WORKSPACE} \
  --analytics-query "
    ContainerAppConsoleLogs
    | where ContainerAppName == '{CONTAINER_APP_API}'
    | where Log has 'ERROR'
    | where TimeGenerated > ago(1h)
    | project TimeGenerated, Log
    | order by TimeGenerated desc
    | take 100
  " \
  --output table

# Slow requests (>2s) from siege-api
az monitor log-analytics query \
  --workspace {LOG_ANALYTICS_WORKSPACE} \
  --analytics-query "
    ContainerAppConsoleLogs
    | where ContainerAppName == '{CONTAINER_APP_API}'
    | where Log has 'duration'
    | extend duration_ms = extract('duration_ms=(\\\\d+)', 1, Log)
    | where toint(duration_ms) > 2000
    | project TimeGenerated, Log, duration_ms
    | order by TimeGenerated desc
  " \
  --output table
```

### Reading backend structured logs

The FastAPI backend emits structured JSON logs. Key fields to filter on:

| Field | Purpose | Example |
|---|---|---|
| `request_id` | Correlates all log lines for a single HTTP request | `"req_abc123"` |
| `siege_id` | Identifies which siege the operation is for | `42` |
| `member_id` | Identifies which member an operation concerns | `17` |
| `level` | Log severity | `INFO`, `WARNING`, `ERROR` |
| `path` | HTTP path | `"/api/sieges/42/board"` |
| `duration_ms` | Request duration in milliseconds | `312` |

To find all log lines for a specific request:

```bash
az monitor log-analytics query \
  --workspace {LOG_ANALYTICS_WORKSPACE} \
  --analytics-query "
    ContainerAppConsoleLogs
    | where ContainerAppName == '{CONTAINER_APP_API}'
    | where Log has '{REQUEST_ID}'
    | project TimeGenerated, Log
    | order by TimeGenerated asc
  " \
  --output table
```

---

## 6. Monitoring and Alerts

### Application Insights role names

Each service emits telemetry tagged with a `cloud_RoleName` that determines
how it appears in the Application Map and in KQL queries.

| Service | `cloud_RoleName` | Set by |
|---|---|---|
| Backend API (`siege-api` container) | `siege-api` | `OTEL_SERVICE_NAME=siege-api` env var (Bicep) |
| Bot HTTP sidecar (`siege-bot` container) | `siege-bot` | `OTEL_SERVICE_NAME=siege-bot` env var (Bicep) |

When writing KQL monitor queries, always filter on the correct role name:

```kusto
-- Backend requests
requests
| where cloud_RoleName == "siege-api"
| where timestamp > ago(10m)

-- Bot sidecar requests
requests
| where cloud_RoleName == "siege-bot"
| where timestamp > ago(10m)

-- Exceptions from the backend
exceptions
| where cloud_RoleName == "siege-api"
| where timestamp > ago(1h)

-- PostgreSQL dependency calls (SQLAlchemy + asyncpg spans — see note below)
dependencies
| where cloud_RoleName == "siege-api"
| where type == "postgresql"
| where timestamp > ago(1h)

-- Regression watch: confirm DB dependency type string has not changed after a deploy
dependencies
| where cloud_RoleName == "siege-api"
| distinct type
```

> **DB OTel type string — confirmed 2026-04-30 (post PR #265, commit `9a11733`):**
> Running `distinct type` on the dev environment returned exactly two values:
> `"postgresql"` and `"InProc"`. Both `SQLAlchemyInstrumentor` and
> `AsyncPGInstrumentor` emit under the same `"postgresql"` type string —
> Application Insights deduplicates server-side, so a single row per `target`
> appears (Pattern A — no span duplication). The AsyncPG instrumentor is
> retained for defensive coverage of asyncpg-bypass paths (Alembic in-process,
> raw utility scripts). No follow-up PR is needed.
>
> Use the `distinct type` query above as a **regression watch** after future
> deploys that touch `backend/app/telemetry.py` — if a new type string appears,
> it indicates a new instrumentor is emitting spans that should be reviewed for
> duplication.

To check for span duplication (regression watch — Pattern A is the expected baseline):

```kusto
dependencies | where cloud_RoleName == "siege-api" | summarize count() by type, target | order by count_ desc
```

Pattern A (expected): a single `postgresql` row per `target`. If two rows with
comparable counts appear for the same `target`, a new instrumentor may be
producing duplicate spans — investigate before retaining it.

The Application Map should render three named nodes: `siege-api`, `siege-bot`,
and the PostgreSQL database, connected by traffic edges.  If the map shows
`unknown_service` instead, verify that `OTEL_SERVICE_NAME` is set on the
relevant Container App revision (see Bicep `infra/modules/container-apps.bicep`).

### What to watch

| Signal | Threshold | Action |
|---|---|---|
| 5xx error rate | > 1% over 5 min | Check recent deployment; check DB connectivity |
| Request duration (p95) | > 3 seconds | Check `/api/sieges/{id}/board` query plan; check connection pool |
| Bot container restarts | Any | Check Discord token validity; check `discord-guild-id` config |
| DB connection errors | Any | Check `database-url` secret; check firewall rules; check pool exhaustion |
| Image generation duration | > 10 seconds | Playwright cold start in wrong container; check `siege-api` container |

These signals are now configured as Azure Monitor alert rules. See the alert inventory below.

### 6A. Workbook URLs

At-a-glance operational vitals (request rates, latency p50/p95, exception count, bot restarts, image gen latency):

- **Dev:** [siege-web-dev workbook](https://portal.azure.com/#@cmbdevoutlook333.onmicrosoft.com/resource/subscriptions/213aa1f8-32d1-4ffe-8f4d-6e60f1cd9dc0/resourceGroups/siege-web-dev/providers/Microsoft.Insights/workbooks/ef1f3d0a-b955-5028-ae97-2b1732a3b5bf/overview)
- **Prod:** [siege-web-prod workbook](https://portal.azure.com/#@cmbdevoutlook333.onmicrosoft.com/resource/subscriptions/213aa1f8-32d1-4ffe-8f4d-6e60f1cd9dc0/resourceGroups/siege-web-prod/providers/Microsoft.Insights/workbooks/c3bfb777-8256-5580-ab51-65f537101966/overview)

Click into Edit mode to modify; layout is deployed from `infra/modules/workbook.template.json` and re-exporting + committing the JSON propagates changes.

### 6B. Alert Inventory

Five alert rules are active in both dev and prod:

| Alert | Threshold | Meaning | First action |
|---|---|---|---|
| `alert5xxRate` | 5xx rate >1% over 5m | Backend is throwing 500s at >1% of requests | Open dev workbook Tile 2 (4xx/5xx rates), then Tile 3 (top 10 exceptions) to identify the failing endpoint |
| `alertLatencyP95` | Request p95 >3s over 5m | At least 5% of requests taking >3s | Tile 1 (volume + p50/p95) to confirm it's not just one outlier; Tile 6 (DB query duration) for DB causation |
| `alertBotRestart` | Any restart | `siege-bot` process restarted (Container App revision recycled, OOM, crash, or deploy) | Check Container App revisions blade in Azure portal for restart cause; query `traces` for the bot in the 5 min before the alert fired |
| `alertImageGenSlow` | Image gen p95 >10s | Playwright image generation latency degraded | Check `requests \| where name has "generate-images"`; usually correlates with high concurrent image gen or Playwright pool exhaustion |
| `alertDbConnectionError` | Any failed PostgreSQL dependency in 5m | siege-api cannot reach the database; all data-path requests will 500 | Check `database-url` secret; check firewall rules; check pool exhaustion — see existing 'DB connection errors' row in 'What to watch' table |

### 6C. Acknowledgement Policy

All alert rules have `autoMitigate: false` — they stay in the **Fired** state until manually acknowledged in the Azure portal (Azure Monitor → Alerts → select the fired alert → Change state to Acknowledged or Closed).

`muteActionsDuration: PT15M` is set on every rule. This 15-minute mute window suppresses re-notification emails during an ongoing incident, but does **not** auto-resolve the alert — manual acknowledgement is still required.

The 4-week post-launch evaluation window (tracked in #263) will produce real fire-pattern data. Per-alert auto-mitigation policy will be revisited after that window closes.

### 6D. Action Group

Email-only at v1. A single recipient (`cmb_dev@outlook.com`) receives alert emails. Discord channel routing is deferred as future work (no specific issue yet).

If the action group email confirmation email from Azure never arrived, the action group is registered but no emails will be delivered. Re-confirm by navigating to Azure Monitor → Alerts → Action groups → select the group → Test.

### Health endpoints

**Backend health** (public via frontend proxy):

```bash
curl https://{FRONTEND_URL}/api/health
# Expected:
# {"status":"ok","db":"connected"}
```

**Bot health** (internal only — run from siege-api container or via az containerapp exec):

```bash
# Exec into the siege-api container and call the bot sidecar
az containerapp exec \
  --name {CONTAINER_APP_API} \
  --resource-group {RESOURCE_GROUP} \
  --command "curl -s http://siege-bot:8001/api/health"
# Expected:
# {"status":"ok","discord_connected":true}
```

### Post-launch 48-hour checklist

Within the first 48 hours after a production deployment, verify:

- [ ] `GET /api/health` returns `{"status":"ok","db":"connected"}` continuously
- [ ] Bot health returns `{"discord_connected":true}`
- [ ] Application Insights shows no spike in 5xx errors
- [ ] At least one full siege workflow completed end-to-end (create → assign → validate → activate)
- [ ] DM notification batch completes without errors for all members
- [ ] Image generation completes in under 5 seconds
- [ ] Channel post delivers images to Discord successfully
- [ ] Assignment board loads in under 2 seconds with a full 30-building siege
- [ ] No unusual log volume in Log Analytics (check for repeated errors or connection retries)
- [ ] Automated PostgreSQL backup confirmed enabled:
  ```bash
  az postgres flexible-server show \
    --resource-group {RESOURCE_GROUP} \
    --name {DB_SERVER_NAME} \
    --query "backup" \
    --output json
  ```

---

## 7. Common Incident Playbooks

### Bot not sending DMs

**Symptoms:** DM notification batch shows all results as failed. Bot health endpoint
returns `{"discord_connected":false}` or times out.

**Steps:**

1. Check bot container health:
   ```bash
   az containerapp show \
     --name {CONTAINER_APP_BOT} \
     --resource-group {RESOURCE_GROUP} \
     --query "properties.runningStatus" \
     --output tsv
   ```

2. Check recent bot logs for connection errors:
   ```bash
   az containerapp logs show \
     --name {CONTAINER_APP_BOT} \
     --resource-group {RESOURCE_GROUP} \
     --tail 50
   ```
   Look for: `discord.errors.LoginFailure`, `Forbidden`, `Invalid token`.

3. Verify the Discord token is valid — log into the Discord Developer Portal and confirm
   the bot token has not been reset. If it has been reset, rotate `discord-token` in
   Key Vault (see Section 4) and force a new bot revision.

4. Verify `discord-guild-id` matches the actual guild:
   ```bash
   az keyvault secret show \
     --vault-name {KEY_VAULT_NAME} \
     --name "discord-guild-id" \
     --query "value" \
     --output tsv
   ```
   Cross-check with the guild ID visible in Discord (Settings → Advanced → Developer Mode,
   then right-click the server).

5. Verify `bot-api-key` and `discord-bot-api-key` match:
   ```bash
   az keyvault secret show --vault-name {KEY_VAULT_NAME} --name "bot-api-key" --query "value" -o tsv
   az keyvault secret show --vault-name {KEY_VAULT_NAME} --name "discord-bot-api-key" --query "value" -o tsv
   ```
   If they differ, update `discord-bot-api-key` to match and force a new `siege-api` revision.

6. If all config looks correct, restart the bot container (see Section 1) and re-trigger
   the notification batch.

---

### User sees "You need the Clan Deputies role" on login

**Symptoms:** User completes Discord OAuth normally but lands on the login page with an
`unauthorized` error message referencing a missing role.

**Cause:** Either the user does not have the required Discord role, or `DISCORD_REQUIRED_ROLE`
is set to a role name that does not exactly match a role in the server.

**Steps:**

1. Confirm the exact role name in your environment. For Docker / VPS deployments check `.env`:
   ```bash
   grep DISCORD_REQUIRED_ROLE .env
   ```
   For Azure deployments check the Container App environment variable:
   ```bash
   az containerapp show \
     --name {CONTAINER_APP_API} \
     --resource-group {RESOURCE_GROUP} \
     --query "properties.template.containers[0].env[?name=='DISCORD_REQUIRED_ROLE'].value" \
     --output tsv
   ```

2. In Discord, open **Server Settings → Roles** and verify the role name matches exactly —
   the check is case-sensitive. Common mismatches: extra spaces, different capitalisation
   (e.g. `Clan deputies` vs `Clan Deputies`), or a role that was renamed since the env var
   was set.

3. Verify the affected user actually holds the role: right-click the user in Discord →
   **Roles** and confirm the required role is listed.

4. If the role name is wrong, update `DISCORD_REQUIRED_ROLE` and restart the `siege-api`
   container (or force a new Container App revision on Azure). No database changes are
   needed — the check happens at login time only.

---

### Board loads slowly

**Symptoms:** `GET /api/sieges/{id}/board` takes more than 2–3 seconds. Frontend board
page shows a spinner for an extended period.

**Steps:**

1. Check if Playwright is running in `siege-api` unexpectedly (image generation is
   CPU-intensive and should only fire on explicit requests):
   ```bash
   az containerapp logs show \
     --name {CONTAINER_APP_API} \
     --resource-group {RESOURCE_GROUP} \
     --tail 100 | grep -i playwright
   ```
   If Playwright log lines appear during board requests, there is a code path triggering
   image generation incorrectly. Roll back to the previous revision.

2. Check DB query duration in Application Insights or Log Analytics for slow board queries.
   The board endpoint issues nested queries for buildings → groups → positions — an N+1
   issue here will be visible as many short queries in rapid succession.

3. Check PostgreSQL connection pool exhaustion:
   ```bash
   az monitor log-analytics query \
     --workspace {LOG_ANALYTICS_WORKSPACE} \
     --analytics-query "
       ContainerAppConsoleLogs
       | where ContainerAppName == '{CONTAINER_APP_API}'
       | where Log has 'QueuePool limit'
       | project TimeGenerated, Log
       | order by TimeGenerated desc
       | take 20
     " \
     --output table
   ```
   If pool exhaustion is found, consider scaling out the Container App or increasing the
   SQLAlchemy pool size in `backend/app/db/session.py`.

4. If the issue is isolated to one large siege, check how many buildings, groups, and
   positions it has. A siege with all 30 buildings at max level has ~200+ positions.
   This is the expected maximum load.

---

### Deployment stuck

**Symptoms:** GitHub Actions shows a successful image push to ACR, but the Container App
is still running the old revision or the new revision shows a failed status.

**Steps:**

1. Check ACR push succeeded:
   ```bash
   az acr repository show-tags \
     --name {ACR_NAME} \
     --repository siege-api \
     --orderby time_desc \
     --output table
   ```
   The new tag should appear at the top. If it is missing, the Docker build or push failed
   in CI — check the GitHub Actions run logs.

2. Check the Container App revision status:
   ```bash
   az containerapp revision list \
     --name {CONTAINER_APP_API} \
     --resource-group {RESOURCE_GROUP} \
     --query "[].{Name:name, Status:properties.runningState, Created:properties.createdTime}" \
     --output table
   ```
   A revision in `Failed` or `Degraded` state means the container crashed at startup.

3. Get logs from the failed revision:
   ```bash
   az containerapp logs show \
     --name {CONTAINER_APP_API} \
     --resource-group {RESOURCE_GROUP} \
     --revision {FAILED_REVISION_NAME} \
     --tail 100
   ```
   Common causes:
   - Missing environment variable or Key Vault reference that couldn't be resolved
   - Application crash on startup (import error, missing migration, etc.)
   - Image pull failure (wrong tag, ACR auth misconfigured)

4. Verify Key Vault references are resolving. In the Azure portal, navigate to the
   Container App → Secrets and check for any red warning icons indicating a failed
   Key Vault reference.

5. If the new revision is broken, route traffic back to the previous revision
   (see Section 2) while investigating.

---

### Database connection errors

**Symptoms:** Backend logs show `asyncpg.exceptions.ConnectionDoesNotExistError`,
`could not connect to server`, or 500 errors on any endpoint that touches the database.

**Steps:**

1. Verify the `database-url` secret is correctly formed:
   ```bash
   az keyvault secret show \
     --vault-name {KEY_VAULT_NAME} \
     --name "database-url" \
     --query "value" \
     --output tsv
   ```
   Expected format: `postgresql+asyncpg://{USER}:{PASSWORD}@{HOST}/{DB_NAME}`
   Confirm the hostname resolves and the database name is correct.

2. Check PostgreSQL server status:
   ```bash
   az postgres flexible-server show \
     --resource-group {RESOURCE_GROUP} \
     --name {DB_SERVER_NAME} \
     --query "{State:state, FQDN:fullyQualifiedDomainName}" \
     --output table
   ```
   Expected state: `Ready`. If `Stopped`, restart it:
   ```bash
   az postgres flexible-server start \
     --resource-group {RESOURCE_GROUP} \
     --name {DB_SERVER_NAME}
   ```

3. Check PostgreSQL firewall rules. The Container Apps environment's outbound IP range
   must be allowed:
   ```bash
   # List current firewall rules
   az postgres flexible-server firewall-rule list \
     --resource-group {RESOURCE_GROUP} \
     --name {DB_SERVER_NAME} \
     --output table

   # Get the Container Apps environment outbound IP(s)
   az containerapp env show \
     --name {ENVIRONMENT_NAME} \
     --resource-group {RESOURCE_GROUP} \
     --query "properties.staticIp" \
     --output tsv
   ```
   Add a firewall rule if the Container App IP is not listed:
   ```bash
   az postgres flexible-server firewall-rule create \
     --resource-group {RESOURCE_GROUP} \
     --name {DB_SERVER_NAME} \
     --rule-name "container-apps-outbound" \
     --start-ip-address {CONTAINER_APP_OUTBOUND_IP} \
     --end-ip-address {CONTAINER_APP_OUTBOUND_IP}
   ```

4. Check for connection pool exhaustion (see Board loads slowly, Step 3 above).
   If the pool is exhausted, restarting the `siege-api` Container App (Section 1) will
   reset all connections as a temporary fix while you investigate the root cause.

---

## 8. Deploys & Drift

### Workflow overview

Two deployment workflows exist:

| Workflow | Trigger | Deploys |
|---|---|---|
| `deploy.yml` | Automatic — `push` to `main`; `v*` tag for prod | Application code (Docker images → Container Apps) |
| `infra-deploy.yml` | Manual — `workflow_dispatch` only (current) | Bicep templates → Azure resource group |

**Bicep changes never deploy themselves.** A PR that modifies both application code and `infra/**` is not fully shipped until you manually run `infra-deploy.yml` against both `dev` and `prod` after the PR merges.

### What the What-if check does (and does not) do

Every PR that touches `infra/**` runs a `What-if Change Impact (dev)` CI check. This check runs `az deployment group what-if` and surfaces what *would* change the next time `infra-deploy.yml` runs — it does not deploy anything. Drift from stacked, unrun Bicep changes is visible here, but the check is a backstop, not a substitute for actually running the workflow.

If the What-if output shows unexpected changes, it is a signal that prior Bicep PRs were merged without a follow-up `infra-deploy.yml` run.

### Hybrid trigger decision (2026-05-07, #272)

**Decision:** `infra-deploy.yml` will adopt **Option C — hybrid trigger**:

- **Dev:** auto-trigger on `push` to `main` with a `paths: ['infra/**']` filter.
- **Prod:** remains `workflow_dispatch`-only.

The `on:` block shape:

```yaml
on:
  push:
    branches: [main]
    paths:
      - 'infra/**'
  workflow_dispatch:
    inputs:
      environment:
        description: 'Target environment'
        required: true
        type: choice
        options: [dev, prod]
```

The `push`-triggered run is gated to `dev` via `if: github.event_name == 'push'`; prod remains reachable only via `workflow_dispatch`.

**Rationale:**

- The failure mode that prompted this decision (#252): multiple Bicep changes stacked between manual runs, were forgotten, and dev drifted silently from `main`. Auto-deploying dev on any `infra/**` push makes that class of drift structurally impossible.
- Prod retains the explicit human gate because (a) it carries the full production blast radius and (b) prod app deploys are already tag-gated — keeping the same shape for infra is consistent with the rest of the deployment story.

**Implementation status:** The workflow change (adding the `paths` filter and the dev/prod job split) is tracked as a follow-up issue filed after #272 merged. Until that follow-up lands, `infra-deploy.yml` is still `workflow_dispatch`-only for both environments — run it manually after every Bicep PR.

---

## Custom Domain — Cloudflare Origin Cert Rotation

The production custom domain (`rslsiege.com`) uses a **Cloudflare Origin Certificate**
rather than an Azure-managed certificate. This is a deliberate architecture decision:

- Azure managed certs require CNAME or HTTP validation by DigiCert. Apex domains
  (`rslsiege.com` without `www`) cannot have CNAME records (RFC 1912), and Cloudflare's
  proxy intercepts DigiCert's validation requests, so Azure-managed certs do not work
  with this topology.
- Cloudflare Origin Certificates are free, issued directly in the Cloudflare dashboard,
  and can be configured for up to 15-year validity. They are only trusted by Cloudflare
  (not by browsers directly), which is correct: all traffic flows through Cloudflare
  first, then from Cloudflare to Azure using the origin cert.

The Bicep infrastructure uses a **two-phase deploy** controlled by the `enableCustomDomain`
parameter in `infra/main.prod.bicepparam`:

- **Phase 1** (`enableCustomDomain = false`): Key Vault and user-assigned managed identity
  (UAMI) are created. No cert is imported and no domain binding is applied to the Container
  App. This lets the infrastructure deploy succeed even before a cert exists.
- **Phase 2** (`enableCustomDomain = true`): The Container Apps environment imports the
  PFX from Key Vault using the UAMI. The frontend Container App binds the cert with
  `SniEnabled`. Cloudflare proxy can stay ON throughout -- Azure never does public validation.

---

### First-time setup (Phase 1 -> Phase 2)

Follow these steps in order the first time you set up the custom domain on a fresh
environment.

**Step 1 -- Confirm Phase 1 infrastructure is deployed**

The Infra Deploy workflow must have run at least once with `enableCustomDomain = false`.
Verify the Key Vault and UAMI exist:

```powershell
az keyvault list --resource-group {RESOURCE_GROUP} --output table
az identity list --resource-group {RESOURCE_GROUP} --output table
# Look for: siege-web-kv-prod-<suffix> and siege-web-cert-uami-prod
```

**Step 2 -- Generate a Cloudflare Origin Certificate**

1. Log in to the Cloudflare dashboard -> select the `rslsiege.com` zone.
2. Go to **SSL/TLS** -> **Origin Server** -> **Create Certificate**.
3. Select:
   - Key type: RSA (2048) -- Azure Key Vault does not support ECDSA p384/p521.
   - Hostnames: `rslsiege.com`, `*.rslsiege.com`
   - Certificate validity: 15 years (maximum; no cost difference).
4. Click **Create**.
5. Copy the **Certificate** (PEM block) and save as `rslsiege-origin-cert.pem`.
6. Copy the **Private Key** (PEM block) and save as `rslsiege-origin-key.pem`.
   **Keep this file secure** -- store it in a local password manager or encrypted volume.
   It is never committed to git.

**Step 3 -- Convert to PFX**

Run the helper script on your local machine (requires openssl, which ships with
Git for Windows):

```powershell
$pw = Read-Host -AsSecureString -Prompt "Enter a strong PFX password"
.\scripts\generate-origin-pfx.ps1 `
    -CertPath .\rslsiege-origin-cert.pem `
    -KeyPath  .\rslsiege-origin-key.pem `
    -OutPath  .\rslsiege-origin.pfx `
    -Password $pw `
    -Verbose
```

The script emits a `NextStep` property with the exact upload command. Save the password in
your password manager -- you will need it if you ever need to inspect the PFX offline.

**Step 4 -- Upload the PFX to Key Vault**

> **Placeholder convention**: `{RESOURCE_GROUP}`, `{KEY_VAULT_NAME}`, and other curly-brace tokens in this section are literal placeholders — replace them with your actual values before running. They are not shell variables; do not prefix with `$`.

```powershell
az keyvault secret set `
  --vault-name {KEY_VAULT_NAME} `
  --name cloudflare-origin-cert `
  --file .\rslsiege-origin.pfx `
  --encoding base64 `
  --content-type application/x-pkcs12
```

Verify it was stored correctly:

```powershell
az keyvault secret show `
  --vault-name {KEY_VAULT_NAME} `
  --name cloudflare-origin-cert `
  --query "{Id:id, ContentType:contentType, Updated:attributes.updated}" `
  --output table
```

Note the **versionless secret URL** -- it looks like:
`https://{KEY_VAULT_NAME}.vault.azure.net/secrets/cloudflare-origin-cert`
(no version GUID at the end). This is what you put in `kvCertSecretUrl`.

**Step 5 -- Update the param file and trigger Phase 2 deploy**

Edit [`infra/main.prod.bicepparam`](../infra/main.prod.bicepparam) (prod only — dev does not set
this param and correctly inherits the `false` default from
[`infra/main.bicep`](../infra/main.bicep)):

- Around **line 132** (at time of writing): change `enableCustomDomain = false` → `true`.
- Around **line 137** (at time of writing): set `kvCertSecretUrl` to the versionless URL from
  Step 4 above.

The file contains an inline comment block (around lines 113–130) that walks through the
two-phase procedure in-situ — read it for additional context.

```bicep
param enableCustomDomain = true
param kvCertSecretUrl = 'https://{KEY_VAULT_NAME}.vault.azure.net/secrets/cloudflare-origin-cert'
```

Commit and push the change, then trigger the **Infra Deploy** workflow manually
(GitHub Actions -> Infra Deploy -> Run workflow -> prod).

The deploy will:
1. Import the PFX from Key Vault into the Container Apps environment.
2. Bind the cert to the frontend Container App with `SniEnabled`.

**Step 6 -- Set Cloudflare SSL mode and enable the proxy**

1. In the Cloudflare dashboard -> **SSL/TLS** -> **Overview** -> set encryption mode to
   **Full (strict)**. This forces Cloudflare to validate the origin cert rather than
   accepting any certificate.
2. Go to **DNS** -> find the `rslsiege.com` A (or CNAME) record -> click the orange cloud
   icon to enable the proxy (orange cloud = proxied).

**Step 7 -- Verify end-to-end**

```bash
# Should return 200 with {"status":"ok","db":"connected"}
curl -v https://rslsiege.com/api/health

# Confirm the cert is served from Cloudflare (not Azure directly)
# The issuer should be "Cloudflare Inc ECC CA-3" or similar, not the Origin cert
curl -v --head https://rslsiege.com 2>&1 | grep -i issuer
```

In the Azure portal: Container Apps Environment -> Certificates -> confirm
`rslsiege-com-origin-cert` shows status `Succeeded`.

---

### Certificate rotation (annual or on-demand)

Cloudflare Origin Certificates have a configurable validity period (up to 15 years).
When the cert approaches expiry, follow these steps.

**Step 1 -- Generate a new Origin Certificate** (repeat Step 2 above)

**Step 2 -- Convert to PFX** (repeat Step 3 above, same or new password)

**Step 3 -- Upload new version to Key Vault**

```powershell
# Re-running `az keyvault secret set` with the same --name creates a new
# version automatically. The versionless URL continues to point at the latest.
az keyvault secret set `
  --vault-name {KEY_VAULT_NAME} `
  --name cloudflare-origin-cert `
  --file .\rslsiege-origin-new.pfx `
  --encoding base64 `
  --content-type application/x-pkcs12
```

**Step 3.5 -- Grant yourself Key Vault Secrets Officer**

The vault has `enableRbacAuthorization: true` (see `infra/modules/keyvault.bicep`). The
Bicep deployment only grants `Key Vault Secrets User` (read-only) to runtime managed
identities — write access is not assigned to operator accounts by the deployment pipeline.
Without this step, Step 3's `az keyvault secret set` returns `(Forbidden)`.

Role selection notes:

- **Not** `Key Vault Secrets User` — that is read-only; it is what the Container App
  managed identities receive at deploy time.
- **Not** `Key Vault Certificates Officer` — that role covers Key Vault certificate
  *objects* (`/certificates/*`). This runbook uploads the PFX as a **secret** with
  `application/x-pkcs12` content type (see Step 3), which Container Apps' `keyVaultUrl`
  binding consumes directly. The correct role for writing secrets is `Key Vault Secrets
  Officer`.

> **Placeholder convention**: `{RESOURCE_GROUP}` and `{KEY_VAULT_NAME}` are literal
> placeholders — replace them with your actual values before running.

```powershell
$MyObjectId = az ad signed-in-user show --query id -o tsv
$VaultName  = az keyvault list -g {RESOURCE_GROUP} --query "[0].name" -o tsv
$VaultId    = az keyvault show --name $VaultName --query id -o tsv

az role assignment create `
  --role "Key Vault Secrets Officer" `
  --assignee $MyObjectId `
  --scope $VaultId
```

RBAC propagation through the Key Vault data plane is eventually consistent. Wait 30--60
seconds before running Step 4. If Step 4 returns `(Forbidden)` shortly after this step,
the role has almost certainly not propagated yet -- retry after another minute rather than
re-running this step.

**Step 4 -- Trigger Infra Deploy**

Re-run the Infra Deploy workflow. Container Apps detects the new secret version and
rotates the certificate automatically within a few minutes. No `enableCustomDomain`
change is needed -- it should remain `true`.

**Step 5 -- Verify** (repeat Step 7 above)

**Step 6 -- (Optional) Revoke operator write access**

Operator write access to Key Vault is only needed during cert rotations, which happen
rarely. Following least-privilege hygiene, revoke the role assignment once the rotation
is confirmed working -- especially on prod.

```powershell
$MyObjectId = az ad signed-in-user show --query id -o tsv
$VaultName  = az keyvault list -g {RESOURCE_GROUP} --query "[0].name" -o tsv
$VaultId    = az keyvault show --name $VaultName --query id -o tsv

az role assignment delete `
  --role "Key Vault Secrets Officer" `
  --assignee $MyObjectId `
  --scope $VaultId
```

This step is not a gate on completing the rotation -- the cert is already in Key Vault
and the deploy has run. Revoke at your convenience, but do not skip it indefinitely on
production.

---

### Checking the current binding state

```powershell
az containerapp show `
  --name {CONTAINER_APP_FRONTEND} `
  --resource-group {RESOURCE_GROUP} `
  --query "properties.configuration.ingress.customDomains" `
  --output json
```

Expected output when fully bound:

```json
[
  {
    "bindingType": "SniEnabled",
    "certificateId": "/subscriptions/.../managedEnvironments/.../certificates/rslsiege-com-origin-cert",
    "name": "rslsiege.com"
  }
]
```

If `bindingType` is missing or `"Disabled"`, Phase 2 has not run -- trigger the
Infra Deploy workflow with `enableCustomDomain = true`.

---

### Checking the UAMI role assignment

If the cert import fails with an authorization error, verify the UAMI has the correct
role on Key Vault:

```powershell
$uamiId = az identity show `
  --name siege-web-cert-uami-prod `
  --resource-group {RESOURCE_GROUP} `
  --query principalId --output tsv

az role assignment list `
  --assignee $uamiId `
  --scope (az keyvault show --name {KEY_VAULT_NAME} --resource-group {RESOURCE_GROUP} --query id --output tsv) `
  --output table
# Expected role: Key Vault Secrets User (4633458b-17de-408a-b874-0445c86b69e6)
```
