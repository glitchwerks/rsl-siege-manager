# Read-only pilot observation and monitoring preparation

This is preparation under #557/#430, with Azure definitions preserved. It neither
activates a monitor nor starts a pilot. The operator separately approves the
isolated host, project, hostname, test Discord identity and observation window.
Do not use a production Discord token or attach a second gateway session. Existing
configuration preflight and approved activation must precede observation.

## VPS starting requirements

Use a dedicated Linux VPS capable of running the selected CI-built image
architectures, Docker Engine and Compose v2. The observer uses the explicitly local
`/var/run/docker.sock` endpoint; it does not change context, install tools, mount a
socket into containers or support remote Docker observation. It reads the local
Linux memory counters and actual PostgreSQL-volume filesystem. Docker access is
powerful: run only as the already approved deployment operator, never grant new
permissions as a side effect. Read-only observation still requires target review.

Start sizing evaluation at **2 vCPU, 4 GiB RAM and 80 GiB SSD**. These are provisional
choices, not measured minimums or a provider commitment. The current mom topology
has approximately 2.8 GiB in service memory ceilings; Chromium/image generation,
PostgreSQL, kernel/cache, backup encryption and concurrent work need additional
headroom. The summed CPU quotas can exceed two cores; they are ceilings rather
than reserved capacity, so evaluate contention before accepting the host.
80 GiB is a starting storage budget only: use measured database, image, log,
retained local dump/encryption sizes and growth to calculate required capacity.
Local backup files are additional to the database volume reserve. Seven-day
retention can retain many hourly copies, and failed runs remain preserved.

Only the proxy publishes TCP 80/443; operator remote access, outbound HTTPS/Discord,
registry pulls, database/private routes and recovery access need a reviewed host
plan. No firewall/DNS changes are performed here. Do not build application images
on the VPS. PostgreSQL 16 clients, Python 3.10+, age and rclone are separate backup
prerequisites; installing them or activating timers requires its own approved
procedure. DigitalOcean is a preference, not a dependency: this configuration
works with an ordinary Linux VPS that meets these requirements.

Before purchasing/accepting capacity, confirm supported image architecture, local
storage persistence, account recovery, independently encrypted backup destination
and recurring costs. This development work neither selects a paid plan nor
claims a provider uptime guarantee. A single VM is a single failure domain; alert
transport and backups must work while it is unavailable.

## One bounded operator report

Prepare a private 0700 operator-owned directory and 0600 configuration copied from
`pilot.json.example`. Replace every placeholder, supply the exact approved Compose
project, image digests, public HTTPS origin and all four expected `/api/version`
fields (including build metadata). Set `storage_path` to the reviewed source path
of the PostgreSQL `/var/lib/postgresql/data` mount. The observer verifies that path
matches the actual selected container mount before measuring disk availability.
Use the `bot` image key with `topology=bundled`; otherwise use `mom`.

Run the complete observation only after isolated activation has been approved:

```sh
python3 scripts/portable-pilot.py \
  --config /protected/pilot/pilot.json \
  --report /protected/evidence/pilot-observation-001.json \
  --confirm-readonly-pilot
```

The runner reserves a fresh private report before any Docker or HTTPS access. It
never overwrites reports and leaves `INCOMPLETE` if forcibly interrupted. It
allows 2–30 samples, 1–10 seconds between samples and a ten-minute total execution
budget, with bounded Docker commands and HTTPS responses. Preserve failed or
incomplete evidence and use a new report filename after diagnosis.

Each sample checks the project-scoped inventory, exact digest references, running
state, container health except proxy (which has no health check), OOM flags and
stable container IDs/restart counts. Running maintenance services, duplicate
services, missing or unexpected runtime services stop observation. Stopped
maintenance containers may remain from an approved migration. No `docker exec`,
restart, stop, pull, build, Compose activation, database SQL, member lookup or
Discord write is performed. The current bot health command checks connection;
container health can lag its underlying probe interval. It does not prove the
scheduler is progressing or health-command integrity; configuration preflight
and scheduler acceptance remain separate gates.

Read-only public GETs check `/api/health` database health and exact `/api/version`
identity. System trust verifies HTTPS; ambient proxies and redirects are refused.
No authentication headers, secret URLs or monitor callbacks are used. Version
GET also exercises the application's existing private bot-version lookup; it
performs no Discord write. Do not run it against an unapproved target.

Reports include fixed service/check labels and numeric sampled memory/CPU maxima,
local host capacity, last sampled available memory/disk, sample count and timing.
They exclude project/container names, IDs, URLs, paths, digests, versions, Docker
metadata, credentials, member data and raw errors. These are **observed sample
maxima**, not peak RSS, a load benchmark or allocation guarantees. Docker CLI
memory accounting may exclude cache; CPU percentages are per-core accounting and
can exceed 100%. Short-lived peaks between samples can be missed.

`PASS` means only this bounded observation passed. `pilot_accepted` is always false.
Repeat during separately approved representative work (largest-board images,
concurrency, sync/reminders and restore) and preserve each report. The observer
creates no load and proves no core workflow by itself. A replacement/restart
during the window stops the report; it does not restart anything automatically.

## Independent monitoring contract

`monitor-policy.json.example` is an inert, provider-neutral requirements template,
not an executable monitor configuration. The chosen external monitoring service
must implement HTTPS health/identity checks, certificate-expiry alerts, failure
thresholds and outage/recovery delivery to an independently available operator
channel. The observer validates TLS today but does not measure expiry. Configure
backup success/failure and overdue signals using the separate backup job runbook.
An unchosen channel or same-host observer alone is not sufficient monitoring.

Private runtime observation must eventually export a sanitized result to an
independent missed-signal monitor; this runner does **not** send that signal.
An observer that stops with the VM cannot notify about its own disappearance.
Scheduler progress instrumentation, a service-specific watchdog with cooldown/
escalation and independent runtime signal delivery remain implementation and
activation gates. No broad host/database restart is approved by this template.
Do not expose unauthenticated sidecar endpoints or mount a Docker socket into an
application/monitor container to fill those gaps.

After selecting the destination/channel, separately approve tests of delivered
outage, recovery and missing/overdue notifications. Full #430 Discord acceptance
writes need separate approval and a dedicated test guild/recipients. OAuth,
secure-cookie behavior, scheduler progress, full core workflows, production-sized
RPO/RTO rehearsal and reverse-data migration are still required before cutover.

Rollback of this preparation is a repository revert. No live resources, monitor
accounts, purchases, schedules or notifications have been changed.
