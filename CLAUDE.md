# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Raid Shadow Legends Siege Assignment Web App — replaces a manual Discord-based workflow for assigning clan members to stronghold siege buildings. See `docs/IMPLEMENTATION_PLAN.md` for the phased roadmap and `docs/WEB_DESIGN_DOCUMENT.md` for full domain spec (validation rules, auto-fill algorithm, attack day logic, building configs).

## Architecture

Monorepo with three independently containerized services:

- **`backend/`** — FastAPI (Python 3.12), SQLAlchemy async + asyncpg, Alembic migrations, PostgreSQL
- **`frontend/`** — React 18 + TypeScript, Vite, React Router v6, React Query, Tailwind CSS, shadcn/ui
- **`bot/`** — discord.py client + FastAPI HTTP sidecar (port 8001); backend calls the bot's HTTP API to send DMs / post images

Data flow:
- Unauthenticated users hit `/login` → `/api/auth/login` → Discord OAuth2 → `/api/auth/callback` → JWT session cookie
- Frontend calls `/api/*` with cookie → backend validates via `get_current_user` dependency → PostgreSQL
- Backend calls bot HTTP API for Discord notifications
- Backend generates images via Playwright (headless HTML/CSS → PNG); bot receives the PNG and posts it

See `docs/superpowers/plans/discord-auth-plan.md` for the canonical auth spec.
See `docs/bot-seam.md` for the backend↔bot HTTP seam reference (URL resolution, auth, deployment shapes, ingress posture, Key Vault secrets).

Project state lives on GitHub — current release in [Releases](https://github.com/glitchwerks/rsl-siege-manager/releases), active workstreams in [Milestones](https://github.com/glitchwerks/rsl-siege-manager/milestones), open work in [Issues](https://github.com/glitchwerks/rsl-siege-manager/issues), recent changes in `CHANGELOG.md`. Do not maintain a local status doc.

## Producer/Receiver Symmetry

Every outbound webhook or RPC the siege-web `backend/` emits **must** have a conforming receiver implementation in the bundled bot at `bot/`. The bundled bot is the **reference sidecar** — a public consumer should be able to stand up siege-web plus the bundled bot and have a fully working pair with no third-party code.

Scope:

- **In scope (the rule applies):** southbound producer surfaces — anything `backend/` calls outward where a third-party service could be the receiver (e.g. `BotClient.sync_day_role`, future webhooks/RPCs of the same shape).
- **Out of scope:** northbound calls from `backend/` into the bundled-bot sidecar's existing HTTP API (`/api/*` on port 8001) — that surface is the sidecar's own contract, not a southbound producer wire. Inbound webhooks _from_ the sidecar are also out of scope.

**Corollary — contract ownership.** Whichever side emits the wire owns the canonical spec. For southbound producer wires that means siege-web owns the spec, and the spec lives under `docs/webhooks/` in this repo (e.g. `docs/webhooks/day-role-sync.md`). Third-party receiver authors consume the spec directly from this public repo; the private coord repo `glitchwerks/rsl-mom-apps` is not in their read path and must not be referenced as the canonical home.

When adding a new southbound producer surface:

1. Add or update the canonical spec under `docs/webhooks/<name>.md` with a semver header.
2. Implement the producer in `backend/` behind a kill-switch env var defaulting to `false`.
3. Implement the conforming receiver in `bot/` before flipping the kill switch.
4. Bump the spec's minor version for additive changes, major for breaking; coordinate cross-repo rollouts via `glitchwerks/rsl-mom-apps`.

See umbrella issue [#458](https://github.com/glitchwerks/rsl-siege-manager/issues/458) for the day-role-sync rollout that established this rule.

## Common Commands

### Full stack (local)
```bash
docker-compose up          # starts postgres → backend → frontend → bot
docker-compose up --build  # rebuild images first
```

Services: frontend `localhost:5173`, backend `localhost:8000`, bot `localhost:8001`.

### Backend
```bash
cd backend
pip install -r requirements-dev.txt

# Run
uvicorn app.main:app --reload

# Test
pytest --ignore=tests/test_schema.py -v     # standard run (test_schema.py requires live DB)
pytest tests/test_health.py                 # single file
pytest -k "test_health"                     # single test by name
pytest --cov=app --cov-report=term-missing

# Lint / format
black .
ruff check .
ruff check . --fix

# Migrations
alembic revision --autogenerate -m "description"
alembic upgrade head
alembic downgrade -1
```

### Frontend
```bash
cd frontend
npm ci

# Dev server (proxies /api/* to localhost:8000)
npm run dev

# Build
npm run build

# Lint / format
npx eslint src/
npx prettier --write src/
```

### Bot
```bash
cd bot
pip install -r requirements-dev.txt
python app/main.py   # runs Discord client + HTTP sidecar concurrently
pytest
```

## Key Conventions

### Backend
- Async everywhere: `AsyncSession`, `asyncpg`, `async def` route handlers.
- Settings via Pydantic `BaseSettings` in `app/config.py`; loaded from `.env`.
- All routes registered under `/api` prefix.
- DB dependency injected via `get_db()` from `app/db/session.py`.
- Models go in `app/models/`; Alembic's `env.py` auto-detects them via `app.db.base`.

### Frontend
- API base URL from `VITE_API_URL` env var; Axios client in `src/api/client.ts`.
- Pages in `src/pages/`; routes wired in `src/App.tsx`.
- `cn()` utility from `src/lib/utils.ts` for Tailwind class merging.
- Prettier enforces double quotes and Tailwind class sorting (`.prettierrc`).

### Bot
- `SiegeBot` (discord.py) and the HTTP API (FastAPI on port 8001) run concurrently via `asyncio.TaskGroup`.
- All bot HTTP endpoints require Bearer token auth (`BOT_API_KEY`).
- Backend communicates with the bot exclusively through the HTTP API (never direct discord.py calls from backend).

## CI (GitHub Actions)

On PR to `main`:
- **Backend**: black check → ruff → pytest (uses test DB URL from env)
- **Frontend**: npm ci → eslint → npm test → npm build
- **Bot**: pytest

Registry image retention is automated via a scheduled ACR Task (`weekly-purge`) in both registries — release tags (`v*`) are preserved forever; SHA/commit tags beyond the last 10 per repo and untagged manifests older than 7 days are deleted every Sunday at 03:00 UTC.

**Application health workbook:** [siege-web-prod](https://portal.azure.com/#@cmbdevoutlook333.onmicrosoft.com/resource/subscriptions/213aa1f8-32d1-4ffe-8f4d-6e60f1cd9dc0/resourceGroups/siege-web-prod/providers/Microsoft.Insights/workbooks/c3bfb777-8256-5580-ab51-65f537101966/overview) — at-a-glance ops vitals (request rates, latency p50/p95, exception count, bot restarts, image gen latency). Dev equivalent at [siege-web-dev](https://portal.azure.com/#@cmbdevoutlook333.onmicrosoft.com/resource/subscriptions/213aa1f8-32d1-4ffe-8f4d-6e60f1cd9dc0/resourceGroups/siege-web-dev/providers/Microsoft.Insights/workbooks/ef1f3d0a-b955-5028-ae97-2b1732a3b5bf/overview).

Two deployment workflows exist and are fully operational:

- **`.github/workflows/deploy.yml`** — triggered automatically on push to `main` (deploys to dev) and on `v*` tag push (deploys to prod). Builds Docker images, pushes to ACR, then updates Container App revisions with the new image tag.
- **`.github/workflows/infra-deploy.yml`** — manual-only (`workflow_dispatch`). Runs `az deployment group create` with the Bicep templates in `infra/`. Use this for any infrastructure change (new resource, config update, cert binding). Requires secrets configured under GitHub Settings → Environments (dev / prod).

## Environment Variables

Copy `.env.example` to `.env`. Required:

| Variable | Used by |
|---|---|
| `DATABASE_URL` | backend, bot |
| `DISCORD_TOKEN` | bot |
| `DISCORD_GUILD_ID` | backend, bot |
| `DISCORD_BOT_API_URL` | backend (calls bot) |
| `DISCORD_BOT_API_KEY` / `BOT_API_KEY` | backend → bot auth |
| `DISCORD_SIEGE_CHANNEL` | backend (channel to post the text summary after image CDN links are known) |
| `DISCORD_SIEGE_IMAGES_CHANNEL` | backend (channel where assignment and reserves images are posted) |
| `ENVIRONMENT` | all (controls debug/docs) |
| `AUTH_DISABLED` | backend (dev-only login bypass; startup guard blocks `true` outside development) |
| `SESSION_SECRET` | backend (HS256 JWT signing key; required when auth is enabled) |
| `DISCORD_REQUIRED_ROLE` | backend (Discord role name required to log in; default `Clan Deputies`; exact, case-sensitive match) |
| `BOT_SERVICE_TOKEN` | backend (Bearer token for bot→backend calls; startup guard rejects empty string outside development) |
| `ALLOWED_ORIGINS` | backend (comma-separated CORS allowlist; required for non-localhost deployments) |
| `VITE_PUBLIC_URL` | frontend (canonical URL used in `<link rel="canonical">` and `og:url` meta tags) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | backend, bot (optional; telemetry no-op when unset) |
| `DAY_ROLE_SYNC_ENABLED` | backend (kill switch for day-role sync webhook; default `false` — set `true` only after a conforming receiver is deployed) |
| `DAY_ROLE_SYNC_URL` | backend (receiver endpoint URL for day-role sync webhook; required when feature is enabled) |

## graphify Knowledge Graph

A knowledge graph of this repo lives in `graphify-out/` (gitignored build artifact). It includes AST-extracted code structure plus semantically-extracted concepts, contracts, and acceptance-criteria → test mappings. See `docs/graphify-bookkeeping/README.md` for the integration rationale (issue #440).

### When to consult the graph FIRST

Run `/graphify query "<reformulated question>"` before doing any Read/Grep work when the question fits one of these shapes:

- **Data flow / pipelines** — "How does X flow through the system?", "What triggers Y?", "Where does Z get scheduled?"
- **Contract / test coverage** — "Where / how is X verified?", "What does this AC prove?", "Is there a test for Y?"
- **Cross-cutting relationships** — "What connects A to B?", "What does X depend on?", "Which mutation seams call into Z?"
- **Rationale lookups** — "Why is this here?", "What's the design rationale for X?" (the graph has `rationale_for` edges from docstrings/ACs back to functions)

For questions like the above, the graph routinely surfaces non-obvious cross-file relationships and bridge-doc edges that grep cannot find in one pass.

### When to SKIP the graph

Don't pay the consult cost for:

- Single-file reads ("what's in `config.py`?")
- Syntax / how-do-I questions
- Line-level lookups ("what does line 42 say?")
- Anything answerable by Read + one Grep

### Keeping the graph fresh

- **Code-only changes** auto-pick-up via the `graphify` post-commit hook (AST-only, no LLM tokens).
- **Doc / markdown / image changes** require a manual `/graphify --update` to re-extract semantics.
- After any meaningful merge to `main`, consider running `/graphify --update` once.

### Bookkeeping

Two telemetry files live at `docs/graphify-bookkeeping/`:

- **`consultations.jsonl`** — auto-appended by a `PostToolUse` hook (`.claude/hooks/graphify-consultation-log.js`) every time the `graphify` skill fires. No action required.
- **`misfires.jsonl`** — manual log of cases where the graph misled. **When the user signals the graph was wrong** (trigger phrases: "that's wrong", "graph misfired", "the graph misled me", "graph said X but it's Y"), append a JSONL entry with:

  ```json
  {"timestamp": "<ISO-8601 UTC>", "query": "<the original question>", "graph_claim": "<what the graph reported>", "correction": "<what's actually true>", "source_file_corrected": "<path showing the truth, optional>", "category": "stale|missing_edge|wrong_direction|fabricated|other"}
  ```

  Use the Edit/Write tool to append. Do NOT prompt the user to fill in fields — extract them from the conversation context. Better an under-specified entry than a missed one.

See `docs/graphify-bookkeeping/README.md` for the schema and how to analyze.

## Domain Reference

Key domain docs (read before implementing business logic):
- **Validation rules** (16 total, errors + warnings): `docs/WEB_DESIGN_DOCUMENT.md` and memory `project_validation_rules.md`
- **BuildingTypeConfig** (base groups + last slot counts): memory `project_building_type_config.md`
- **Auto-fill algorithm**: preview stores result; apply commits exactly what was previewed — `project_autofill.md`
- **Attack day algorithm**: pinned members count toward Day 2 threshold — `project_attack_day.md`
- **Board API response**: nested hierarchy (buildings → groups → positions) — `project_api_decisions.md`
- **Image generation**: Playwright headless HTML/CSS → PNG — `project_image_generation.md`
- **Notifications**: async DM batches tracked via `NotificationBatch` + `NotificationBatchResult` DB tables — `project_notifications.md`
- **Excel import**: one-time backend CLI script only, no UI or API endpoint — `project_excel_import.md`

