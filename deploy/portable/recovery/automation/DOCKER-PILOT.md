# Durable Docker client transport for the test VM

This optional path runs the existing complete backup job using PostgreSQL 16
clients in the selected local database container. It avoids a PostgreSQL client
installation on the host and does not publish database ports. It is a pilot
installation example; repeat review on the real VM before enabling its timer.
Use merged/reviewed code for production, not an unchecked feature worktree.

## Operator and prerequisites

Requires Linux/systemd, Python 3, Docker at `/usr/bin/docker`, age, and rclone with
SMB support. Existing database objects must be readable by `siege_app`/`mom_app`.
Verify container-local Unix socket login under those roles before installation.
The helper does not create roles, grant privileges, change authentication or
install tools. Do not weaken database authentication if that login is unavailable;
prepare a separately reviewed connection path instead.

The example service runs as root because it accesses the privileged Docker socket.
It uses root-owned code/configuration, `NoNewPrivileges`, a read-only host filesystem
view, protected home directories and writes only under its state root. These
restrictions do not turn Docker access into an unprivileged capability. The helper
pins project/service/image, maps only the two expected database roles and appends
`default_transaction_read_only=on` to database commands. Its `pg_restore` route
supports only version/archive listing, never database restoration. Reassess this
administrative access model for the real host.

## Protected installation layout

- `/opt/rsl-pilot-backup/scripts`: the reviewed `portable-backup-job.py`,
  `portable-backup-copy.py`, `portable-recovery.py`, `portable-postgres-client.py`.
- `/opt/rsl-pilot-backup/bin`: executable launchers named `psql`, `pg_dump`,
  `pg_restore` as shown below.
- `/etc/rsl-pilot-backup`: `job.json`, `transport.json`, `source-services.conf`,
  `source.pgpass`, `upload.conf`, `recipients.txt`, `heartbeat-url`, plus an empty
  `docker` directory for the Docker CLI configuration.
- `/var/lib/rsl-pilot-backup/runs`: job artifacts and reports.

All directories are root-owned 0700; private files are 0600 and launchers 0700.
The two systemd unit files are root-owned 0644. Never point installed code or
credentials at a user-writable checkout or `/tmp`. Copy a stable reviewed code
snapshot and record source revision and installed file hashes. Preserve encrypted
secret/configuration recovery independently; database dumps do not replace it.

Prepare `source-services.conf` using the existing service-file example, with
`host=/var/run/postgresql`, `port=5432`, `sslmode=disable`, service `siege` targeting
`dbname=siege,user=siege_app`, and service `mom` targeting
`dbname=mom_bot,user=mom_app`. These socket paths are inside the selected container,
not host sockets. `source.pgpass` is a protected comment-only file when the existing
socket authentication needs no password. Do not grant host TCP trust access.

Copy the shape of `docker-transport.json.example`; set the exact approved Compose
project and PostgreSQL 16 image digest. The helper discovers the container by
project/service on each invocation, so recreating a container does not leave a
stale captured container ID. It refuses duplicate/mismatched containers.

Each launcher follows this example (substitute `pg_dump` or `pg_restore` for the
last `psql` token in those launchers):

```sh
#!/bin/sh
exec /usr/bin/python3 /opt/rsl-pilot-backup/scripts/portable-postgres-client.py /etc/rsl-pilot-backup/transport.json psql "$@"
```

Prepare `job.json` from the standard example with the above state/config paths,
the reviewed SMB prefix such as `SHARE/siege-backups`, a 1800-second budget,
`keep_days=7` and `keep_successful=24`. The age public recipient is portable; the
private recovery key stays outside this installation. `heartbeat-url` belongs to
a distinct test monitor, with the confirmed hourly period/40-minute grace and
Slack destination. Do not print rendered credentials to a terminal or commit them.

## Service acceptance and activation

Finish database recovery and explicit-failure, recovery and overdue Slack
notification acceptance first. Capture the host's existing timers/services. Copy
`rsl-pilot-backup.service` and `rsl-pilot-backup.timer` to `/etc/systemd/system` only
for the explicitly approved pilot. Review every path, account and write scope.
Then, as an administrator:

```sh
systemd-analyze verify /etc/systemd/system/rsl-pilot-backup.service /etc/systemd/system/rsl-pilot-backup.timer
systemctl daemon-reload
systemctl start rsl-pilot-backup.service
systemctl show rsl-pilot-backup.service -p Result -p ExecMainStatus
```

Inspect the newest private `/var/lib/rsl-pilot-backup/runs/UUID/job.json`. Require
PASS for database backup, encryption, remote ciphertext readback, remote receipt
readback and acknowledged monitoring. This tests the exact scheduler account and
restrictions; a prior interactive run alone is insufficient.

Only after that succeeds:

```sh
systemctl enable --now rsl-pilot-backup.timer
systemctl show rsl-pilot-backup.timer -p ActiveState -p UnitFileState -p NextElapseUSecRealtime
```

The timer runs hourly with up to two minutes of jitter and catches up once after a
missed interval. Confirm the first timer-triggered job and subsequent startup
behavior without restarting unrelated services or the host just for testing.
The regular job lock prevents overlapping runs. An unhealthy running application
is not restarted by this backup service.

## Bounded timer-trigger acceptance

When the next hourly slot is far away, a separately approved pilot test can add a
single temporary timer trigger instead of changing the system clock or stopping
apps. First verify the backup service is idle and preserve its previous report.
Create only `/etc/systemd/system/rsl-pilot-backup.timer.d/90-bounded-acceptance.conf`
with the following content (refuse to overwrite an existing file):

```ini
[Timer]
OnActiveSec=15s
RandomizedDelaySec=0
AccuracySec=1s
```

Reload systemd and restart only `rsl-pilot-backup.timer`. Watch for a new managed
job UUID, a nonempty `LastTriggerUSec` and full job PASS. Use a bounded observation
window; a failed/incomplete report is not acceptance. Always remove exactly this
temporary file in cleanup, reload and restart only the timer, and verify the next
hourly deadline and enabled state. Do not leave the shortened trigger or zero
jitter as the permanent configuration. Preserve the acceptance report outside the
run's data folders. This proves timer-to-service execution; subsequent normal
hourly slots and recovery after a real reboot still need observation.

## Retention, evidence and rollback

The timer runs the job's `run` phase only. It enables no local or remote deletion.
The seven-day/24-success settings govern a later explicitly reviewed retention
plan, not an automatic purge. Preserve reports, failures and a verified recovery
anchor. Budget growing local plaintext and NAS ciphertext until cleanup policy
is accepted. Do not enable unbounded unattended operation on the production host
without capacity monitoring and a reviewed retention policy.

A transfer of a Windows-decrypted NAS archive can prove manual recovery while
keeping the private key off the application VM. The automatic retention runner
additionally requires matching canonical `recover` and `restore` reports for a
copy belonging to a managed successful job. Do not forge those reports or treat a
manual archive hash comparison as satisfying that separate retention gate.

To roll back just the backup schedule:

```sh
systemctl disable --now rsl-pilot-backup.timer
```

If a job is still active, stop only `rsl-pilot-backup.service` after reviewing its
state. Preserve its evidence. Do not remove backups, stop application containers,
restart PostgreSQL, prune volumes or change Azure. An intentionally disabled
schedule will eventually trigger its external overdue alert; adjust the monitor
explicitly rather than sending fake successes.

## One-time post-backup readiness review

For the test VM, an operator can schedule a separate one-time systemd check after
the next normal hourly slot. Keep it separate from the backup timer and job: it
must not send fake success signals, change application state, merge a PR or begin
migration. The check reads the reviewed PR head's public CI check results (without
storing a GitHub credential), requires a new successful managed backup after the
chosen hourly boundary, and checks the five expected pilot services are healthy.
Record PASS, NEEDS_REVIEW or STOP in a private JSON report. Pending CI or a still
running backup must not be treated as migration approval.

The test installation uses `rsl-pilot-readiness.service` and a one-date
`rsl-pilot-readiness.timer`, writing
`/var/lib/rsl-pilot-backup/readiness-after-hourly.json`. Its target PR, expected CI
checks, backup boundary and calendar date are operator-specific and must be
reviewed again on another host. Inspect the service result and report after its
scheduled time, then disable the spent timer while preserving evidence. This is a
VM-side collector; it does not resume an agent conversation or send a chat message.
Review the collected results before proceeding to migration preparation. The
existing external backup monitor remains responsible for real backup alerts.
