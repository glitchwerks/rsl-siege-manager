# Hourly portable backups and independent monitoring

Preparation under #557. These scripts/templates do not install or enable services,
create users, obtain credentials, purchase storage, create monitors, send real
notifications during CI, restore databases, or alter Azure infrastructure. Live
backup execution, catalog publication, monitor signals, scheduler activation and
local cleanup require their respective operator approvals. The supplied defaults
are proposals to measure on the isolated pilot, not an accepted recovery guarantee.

## Job and evidence

`python3 scripts/portable-backup-job.py run --config /protected/job.json --confirm-run`
executes one approved sequence: PostgreSQL snapshot backup, encryption, encrypted
object publication/readback, receipt publication/readback, then independent monitor
success. It never runs migrations, restores data or starts a Discord bot.

Use the shape of `job.json.example`. Keep config/connection/password/recipient/
rclone/heartbeat files private (0600) under private operator-owned directories
(0700); all paths are absolute, nonsymlinked and stable while jobs run. `root` and
`root/runs` must already exist, be private and owned by the job operator. Credentials
must remain outside `root/runs`. Copy the reviewed code to a protected deployment
path separately; this runner does not install PostgreSQL clients, age, rclone or
Python. Use PostgreSQL 16 and the reviewed private source connection path from
[the recovery instructions](../README.md). Approve source read/load and credential
renewal before unattended operation, particularly Azure short-lived authentication.

Only one job/retention phase holds the local lock at a time; an overlapping call
returns STOP without another backup or misleading success signal. Use exactly one
scheduler/operator and one job root per source/destination. The lock does not
coordinate multiple VMs or jobs configured with different roots.

Every run gets an exclusive UUID directory under `root/runs` and a managed
`job.json`. It reserves INCOMPLETE evidence before database work. Individual phase
reports live beside it; the final report contains fixed checks, UUIDs, times and
PASS/STOP, never endpoints, keys, credentials, workload rows or raw command output.
The proposed 30-minute budget covers all subprocess phases; the runner owns and
terminates only each phase's separate process group on timeout/interruption, even
if its parent exited. No broad process/host/service restart is used. A targeted
service stop/host loss may leave INCOMPLETE; that is never acceptance.

Local bundles/tars remain plaintext protected artifacts. Failed/partial runs are
preserved for diagnosis and can consume storage. A failed backup never authorizes
cleanup. Keep disk pressure monitoring and capacity alerts independent of this
job; separately approve precise failed-run cleanup after preserving evidence.

## Independent receipt catalog

The job additionally uploads `<copy-UUID>.receipt.json` beside the encrypted object
and verifies it by remote readback. Receipts contain only UUIDs, time, size and
SHA-256; unlike manifests they contain no schema/table or endpoint information.
Success requires both remote files to pass verification. On failure either may
already exist; there is no automatic remote delete or overwrite.

Protect this catalog with independently recoverable read access and trusted
storage/account controls. Receipt checksums do not authenticate attacker-written
receipts; age public recipients also do not prove sender identity. Rehearse access
to a **trusted** receipt and the recovery identity without any files/accounts on
the lost VM. Independent catalog trust, storage failure domain, key access and
production-sized database restoration remain operator acceptance requirements.
Use the [copy recovery phase](../COPIES.md) and then the isolated database restore
runner to validate a recovered catalog entry; do not restore the original local
copy and call that off-host recovery.

## Independent monitor contract

`heartbeat_file` contains one secret HTTPS success URL for an independently hosted
missed-signal monitor. The monitor must accept an empty POST at that URL for success
and at its `/fail` suffix for failure; it must reply HTTP 200 with exactly `OK`
(whitespace allowed). This is compatible with the documented
[Healthchecks ping interface](https://healthchecks.io/docs/http_api/), including a
separately operated compatible monitor. No provider SDK/account is assumed.

URL credentials/query strings/fragments, HTTP, redirects and ambient proxies are
refused. No report, hostname, dump contents or logs are sent. A nominal 200 response
such as `OK (not found)`/`OK (rate limited)` is refused. Job success requires monitor
acknowledgment. When a phase fails after configuration/lock acceptance, the runner
attempts `/fail` and records whether it was acknowledged. Missing credentials,
startup/config/report/lock errors, SIGKILL, network failure or a dead VM may prevent
any signal: **the external overdue deadline must independently alert anyway**.

Configure hourly expectation with a proposed 40-minute grace to cover the
30-minute job budget and timer jitter; validate the actual monitor semantics and
notification latency before activation. Choose an alert channel/account independent
of the app, bot and VM. Do not run this monitor only on the application VPS or use
Discord bot delivery as its sole notification path. Independently review the URL,
recipients/channel and notification authorization before enabling the service.
Require a real delivered test failure and missing-signal notification on the
approved pilot; a successful mock or local PASS file is insufficient.

## Scheduler templates and activation gate

`rsl-backup.service` and `rsl-backup.timer` are inert examples for an ordinary Linux
host with systemd. The timer is hourly, adds up to two minutes of jitter and catches
up once after a missed interval; it does not replay every missed backup. The service
runs under a dedicated pre-existing operator, private umask, restricted writes to
its state directory, and a 35-minute outer timeout. A service sandbox is not proof
that its private database route/TLS/root certificates or tools are available.
Keep exact deployment paths/account/state/config consistent with the reviewed unit.

Before installation/activation, capture existing host services, scheduled jobs,
permissions, networking/source access, storage budget and remote access dependencies.
Review the target operator, blast radius and recovery path, then run the isolated
job and source checks with independently approved connection/storage/monitor
material. Require the one consolidated job report and independent recovery/alert
evidence. Review rendered unit paths and compatibility on the target systemd
version. CI only checks syntax on its disposable runner; it enables nothing.

Only then separately approve copying/enabling these two specific units. Do not
restart other services, change firewall/routes or install packages as an implicit
activation step. After activation verify timer cadence, the complete job report,
external monitor deadlines, unchanged source/app health, independent catalog/key
access and disk pressure. Proposed hourly scheduling permits a loss window longer
than one hour due to job duration/failure; measure it before accepting an RPO.

Rollback: disable/stop only the approved backup timer/service and preserve its
private state, reports and remote backups. Inspect ownership/use before separately
removing just those units. Do not drop databases, delete off-host copies, prune
volumes or restart the host. Monitor failure/overdue during an intentional stop
is expected; change its schedule only through the separately approved monitor
configuration, never by sending fake successes.

## Local retention: plan, review, apply

Local retention is a separate gated phase; it does not run automatically in the
hourly service. Defaults retain at least seven days, the newest 24 successful runs,
and the run whose remote copy has a successful key recovery **and both database
restores**. Failed/interrupted runs, all job/phase reports and the independent
catalog readback remain preserved. No remote object/receipt is deleted. Increasing
retention is configurable; lower than these proposed floors is refused.

Keep a recovery-phase PASS report and its matching database-restore PASS report
from the approved rehearsal. They must match bundle identity; the recovered copy
UUID identifies the protected anchor. That anchor must belong to a successful
managed job. The newest complete backup must be no older than two hours. Preserve
these proof reports outside the data folders scheduled for cleanup.

One phase produces a private plan with exact UUID folders, fixed relative data
filenames, sizes and hashes (no workload contents):

```sh
python3 scripts/portable-backup-job.py plan --config /protected/job.json \
  --recover-report /protected/evidence/recover.json \
  --restore-report /protected/evidence/restore.json \
  --plan /protected/evidence/retention-plan-001.json \
  --report /protected/evidence/retention-plan-check-001.json
```

Review this concrete plan, its source root, protected anchor, capacity benefit,
impact and recovery path. Only after explicit local deletion approval:

```sh
python3 scripts/portable-backup-job.py apply --config /protected/job.json \
  --recover-report /protected/evidence/recover.json \
  --restore-report /protected/evidence/restore.json \
  --plan /protected/evidence/retention-plan-001.json \
  --report /protected/evidence/retention-apply-001.json --confirm-retention
```

Plans expire after one hour. Application rechecks policy, anchor, fresh backup and
all candidate file hashes before any unlink. Changed plans/files/configuration,
unknown artifacts or symlinks stop the whole preflight. A new run between plan and
apply invalidates the plan; regenerate rather than widening scope. Only known
`source`, `seal` and `publish` data files are removed; corresponding empty folders
are removed without recursive deletion, then a private `pruned.json` marks success.
There is no secure-erasure guarantee. I/O failure/interruption can leave partial
cleanup; preserve reports and use independently reviewed focused diagnosis, not
a broad recursive retry. Never run database cleanup as part of retention.

Off-host retention is deliberately **no deletion** until the destination's own
policy is reviewed. Before activation, budget growing ciphertext/receipt storage
and choose a bounded remote policy with a protected, restore-tested anchor and
minimum recent-copy floor. Do not apply blanket bucket/container/prefix expiry
that could delete that anchor or its receipt. Rotating the protected anchor requires
a successful recovery/restoration of its replacement first. Provider lifecycle
rules/deletion credentials and scheduling of local retention need separate,
concrete reviews; this PR grants neither remote-delete permissions nor activation.

Existing Azure definitions and alert infrastructure remain supported. Completing
this preparation does not close #557: actual independent storage/monitor acceptance,
production sizing, service/scheduler health checks, pilot and cutover remain.
