# Production backup incident inspection

This is the read-only diagnosis for #655. The installed hourly job has sent
explicit failure pings since 2026-11-15. Do not restart it, run it manually,
delete failed runs, change credentials, or activate retention during diagnosis.

The reviewed `scripts/portable-backup-incident-inspect.py` uses the verified
installed root-run service profile at
`/srv/rsl-siege/backup-candidate/job.json`. It validates the private state
root named in that profile and reads only its private
`job.json` and phase reports,
systemd timer/service properties, and local free space. It does not read
password, recipient, remote transport, heartbeat, database, or workload files.
It makes no network request and writes nothing on the VPS. The single JSON
output contains fixed check labels, UTC times, safe error codes, unit state,
and capacity totals; it excludes paths, UUIDs, endpoints, credentials, and raw
logs. `inspection_status=PASS` means the inspection succeeded, **not** that
backups are healthy. Preserve the output privately and share only the
sanitized JSON with the incident owner.

From the existing Windows admin PC's Tailscale SSH route, use the exact
reviewed commit of this repository and stream the script to Python on the VPS.
For example, in PowerShell from a local clone (replace `REVIEWED_SHA` with the
merged commit):

```powershell
git show REVIEWED_SHA:scripts/portable-backup-incident-inspect.py |
  ssh -T root@100.117.110.57 python3 -I -B - --confirm-readonly
```

The command must use the existing admin SSH identity and pinned known host.
It does not work with the restricted GitHub deployment key, which intentionally
denies arbitrary commands. If the SSH identity or host key differs from the
reviewed route, stop rather than disabling host-key checking. If the report
stops at `inspection_status=STOP`, diagnose only its fixed failed check before
considering any further access. Do not paste raw journal output or private job
files into GitHub or chat.

After identifying the failed phase, compare the latest and first failed run
with the last successful local and off-host receipt. A successful historical
`remote_receipt_readback` check does not prove the NAS copy is retrievable now.
Plan any repair separately with its dependency, blast radius, rollback, and
post-repair backup/receiver verification. Preserve the failed run evidence.
