# Backend ↔ Bot Seam Reference

Single source of truth for the HTTP contract between `backend/` and the Discord bot
sidecar (`bot/`). All other files that previously described this seam have been trimmed
to a one-line link here; see each file's "See also" pointer for the change.

Related docs:

- **Endpoint contract (request/response shapes, conformance table):** [`bot/INTERFACE.md`](../bot/INTERFACE.md)
- **Southbound webhook spec (day-role sync):** [`docs/webhooks/day-role-sync.md`](webhooks/day-role-sync.md)
- **Producer/Receiver Symmetry rule:** `CLAUDE.md § Producer/Receiver Symmetry`
- **Umbrella issue (seam hardening):** [#347](https://github.com/glitchwerks/rsl-siege-manager/issues/347)

---

## 1. Overview

`backend/` never calls discord.py directly. All Discord operations — DMs, channel
posts, image uploads, member lookups — are delegated to a Discord bot process over
HTTP. The bot sidecar runs FastAPI on port 8001 alongside a discord.py client in the
same process via `asyncio.TaskGroup`.

```
browser / Discord
     │
     ▼
 frontend:5173
     │  /api/* proxy
     ▼
 backend:8000  ──── HTTP (Bearer) ────►  bot sidecar:8001
     │                                        │
     ▼                                        ▼
 PostgreSQL                              discord.py
                                              │
                                              ▼
                                         Discord API
```

The three deployment shapes differ only in where the bot sidecar process runs and how
`DISCORD_BOT_API_URL` is set. The HTTP contract is identical in all three shapes.

---

## 2. URL resolution

`backend/` resolves the sidecar URL from the `DISCORD_BOT_API_URL` environment
variable at startup. There is no runtime discovery.

| Deployment shape | `DISCORD_BOT_API_URL` value | Notes |
|---|---|---|
| Local Docker Compose — bundled bot | `http://bot:8001` | Default in `.env.example`. Bot is reachable by its Compose service name on the internal network. |
| Azure — bundled bot Container App | `http://siege-bot-<env>` | Injected by Bicep from the bot Container App's internal service name. Backend and bot share the same Container Apps environment; no public ingress needed. |
| Azure — external sidecar (`useExternalSidecar=true`) | value of `externalBotApiUrl` Bicep parameter | Operator-supplied HTTPS URL. Must use `https://` in production (Bicep enforces this; dev environments may use `http://`). |

Source: `infra/modules/container-apps.bicep:138-148` (API ingress), `infra/modules/container-apps.bicep:433-455` (bot ingress, conditional).

---

## 3. Auth

### 3.1 Forward direction — backend → bot

Every protected bot endpoint requires a Bearer token.

```
Authorization: Bearer <token>
```

| Side | Environment variable | Notes |
|---|---|---|
| Backend (caller) | `DISCORD_BOT_API_KEY` | Sent as the Bearer token in every outgoing request from `BotClient` |
| Bot sidecar (validator) | `BOT_API_KEY` | Compared using `secrets.compare_digest`; never stored in plaintext beyond the env var |

`DISCORD_BOT_API_KEY` and `BOT_API_KEY` are two parameter names for the same secret
value. They are stored as separate Key Vault secrets (`discord-bot-api-key` and
`bot-api-key`) and injected into their respective services — both must always carry
the same value. If they diverge, every backend call to the bot fails with 401.

Generate a key (PowerShell):

```powershell
[Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }))
```

Failure modes (source: `bot/INTERFACE.md § Authentication`):

- **401** — `Authorization` header present, token wrong or scheme not `Bearer`.
  Response includes `WWW-Authenticate: Bearer`.
- **403** — `Authorization` header absent entirely.

`GET /api/version` and `GET /api/health` are unauthenticated.

### 3.2 Reverse direction — bot → backend

`BOT_SERVICE_TOKEN` authenticates calls that the bot sidecar makes back to the backend
(for example, the `/api/members/me/preferences` endpoints invoked via the
`X-Acting-Discord-Id` header mechanism). The backend uses this token to verify that an
inbound request originates from the trusted sidecar process.

The backend refuses to start in non-development environments if `BOT_SERVICE_TOKEN` is
missing or empty — startup will fail with:

```
RuntimeError: BOT_SERVICE_TOKEN must be set in non-development environments
```

Source: `wiki/Self-Host-on-Any-VPS.md:154-170`.

### 3.3 Rotation rules

Rotate `DISCORD_BOT_API_KEY` / `BOT_API_KEY` together in one operation — they must
always match. In Azure, rotate both Key Vault secrets in the same step then create a
new Container App revision to pick up the change:

```bash
az keyvault secret set --vault-name <VAULT> --name discord-bot-api-key --value "$NEW"
az keyvault secret set --vault-name <VAULT> --name bot-api-key --value "$NEW"
az containerapp update --name siege-api-<env> --resource-group <rg> \
  --revision-suffix "secret-rotate-$(date +%Y%m%d)"
az containerapp update --name siege-bot-<env> --resource-group <rg> \
  --revision-suffix "secret-rotate-$(date +%Y%m%d)"
```

`BOT_SERVICE_TOKEN` is independent and can be rotated separately; only the backend and
bot sidecar read it.

---

## 4. Deployment shapes

### 4.1 Local Docker Compose — bundled bot (default)

All four services start together. The bot is reachable on the internal Docker network.

```bash
docker-compose up
```

Services started: `postgres`, `backend`, `frontend`, `bot`.

### 4.2 Local Docker Compose — external sidecar

Bot is excluded. Operator starts their own sidecar separately and sets
`DISCORD_BOT_API_URL` to point at it.

```bash
docker-compose -f docker-compose.yml -f docker-compose.sidecar-external.yml up
```

Services started: `postgres`, `backend`, `frontend` (bot excluded).

Set `DISCORD_BOT_API_URL=http://localhost:8001` (or wherever the external sidecar
listens) in `.env`.

### 4.3 Azure — bundled bot Container App (`useExternalSidecar=false`)

Default. Bicep provisions the bot Container App alongside backend and frontend in the
same Container Apps environment. The backend's `DISCORD_BOT_API_URL` is set to the
bot's internal service name automatically.

```bash
az deployment group create \
  --resource-group <rg> \
  --template-file infra/main.bicep \
  --parameters infra/main.prod.bicepparam \
  --parameters useExternalSidecar=false \
  ...
```

### 4.4 Azure — external sidecar (`useExternalSidecar=true`)

Bot Container App is **not provisioned**. Backend points at the operator-supplied URL.

```bash
az deployment group create \
  --resource-group <rg> \
  --template-file infra/main.bicep \
  --parameters infra/main.prod.bicepparam \
  --parameters useExternalSidecar=true \
  --parameters externalBotApiUrl="https://my-bot.example.com" \
  ...
```

Via the **Infra Deploy** workflow (`workflow_dispatch`): set `useExternalSidecar=true`
and ensure `externalBotApiUrl` is populated in the relevant `.bicepparam` file before
triggering.

Source: `infra/modules/container-apps.bicep:433-440`.

---

## 5. Ingress posture

The seam is internal-only. Neither the backend nor the bot has public internet ingress
in the Azure deployment.

| Service | `external` (Bicep) | Reachable from |
|---|---|---|
| `siege-api` (backend) | `false` | Frontend Nginx proxy only, via `/api/*` prefix |
| `siege-bot` (bot) | `false` | `siege-api` only, within the same Container Apps environment |
| `siege-frontend` | `true` | Internet (Cloudflare → Azure Container App ingress) |

The bot sidecar has no public ingress at all — it is only reachable from `siege-api`
within the same Container Apps environment via its internal service name
`http://siege-bot-<env>`.

Source: `infra/modules/container-apps.bicep:141-147` (API, `external: false`),
`infra/modules/container-apps.bicep:450-455` (bot, `external: false`).

---

## 6. Operational reference

### 6.1 Key Vault secret names

| Secret name | Injected as | Used by |
|---|---|---|
| `discord-bot-api-key` | `DISCORD_BOT_API_KEY` | `siege-api` → outgoing Bearer token |
| `bot-api-key` | `BOT_API_KEY` | `siege-bot` → inbound token validation |
| `discord-token` | `DISCORD_TOKEN` | `siege-bot` → Discord gateway login |
| `discord-guild-id` | `DISCORD_GUILD_ID` | `siege-api`, `siege-bot` |
| `database-url` | `DATABASE_URL` | `siege-api`, `siege-bot` |

> `discord-bot-api-key` and `bot-api-key` must always be rotated together and set to
> the same value. See [§ 3.3 Rotation rules](#33-rotation-rules).

Source: `infra/README.md § The bot API key pair`.

### 6.2 `externalBotApiUrl` (external sidecar only)

When `useExternalSidecar=true`, the backend's `DISCORD_BOT_API_URL` is sourced from
the `externalBotApiUrl` Bicep parameter. In production this must be an `https://` URL;
the Bicep template enforces this with an `assert` that is bypassed only when
`ENVIRONMENT=dev`.

Set `externalBotApiUrl` in `infra/main.prod.bicepparam` (or pass it as a parameter
override) before running the Infra Deploy workflow.

### 6.3 Singleton Discord-token constraint

Discord allows only one active WebSocket session per bot token. The bundled bot and
any external sidecar **cannot share the same `DISCORD_TOKEN`** — the second connection
attempt disconnects the first.

When substituting an external sidecar, you must exclude the bundled bot at the
infrastructure layer (not just in configuration). The Bicep `useExternalSidecar`
parameter and the `docker-compose.sidecar-external.yml` override file both enforce this.

The external sidecar must implement the HTTP API contract described in
[`bot/INTERFACE.md`](../bot/INTERFACE.md).

### 6.4 Producer/Receiver Symmetry

Every southbound webhook or RPC that `backend/` emits must have a conforming receiver
in the bundled `bot/`. The bundled bot is the reference sidecar — siege-web plus the
bundled bot must form a fully working pair without third-party code.

For the full rule, corollary, and procedure for adding new southbound surfaces, see
`CLAUDE.md § Producer/Receiver Symmetry` and umbrella issue
[#458](https://github.com/glitchwerks/rsl-siege-manager/issues/458).

---

## Design rationale (from plan `2026-05-10-bot-seam-hardening.md`)

The following decisions were extracted from the now-deleted plan file before deletion.
The plan was written to address seam-hardening work tracked in issue
[#347](https://github.com/glitchwerks/rsl-siege-manager/issues/347).

**Why the seam is internal-only, not a public versioned contract.** Promoting this to
a versioned public surface was considered and rejected: the ceremony of semver + public
schema is YAGNI for a single known second implementer (mom-bot). The seam is documented
precisely enough for an alternate sidecar to implement against it, but without the
overhead of a version-handshake endpoint or a plugin registry.

**Why `useExternalSidecar` is a Bicep parameter (not only documentation).** Earlier
drafts only documented the singleton-token constraint. An adversarial review found that
docker-compose profiles only cover local dev; prod deployments via Container Apps had
no enforcement. The Bicep parameter closes this gap — when `useExternalSidecar=true`
the bundled-bot Container App resource is conditionally excluded from the deployment,
making coexistence physically impossible rather than merely discouraged.

**Why the `channel_name` field is a multipart form part (not a query parameter) on
`POST /api/post-image`.** The endpoint's pre-cleanup shape sent `channel_name` as a
query parameter while all sibling endpoints used the request body. This was a wart
cleaned up in PR [#415](https://github.com/glitchwerks/rsl-siege-manager/pull/415)
(Step 1 of #347) to make the surface consistent. The change was coordinated with the
only in-repo consumer (`BotClient.post_image`) in the same PR.

**Why `GET /api/members` uses `id` while `GET /api/members/{discord_user_id}` uses
`discord_id`.** This is a load-bearing inconsistency: `id` was the original field name
from the generic schema; `discord_id` was introduced in PR #415 when the discriminated
shape was added for the single-member endpoint. Renaming either field would break
existing consumers. Alternative sidecars must use both as documented.

**Why the integration test suite is the normative source of truth (not this doc or
`bot/INTERFACE.md`).** Human-readable docs drift; tests run on every PR. Per Step 2 of
#347, `bot/INTERFACE.md` is updated in the same PR as any change to the seam, but when
doc and test disagree, the test wins.
