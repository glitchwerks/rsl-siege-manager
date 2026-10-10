# Portable production stack

This is a standalone production configuration, independent of VM provider. It is
**not approved for activation yet**. Issue #557 tracks the remaining pilot,
monitoring, backup, and recovery evidence. Existing Azure Bicep, deployment
workflows, and development Compose files remain supported and unchanged.

Use this configuration instead of layering `docker-compose.prod.yml` on the
development file: that older path inherits builds and public service ports.
Only Caddy publishes host ports (80/443). API requests go directly from Caddy to
the backend, preserving public HTTPS/Host and avoiding a second proxy rewriting
forwarded headers. The API upstream alias `api-proxy` exists only on the fixed-address proxy network,
so Caddy cannot accidentally connect over the shared application network.
Uvicorn trusts only Caddy's fixed private address; it does not
trust arbitrary caller headers. Access logging is disabled on this API entrypoint
so OAuth callback query credentials are not written into access logs.

## Images and runtime configuration

An operator prepares these files under a protected deployment directory. Copy
`Caddyfile` and `init-databases.sql` there too; Compose resolves their mounts
relative to `--project-directory`. Preflight requires both copies to match the
reviewed repository versions; refresh them after updating this configuration:

- `stack.env`: copy the shape of `stack.env.example`; use actual image digests
  obtained from CI/registry metadata. These examples are intentionally invalid
  until populated. Authenticate separately using read-only registry access.
- `runtime/backend.env`: based on `backend.env.example`.
- `runtime/database.env`: two distinct raw application database passwords.
- `runtime/postgres-admin`: a separate administrator password, mounted as a
  Compose secret. Keep it out of application containers.
- Exactly one of `runtime/mom.env` or `runtime/bundled.env`.

Restrict directories to the deployment operator (0700) and credential files to
0600. These paths are ignored by Git. Never publish rendered `compose config`
output or raw environment files: they contain credentials. The preflight captures
rendered values internally and emits only fixed check names and booleans. Do not
use shell tracing. Keep secret recovery material encrypted outside this host.

Choose a unique Compose project name and non-conflicting private proxy subnet
**after** reviewing the approved target host's routes, containers, services, port
usage, storage capacity, remote access, and recovery path. Example subnets are not
universal defaults. Never install/change Docker, firewall, networks, DNS, or mounts
on an existing infrastructure host as a side effect of preparing this stack.

Pin PostgreSQL 16 and Caddy by digest, along with application images. Siege images
are currently built in CI and available from retained Azure registries; using
those images is transitional, not a requirement for Azure at runtime. Before
retiring a registry, preserve all active/recovery image digests in a provider-neutral
registry/archive and verify pulls. Do not point the VPS at mutable `main` or
`latest` images or build Playwright on the small production VM.

Frontend public/canonical URL is baked into the CI-built bundle. Verify its value
for the pilot/production domain and build a reviewed matching image where needed;
a runtime environment variable does not rewrite Vite assets. Browser calls remain
same-origin `/api`. Backend `DISCORD_REDIRECT_URI` and `ALLOWED_ORIGINS` must match
`https://PUBLIC_HOST`; register that exact callback with Discord. Production mode
keeps secure session cookies and disables demo authentication.

## Select one bot topology

Use `compose.yml` plus **one** overlay:

- `compose.bundled.yml`: reference bundled bot on `http://bot:8001`; no mom-bot.
- `compose.mom.yml`: mom-bot on `http://mom:8001`, using its explicit portable
  environment-secret/password-database mode. Requires the portable configuration
  change in a tested immutable candidate or mom-bot v1.6.0 or later. No bundled bot.

The two modes preserve existing wire contracts. Sidecar and database ports are
private Docker-network endpoints. Caddy exposes only the authenticated role-sync
POST described below; the general sidecar API remains private.
The reverse-call key, forward sidecar key, and session-signing key must all be
distinct; preflight rejects reuse in either topology. Match both service ends according
to the examples. Environment values are deployment-scoped, not dev/prod-prefixed.
Use a dedicated test guild/token/recipient set during pilot work. Never start a
second process with a production Discord token.

The first boot of an **empty** PostgreSQL volume creates `siege`/`siege_app` and
`mom_bot`/`mom_app`, with no superuser, role-creation, or database-creation privileges
for either application role. Cross-database PUBLIC access is revoked. Encode URL
passwords correctly; the database environment holds raw values. Existing volumes
are not reinitialized when environment passwords change. Rotation must update the
actual database role and all consumers through a separate reviewed procedure;
never remove a volume to make changed credentials take effect.

The portable API services explicitly clear the existing image's entrypoint,
which otherwise runs migrations automatically and ignores the supplied command.
Runtime starts Uvicorn with the reviewed proxy trust settings; only the opt-in
maintenance service invokes Alembic. This override is confined to these files.

For mom-bot migrations use the `migrate-mom` service's direct Alembic command. Its
image entrypoint is explicitly cleared so an inherited entrypoint cannot invoke
the Azure-only `migrate.sh` script. `migrate-siege` and
`migrate-mom` belong to the opt-in `maintenance` profile; they are not started by
normal service activation. Migrations do not start Discord. Apply them only to the
intended reviewed database, with verified backup/recovery first.

Day-role sync is disabled in the example until conforming receiver acceptance.
For production cutover preserve today's enabled state, role IDs, and recipient
settings rather than silently adopting pilot defaults. Set `DAY_ROLE_SYNC_URL` to
`https://PUBLIC_HOST/api/internal/role-sync` with the mom topology. Caddy terminates
public TLS and forwards only this POST to the selected receiver on the application
network. The receiver verifies
the existing sidecar bearer key. No other sidecar endpoint is exposed. Preflight
rejects an enabled configuration with a missing, HTTP, or mismatched webhook URL,
and verifies the overlay's receiver route and network connectivity.
The bundled receiver currently cannot apply the producer's unassign payload;
preflight blocks enabled role sync in that topology, and its proxy does not route
to the bot. If production role sync is enabled, use the mom topology and preserve
that setting. Bundled support requires a separate contract fix before activation. Pilot acceptance
must verify TLS, a rejected unauthenticated webhook, and a successful authenticated
role update in the test guild before production activation.

## Read-only configuration gate

From the repository root, with Docker Compose v2 installed on the reviewed target:

```sh
python3 scripts/portable-preflight.py --topology mom \
  --stack-env /protected/deployment/stack.env \
  --project-directory /protected/deployment \
  --report /protected/evidence/portable-config-001.json
```

Select `--topology bundled` for the reference bot. The report directory must already
exist, be owned by the invoking operator, have mode 0700 or stricter, and be a
real directory rather than a symlink. Before rendering, the gate checks private directories (0700) and regular credential
files (0600 or stricter), rejecting symlinks and group/other access. Reports are
created exclusively at mode 0600; an existing
report is never overwritten. A STOP requires diagnosis before any next phase.
If report creation or writing fails, the command exits nonzero and prints a
sanitized `STOP` with `report_write_failed`, without a traceback or raw filesystem
diagnostics. Preserve any existing/partial file and retry with a new report path
after correcting the directory/access issue; never treat incomplete evidence as
acceptance.
The administrator secret must be a readable regular file with no group/other
permissions, no symlink, at most 4 KiB, and a non-placeholder value of at least
16 characters distinct from both application passwords. Its value stays in memory
for comparison and never appears in the report. Terminal LF characters are
handled as the PostgreSQL image handles them.
The command renders configuration only: no pull, build, service/container creation,
network creation, database access, Discord traffic, or host configuration change.
It rejects OAuth credential placeholders and requires distinct raw application
database passwords, including when both connection URLs match their configured
passwords. The database roles are created in either topology, so password
separation is required even in bundled mode.
The selected bot must have a nonblank, non-placeholder Discord token and a positive
numeric guild ID matching the backend. This checks configuration shape and
consistency only; live token validity remains a separate pilot check.
Use an operator-controlled public hostname; reserved example/test hostnames are
rejected even when OAuth URLs match. CI uses a synthetic non-reserved hostname
without performing DNS, certificate, or network validation. Domain ownership,
DNS readiness, and TLS issuance remain pilot gates.
Keep Compose diagnostics private; the sanitized report intentionally suppresses
raw stderr. This report does not validate host suitability or live identity access.

CI renders both actual topologies using synthetic secrets without starting apps.
A separate disposable CI PostgreSQL check verifies database initialization and
role separation. None of that evidence replaces a pilot on the intended target.

## Resource and health baseline

The initial runtime memory ceilings total about 2.8 GiB with mom-bot: backend
1280 MiB (including Chromium headroom), PostgreSQL 768 MiB, mom-bot 512 MiB, frontend
128 MiB, and proxy 128 MiB. A 4 GiB VM is a sizing hypothesis, not acceptance.
Maintenance jobs require additional headroom: keep apps stopped for first restore
and migrations, and measure later migration peaks before concurrent operation.
Image generation includes shared memory, so include it in observed cgroup usage.
Measure peak RSS/CPU/disk for largest boards, concurrent image requests, member
sync, reminders, and restore. Raise bounds or serialize work before accepting
OOM/restart behavior. JSON container logs are capped at 3 × 10 MiB per service.

Backend health checks database connectivity. Bot health checks verify
`bot_connected`, rather than accepting HTTP 200 alone. `restart: unless-stopped`
recovers exited processes; it does **not** restart a running unhealthy process.
Docker health status is a local signal, not an external uptime guarantee.

Required before pilot acceptance:

- Independent HTTPS `/api/health` monitor and outage/recovery notification, outside
  this VM; HTTPS `/api/version` as a release-identity check, plus certificate expiry.
- Private bot connection **and scheduler progress/heartbeat**, backup age/failure,
  disk capacity, and memory/OOM signals exported without secrets/member data.
  The current bot health endpoint has no scheduler heartbeat, so this remains work.
- A bounded service-specific watchdog with cooldown and escalation. Never restart
  PostgreSQL or the whole host merely because one endpoint is unhealthy. Avoid
  mounting the Docker socket into monitoring/application containers.
- Alerts must reach the operator even when the bot/VM is down. Choose an approved
  destination and test outage/recovery separately; do not assume provider VM SLA
  includes application support.

## Backup, restore, and cutover gates

This configuration prepares the stack; automatic backups and restore scripts are
still required before activation. Never treat the named database volume or a VM
snapshot as the only backup. Complete these independent reviewed phases:

1. **Preparation:** preserve Azure resource/configuration/identity/image inventory;
   define acceptable Azure retention costs and a recovery window; verify target
   host suitability and registry/secret access without changing live services.
2. **Backup:** create versioned PostgreSQL 16 custom-format dumps for both databases,
   protected configuration/role reconstruction material, and encrypted off-host
   copies. Record sanitized timestamps/checksums/control totals. Coordinate a
   write/bot pause for cross-application consistency at final cutover. Verify backup
   age from an independent monitor. Proposed RPO is one hour, pending rehearsal.
3. **Isolated restore:** restore into fresh pilot databases, owned by the matching
   application users, without carrying Azure ownership/privileges blindly. Use
   `--no-owner --no-acl` and reviewed role/grant recreation. Do not overwrite live
   databases. Prove representative control totals and schema versions, then run
   separately approved migrations. Restore with the same major PostgreSQL version.
4. **Pilot activation:** explicitly approved dedicated target and test bot identity;
   validate OAuth/secure cookies/proxy rate-limit attribution, health/version,
   largest-board images, core workflows, scheduler behavior, and read-only sidecar
   acceptance (#430). Full Discord writes are a separate gate. Retain one sanitized
   report per phase. Rebuild from independent backups and measure RTO (proposed
   1–2 hours, not yet demonstrated). Preserve evidence before pilot cleanup.
5. **Production cutover:** separately approve the target and write/notification
   pause. Stop Azure writers and the old connected bot; final backup, restore,
   reconcile, then start only one producer/bot and switch public origin/DNS. Verify
   intended service and unaffected services before releasing the pause.
6. **Recovery/retention:** keep Azure definitions, secrets/identity recovery, images,
   and data restore procedure. Before new VPS writes, recovery can use the paused
   original state. After writes resume, returning to Azure needs a new pause and
   reverse data restore/reconciliation; DNS reversal alone loses newer state.
   Schema rollback needs a compatible image/database backup, not only a container
   restart. Retire expensive Azure resources only with explicit approval after the
   observation period and successful independent recovery.

No automated activation, backup/restore mutation, DNS change, Azure retirement,
or Discord write test is included here. Never use `down -v` or broad Docker prune
as recovery or cleanup. Keep the coordination issue open until pilot, production,
and recovery gates are actually complete.

References: [Compose service settings](https://docs.docker.com/reference/compose-file/services/),
[Caddy proxy header behavior](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy),
[Uvicorn proxy trust](https://uvicorn.dev/settings/), and
[PostgreSQL role initialization](https://www.postgresql.org/docs/16/app-psql.html).
