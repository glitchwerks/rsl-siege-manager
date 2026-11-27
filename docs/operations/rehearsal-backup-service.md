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
and console access, and confirm that both `rsl-production-backup.service` and
`rsl-production-backup.timer` are unused. **Stop before copying either unit if
either name is already present or its ownership is uncertain.** Record that
both names were absent; the recovery steps below apply only to units installed
by this rehearsal. Prepare the distinct NAS share/account, root-private job
profile and inputs, separate
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
regular file with mode `0600` in a separately confirmed marker phase. Stop if
that path already exists or has an unexpected type, owner, or mode. Verify the
marker and effective unit. In a later confirmed activation phase, enable the
timer and verify both the fixed SSH access check and the next scheduled backup.
Test failure and missed-signal delivery to the independent monitor separately.

If any phase fails after this rehearsal enabled the previously absent timer,
stop and disable only that timer with
`sudo systemctl disable --now rsl-production-backup.timer`, then verify that
it is inactive and no backup job is running. If the timer was never enabled by
this rehearsal, do not disable a unit whose ownership is uncertain. If a job
is still active, stop the recovery phase and inspect it before any targeted
interruption. Remove
only the exact rehearsal marker if this activation created it. Before any VM
snapshot restore, read the `root` field from the reviewed private job profile
and copy the failed run's sanitized job and phase reports from `<root>/runs`
to protected operator storage outside the VM; verify the copy completed. If
the failure happened before a run report was created, preserve the relevant
service journal in protected operator storage outside the VM, review and redact
it before sharing any evidence, and verify that copy before restoring. A
missing profile or uncertain report root is a stop condition for recovery,
not a reason to assume the default path.
Do not copy credentials, workload content, or raw backup objects into evidence.
Leave the production NAS share, production VPS, and production credentials
untouched. Remove rehearsal objects and credentials only after reviewing the
exact cleanup target. Restore the VM snapshot for an ambiguous unit or runtime
change only after report preservation; never use a broad service or firewall
reset.
