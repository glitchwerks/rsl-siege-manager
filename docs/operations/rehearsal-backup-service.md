# Isolated VM backup service templates

These inert unit templates support the rehearsal backup path tracked in #676.
The timer retains the name required by the current forced SSH access check,
`rsl-production-backup.timer`, but runs only on the separate rehearsal VM.
The name does not select production storage or credentials. **Do not copy these
templates to the production VPS.**

The service requires a private rehearsal marker at
`/etc/rsl-backup/rehearsal-only.marker` and runs the reviewed portable backup
job from `/opt/rsl-siege-manager`. That marker prevents an accidental run before
the rehearsal configuration is prepared; an active timer alone does not prove
the backup job succeeds. Keep the timer disabled until every prerequisite is
verified and its activation is separately approved.

Before installation, inspect existing units and paths, preserve the VM snapshot
and console access, and confirm that these exact unit names are unused. Prepare
the distinct NAS share/account, root-private job profile and inputs, separate
age recipient and recoverable key, isolated application databases, and the
independent monitor. The profile's `prefix` must name only the rehearsal share
and disposable object path. The service needs Docker, PostgreSQL client tools,
age, rclone, and the reviewed checkout. Verify the transport with a read-only
remote listing and a denied production-share connection using the rehearsal
account. Do not expose credential or workload content in reports.

Once the root-private `/etc/rsl-backup/job.json` and rclone config exist, run
the reviewed read-only transport inspector as one consolidated check:

```sh
sudo python3 -I -B /opt/rsl-siege-manager/scripts/portable-backup-transport-inspect.py \
  --confirm-readonly --profile /etc/rsl-backup/job.json \
  --expected-prefix rsl_rehearsal/siege-backups \
  --expected-host server-nas --require-remote-read
```

Use the actual reviewed share and MagicDNS name if they differ. Require
`transport_ready: true` and exit status zero. The report does not contain the
SMB password, resolved address, or remote object listing. This check only
lists the remote prefix; it does not create a backup or prove write access.

Install and activate in separate confirmed phases. First copy only the exact
reviewed units and inspect their effective systemd properties. Then run one
manual disposable backup; require encrypted upload, independent remote
readback, receipt readback, and success notification. Verify a restore from
the remote copy in an isolated target. Only after those pass, create the
previously absent `/etc/rsl-backup/rehearsal-only.marker` as a root-owned
regular file with mode `0600` in a separately confirmed service-activation
phase. Stop if that path already exists or has an unexpected type, owner, or
mode. Verify the marker and effective unit before enabling the timer. Then
enable the timer
and verify both the fixed SSH access check and the next scheduled backup.
Test failure and missed-signal delivery to the independent monitor separately.

If any phase fails, disable only the rehearsal timer, remove only the exact
rehearsal marker if this activation created it, and preserve its job reports,
and leave the production NAS share, production VPS, and production credentials
untouched. Remove rehearsal objects and credentials only after reviewing the
exact cleanup target. Restore the VM snapshot for an ambiguous unit or runtime
change; never use a broad service or firewall reset.
