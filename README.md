# Siege Assignment Web App

[![CI](https://img.shields.io/github/actions/workflow/status/glitchwerks/rsl-siege-manager/ci.yml?branch=main&label=CI&logo=github)](https://github.com/glitchwerks/rsl-siege-manager/actions/workflows/ci.yml)
[![Deploy](https://img.shields.io/github/actions/workflow/status/glitchwerks/rsl-siege-manager/deploy.yml?branch=main&label=deploy&logo=github)](https://github.com/glitchwerks/rsl-siege-manager/actions/workflows/deploy.yml)
[![Latest release](https://img.shields.io/github/v/release/glitchwerks/rsl-siege-manager?label=release&logo=github&color=blue)](https://github.com/glitchwerks/rsl-siege-manager/releases/latest)
[![Live site](https://img.shields.io/badge/live-rslsiege.com-success?logo=cloudflare&logoColor=white)](https://rslsiege.com)

🌐 **Live site:** <https://rslsiege.com>

A comprehensive web utility for coordinating Raid Shadow Legends clan siege assignments — validated, automated, Discord-native, and self-hostable.

## Features

- **Validated assignments** — 16 rule checks catch overlaps, capacity errors, and misplacements before you post.
- **One-click auto-fill** — preview a complete assignment for any empty positions, then commit exactly what you saw.
- **Attack-day logic** — pinned members count toward Day 2 thresholds automatically; no manual bookkeeping.
- **Discord-native sign-in and notifications** — OAuth2 login gated by Discord role, async DM batches with delivery tracking, and assignment images posted to your siege channels.
- **Generated assignment images** — server-rendered PNGs of the full board, posted directly to Discord (no screenshots, no manual cropping).
- **Self-hostable** — runs on any Docker host or a managed Azure stack. Open source, MIT licensed, no SaaS dependency.

## Architecture

| Service | Stack | Port |
|---|---|---|
| Backend | FastAPI, SQLAlchemy async, PostgreSQL | 8000 |
| Frontend | React 18, TypeScript, Vite, Tailwind, shadcn/ui | 5173 |
| Bot | discord.py + FastAPI HTTP sidecar | 8001 |
| Database | PostgreSQL 16 | 5432 |

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)

That's it for the Quick Start. Python and Node are only needed if you want to run services outside Docker.

## Quick Start

Get a populated local UI running in under 5 minutes — no Discord setup required.

```bash
# 1. Clone and enter the repo
git clone https://github.com/glitchwerks/rsl-siege-manager.git
cd rsl-siege-manager

# 2. Copy the example env (auth is disabled by default for local dev)
cp .env.example .env

# 3. Start everything (including the bundled Discord bot sidecar)
docker-compose up --build
```

Open http://localhost:5173 — the app will load with 25 demo members and an active siege already populated. A thin amber banner at the top confirms you are in demo mode.

> **What happens on first boot:** the backend container runs `alembic upgrade head` then `python scripts/seed_demo.py` before starting. The seed script is idempotent — restarting the stack does not create duplicates.

To stop and wipe data: `docker-compose down -v`

## Dev Mode (hot reload)

Runs PostgreSQL in Docker; backend and frontend run natively for fast iteration.

```bash
# 1. Start PostgreSQL only
docker-compose up postgres

# 2. Backend (new terminal)
cd backend
pip install -r requirements-dev.txt
alembic upgrade head
python scripts/seed_demo.py   # populate demo data
uvicorn app.main:app --reload

# 3. Frontend (new terminal)
cd frontend
npm ci
npm run dev
```

- Frontend: http://localhost:5173 (proxies `/api/*` to backend)
- API docs: http://localhost:8000/api/docs

### VS Code

Launch configurations are included in `.vscode/`:

- **F5 → "Full Stack"** launches both backend and frontend
- **Ctrl+Shift+P → "Run Task"** for Docker, migrations, seeding, and tests

Requires the "Docker: Start PostgreSQL" task to be running first.

## Running with real Discord OAuth

1. Set `AUTH_DISABLED=false` in `.env`
2. Fill in the **Tier 2** variables in `.env` (Discord OAuth2 app credentials, bot token, guild ID)
3. Restart the stack

See `.env.example` for the full variable reference organized by tier.

## Database

Migrations and seeds only need to run once per fresh volume. Data persists across restarts in the `postgres_data` Docker volume.

```bash
# Run migrations
cd backend && alembic upgrade head

# Seed reference + demo data
cd backend && python scripts/seed_demo.py

# Reference data only (no demo members/siege)
cd backend && python scripts/seed.py

# Create a new migration after model changes
cd backend && alembic revision --autogenerate -m "description"
```

## Tests

```bash
# Backend
cd backend && python -m pytest --ignore=tests/test_schema.py -v

# Frontend unit tests (Vitest)
cd frontend && npm test

# Frontend type check + build
cd frontend && npm run build

# Bot
cd bot && python -m pytest -v
```

## Linting

```bash
# Backend
cd backend && black . && ruff check .

# Frontend
cd frontend && npm run lint
```

## Changelog

The in-app changelog dropdown reads from `CHANGELOG.md` at the repo root. Parsing happens at build time via a Vite plugin (`frontend/src/build/changelog-plugin.ts`), so any edits to `CHANGELOG.md` require a frontend rebuild (`npm run build` or `npm run dev` restart) before they appear in the UI. The `[Unreleased]` section is included during `npm run dev` so you can preview work-in-progress entries locally, but it is stripped from production builds. If `CHANGELOG.md` is malformed — missing version headings or unparseable dates — the build fails with an error rather than silently producing an empty dropdown.

## Deployment Modes

Siege Manager supports two deployment topologies: **bundled bot** (default) and **external sidecar**.
For the full seam reference — URL resolution, auth key pair, Key Vault secrets, and ingress posture — see [`docs/bot-seam.md`](docs/bot-seam.md).

### Singleton-token constraint

Discord allows only one active WebSocket session per bot token. The bundled bot and any alternate sidecar (e.g. `mom-bot`) **cannot share the same `DISCORD_TOKEN`** — the second connection attempt will disconnect the first. When substituting an external sidecar, you must exclude the bundled bot at the infrastructure layer, not just in documentation. Both the Bicep parameter and the docker-compose profile enforce this.

The alternate sidecar must implement the HTTP API contract described in [`bot/INTERFACE.md`](bot/INTERFACE.md).

### Local dev (docker-compose)

| Mode | Command | Services started |
|---|---|---|
| Bundled bot (default) | `docker-compose up` | postgres, backend, frontend, bot |
| External sidecar | `docker-compose -f docker-compose.yml -f docker-compose.sidecar-external.yml up` | postgres, backend, frontend (bot excluded) |

When running in external sidecar mode, start your alternate sidecar separately and set `DISCORD_BOT_API_URL` in `.env` to point at its HTTP API (e.g. `http://localhost:8001`).

### Azure (Bicep / infra-deploy workflow)

| Mode | Parameter | Effect |
|---|---|---|
| Bundled bot (default) | `useExternalSidecar=false` | Bot Container App is provisioned alongside backend and frontend |
| External sidecar | `useExternalSidecar=true` | Bot Container App is **not** provisioned; backend points at `externalBotApiUrl` |

Deploy with the bundled bot (default — no change needed):

```bash
az deployment group create \
  --resource-group <rg> \
  --template-file infra/main.bicep \
  --parameters infra/main.prod.bicepparam \
  --parameters useExternalSidecar=false \
  ... # other required params
```

Deploy with an external sidecar (bot excluded):

```bash
az deployment group create \
  --resource-group <rg> \
  --template-file infra/main.bicep \
  --parameters infra/main.prod.bicepparam \
  --parameters useExternalSidecar=true \
  --parameters externalBotApiUrl="https://my-bot.example.com" \
  ... # other required params
```

Via the **Infra Deploy** workflow (`workflow_dispatch`): set the `useExternalSidecar` input to `true` and ensure `externalBotApiUrl` is set in the relevant `.bicepparam` file before triggering.

**Dev exemption:** When `ENVIRONMENT=dev`, the HTTPS-enforcement Bicep assert is bypassed so `externalBotApiUrl` may use `http://localhost:<port>` for local-stack iteration. Production deployments enforce `https://` unconditionally.

## Run it yourself

Full self-host guides live in the [project wiki](https://github.com/glitchwerks/rsl-siege-manager/wiki):

- **[Self-Host on Any VPS](https://github.com/glitchwerks/rsl-siege-manager/wiki/Self-Host-on-Any-VPS)** — any Linux host that runs Docker, with Caddy for free TLS and an optional Cloudflare Tunnel path if you can't open ports. The portable path — no cloud account required.
- **[Self-Host on Azure](https://github.com/glitchwerks/rsl-siege-manager/wiki/Self-Host-on-Azure)** — Container Apps + Key Vault + PostgreSQL Flexible Server, with a GitHub Actions deploy pipeline. The managed, hands-off path.

New to the project? Start at **[Getting Started](https://github.com/glitchwerks/rsl-siege-manager/wiki/Getting-Started)** in the wiki.

## Deploy Environments

Secrets for Azure deployments are stored in per-environment files (both gitignored):

| File | Purpose |
|---|---|
| `.env.deploy.dev` | Secrets for the dev Azure instance (`siege-web-dev` resource group) |
| `.env.deploy.prod` | Secrets for the prod Azure instance (`siege-web-prod` resource group) |

Copy `.env.deploy.example` to both files and fill in the values.

```powershell
# Build and deploy to dev
.\bootstrap-images.ps1 -Env dev -EnvFile .env.deploy.dev

# Build and deploy to prod
.\bootstrap-images.ps1 -Env prod -EnvFile .env.deploy.prod
```

### CI/CD Pipelines

Two GitHub Actions workflows handle deployments:

| Workflow | File | When it runs | What it does |
|---|---|---|---|
| **Deploy** | `.github/workflows/deploy.yml` | Push to `main` (deploys dev); push a `v*` tag (deploys prod) | Builds Docker images, pushes to ACR, deploys Container Apps |
| **Infra Deploy** | `.github/workflows/infra-deploy.yml` | Manual (`workflow_dispatch`) only | Runs `az deployment group create` with the Bicep templates |

The infra workflow is intentionally manual -- infrastructure changes require a human to
initiate them. The application deploy is automatic on merge to main (dev) or on tagging
(prod). Both workflows require environment secrets configured under GitHub Settings ->
Environments (dev / prod).

### Custom Domain (Cloudflare Origin Cert)

The production custom domain `rslsiege.com` uses a **Cloudflare Origin Certificate** stored
in Azure Key Vault rather than an Azure-managed certificate. This is required because:

1. `rslsiege.com` is an apex domain -- apex domains cannot have CNAME records, so Azure's
   CNAME-based certificate validation cannot work.
2. The Cloudflare proxy is permanently ON for DDoS protection -- even if a CNAME existed,
   Cloudflare's proxy would intercept DigiCert's validation requests.

Deployment uses a two-phase approach controlled by `enableCustomDomain` in
`infra/main.prod.bicepparam`:

- **Phase 1** (`enableCustomDomain = false`): Deploy infra. Key Vault + managed identity are
  created but no cert is bound.
- **Phase 2** (`enableCustomDomain = true`): Upload the PFX to Key Vault, then redeploy.
  Azure imports the cert and binds it to the frontend Container App.
  (Flip `enableCustomDomain` at line 132 and set `kvCertSecretUrl` at line 137 in
  `infra/main.prod.bicepparam`.)

See `scripts/generate-origin-pfx.ps1` to convert the Cloudflare-issued PEM files to PFX,
and `docs/RUNBOOK.md` section 8 for the full step-by-step guide including cert rotation.

## Environment Variables

`.env.example` is organized into three tiers:

| Tier | Description |
|---|---|
| **1 — Always required** | `DATABASE_URL`, `ENVIRONMENT`, `AUTH_DISABLED`, `SESSION_SECRET` |
| **2 — Discord / real auth** | OAuth2 credentials, bot token, guild ID, channel names, API keys, `DISCORD_REQUIRED_ROLE` |
| **3 — Azure / deploy only** | `ALLOWED_ORIGINS`, `IMPORT_EXCEL_PATH` and anything used only by Bicep or CI |

`AUTH_DISABLED=true` (the default in `.env.example`) bypasses login entirely — anyone who can reach the URL has full access. Never use this outside a local dev environment.

`SESSION_SECRET` is required when `AUTH_DISABLED=false`. Generate a secure value with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## License

This project is licensed under the MIT License — see [LICENSE](LICENSE) for the full text.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before opening a PR.
