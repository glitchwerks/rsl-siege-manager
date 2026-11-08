# Repeat the portable pilot on another VM

This runbook describes a repeatable deployment and recovery process. A successful
experiment on a temporary VM does not approve a production cutover. Track
acceptance evidence in issue #557; keep credentials and workload evidence outside
Git. Existing Azure services remain in place until a separate cutover decision.

## What moves, and what must be configured again

| Item | Transfer approach |
| --- | --- |
| Application and infrastructure code | Use reviewed, merged repository revisions. Do not copy an unreviewed feature branch into production. |
| Container images | Record exact digests and trusted build/source evidence. Verify pull access from the new VM. No mutable tags or local application builds. |
| Compose configuration | Use `deploy/portable/compose.yml` with exactly one Siege sidecar overlay. Inventory any independent bot separately; a sidecar choice does not authorize retiring that bot. Render and review target-specific values privately. |
| Database contents | Use verified PostgreSQL 16 custom dumps, reviewed role reconstruction, and an isolated restore rehearsal. Never copy a running database volume as the migration procedure. |
| Encryption public recipient | Reuse the tested `age1…` recipient if its private key is still independently recoverable. It is not tied to Windows hardware or a Windows login. |
| Encryption private key | Preserve protected copies outside the application VM, separate from backup upload credentials. Do not commit it or install it on the application VM. |
| NAS connection | Recreate a private configuration with the intended host, share prefix, account/domain, and password. Recheck access and share encryption. |
| Monitoring | Create a distinct check for each VM/environment. Connect its intended Slack channel and test delivered alerts. Never let the test VM send success signals to the production check. |
| Scheduler and retention | Recreate and verify target-specific units after the complete job and recovery tests pass. They do not move merely because configuration files were copied. |
| HTTPS and DNS | Choose the real hostname and trust model afresh. Re-register the matching Discord callback and verify client trust. |
| Discord identity | Use a dedicated test application/server for rehearsal. At cutover, start exactly one producer for the intended production identity. |

## 1. Inventory the new host before changing it

Record the distribution, CPU/RAM/swap, free disk, network routes, ports, existing
containers/services, Docker/Compose versions, remote administrator access and
recovery access. Review address/subnet conflicts and persistence separately.

The VMware test host is not a template for provider provisioning. Do not use
Incus tooling for a VMware VM. Select provisioning only for the actual target.
Keep the developer bot off and Azure running throughout preparation and rehearsal.

See [portable stack requirements](../../deploy/portable/README.md) and
[bounded observation](../../deploy/portable/monitoring/README.md). Size the real
host from representative work, including image generation, concurrent requests,
backups and restore; an idle test is insufficient.

Use the [Debian VPS bootstrap procedure](portable-host-bootstrap.md) for the
separate runtime, directory and firewall phase before application setup.

### Verify identities before selecting the production bot topology

Inventory actual production identities before choosing a portable topology. If the
existing installation runs both the bundled Siege sidecar and an independent Mom
bot, the base stack's mutually exclusive overlays only select the Siege sidecar.
They do not describe or replace the independent bot. Do not combine the two
existing overlays: the Mom overlay rewrites the backend and webhook upstreams.
Read each existing credential's application identity through the authenticated
Discord application API before assuming these are different bots. Two containers
can share one Discord application. If identities differ, preserve each one and
review an independent-Mom definition and validation. If they match, prepare the
supported single-Mom topology only after verifying the Mom receiver covers the
required Siege APIs and existing reminder/activity features. Include this
consolidation explicitly in the cutover proposal; preserve both databases and
stop both old gateway processes before starting the replacement. Do not start an
extra gateway to check a token. Existing development sidecars must be inventoried
separately and remain unchanged unless the approved pause scope includes them.
Keep every new Discord gateway and scheduler off during configuration preparation.

Review the exact Mom image source, not just its tag. Older production images may
hard-code Azure managed identity and Key Vault and lack portable environment
secrets, password database authentication and the scheduler-progress health probe.
Those images cannot run unchanged on a Debian VPS. Moving to an already tested
portable image is a deployment version change: record its immutable digest,
source and migration/behavior review rather than silently copying the pilot's
image approval. Do not supply an Azure administrator cache as a runtime workaround.

An independent Mom can use a separate Compose project attached to the base
stack's explicitly named application and database networks. Keep its gateway in
an opt-in producer profile and its migration command in a separate maintenance
profile. The base bundled-sidecar configuration still needs its canonical
preflight; separately validate the independent project's image approval, exact
entrypoints, password/environment modes, least-privilege database URL, private
ports, resource/log limits, reverse-call key pairing and health probe. A successful
base preflight does not validate an additional project.

Before transferring production runtime credentials, obtain approval for the exact
payload and target. The replacement needs only the credentials required by the verified topology
and OAuth client secret, plus their non-secret identity/origin settings. Generate new
database passwords, session-signing and internal API keys directly on the target;
do not copy old internal keys or source database/admin credentials unnecessarily.
Use host-key-verified SSH, private directories/files and stdin rather than shell
arguments or logs. Stage and validate without starting either producer. Source
settings read access is not implicit approval for every subsequent secret export.

Budget memory for the complete intended service set. The portable defaults plus
a separate 512 MiB Mom service sum to 3,200 MiB of container limits before the host,
Docker, backup clients or transient work. This is a planning bound, not a measured
working set. A nominal 4 GB host has little reserve with that topology; choose a
larger instance or demonstrate an accepted, representative load with reviewed
limits before cutover. Do not count swap or the idle rehearsal-container restore
as proof that both bots and concurrent browser image generation will fit.

### Stage cached images without moving a registry administrator login

Verify source tags against exact registry digests before transfer. A temporary
operator-side registry login can pull the selected images, then be logged out and
removed. Transfer image content over verified SSH; do not copy the operator's
Azure/registry credential cache to the target. Check imported platform and content
identity against the source. Archive loading may preserve content but lose its
repository-name association. A local name can be restored only after proving its
image/manifest digest matches the trusted source and ensuring no conflicting local
tag is overwritten. Independently resolve the full original `repository@sha256`
reference afterward. If that fails, stop: a matching layer cache or mutable tag
alone is not readiness for the pinned Compose configuration.

## 2. Prepare protected target configuration

Use an operator-owned deployment directory with mode 0700. Credential files and
private reports must be 0600. Follow the portable stack examples for `stack.env`,
`runtime/backend.env`, `runtime/database.env`, a separate PostgreSQL administrator
secret, and the selected bot configuration. Copy the canonical Caddy and database
initialization files as instructed by the stack runbook.

Choose a new Compose project and non-conflicting proxy subnet. Publish only the
proxy's reviewed host bindings; do not expose database, API or bot sidecar ports.
Use distinct database passwords, session-signing and sidecar keys. Obtain scoped
read-only registry credentials and record their renewal/expiration procedure.
Never publish rendered Compose output: it contains secrets.

For the mom topology, verify both portable authentication and scheduler-progress
health support in the exact image and prepare `runtime/mom-image.json`. Keep the
migration entrypoint overrides from the reviewed Compose files.

For test Discord setup, use the coordination repository's documented
`prepare-test-server.py` and `prepare-test-application.py` workflows. Application
creation is a portal step; the tool copies supported settings into the create-once
test application. Inspect IDs and permissions before applying. Do not reuse a
production bot token for a parallel test process. Preserve exact role/channel IDs;
role names alone do not establish identity.

### HTTPS differences between LAN testing and the real target

The LAN pilot uses a target-specific overlay with Caddy `tls internal`, explicit
LAN bindings and an independently installed public CA certificate on clients.
Do not copy the test VM's address or assume its CA is trusted on another client.
Keep CA private material protected. Never disable certificate verification.

For a public deployment, use the reviewed public TLS/DNS configuration instead of
the LAN overlay. For another private deployment, explicitly select its CA/trust
recovery procedure. Match public origin, allowed origins, Discord redirect URI
and portal callback exactly: `https://HOST/api/auth/callback`.

Verify frontend canonical metadata in the chosen image. A runtime environment
variable does not rewrite built Vite assets. The test pilot required a read-only
index override for literal `%VITE_PUBLIC_URL%` placeholders; this is a pilot
workaround, not a production-ready image. Prefer a reviewed correctly built image
for the real hostname. Do not copy an undocumented HTML patch.

### Test a VPS hostname without changing public DNS

Prefer a local DNS record in the operator's network-wide Pi-hole resolver when
that is the established DNS routing system. Map only the dedicated rehearsal
hostname to the new VPS public IPv4 address; leave the production hostname and
public authoritative DNS unchanged. This avoids per-workstation hosts files.
Verify resolution from the actual clients, flush stale DNS caches if necessary,
and retain certificate trust installation on each browser/client that needs it.
A local DNS record supplies name resolution, not certificate trust. Record the
Pi-hole override for later removal or deliberate retention after acceptance.

When explicitly requested, a Windows hosts-file override is an alternative that
maps the dedicated rehearsal hostname directly to the new VPS. Do not override
the production name globally or change the public DNS record for this test. Edit
`C:\Windows\System32\drivers\etc\hosts` with administrator privileges, adding
one `VPS_PUBLIC_IP rehearsal.example.com` entry. Check for an existing conflicting
entry first and flush the Windows DNS client cache after the edit. Record which
workstation has the override so it can be removed after the rehearsal.

Prepare a separate, bounded Caddy-only Compose project on the target with a pinned
image, no application credentials, a hostname-specific site and `tls internal`.
Initially serve only a harmless readiness page and a health endpoint. Persist its
CA/TLS state in dedicated volumes; keep the admin API disabled and access logging
off. Publish only reviewed web ports and use the existing Docker-aware firewall.
Do not attach a production database or start either bot to prove HTTPS. Reserve
web-port ownership explicitly: this project must be stopped or deliberately
replaced before another proxy binds the same ports at cutover.

Validate the configuration before starting it. A hardened Caddy image with a
file-level bind-port capability needs `NET_BIND_SERVICE` in its capability bounding
set even for validation; dropping all capabilities without adding that one can
prevent the executable from starting. Quote a comma-containing tmpfs option in a
YAML flow list (for example `["/tmp:size=16m,noexec,nosuid"]`) so its options are not
parsed as separate mount paths. Bound Caddy memory, CPU, process count and logs;
mount its configuration read-only and leave only TLS/config storage and temporary
space writable.

Export only `/data/caddy/pki/authorities/local/root.crt` over host-key-verified SSH,
never the CA private key. Verify the public certificate fingerprint against the
protected operator evidence. Import the certificate into the intended Windows
user's trusted root store with `Import-Certificate -CertStoreLocation
Cert:\CurrentUser\Root`. A CA trusted for the VMware pilot is not automatically
the new VPS CA. Keep the rehearsal trust independent of public production TLS,
and record the imported certificate thumbprint for later removal.

Before asking for browser acceptance, use `curl --resolve` with the hostname,
actual VPS address and exported CA (`--cacert`) to test from outside the VPS.
Require a successful hostname/chain verification, expected readiness response and
HTTP-to-HTTPS redirect. Do not use `-k` or click through certificate warnings.
Then verify the page in the intended Windows browser using the Pi-hole local DNS
record or explicitly selected hosts-file entry.
This proves connectivity and TLS only, not application, OAuth or cutover readiness.

For subsequent application testing, the test Discord application's registered
callback, backend redirect and allowed origin must use the same rehearsal
hostname. Preserve the test application and guild separation. Registering a
callback alone does not authorize starting a gateway with production credentials.
A final public deployment uses the separately reviewed public TLS configuration,
not an undocumented continuation of the rehearsal's internal CA.

### Bring up a web-only rehearsal before moving a gateway

Keep the production candidate separate from a dedicated rehearsal Compose project.
Use only the existing test application's OAuth client ID/secret, test guild/roles
and a verified snapshot of the local test-pilot databases. Generate fresh target
database passwords, session-signing and internal API keys. Do not attach the prior
production-data restore volume or production runtime environment to the rehearsal.

Keep the rehearsal definition limited to PostgreSQL, API and frontend, with no
bot service, published database/API ports, automatic migration entrypoints or
production mounts. Bound memory/CPU/processes/shared memory and logs as for the
reviewed portable stack. Restore into fresh UUID targets with the canonical
least-privilege restore checks first. On this new isolated PostgreSQL instance,
rename only those restored test databases to the runtime names after verification
and before any application connects; never rename an existing production target.
Do not seed over restored data or remove volumes on failure.

After API/frontend health checks pass, attach the existing rehearsal Caddy to a
non-conflicting private proxy network at the API's exact trusted proxy address.
Preserve Caddy's existing TLS volumes and public CA certificate, then replace only
the rehearsal readiness route with API/frontend upstreams. Keep a rollback to the
original readiness Compose file if HTTPS or application health fails. Set
`X-Robots-Tag: noindex, nofollow` on the rehearsal. Verify the real website returns
200, a protected API route returns 401 without authentication, and OAuth initiation
uses the test application/new callback and Secure/HttpOnly state cookie.

Register the additional rehearsal callback in the **test** Discord application,
keeping the original pilot callback. Do not change production OAuth settings for
this check. Starting OAuth is not a completed sign-in: the backend also asks its
bot sidecar to verify live guild membership and the required role.

If the existing test bot remains on the original VM, a temporary private read-only
relay can reuse that real membership check without starting a duplicate gateway.
Verify the existing test application/guild and exact bot image before connecting.
Use a dedicated root-only Unix socket on the VPS and an authenticated SSH reverse
forward from the operator VM to the existing test sidecar's private Docker address.
This requires no public sidecar port or SSH GatewayPorts change. A separate bounded
Nginx relay on only the rehearsal application network can proxy that Unix socket.
Allow only GET on `/api/members/<17-to-20-digit-id>`, `/api/health` and
`/api/version`; deny every other route and non-GET method. Validate that notification
POSTs and member POSTs are rejected before exposing browser sign-in. Authenticate
with the existing **test-sidecar** key, never a production bot token, and keep keys
out of command arguments and logs. Do not substitute fake role checks or disable
authentication.

Bound this temporary SSH process (for example, an eight-hour transient systemd
unit running as the deployment user with strict host-key checking and keepalives).
Do not enable it on boot. Record the deadline and source-VM dependency: expiry,
VM shutdown or test-bot recreation must fail sign-in closed. Do not restart a dead
forward blindly over a stale socket; prove the old unit is inactive before removing
only its owned socket. Stop the transient unit and relay after acceptance, restore
or deliberately replace its target configuration, and ensure production uses its
own reviewed sidecar. This relay is a rehearsal dependency, not production design.

### Use measured evidence when retaining a 4 GB target

An operator may retain the existing 4 GB target pending evidence of insufficiency.
Do not require an upgrade based solely on the sum of container limits. Measure
actual available memory (including reclaimable cache), per-container current/peak
usage and OOM events, swap-in/out deltas, response times and representative
concurrency on that host. Record idle/before/during/after samples with the intended
bounds. A small existing swap allocation alone does not prove current pressure.

Start with concurrent image API calls against the restored test board, verifying
that board state stays unchanged. Also use the pinned API image in a separate
network-isolated, credential-free worker for the documented 30-fictional-member
render workload. Do not persist fictional assignments, publish images to Discord
or contact a live production bot. Visually inspect the complete board and final
roster row; retain the private images and metrics. Separate actual API latency
from worker-only rendering time and account for the worker in host-wide peaks.

A successful bounded web workload supports continuing on the existing instance;
it does not certify both production bots, the largest upgraded board, long-running
traffic or backup overlap. Gate any sizing change on measured pressure, OOMs,
sustained swap activity or unacceptable latency under the full intended workload,
and review tuning or a larger instance when that evidence appears.

## 3. Initialize or restore databases deliberately

A fresh PostgreSQL volume creates the two databases and least-privilege roles;
changing environment files does not rotate passwords in an existing volume.
Never remove a volume to force reinitialization.

Apply separately reviewed migrations through the maintenance services, without
starting a Discord gateway. Initialize standard reference data on a fresh Siege
database using `backend/scripts/seed.py`; migrations alone do not populate the
building configuration, post conditions and priority defaults. Do not use the demo
seed, which creates fictional members and a siege. When restoring real data,
compare existing reference configuration before any seed operation.

Test OAuth authorization with the intended guild membership, Discord role and
matching application member record. A synthetic pilot member is not production
member migration. Keep day-role sync disabled for synthetic checks unless its
separate test-guild acceptance has been approved.

### Rehearse with the actual Azure sources before the maintenance window

Inventory every production resource group explicitly. An independent bot may live
in a group without a `prod` suffix. Record running revisions, exact image digests,
all writer identities, database names/majors, authentication modes, schema heads,
migration jobs and scheduler triggers. Preserve both a bundled Siege sidecar and
an independent Mom bot when both exist; a successful single-bot pilot does not
approve consolidating production identities. Do not invoke the Azure migration job.

Get explicit approval before exporting production data or changing a source
firewall. Scope that approval to read-only snapshots, private staging, a named
target, an isolated restore and the encrypted NAS destination. Do not include
cutover or production Discord delivery implicitly.

Use a private, short-lived Azure CLI session on the operator machine. For a
password-authenticated source, retrieve only the existing application database
secret and parse it with the application's URL parser; special password characters
can make generic URL parsing disagree. Do not reset a live password to simplify
export. For an Entra-only source, use an existing approved database administrator
and a short-lived PostgreSQL access token. Check expiry before starting. Never
copy the Azure login cache, source passwords or access tokens to the VPS.

If direct source access requires a temporary firewall rule, allow only the
operator's current public IPv4 address (`start == end`), use a unique
`rsl-rehearsal-...` name and verify ownership/address before deletion. With the
current Azure CLI, flexible-server firewall commands use `--name` for the rule
name and `--server-name` for the PostgreSQL server. Reserve a private operation
report before creation. Remove that exact rule in a `finally` handler, verify its
absence and record cleanup even when export fails. An interrupted operator machine
requires a manual check of this uniquely named rule before continuing.

Use PostgreSQL 16 clients with TLS `verify-full` and a trusted CA, disable ambient
connection overrides and force source connections read-only. The canonical
`portable-recovery.py backup` phase shares one exported snapshot between each
individual database dump and its control totals. While Azure writers continue,
these are independently consistent snapshots, **not an atomic snapshot across
both databases**. They are suitable for rehearsal; the final cutover requires all
writers paused before the final snapshots and reconciliation.

Verify custom archive format, hashes and manifest locally. Transfer only the two
dumps, manifest and reviewed recovery tools over host-key-verified SSH into a new
0700 VPS directory; keep each file 0600 and verify every transferred hash. Do not
transfer source connection files. Run a fresh pinned PostgreSQL 16 container with
`--network none`, no published ports, bounded memory/CPU and a new uniquely named
volume. Generate its independent administrator secret locally on the VPS.

Create only the two fresh `rsl_restore_UUIDHEX_siege` and
`rsl_restore_UUIDHEX_mom` targets, owned respectively by `siege_app` and `mom_app`.
Use LOGIN roles without superuser, role/database creation or role memberships,
and revoke database privileges from PUBLIC. The canonical restore phase checks
both empty targets before writing either, restores without source ownership/ACLs
and compares every table total and schema revision with the snapshot manifest.
Do not run application migrations, reference/demo seeds, gateways or schedulers
against these targets. A container-local client adapter may stream the private
archive to `pg_restore` on stdin; omit the filename rather than passing `-` as a
literal archive filename.

Seal and publish the verified bundle using the recovery copy phases and the
existing public age recipient. Upload the matching receipt under the copy UUID
and independently read back both objects. Leave the private decryption key on the
recovery machine. Encrypted-object readback plus an SSH-copy restore does not prove
a new restore from the NAS ciphertext; perform the separate decryption/recovery
acceptance when required, and do not enable automatic retention from this evidence.

Stop only the isolated rehearsal container and preserve its volume and private
reports. Record export, transfer and restore durations separately; include HTTPS,
OAuth, producer ownership and public-origin checks in the complete downtime
budget. Clear the temporary Azure login cache and source credential files after
the rehearsal, retaining sanitized evidence and protected archives. Reauthenticate
for final cutover instead of keeping an administrator session indefinitely.

## 4. Configure encrypted NAS copies

Follow [encrypted copy phases](../../deploy/portable/recovery/COPIES.md).
For Windows SMB, use a dedicated account and an explicit remote of this shape:

```ini
[backup]
type=smb
host=NAS_ADDRESS
user=BACKUP_ACCOUNT
domain=NAS_COMPUTER_OR_AD_DOMAIN
port=445
pass=RCLONE_OBSCURED_PASSWORD
```

Use a prefix such as `SHARE/siege-backups`; the first path component is the share
name. Grant create/read access through both share and NTFS permissions. Require
SMB encryption on the server share and test the installed client against that
requirement; do not weaken the server to work around client incompatibility.
Encrypt the backup files with age before upload as well. Password obscuring is
reversible and does not replace private file permissions.

Use a local interactive password prompt, never a password in chat, shell history,
command arguments or Git. On a new host, prove DNS resolves to the actual NAS,
TCP 445 is reachable, login works, and a uniquely named harmless file can be
created and read back byte-for-byte. A successful connection from another Windows
PC does not prove connectivity from the application VM.

Generate the age recovery key on an independently protected recovery machine.
The official Windows binaries can be extracted directly; WinGet is not officially
supported on Windows Server 2019. Preserve a second protected key copy outside the
NAS. Only the public recipient belongs on the application VM.

Seal a verified backup, publish ciphertext and its receipt, and verify both remote
objects by readback. Keep a trusted independent copy of the receipt. Preserve the
private manifest and reports without publishing member data.

## 5. Prove recovery from the NAS copy

Download the NAS object and compare its receipt/hash with trusted evidence.
Decrypt on the recovery machine with the recovered private key. A zero exit code
proves decryption, not a database restore.

To restore on a separate rehearsal VM without moving the private key, transfer the
decrypted archive over an authenticated private transport such as SSH/SCP into a
0700 staging directory; restrict the received file to 0600. Compare its manifest
and both dump hashes with the trusted backup records before restoring. Extract
only the expected flat regular files; reject links, traversal paths and extras.
Alternatively use the copy runner's `recover` phase on a recovery machine that
has the private key, then perform the isolated database phase there.

Follow [isolated recovery](../../deploy/portable/recovery/README.md): fresh UUID
named databases, matching least-privilege owners, PostgreSQL major 16,
`--no-owner --no-acl`, and matching table totals/schema revisions. Run no app or bot
against rehearsal targets. Preserve evidence and stop only the rehearsal container
when complete; do not delete unrelated data or perform broad Docker cleanup.

The test VM used temporary Docker transport adapters for PostgreSQL 16 clients
and a network-isolated restore container. These `/tmp` helpers are experiment
artifacts, not installed production dependencies. For the durable pilot installation, use the
[guarded Docker client transport](../../deploy/portable/recovery/automation/DOCKER-PILOT.md)
and verify it under the actual scheduler account and service restrictions. Do not depend on a captured
container ID or a temporary script surviving a reboot.

## 6. Configure independent Slack alert delivery

Use an external Healthchecks-compatible monitor; do not host the only monitor on
the application VM. Create a separate `Siege pilot backups` check for the test
machine, then a separate production check when preparing the real VM. Proposed
pilot settings are a one-hour period and 40-minute grace. Connect Slack through
the monitor's integration and enable it for that specific check.

Store its secret HTTPS ping URL in a private `heartbeat_file`. Never commit it or
share the URL in chat. The job sends empty success/failure POSTs only; it must not
send database data or secrets. Configure Slack as the intended alert destination,
and disable unwanted email notifications in the monitor settings if appropriate.

Test real delivered failure, recovery and missing-signal notifications on the
approved pilot check. A success request alone does not prove Slack delivery or
that missed backups will alert after the VM disappears. Do not stop Azure or the
application VM to simulate a missing backup; use a dedicated monitor test/check
or an approved bounded pause of only the test backup schedule.

### Bounded overdue-alert rehearsal on the test check

The ping URL can report events; it cannot change the monitor schedule. The
operator must change schedule settings in the portal, or separately provision a
management credential. Do not request broad API access just for this one test.

1. Record the existing test check period/grace and verify no automatic backup
   timer is active. Never shorten the production monitor's schedule for this test.
2. Temporarily set the test check to a one-minute period and one-minute grace.
   Record the setting-change time and the last acknowledged successful backup.
3. Send no success or failure requests during the observation. Confirm a real
   overdue Slack notification after the monitor's deadline. If the previous ping
   is already older than the shortened deadline, the alert may arrive immediately.
4. Restore the intended one-hour period and 40-minute grace, then run a complete
   verified backup to generate a real recovery signal. Confirm Slack recovery.
5. Record user-confirmed overdue delivery separately from explicit `/fail`
   delivery. If no overdue alert arrives, diagnose check activation, schedule and
   Slack routing before enabling the backup timer. Do not report PASS from a
   `/fail` request or a local waiting period alone.

## 7. Enable the complete job only after acceptance

Use [job and scheduler instructions](../../deploy/portable/recovery/automation/README.md)
and the checked-in templates. Proposed pilot policy is hourly backups, a
30-minute job budget, seven days of local successful data and at least the newest
24 successful runs. These are starting settings, not measured production recovery
or data-loss guarantees.

The full job must pass snapshot backup, encryption, ciphertext/receipt upload and
readback, and acknowledged independent monitoring. Test it under the exact intended
service identity. Review systemd paths, permissions, Docker/client access and
restrictions; a service with `NoNewPrivileges=true` cannot rely on escalating
through sudo. Do not grant extra privileges merely to make a template run.

Then install only the reviewed backup service/timer, verify its cadence, and
repeat after a reboot. Check independent backup-age alerts, disk capacity and
source/application health. Keep job overlap prevention enabled.

Local retention is a separate plan/review/apply operation. Preserve failed runs,
reports, recent successful copies and a tested recovery anchor. Do not schedule
unreviewed deletion. Remote retention currently means no automated deletion;
review NAS capacity and a bounded policy before long-term activation. Do not
expire the protected anchor or its receipt blindly.

### Bounded image workload acceptance

On the approved test deployment, issue two concurrent calls to the synthetic
planning siege's `generate-images` endpoint without calling notification/post
endpoints. Capture request duration, generated PNGs, backend cgroup peak memory,
OOM events, board state before/after and bot health. Use the configured internal
service credential privately; never print it or bypass HTTPS certificate checks.
Verify no assignments changed.

For a larger rendering workload without persisting fictional members, read the
standard board and construct a separate in-memory `BoardResponse` with 30 synthetic
names, all four gameplay roles and every enabled position populated. This checks
rendering scale, not valid game assignments or a database-backed roster workflow.
Use the exact pinned backend image in a separate container with `--network none`,
no credentials, no public ports, and the installed backend's memory/CPU/shared
memory bounds. Invoke `generate_assignments_image` and `generate_reserves_image`
with a 30-row `SiegeMemberWithName` list, running two complete image pairs
concurrently. Collect PNGs and cgroup peak/OOM counters before the worker exits.
Inspect the complete images visually; a PNG signature alone cannot prove that the
board or the last member row is visible. Preserve an exited worker only as scoped
evidence, not as another deployed application service.

Document the actual board size and populated-position count. A standard level-one
board is not proof of the largest upgraded board, long names, database concurrency
or a production-sized restore. Test those separately when the real dataset and
traffic profile are available. Do not claim minimum VM sizing or long-term
stability from this bounded workload.

## 8. Keep production cutover separate

Use the [production cutover and rollback procedure](portable-production-cutover.md)
to turn the accepted rehearsal into a separately approved maintenance window.

Retain target preflight, image provenance, OAuth, core-workflow, Discord delivery,
resource observation, recovery, alert delivery and scheduler evidence. Repeat
production-sized recovery and representative load tests on the real target.

Only a separately approved cutover can pause Azure writers/the old bot, take a
final consistent backup, restore/reconcile production data, switch the public
origin and start one producer per intended production identity. Pause every
independent bot, sidecar and scheduled writer on the old host before enabling its
replacement. Cached requests reaching the old origin must remain write-blocked. Reversing DNS after new writes does not
reverse data; prepare a reverse restore/reconciliation procedure. Do not retire
Azure resources because a temporary VM's pilot passed.
