# Portable database signal contract

This is the read-only signal producer for #585, with an optional sender for the
private missed-signal receiver contract in #579. No monitoring schedule,
receiver configuration, restart or production query is enabled.
Run it on the reviewed VPS only after a separate target and read-only
activation approval. The independent receiver in #579 must deliver down,
recovery and missed-signal alerts even when this host is unavailable.

## Inputs and bounded checks

Copy `deploy/portable/monitoring/database-signal.json.example` to a root-owned
0600 file in a root-owned 0700 directory. Set the exact Compose project,
immutable PostgreSQL image, current container ID, Docker start time and
restart count. Use the reviewed PostgreSQL data-volume source path. Set each
database's exact approved Alembic revision from the installed release. The
size, free-space and connection thresholds in the example are placeholders:
choose values after measuring the installed 4 GiB host and its data growth.
Do not copy or log database passwords into this configuration.

The producer requires one exact project-scoped PostgreSQL container that is
running and Docker-healthy, has not restarted or been OOM-killed, and still
uses the approved immutable image and data mount. It checks free space on the
verified data filesystem. It then makes one bounded, read-only SQL query over
TCP per database, authenticated as `siege_app` and `mom_app` using the existing
passwords already inside the PostgreSQL container. A socket listener or
`pg_isready` alone does not pass. Queries verify the database and role,
single expected Alembic revision, and presence of the `member` or `reminders`
bootstrap table. They read database size and a global count of backends
connected to databases from `pg_stat_database.numbackends`; this conservative
pressure count can include a database-connected maintenance worker, but it
cannot miss sessions owned by the other application role. They do not read
roster, message or reminder rows. The SQL transaction is
read-only with a three-second statement timeout. The script rechecks the
PostgreSQL container after both queries. Every Docker command has a bounded
timeout, with a 60-second observation budget.

After separate authorization, invoke from a reviewed checkout on the host:

```sh
python3 scripts/portable-vps-database-signal.py \
  --config /protected/database-signal.json \
  --report /protected/evidence/database-signal-001.json \
  --confirm-readonly-databases
```

The report path must be fresh and private. An interrupted run retains an
`INCOMPLETE` marker; final evidence is published atomically. It has fixed
boolean check labels, a UTC timestamp, and numeric database-size,
available-space and connection measurements. It excludes passwords, database
URLs, host paths, container IDs, revisions, table data and raw errors. Preserve
STOP/INCOMPLETE evidence and use a new path for another attempt.
An exhausted overall observation deadline or an internal probe exception is
`INCOMPLETE`, distinct from a completed failed database check.

## Optional independent receiver delivery

After separate approval for the read-only target and receiver notification,
put the receiver's private success URL in a root-owned 0600 file under a
root-owned 0700 directory, separate from the database configuration and
evidence. The URL uses the same [empty POST/`OK` contract](../../deploy/portable/monitoring/signals.md)
as the runtime sender. Do not place a Slack credential on the VPS. Use a
database-specific receiver check; runtime and backup signals must not reset
its missed-signal deadline.

One phase-scoped invocation from a reviewed host checkout is:

```sh
python3 scripts/portable-vps-database-monitor.py \
  --config /protected/database/database-signal.json \
  --endpoint-file /protected/receiver/database-url \
  --observation-report /protected/evidence/database-observation-001.json \
  --report /protected/evidence/database-delivery-001.json \
  --confirm-readonly-databases --confirm-notification
```

The root-only sender runs a fresh producer and checks its private report before
it sends an empty success or `/fail` signal. Invalid inputs, overlap, exhausted
observation deadline, interrupted/mismatched evidence or delivery failure never
become a monitoring PASS. An incomplete observation sends nothing; the external
receiver must detect silence. The atomic delivery report contains only a UTC
timestamp, status and booleans for observation and receiver acknowledgement.
It omits the URL, credentials, host paths, database measurements and raw errors.
Receiver acknowledgement proves receipt, not Slack/email delivery. Keep both
reports and use fresh paths for another attempt.

## Initial policy and acceptance

Request a signal every five minutes. The independent receiver should open an
incident after two consecutive failed signals, resolve after a successful
signal, and alert when no signal arrives for 20 minutes. After a separately
approved PostgreSQL restart, apply a five-minute startup grace at the receiver
without treating an unhealthy database as PASS. Record the incident owner and
correlate this signal with the public API check in #584; an API outage alone
does not establish a database outage. Configure the eventual receiver to send
Slack as primary and email as fallback.

Before activation, choose the free-space floor and each maximum database size
from representative measured usage plus an explicit growth budget. The
connection threshold should leave room for administration and recovery; 80%
is an initial ceiling, not a measured production value. Compare successive
numeric reports to identify sustained growth, and revise thresholds only from
reviewed measurements. Exercise pass, wrong password, missing schema, capacity
pressure, recovery and missed-signal cases against disposable infrastructure.
Do not stop or mutate production PostgreSQL to force an alert.

On false positives, disable only this signal at the receiver and inspect the
private report and approved baseline. Never auto-restart PostgreSQL or the
whole stack from this check. Production activation and receiver integration
remain separate gates under #579. Reverting this repository change removes
the producer without changing the running host.
