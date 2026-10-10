# Portable database recovery runner

`python3 scripts/portable-recovery.py` supplies three separate phases: `backup`,
`verify`, and `restore`. It requires existing PostgreSQL 16 client tools and an
approved connection path. It does not install packages, publish ports, activate
Compose, start applications, send Discord messages, create/drop databases, change
roles, schedule jobs, or upload anything. CI alone uses a disposable database.

This is preparation under #557. No production backup, restore, pilot activation,
write pause, or new host access is approved by merging these files. Independently
approve the source, isolated target, load/storage budget, impact, and recovery
path before running each applicable phase. Keep Azure definitions and inventory.

## Protected connection and evidence files

Use a deployment-operator-owned 0700 directory and 0600 files for libpq service
and password configuration. Copy the shape of `services.conf.example`; exactly
`siege` and `mom` services are required. Database names may match the reviewed
Azure source or portable source. Point each service at the intended endpoint and
role. Populate a separate `.pgpass` file in libpq's `host:port:database:user:password`
format; escape colons/backslashes as libpq requires. Never paste credentials into
commands, logs, reports, Git, or CI artifacts. Do not use shell tracing.

Use `verify-full` for remote TLS connections, with approved trust material.
`disable` is supported for an explicitly reviewed local/private client path only.
Do not weaken Azure TLS or expose the VPS database port for this script. Running
it from a reviewed client location on the private database network is an operator
preparation decision, separate from installation/activation on an existing host.
Azure managed-identity authentication needs separately prepared short-lived
connection/password material; this runner does not acquire Azure tokens.

The runner clears ambient `PG*` connection overrides, uses only the two service
entries, sets connection/query/lock timeouts, and suppresses raw PostgreSQL output.
Reports require a private operator-owned directory and a fresh filename. Evidence
is reserved before any database work; interrupted runs leave `INCOMPLETE`/`STOP`,
which are never acceptance. Existing reports/bundles are never overwritten.

## Backup phase

After explicit source/read-load approval, run one command for the full sequence:

```sh
python3 scripts/portable-recovery.py backup \
  --services /protected/recovery/source-services.conf \
  --passwords /protected/recovery/source.pgpass \
  --bundle /protected/backups/backup-001 \
  --report /protected/evidence/backup-001.json \
  --confirm-backup
```

The backup bundle must not exist; its parent and the evidence directory must
already be private. Both clients and source servers must be PostgreSQL 16. Use
roles with sufficient SELECT/sequence access for a complete application dump,
without granting superuser just to run the script. Check source extensions and
restore privilege requirements before accepting the result.

For each database, a read-only transaction exports a snapshot used by both the
custom-format dump and private control totals. The manifest records archive
SHA-256/size, per-database capture windows, table row counts, and Alembic revisions.
Only fixed check labels, bundle ID, phase, timestamp, duration, and PASS/STOP appear
in the report. The manifest and dumps contain sensitive schema/workload evidence
and must remain private. Backup success proves readable archives, not recovery.

Each database has its own consistent snapshot; the two databases are captured
sequentially. This does **not** provide an atomic cross-application snapshot.
For final cutover, separately approve and verify a write/bot pause across both
apps, hold it throughout both dumps, and reconcile afterward. Routine backup
policy must account for the capture window and application reconciliation.

A failed run retains the private partial bundle and STOP report, without a
completed manifest. Preserve evidence, diagnose privately, and choose a fresh
bundle/report path. Never infer success from one dump being present.

## Offline verification phase

```sh
python3 scripts/portable-recovery.py verify \
  --bundle /protected/backups/backup-001 \
  --report /protected/evidence/verify-001.json
```

No database connection is made. Both archives must be private regular files with
matching manifest hashes/sizes, and PostgreSQL 16 `pg_restore --list` must read them.
Checksums detect accidental corruption; they do not authenticate an untrusted
manifest/archive. Restore only backups from approved trusted source servers.
Do not execute arbitrary third-party archives on an infrastructure host.

## Isolated restore phase

Obtain explicit approval for a dedicated rehearsal server and two **fresh** target
databases before running. Use one UUID for the rehearsal, remove its hyphens in
database names, and configure these exact target names in a new service file:

- `rsl_restore_UUIDHEX_siege`, owned by `siege_app`.
- `rsl_restore_UUIDHEX_mom`, owned by `mom_app`.

Targets must already exist and be empty, deny all PUBLIC database privileges,
and use the matching owner roles with no superuser/createdb/createrole or role
memberships. A reviewed provisioning phase creates them; this runner cannot.
Production database names are rejected. Rehearsal names are never app endpoints.
Do not start a bot or apply migrations as a side effect of restoring data.

```sh
python3 scripts/portable-recovery.py restore \
  --services /protected/recovery/rehearsal-services.conf \
  --passwords /protected/recovery/rehearsal.pgpass \
  --bundle /protected/backups/backup-001 \
  --run-id REVIEWED_REHEARSAL_UUID \
  --report /protected/evidence/restore-001.json \
  --confirm-isolated-restore
```

All archive checks and both target identity/privilege/emptiness checks run before
the first restore. Each archive uses `--no-owner --no-acl --single-transaction
--exit-on-error`, under the application owner role. Azure ownership and grants
are not imported. Compare restored table counts and schema revisions against the
exact source snapshot. CI additionally checks object ownership, sequence recovery,
unchanged sources, and refusal to retry into populated targets.

A failure rolls back that database's restore transaction; a previously completed
restore of the other database is retained. STOP is never pair-wide success.
Preserve reports and both targets for diagnosis; do not retry into a populated
target or drop it automatically. Cleanup/reprovisioning requires a separately
approved operation limited to the two UUID-named rehearsal databases on the
approved rehearsal server. Preserve evidence first, confirm no app is using them,
and do not use `down -v`, broad prune, or any production database cleanup. GitHub
cleans up the entire disposable CI server even if its rehearsal test fails.

## Remaining activation requirements

This runner does not yet deliver automatic/off-host recovery. Before VPS activation:

- Select an independent encrypted backup destination and tested key recovery.
  Keep the local plaintext bundle only in protected storage under a reviewed
  retention policy; encrypt before off-host transfer, verify the transferred
  object, then decrypt/restore from that copy on a fresh isolated target.
- Approve hourly scheduling and bounded backup concurrency/retention. Prove the
  proposed one-hour data-loss window with measured timings and storage costs.
- Add independent backup-age/failure alerts that work while the VM/bot is down.
  A same-host PASS file is insufficient monitoring or off-host backup proof.
- Measure production-sized dump/restore duration and disk/memory peaks, check
  extensions, rehearse compatible-image migrations and core behavior under #430.
- Preserve secret/configuration recovery and immutable images separately; this
  runner intentionally does not dump password-bearing global roles or secrets.
- Approve production data migration/cutover separately. These isolated targets
  and control checks do not authorize overwriting live Azure/VPS databases.

References: [PostgreSQL custom dumps](https://www.postgresql.org/docs/16/app-pgdump.html),
[restore options](https://www.postgresql.org/docs/16/app-pgrestore.html),
[connection services](https://www.postgresql.org/docs/16/libpq-pgservice.html), and
[password files](https://www.postgresql.org/docs/16/libpq-pgpass.html).
