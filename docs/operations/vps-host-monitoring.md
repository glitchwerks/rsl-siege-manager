# Portable VPS host pressure signal

This is a read-only local producer and separately confirmed private receiver
sender for #579. It does not install a schedule, send Slack directly, restart
services, run backups or delete anything. No production host check or receiver
notification is authorized by merging this code.

After reviewing the installed host and separately approving a read-only check,
copy `deploy/portable/monitoring/host-signal.json.example` to a 0600 file in a
0700 directory owned by the approved monitor operator. This check requires read
access to Linux counters and both storage paths, not root or Docker access.
Replace both paths with the exact, canonical
PostgreSQL data-volume source and backup state root. Record the current boot UUID
and `/proc/vmstat` `oom_kill` counter as a reviewed baseline. Choose the memory,
swap and free-space thresholds from representative 4 GiB-host measurements,
backup/encryption workspace needs and observed growth; the example numbers are
placeholders. Keep configuration and reports outside the backup job's `runs`
directory. Do not put credentials or secret URLs in this configuration.

The single phase-scoped command is:

```sh
python3 scripts/portable-vps-host-signal.py \
  --config /protected/monitor/host-signal.json \
  --report /protected/evidence/host-signal-001.json \
  --confirm-readonly-host
```

The report path must be fresh and private. The producer reads Linux memory and
swap counters, the kernel OOM-kill counter, and available bytes on both reviewed
filesystems. It requires the same boot and unchanged OOM count, then compares
the other measurements with the reviewed thresholds. It emits fixed checks and
numeric measurements only; it omits paths, boot UUID, service names and raw
errors. A completed pressure failure is `STOP`. An interrupted, malformed or
timed-out observation is `INCOMPLETE` and must never be treated as a healthy or
completed failure signal. Preserve the report for diagnosis and use a new path
for each run.

A planned reboot changes the boot UUID and resets the OOM counter. Treat that
as a signal to recheck the host and establish a new reviewed baseline, never as
an automatic counter reset. This producer detects a changed OOM count since the
baseline; it does not identify the killed process or replace Docker container
health/OOM checks. A local producer cannot alert when the VPS is down. Connect
it to a separately approved independent missed-signal receiver before relying
on it for production alerting. Use a host-specific private receiver URL in its
own operator-owned 0600 file and 0700 directory, separate from runtime,
database, public and backup checks. The sender takes a fresh observation,
validates its completed protected report, and sends an empty success or `/fail`
POST using the [shared receiver contract](../../deploy/portable/monitoring/signals.md).
It sends nothing for an incomplete observation or failed preflight so the
receiver's missed-signal deadline remains meaningful. After separate approval
for both the read-only host observation and receiver notification, run:

```sh
python3 scripts/portable-vps-host-monitor.py \
  --config /protected/monitor/host-signal.json \
  --endpoint-file /protected/receiver/host/url \
  --observation-report /protected/evidence/host-observation-001.json \
  --report /protected/evidence/host-delivery-001.json \
  --confirm-readonly-host --confirm-notification
```

The two reports must use fresh private paths. The delivery report contains only
fixed booleans, status and time; receiver acknowledgement does not prove Slack
or email delivery. A provisional policy is one observation every five minutes,
an incident on the first completed failed observation, and a 20-minute
missed-signal deadline. The sender posts `/fail` for a completed failure;
receiver grace applies only to missing signals. Review actual durations before
activation. Keep the existing hourly backup completion check
separate: it provides backup-age/failure coverage and must not be reset by a
healthy host signal. Verify both checks' Slack and fallback email delivery with
disposable receiver-side failure, recovery and silence tests before activation.

Rollback after a future approved activation stops only this signal's schedule
and pauses its matching receiver check. Do not stop the backup timer, database,
public monitor or whole stack to test or roll back the host signal.
