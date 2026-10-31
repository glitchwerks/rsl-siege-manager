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
| Compose configuration | Use `deploy/portable/compose.yml` with exactly one bot overlay. Render and review target-specific values privately. |
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
artifacts, not installed production dependencies. Before unattended use, replace
that arrangement with reviewed stable client/connection setup and verify it under
the actual scheduler account and service restrictions. Do not depend on a captured
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

## 8. Keep production cutover separate

Retain target preflight, image provenance, OAuth, core-workflow, Discord delivery,
resource observation, recovery, alert delivery and scheduler evidence. Repeat
production-sized recovery and representative load tests on the real target.

Only a separately approved cutover can pause Azure writers/the old bot, take a
final consistent backup, restore/reconcile production data, switch the public
origin and start the intended single bot. Reversing DNS after new writes does not
reverse data; prepare a reverse restore/reconciliation procedure. Do not retire
Azure resources because a temporary VM's pilot passed.
