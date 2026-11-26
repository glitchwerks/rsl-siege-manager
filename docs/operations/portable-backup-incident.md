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

## Upload transport diagnosis

The 2026-11-25 inspection found that the backup and encryption phases pass,
but the encrypted upload fails before readback (`publish: tool_failed`,
`encrypted_object_uploaded` absent). Run the reviewed
`scripts/portable-backup-transport-inspect.py` **once** through the existing
root console or admin SSH route. It reads only the installed private profile
and transport config, checks DNS/TCP reachability, and performs a bounded
`rclone lsd` of the configured remote directory. It suppresses the listing and
raw rclone errors and emits only fixed outcomes. It does not upload, download,
start a backup, or change host state.

```bash
set -o pipefail
curl -fsSL --max-time 20 https://raw.githubusercontent.com/glitchwerks/rsl-siege-manager/REVIEWED_SHA/scripts/portable-backup-transport-inspect.py |
  python3 -I -B - --confirm-readonly
```

Replace `REVIEWED_SHA` with the exact reviewed commit. Preserve only the
sanitized JSON output; do not paste the private profile, transport config,
raw rclone stderr, or NAS directory listing into an issue or chat.

`inspection_status=PASS` means the probe ran, even if a transport check failed.
If DNS/TCP fails, inspect the NAS and Tailscale route before changing the backup
job. If remote listing fails, investigate the fixed `failure_class` against
NAS authentication/share state. If listing passes, a read-only check cannot
prove upload permission or NAS free capacity; obtain that evidence on the NAS
before planning a separately approved write or repair. Keep failed backup runs
and the last successful off-host copy intact.

## Rebuilt NAS address repair

The focused 2026-11-25 check found that the VPS can reach the rebuilt NAS at
`100.117.158.121:445`, while the installed backup configuration does not
resolve to that address. This is a live private configuration repair, separate
from the release backup workflow. Do not restart Tailscale or the application.

After reviewing the installed unit and retaining console access, run the
one-time `scripts/portable-backup-nas-repoint.py` from an exact reviewed commit
as root with `--confirm-repoint`. It requires the installed SMB host to be
exactly the former NAS address `100.126.80.92`, an idle backup service, private
root-owned configuration, and reachable TCP 445 on the new address. Any other
host value stops without changing the config. The script preserves the original
secret-bearing config in a root-private sibling file ending in
`.before-nas-repoint`, then atomically replaces only the host line. Its JSON
report contains no host config, credentials, or workload data. Save that report
privately. Do not paste either config file into an issue or chat.

Keep mutation and validation separate. Run the read-only transport inspector
again and require `tcp_reachable=true` and `remote_read=true`. If it fails,
preserve the rollback copy and diagnose the fixed failure class before starting
a backup. If it passes, start `rsl-production-backup.service` once from the
existing root console, away from the hourly timer boundary, and require a new
managed job report with encrypted object and remote receipt readback plus the
independent success ping. Do not use `vps-release-backup.yml`: that workflow is
bound to paused writers and a staged release candidate.

Rollback is the preserved `.before-nas-repoint` file. If the change must be
reversed, first confirm the backup service is idle, preserve the failed
post-change evidence, and atomically restore that file with its original
ownership and mode; rerun the read-only transport inspector afterward. The old
address is known to fail, so rollback is for unexpected impact, not a substitute
for repairing NAS authentication or share access. A failed manual backup does
not authorize retention, release promotion, or deletion of failed evidence.
