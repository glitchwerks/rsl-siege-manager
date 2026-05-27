# Deploy anywhere — VPS, home server, or any Docker host

> **No Azure account required.** This is the portable, hands-on path: you bring a Linux host capable of running Docker, a domain name (or a tunnel), and a Discord application, and this guide takes you the rest of the way. If you'd rather let Azure handle TLS, scaling, and secret management, see [Self-Host on Azure](Self-Host-on-Azure) for the managed path.

**What you'll end up with after following this guide:**

- All four services (PostgreSQL, backend API, frontend, Discord bot) running as Docker containers on your own host
- Free TLS via Caddy as a reverse-proxy sidecar (or a tunnel alternative if you can't open ports)
- A publicly reachable Discord OAuth2 callback so your clan members can sign in
- `restart: unless-stopped` on every service so the stack survives reboots

**Estimated time:** 30–60 minutes on a VPS you have access to.

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Register a Discord application](#2-register-a-discord-application)
3. [Clone the repo and create your environment file](#3-clone-the-repo-and-create-your-environment-file)
4. [Production compose overlay](#4-production-compose-overlay)
5. [HTTPS with Caddy as a sidecar](#5-https-with-caddy-as-a-sidecar)
6. [Tunnel alternatives (no open ports)](#6-tunnel-alternatives-no-open-ports)
7. [Database options](#7-database-options)
8. [First boot and smoke test](#8-first-boot-and-smoke-test)
9. [Troubleshooting](#9-troubleshooting)
10. [Day-two ops](#10-day-two-ops)

---

## 1. Prerequisites

### Tools (install on your host)

| Tool | Install | Version check |
|---|---|---|
| Docker Engine 24+ | [docs.docker.com/engine/install](https://docs.docker.com/engine/install/) | `docker --version` |
| Docker Compose v2 | Included with Docker Engine 24+ | `docker compose version` |
| Git | [git-scm.com](https://git-scm.com/) | `git --version` |

> **Docker Compose v2 vs v1:** This guide uses `docker compose` (with a space). If your host only has `docker-compose` (with a hyphen), either upgrade or prefix all compose commands with `docker-compose`.

### Host requirements

- A Linux VPS or server (Ubuntu 22.04 LTS or Debian 12 recommended; any x86-64 Linux that runs Docker works)
- At least 1 GB RAM (2 GB recommended — Playwright inside the backend container needs headroom for image generation)
- A public IPv4 address **and** ports 80 and 443 open in your firewall, **or** the ability to run a tunnel (see [Section 6](#6-tunnel-alternatives-no-open-ports) if you can't open ports)

### Domain

You need a domain name pointed at your server's IP via an A record. This guide uses `siege.example.com` as a placeholder throughout — substitute your actual domain.

If you don't have a domain or can't open ports, skip ahead to [Section 6](#6-tunnel-alternatives-no-open-ports) and set up a tunnel first, then return to Section 3 with the tunnel hostname in hand.

### Discord

- A Discord server (guild) where you're an admin.
- A Discord developer application — you'll create it in the next section.

---

## 2. Register a Discord application

The app uses Discord OAuth2 for user login. Only the backend's `/api/auth/callback` endpoint needs to be publicly reachable — the frontend and bot do not need their own public URLs.

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) and click **New Application**.
2. Name it (e.g. "RSL Siege Manager") and click **Create**.
3. Under **OAuth2 → General**, note the **Client ID** and click **Reset Secret** to generate a **Client Secret**. Copy both — these become `DISCORD_CLIENT_ID` and `DISCORD_CLIENT_SECRET` in your env file.
4. Under **OAuth2 → Redirects**, click **Add Redirect** and enter your callback URL:
   ```
   https://siege.example.com/api/auth/callback
   ```
   If you're using a Cloudflare Tunnel or Tailscale Funnel, use that hostname instead. Click **Save Changes**.

   > If you don't know your public URL yet, use a placeholder and come back to update this after you complete Section 5 or 6.

5. Under **Bot**, click **Add Bot**, then click **Reset Token** and copy the **Bot Token** — this becomes `DISCORD_TOKEN`.
6. Under **Bot**, enable the **Server Members Intent** (required for the bot to read your guild's member list).
7. Invite the bot to your Discord server: go to **OAuth2 → URL Generator**, select scopes `bot` and `applications.commands`, grant **Send Messages** and **Read Message History** permissions, then open the generated URL and select your server.
8. Copy your **Discord Server ID**: right-click your server icon → **Copy Server ID** (requires Developer Mode enabled under User Settings → Advanced). This becomes `DISCORD_GUILD_ID`.

> For detailed screenshots of the Discord Developer Portal, see [Section 3 of the Azure guide](Self-Host-on-Azure#3-register-a-discord-application) — the portal UI is identical regardless of where you host the app.

---

## 3. Clone the repo and create your environment file

SSH into your server and run:

```bash
git clone https://github.com/glitchwerks/rsl-siege-manager.git
cd rsl-siege-manager
cp .env.example .env.production
```

Now edit `.env.production`. The file is organized into three tiers:

### Tier 1 — Always required

These variables must be set for the app to start.

| Variable | Production value |
|---|---|
| `DATABASE_URL` | See [Section 7](#7-database-options) — set to your Postgres connection string |
| `ENVIRONMENT` | Set to `production` (the compose overlay enforces this, but set it here too) |
| `AUTH_DISABLED` | **Must be `false` or absent.** Never leave this as `true` in production — it grants any visitor full access without signing in. |
| `SESSION_SECRET` | **Must be a real random value.** The backend refuses to start if this is missing or equals `changeme-...`. Generate one: |

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output and set `SESSION_SECRET=<that value>` in `.env.production`.

> **Why `SESSION_SECRET` matters:** it's the signing key for all session JWTs. A guessable value means any visitor could forge a session cookie and impersonate any user.

### Tier 2 — Discord / real auth (required for OAuth login)

Fill in the values you collected in Section 2:

```
DISCORD_CLIENT_ID=<your app's client ID>
DISCORD_CLIENT_SECRET=<your app's client secret>
DISCORD_REDIRECT_URI=https://siege.example.com/api/auth/callback

DISCORD_TOKEN=<your bot token>
DISCORD_GUILD_ID=<your server ID>

DISCORD_SIEGE_CHANNEL=clan-siege-assignments
DISCORD_SIEGE_IMAGES_CHANNEL=clan-siege-assignment-images

DISCORD_BOT_API_URL=http://bot:8001

DISCORD_REQUIRED_ROLE=Clan Deputies
```

`DISCORD_BOT_API_URL` stays as `http://bot:8001` when running inside Docker Compose — the bot service is reachable by its service name on the internal Docker network.

`DISCORD_REQUIRED_ROLE` controls which Discord role a user must have to log in. The default is `Clan Deputies` — **change this to whatever role your clan uses for siege managers or officers.** The role name must be an exact, case-sensitive match to a role that exists in your Discord server. If the variable is unset, it falls back to `Clan Deputies`. Multi-role support may be added later if needed.

Generate shared secrets for `DISCORD_BOT_API_KEY` and `BOT_API_KEY`. **These two must be identical** — the backend uses `DISCORD_BOT_API_KEY` to authenticate calls it sends to the bot, and the bot uses `BOT_API_KEY` to validate those calls on arrival. For the full auth model and rotation rules, see [`docs/bot-seam.md § Auth`](https://github.com/glitchwerks/rsl-siege-manager/blob/main/docs/bot-seam.md#3-auth).

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Set both in `.env.production`:

```
DISCORD_BOT_API_KEY=<generated value>
BOT_API_KEY=<same generated value>
```

### Backend service token

`BOT_SERVICE_TOKEN` authenticates calls that the bot makes back to the backend API. **The backend refuses to start in production if this value is missing or empty** — you will see `RuntimeError: BOT_SERVICE_TOKEN must be set in non-development environments` in the startup logs.

Generate a secure token:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set it in `.env.production`:

```
BOT_SERVICE_TOKEN=<generated value>
```

Both the backend and the bot read this value — make sure it is the same in both services (the single `.env.production` file loaded by both services via `env_file` ensures this when using the production compose overlay).

### Tier 3 — Custom domain / deploy-only

These variables are optional for a basic deployment but required when you use a custom domain.

**`ALLOWED_ORIGINS`** — Comma-separated list of origins the backend allows for CORS. Defaults to `http://localhost:5173` (local dev frontend). When self-hosting behind a reverse proxy or custom domain, set this to your public URL so the browser can make API calls:

```
ALLOWED_ORIGINS=https://siege.example.com
```

If you serve the app from multiple origins (e.g. with and without `www`), separate them with commas:

```
ALLOWED_ORIGINS=https://siege.example.com,https://www.siege.example.com
```

**`VITE_PUBLIC_URL`** — Canonical URL injected into the frontend for `<link rel="canonical">` and `og:url` meta tags. Set it to the same value as your public-facing URL:

```
VITE_PUBLIC_URL=https://siege.example.com
```

Leave both unset (or use the defaults) for a purely local deployment.

### Lock down the env file

```bash
chmod 600 .env.production
```

This prevents other OS users on the same host from reading your secrets.

---

## 4. Production compose overlay

The repo includes a production compose overlay at `docker-compose.prod.yml` that changes the development defaults to production-safe values:

- Every service gets `restart: unless-stopped` so the stack survives host reboots
- `env_file` switches from `.env` to `.env.production`
- `ENVIRONMENT: production` and `AUTH_DISABLED: "false"` are enforced, preventing accidental demo-mode starts
- Sane memory limits are applied so a runaway service doesn't starve the host

**Always use both files together:**

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

The overlay *overrides* matching keys from the base file — services not mentioned inherit the base file's definition unchanged. See the [Docker Compose file merge documentation](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/) for how key merging works.

> If you omit `-f docker-compose.prod.yml`, the stack starts in development mode: auth is disabled, demo seed data runs, and the `postgres_data` volume uses dev defaults. That's intentional for local dev (`docker-compose up` with no flags), but wrong for a server.

---

## 5. HTTPS with Caddy as a sidecar

[Caddy](https://caddyserver.com/) automatically obtains and renews Let's Encrypt certificates. Running it as a sidecar container in the same Compose network is the simplest way to get free TLS on any VPS.

### Caddyfile

Create `Caddyfile` in the repo root:

```
siege.example.com {
    reverse_proxy frontend:80
    reverse_proxy /api/* backend:8000
}
```

- All traffic to `siege.example.com` is proxied to the `frontend` service (port 80 inside Docker)
- Requests matching `/api/*` are forwarded to the `backend` service (port 8000 inside Docker)
- Caddy handles TLS automatically — no certificate management required

> Replace `siege.example.com` with your actual domain. Caddy will fail the ACME challenge (and print an error) if the domain doesn't point to this server's IP yet.

### Add Caddy to the compose overlay

Append to `docker-compose.prod.yml` (or create a separate `docker-compose.caddy.yml` and add it as a third `-f` argument):

```yaml
services:
  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - frontend
      - backend

volumes:
  caddy_data:
  caddy_config:
```

Then start everything:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Caddy will request a certificate on the first request to your domain. Certificate files persist in the `caddy_data` volume across restarts.

> **Firewall:** ports 80 and 443 must be reachable from the internet. On Ubuntu: `sudo ufw allow 80/tcp && sudo ufw allow 443/tcp`

> **Other reverse proxies:** Traefik, nginx-proxy, or your own nginx config all work. The only requirement is that requests to `/api/*` reach the backend container on port 8000 and all other requests reach the frontend container on port 80.

---

## 6. Tunnel alternatives (no open ports)

If you can't or don't want to open ports 80 and 443, tunnel services create a secure public URL that routes to your local/VPS Docker stack without any firewall changes. For each option below, the hostname the tunnel gives you is what you put in `DISCORD_REDIRECT_URI` and the Discord Developer Portal.

### Cloudflare Tunnel (recommended)

[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) is the most robust free option. It routes traffic through Cloudflare's network with no open ports required, and the hostname is stable across restarts.

1. Install `cloudflared` on your host: [developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
2. Authenticate and create a tunnel:
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create siege-app
   ```
3. Create a tunnel config at `~/.cloudflared/config.yml`:
   ```yaml
   tunnel: <your-tunnel-id>
   credentials-file: ~/.cloudflared/<your-tunnel-id>.json

   ingress:
     - hostname: siege.example.com
       service: http://localhost:5173
     - service: http_status:404
   ```
   The tunnel routes to port 5173 (the frontend container's host-mapped port). Caddy is not needed when using a Cloudflare Tunnel since Cloudflare handles TLS.
4. Add a DNS CNAME in your Cloudflare dashboard pointing `siege.example.com` to `<tunnel-id>.cfargotunnel.com`.
5. Start the tunnel:
   ```bash
   cloudflared tunnel run siege-app
   ```

Update `DISCORD_REDIRECT_URI=https://siege.example.com/api/auth/callback` in `.env.production` and in the Discord Developer Portal.

> For production use, run `cloudflared` as a systemd service: `sudo cloudflared service install`

### Tailscale Funnel

[Tailscale Funnel](https://tailscale.com/kb/1223/funnel) exposes a port on your Tailscale node to the public internet. It requires a Tailscale account and the `tailscaled` daemon running on your host.

```bash
tailscale funnel 5173
```

Tailscale assigns you a stable HTTPS URL like `https://your-hostname.ts.net`. Use this as your `DISCORD_REDIRECT_URI`. See the [Tailscale Funnel docs](https://tailscale.com/kb/1223/funnel) for how to make it persistent across reboots.

### ngrok

[ngrok](https://ngrok.com/) works but the free tier rotates URLs on each restart — your Discord redirect URI becomes stale every time you restart the tunnel. Use ngrok for local testing only, not for a clan-facing deployment.

```bash
ngrok http 5173
```

Update `DISCORD_REDIRECT_URI` and the Discord Developer Portal each time the URL changes. For a stable deployment, use Cloudflare Tunnel or Tailscale Funnel instead.

---

## 7. Database options

### Option A — Postgres in the compose stack (simplest)

The default `docker-compose.yml` includes a `postgres:16` service. When you use the production overlay, this Postgres container runs with `restart: unless-stopped` and stores data in the `postgres_data` named volume (persists across restarts and upgrades).

The compose overlay's `backend` service sets:
```
DATABASE_URL: postgresql+asyncpg://postgres:password@postgres:5432/siege
```
This points at the sibling `postgres` container on the internal Docker network.

> ⚠️ **Do not use the default `password` value in production.** Generate a strong password with `openssl rand -base64 24` and set it in `.env.production` as `POSTGRES_PASSWORD=<generated value>`. Then update `DATABASE_URL` to match.

Change the password in `docker-compose.yml` (or override it in `docker-compose.prod.yml`) before going to production:

```yaml
# In docker-compose.prod.yml, under services.postgres.environment:
    POSTGRES_PASSWORD: "a-real-strong-password-here"
```

And update `DATABASE_URL` in `.env.production` to match:
```
DATABASE_URL=postgresql+asyncpg://postgres:a-real-strong-password-here@postgres:5432/siege
```

**Backups:** named volumes are not backed up automatically. Set up a cron job or use `pg_dump`:
```bash
docker exec <postgres-container-name> pg_dump -U postgres siege > backup-$(date +%Y%m%d).sql
```

### Option B — Managed Postgres (Supabase, Neon, ElephantSQL, or self-hosted)

Point `DATABASE_URL` in `.env.production` at your managed Postgres instance. Remove the `postgres` service from the compose stack by adding to `docker-compose.prod.yml`:

```yaml
services:
  postgres:
    # Disable the local Postgres container — using a managed instance instead.
    profiles:
      - disabled
```

Then update the backend's `DATABASE_URL` override in the same overlay:

```yaml
  backend:
    environment:
      DATABASE_URL: "postgresql+asyncpg://user:password@db.supabase.co:5432/siege"
```

Example managed Postgres connection strings:

| Provider | Format |
|---|---|
| Supabase | `postgresql+asyncpg://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres` |
| Neon | `postgresql+asyncpg://<user>:<password>@<host>.neon.tech:5432/neondb?ssl=require` |
| ElephantSQL | `postgresql+asyncpg://<user>:<password>@<host>.db.elephantsql.com:5432/<user>` |

### Schema initialization

**No manual migration step is needed.** On every startup, the backend container runs `alembic upgrade head` before the API server starts (see `backend/entrypoint.sh`). If the migration fails — for example because `DATABASE_URL` is wrong or the database isn't reachable — the container exits immediately with a non-zero code and the API never starts. This is intentional: a failed migration is always a hard error.

On a clean database, the first `alembic upgrade head` creates all tables from scratch. On an existing database, it applies only the pending migrations. It is safe to run on every restart.

---

## 8. First boot and smoke test

### Start the stack

```bash
cd rsl-siege-manager
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Watch startup logs to confirm migrations and service health:

```bash
docker compose logs -f backend
```

You should see:
```
Running database migrations...
Migrations complete.
INFO:     Application startup complete.
```

If `ENVIRONMENT=production` is set correctly you will NOT see `Development environment detected — seeding demo data...` — the demo seed is dev-only.

### Check service status

```bash
docker compose ps
```

All four services (`postgres`, `backend`, `frontend`, `bot`) should show `running` (or `healthy` for postgres). If any show `exited`, run `docker compose logs <service-name>` to see why.

### Smoke test checklist

```bash
# 1. Health endpoint — should return {"status":"ok","db":"connected"}
curl https://siege.example.com/api/health

# 2. Frontend loads
curl -sI https://siege.example.com | grep -i "HTTP/"
```

Then in a browser:

- [ ] `https://siege.example.com` loads the app (or landing page)
- [ ] Clicking **Sign in with Discord** completes the OAuth flow and returns you to the app as an authenticated user
- [ ] The members list shows your Discord guild members (requires the bot to be connected and `DISCORD_GUILD_ID` to be correct)
- [ ] You can create a new siege
- [ ] You can assign a member to a building position
- [ ] Saving a siege and triggering notifications sends a DM through the bot (confirms backend → bot communication is working)

### First-time demo seed (optional)

If you want to explore the UI with pre-populated data before adding real clan data:

```bash
docker compose exec backend python scripts/seed_demo.py
```

The seed script creates 25 fictional demo members and a populated demo siege. It is idempotent — running it twice does not create duplicates. To wipe the demo data and start fresh:

```bash
docker compose down -v    # destroys the postgres_data volume
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Do not leave demo data in a production instance your clan members will use — the fictional members ("Demo Member 01" etc.) will appear in the real member list.

---

## 9. Troubleshooting

### Discord OAuth redirect URI mismatch

**Symptom:** After clicking "Sign in with Discord" you land on an error page from Discord saying "The redirect_uri did not match any of the allowed redirects."

**Cause:** The `DISCORD_REDIRECT_URI` value in `.env.production` does not exactly match one of the redirect URIs registered in the Discord Developer Portal. Discord requires an exact string match including protocol, hostname, port (if non-standard), and path.

**Fix:**

1. In the Discord Developer Portal → your app → **OAuth2 → Redirects**, verify the registered URI is exactly:
   ```
   https://siege.example.com/api/auth/callback
   ```
2. In `.env.production`, verify `DISCORD_REDIRECT_URI` is the same string.
3. Common mistakes: trailing slash (`/api/auth/callback/`), `http://` instead of `https://`, port number included for port 443, or the redirect pointing at `localhost` (left over from a dev copy of the env file).

### Postgres permission / connection errors

**Symptom:** Backend container exits on startup with an error like `FATAL: role "postgres" does not exist` or `could not connect to server: Connection refused` or `asyncpg.exceptions.InvalidCatalogNameError: database "siege" does not exist`.

**Cause:** Either the `DATABASE_URL` in `.env.production` doesn't match the credentials set on the Postgres container/server, or the Postgres container isn't healthy yet when the backend tries to connect.

**Fix:**

1. Check that the username, password, host, and database name in `DATABASE_URL` match the `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` set on the `postgres` service.
2. Confirm the Postgres container is healthy before restarting the backend:
   ```bash
   docker compose ps postgres
   # Should show "healthy", not "starting"
   ```
3. For a managed Postgres instance (Option B): verify the connection string, that the database exists, and that SSL is required if the host demands it (e.g. add `?ssl=require` to the connection string for Neon).
4. On a fresh volume, if you changed `POSTGRES_PASSWORD` after the volume was initialized, the password change won't take effect — the volume stores the old password. Fix by destroying the volume: `docker compose down -v` then starting fresh.

### SESSION_SECRET rejected at startup

**Symptom:** Backend container exits immediately with an error containing `SESSION_SECRET` or `session secret` — for example `ValueError: SESSION_SECRET must be set to a real value in production`.

**Cause:** `SESSION_SECRET` in `.env.production` is missing, empty, or still set to the placeholder value `changeme-use-a-long-random-string-in-production`. The backend explicitly rejects placeholder values to prevent insecure deployments.

**Fix:** Generate a real value and set it in `.env.production`:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Then restart the backend:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d backend
```

### DISCORD_BOT_API_KEY / BOT_API_KEY mismatch

**Symptom:** Siege notifications (Discord DMs and image posts) silently fail. Backend logs show `401 Unauthorized` when calling `http://bot:8001/...`.

**Cause:** `DISCORD_BOT_API_KEY` (used by the backend to authenticate outbound calls to the bot) and `BOT_API_KEY` (used by the bot to validate inbound calls) are set to different values — or one of them was left as `changeme`.

**Fix:** Make sure both variables are set to the same random value in `.env.production`. See [Section 3](#3-clone-the-repo-and-create-your-environment-file) for how to generate one.

### Playwright / image generation fails on Alpine or low-memory hosts

**Symptom:** Creating a siege and generating an image fails with an error like `Error: Failed to launch the browser process` or Playwright logs show missing system libraries.

**Cause:** Playwright runs a headless Chromium browser inside the backend container to render siege images. On hosts with less than ~800 MB of free RAM, or on Alpine-based images with missing glibc dependencies, Chromium fails to launch.

**Fix:**

1. Ensure the host has at least 1 GB RAM (2 GB recommended). Check available memory: `free -h`.
2. The backend image is built on `python:3.12-slim` (Debian-based) and installs Playwright's system deps at build time. If you've customized the `Dockerfile`, ensure `playwright install-deps` and `playwright install chromium` are still present.
3. Add `--no-sandbox` to the Playwright launch args if running on a VPS without kernel namespace support (some cheap VPS providers disable user namespaces). This is controlled in `backend/app/services/image_generation.py`.
4. If image generation fails or containers restart during siege image creation, increase the backend `mem_limit` in `docker-compose.prod.yml` to `1g`.

---

## 10. Day-two ops

For incident playbooks, log queries, secret rotation, and rollback procedures, see [RUNBOOK.md in the main repo](https://github.com/glitchwerks/rsl-siege-manager/blob/main/docs/RUNBOOK.md).

### Updating to a new version

```bash
cd rsl-siege-manager
git pull origin main
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The `--build` flag rebuilds images from the updated source. `alembic upgrade head` runs automatically on backend startup and applies any new migrations before the API starts accepting requests.

### Stopping and starting

```bash
# Stop without losing data
docker compose -f docker-compose.yml -f docker-compose.prod.yml down

# Start again
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Wipe all data (destructive — drops the postgres volume)
docker compose down -v
```

### Viewing logs

```bash
docker compose logs -f backend     # API and migration output
docker compose logs -f bot         # Discord bot and notification output
docker compose logs -f frontend    # Nginx access log
```

### If you decide you want Azure instead

See [Self-Host on Azure](Self-Host-on-Azure) for the managed path: Azure Container Apps + Key Vault + PostgreSQL Flexible Server with automated backups, managed TLS, and a GitHub Actions deploy pipeline.
