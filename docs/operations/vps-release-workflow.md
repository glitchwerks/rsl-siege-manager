# Guarded VPS release workflow

Production remains on its currently approved image digests. A successful
connection check grants no permission to replace them. The release path has
separate candidate, access, staging, backup, migration, promotion, acceptance,
and recovery phases. Candidate and access checks are automated by
`.github/workflows/vps-release-readiness.yml`. A separate
`.github/workflows/vps-release-stage.yml` can store the same reviewed candidate
as private host evidence after another explicit approval. Backup, migration,
promotion, acceptance and recovery remain unavailable to the GitHub deploy key
until their reviewed, phase-scoped host implementation and isolated rehearsal.

## Candidate and read-only access

Create a data-only release JSON file under `releases/` in `rsl-mom-apps`, using
the [combined manifest contract](https://github.com/glitchwerks/rsl-mom-apps/blob/main/docs/vps-release-manifest.md).
Review and merge that file. Record its full commit SHA, repository-relative path,
and SHA-256 file digest. The manifest names exact Siege and Mom source commits,
successful push CI runs, and immutable GHCR image digests. The Siege reference
bot can be recorded for recovery but cannot become the production gateway.

Manually dispatch **VPS release readiness** from Siege `main` with those three
inputs. Its first job checks the selector, requires the candidate commit to be
reachable from coordination `main`, verifies the exact file digest, and runs the
reviewed validator pinned to coordination commit
`fb8c0d795e1b06c93058f1cce2ab612491ce4cec` (coordination PR #32). It
verifies the two CI runs, three SHA-tagged GHCR image digests, clean-runner
pulls of every candidate image, the API/Mom image Alembic heads, and the exact
Mom image's gateway and scheduler health behavior under isolated simulated
responses. The candidate checkout is data only:
the workflow never executes its code. The pinned validator can be updated only
through a reviewed Siege workflow change.

Because coordination and Mom repositories are private, the candidate job needs
two selected organization Actions secrets:

- `VPS_RELEASE_REPO_READ_TOKEN`: a fine-grained token with Contents read on
  `rsl-mom-apps` and Actions read on the Siege and Mom repositories. It has no
  write permissions. A GitHub App installation token can replace this if it is
  minted per run with the same scope.
- `VPS_RELEASE_PACKAGE_READ_TOKEN`: a separate classic token with only
  `read:packages` for GHCR digest inspection. Do not add the broad `repo` scope.

Set the organization Actions variable `VPS_RELEASE_READ_USER` to the package
token owner's GitHub login. Select only Siege Manager as a consumer for these
secrets and the variable. GitHub Packages currently requires a classic token
for this registry authentication; a fine-grained repository token alone does
not cover it. This avoids granting the public Siege repository direct Actions
access to Mom's private package. The candidate job never receives the VPS
SSH/Tailscale secrets; the `vps-prod` access job never receives these two
cross-repository credentials. See [GitHub's package authentication guidance](https://docs.github.com/en/packages/learn-github-packages/about-permissions-for-github-packages)
and [cross-repository workflow token guidance](https://docs.github.com/en/actions/concepts/security/compromised-runners).

After candidate checks pass, GitHub requests `vps-prod` environment approval.
The approved job joins Tailscale and runs the same pinned-host, read-only forced
SSH command used by **VPS access check**. Both workflows share the
`vps-production-promotion` concurrency group. Download and preserve the two
sanitized reports; a STOP ends this attempt. This workflow does not stage a
release, run a backup or migration, pull an image on the VPS, or restart a
service.

## Bounded candidate staging

After the host command upgrade below passes local `verify`, manually dispatch
**VPS release stage** from Siege `main` with the same three candidate inputs.
This run repeats CI/digest provenance verification, then requests a separate
`vps-prod` approval. The candidate job passes only the verified manifest bytes
to the protected job; it does not pass the cross-repository credentials. The
protected job rechecks the digest, performs the pinned-host read-only baseline,
and sends `stage-release <digest> <run-id>-<attempt> confirm-stage` with at most
16 KiB of candidate JSON through the restricted SSH key. No arbitrary shell
command is accepted.

The host rechecks the digest and schema-v2 manifest shape, including explicit
per-database migration decisions, then creates exactly one
root-private `/srv/rsl-siege/evidence/release-stages/<run-id>-<attempt>/`
directory containing `candidate.json` and a boolean-only `report.json`. An
existing run directory, changed digest, duplicate JSON key, malformed manifest,
or unsafe path is a STOP. The runner preserves a separate sanitized stage report.
Staging does not alter `stack.env`, pull images, run preflight against runtime
configuration, execute backup/migration, or touch Compose services. Keep the
candidate and reports for the later preflight/promotion phases.

The host-side forced-command script must be installed separately from a reviewed
commit. Its current `update-script` phase accepts only the eight reviewed prior
script hashes, preserves a root-private copy at
`/root/portable-vps-remote.py.before-release-migrate`,
and atomically changes only that script with its committed `0644` mode. An
earlier stage-only installation may also have left the separate reviewed
`scripts/portable-vps-remote.py.before-release-stage` backup in the checkout;
a prior preflight installation may have left the preserved
`/root/portable-vps-remote.py.before-release-preflight` backup.
Keep an admin SSH session and provider
console recovery available. From the reviewed checkout on the VPS, run the
phase-scoped installer and verification with fresh report paths:

```sh
python3 scripts/portable-vps-host-access.py update-script \
  --public-key deploy/portable/host/github-vps-deploy.pub \
  --confirm update-script --report /root/vps-stage-script-update.json
python3 scripts/portable-vps-host-access.py verify \
  --public-key deploy/portable/host/github-vps-deploy.pub \
  --report /root/vps-stage-script-verify.json
```

Require both reports to pass and recheck a fresh admin login and the existing
read-only GitHub access check before any staging dispatch. If the new script
must be removed, use `restore-script --confirm restore-script` with the same
public key and a new report path; it restores only the preserved script. The
stage command is a state-changing validation and needs its own approval after
the host update.

`restore-script` deliberately retains its backup. A later `update-script`
stops while that backup path is occupied. To retry an upgrade after a restore,
review a new installer revision with a distinct root-private backup path; keep
the earlier recovery copy rather than deleting or renaming it in place.

## Read-only candidate configuration preflight

`scripts/portable-vps-release-preflight.py` accepts an existing staged run ID
and manifest SHA-256. It requires a clean, reviewed Siege checkout at the
candidate's configuration source commit. The protected deployed Compose files
must exactly match that checkout; the Caddy and initialization SQL bind sources
must pass the reviewed portable preflight, and runtime credentials and Mom image
approval must remain private. It renders the currently installed configuration
and requires each running backend, frontend, Mom, PostgreSQL, and proxy container
to match the installed image reference. No bundled bot or migration service may
be running.

The command then renders the candidate's three application image digests in
memory against the same protected runtime environment and runs all portable
configuration checks, including exact runtime and maintenance-service image
matching. It does not edit `stack.env`, runtime files, or Compose services. A
root-private baseline stores the staged manifest digest, reviewed source commit,
installed image references/container IDs, Mom approval source, and hashes of the
installed configuration, `stack.env`, and private runtime inputs. Runtime files
and bind files are hashed before rendering and rechecked afterward; private
contents never enter the baseline or report. The separate report contains
booleans only. Any source, bind file, image, health, or configuration drift is
STOP. The writer pause plan binds the exact accepted baseline digest and
rechecks it before each state-changing boundary; this command alone does not
authorize a pause or deployment.

No live VPS preflight has run. Candidate
source changes to the Compose or bind files require a separately reviewed
configuration staging step before this gate can pass; the current installed
files are never silently replaced.

The manual **VPS release preflight** workflow now provides the read-only
dispatch path. It accepts the exact manifest SHA-256 and the successful stage
run ID, uses the protected `vps-prod` environment and the same production
promotion concurrency group, and sends only
`preflight-release <digest> <run-id> confirm-readonly-preflight` through the
restricted SSH key. The host rechecks the private stage candidate and its
successful report, then requires a clean root-owned checkout at the candidate's
configuration source SHA before executing the fixed preflight script. The one
previous installer backup is permitted in that checkout only if root-private
and equal to its reviewed hash; any other untracked file is a STOP. It keeps
the preflight baseline under the private stage, puts the script's separate
report under `/root/vps-release-reports/`, and returns only boolean checks to
GitHub. It does not change images or services.

Before dispatch, separately review and install the new forced-command script
from the exact reviewed checkout with `portable-vps-host-access.py
update-script --confirm update-script`, a fresh root-private report, and the
dedicated public key. The current updater accepts only the eight reviewed
earlier script hashes, backs up the current script to
`/root/portable-vps-remote.py.before-release-migrate`,
and requires the new reviewed hash. Verify the installation with a second
fresh report, a new admin login, and the existing read-only GitHub access
check. `restore-script --confirm restore-script` restores only that exact
preserved script if the update must be reversed. Keep provider-console access
available through this host update. A clean candidate checkout and protected
Compose/bind files are separate preflight prerequisites; the workflow does not
check out source or copy configuration onto the VPS. No host update or workflow
dispatch has been performed for this preflight bridge.

## Read-only writer pause plan

The manual **VPS release pause plan** workflow accepts the same exact staged
manifest SHA-256 and run ID, uses the protected `vps-prod` environment and
production promotion concurrency group, and sends only
`plan-release <digest> <run-id> confirm-readonly-plan` through the restricted
SSH key. The host checks the staged candidate digest, reviewed clean checkout,
and a root-private `/srv/rsl-siege/runtime/release-backup-profile.json` before
invoking the reviewed `portable-vps-release-pause-backup.py plan` phase. The
profile binds the project, backup owner/root, installed backup unit and timer,
and SHA-256 digests of the unit file and effective loaded unit. Create and
review this profile separately from measured host state; the workflow does not
guess or write those values.

The plan script requires the accepted preflight baseline to remain current,
healthy backend/Mom/PostgreSQL and proxy containers, no extra writers, an active
backup timer, and an idle backup service. It records a root-private plan bound
to the candidate and baseline. GitHub receives only boolean checks and the
SHA-256 of that private plan for a later separately confirmed pause phase. It
does not stop containers, start a backup, or enable deletion. The updated host
script must be installed and verified through the separate hash-bound
`update-script` phase before a plan dispatch. No plan dispatch or VPS host
update has been performed for this bridge.

## Confirmed writer pause workflow

The manual **VPS release writer pause** workflow requires the exact staged
candidate SHA-256, stage run ID, accepted pause-plan SHA-256, and the literal
`pause-reviewed-writers` confirmation. It runs only from `main` under the
protected `vps-prod` environment and production-promotion concurrency group.
The fixed restricted SSH command is
`pause-release <digest> <run-id> <plan-sha256> confirm-pause`. The host checks
the private candidate and accepted plan, clean reviewed checkout, root-private
backup profile, and active read-only baseline before invoking only the reviewed
`pause` phase. The pause script independently checks the current baseline and
plan before stopping Mom and backend. An exclusive private stage marker
reserves the attempt so a concurrent request cannot replace its final report.
GitHub receives a boolean-only report;
private phase reports remain on the host.

This operation stops production writers. Dispatch only as part of an approved
full deployment after the backup and recovery sequence is ready. Keep an admin
SSH session and provider-console access available. If the workflow stops or
times out after either writer has stopped, treat the phase as INCOMPLETE; inspect
`/root/vps-release-reports/<run-id>-pause.json` and the private stage reports
before using the reviewed recovery path. Do not retry the same stage or start
new writers until the old writer state and database backup status are known.
The host forced-command script must be installed and verified with the
hash-bound `update-script`/`restore-script` phases before dispatch. No host
update or writer-pause dispatch has been performed for this workflow.

## Confirmed managed backup workflow

The manual **VPS release managed backup** workflow is a separate protected
phase after an accepted writer pause. It requires the staged candidate digest,
stage run ID, the exact accepted pause-plan digest, and the literal
`backup-reviewed-writers` confirmation. It runs only from `main` in the
protected `vps-prod` environment under the production-promotion concurrency
group. The restricted SSH command is
`backup-release <digest> <run-id> <plan-sha256> confirm-backup`.

The host checks the private candidate, accepted plan and pause reports,
reviewed checkout, root-private backup profile, and an available final report
path before reserving an exclusive private stage attempt and invoking the
reviewed `backup` phase. The phase independently
rechecks paused writers, the current preflight baseline, backup unit digest,
and service idle state; starts the existing managed backup service once; and
requires one new run with ordering and post-backup checks. Only boolean checks
return to GitHub. The run selector and full private evidence remain on the
host. A failed or timed-out backup is a STOP, and the old writers remain
paused. Inspect `/root/vps-release-reports/<run-id>-backup.json` and the
private stage reports before recovery; do not retry the same stage or resume
writers until backup state is known. The workflow must only be dispatched as
part of an approved full release. No host update or backup dispatch has been
performed for this bridge.

## Read-only recovery proof workflow

The manual **VPS release recovery proof** workflow binds the exact selected
managed backup to off-host recovery and isolated restore evidence. It accepts
the staged candidate digest, stage run ID, accepted pause-plan digest, and two
absolute private report paths from the operator. The path inputs allow only
letters, digits, `/`, `.`, `_`, and `-`, reject `..`, and must be distinct.
This is an evidence selector, not a credential; do not put secret values or
workload content in these inputs. The workflow runs only from `main` under the
protected `vps-prod` environment and production-promotion concurrency group.
The restricted SSH command is
`proof-release <digest> <run-id> <plan-sha256> <recover-report> <restore-report> confirm-readonly-proof`.

The host checks the private staged candidate, accepted plan, pause, and backup
reports, reviewed checkout and script, root-private backup profile, and an
exclusive stage attempt reservation. The reviewed proof script then verifies
the selected backup's same-copy off-host readback and isolated database restore,
rechecks paused writers and evidence hashes, and records a private proof
baseline for migration readiness. GitHub receives boolean checks only. This
workflow does not run recovery, restore, migration, or promotion; those reports
must be produced and reviewed separately. Inspect the private host report on
STOP and use a fresh stage for a corrected proof. No VPS host update or proof
dispatch has been performed for this bridge.

## Read-only migration readiness workflow

The manual **VPS release migration readiness** workflow is a separate gate
after same-copy recovery proof. It accepts the staged candidate digest, run ID,
and accepted pause-plan digest; runs only from `main` under the protected
`vps-prod` environment and production-promotion concurrency group; and sends
`ready-release <digest> <run-id> <plan-sha256> confirm-readonly-ready` through
the restricted SSH key. The host checks the candidate, plan, accepted recovery
proof and its private baseline, reviewed checkout, and root-private backup
profile before reserving one private stage attempt and invoking the reviewed
`portable-vps-release-migration-ready.py` script.

That script rechecks the selected backup proof and paused writers, queries only
the two database schema revision identifiers, compares them to the manifest's
exact `from_revision` values, and records a private migration-readiness
baseline. GitHub receives only boolean checks. A mismatch is STOP; a corrected
candidate requires a new stage. A successful dispatch also returns the
SHA-256 of the private readiness baseline for the separately confirmed
migration phase. This workflow does not run a migration or
restart writers. No VPS host update or readiness dispatch has been performed
for this bridge.

## Separately confirmed database migration workflow

The manual **VPS release confirmed migration** workflow runs one database
migration only after the read-only readiness baseline is accepted. It requires
the exact candidate, plan, and readiness SHA-256 values, a `siege` or `mom`
database selection, and a matching literal confirmation
(`migrate-reviewed-siege` or `migrate-reviewed-mom`). It runs only from `main`
under the protected `vps-prod` environment and production-promotion
concurrency group. The restricted command is
`migrate-release <digest> <run-id> <plan-sha256> <readiness-sha256> <database> confirm-migrate-<database>`.

The host checks the private candidate and accepted readiness report, binds
both baseline hashes, reviews the clean checkout and script, and reserves an
exclusive private attempt for the selected database before invoking only the
reviewed `portable-vps-release-migrate.py` script. That script rechecks the
same-copy recovery proof, stopped writers, exact live revisions, migration
order, candidate image, and migration head before running its one-off Compose
maintenance service. It verifies the target revision afterward. Each database
requires its own dispatch in the manifest order. GitHub receives only boolean
checks; private evidence stays on the host. STOP leaves writers paused for
manual diagnosis and recovery. Never blindly retry a timed-out or failed
migration; database writes may already have occurred. No migration workflow
has been dispatched and no VPS host script has been updated for this bridge.

## Promotion phases to implement and rehearse

The read-only `scripts/portable-vps-release-promotion-ready.py` gate now checks
the exact accepted migration chain and final revisions in both databases. It
requires the same accepted recovery proof, stopped writers, unchanged installed
configuration and container identities, and cached candidate image digests.
For a database with `mode: none`, any migration attempt is a STOP. It records a
root-private baseline and a separate sanitized pass/fail report. A passing
report does not switch images or start writers; promotion still needs its own
confirmed implementation and isolated rehearsal. This script is reviewed code
only and is not installed on the VPS or callable by the GitHub deploy key.

The separately confirmed `scripts/portable-vps-release-promote.py` phase consumes
that accepted baseline by exact digest and re-runs its checks immediately before
writing. It preserves the original protected `stack.env` and Mom image approval
bytes in the stage directory, replaces only the three application image
assignments and the Mom approval, and verifies the rendered candidate. Before
reading the stage, it takes a project-wide nonblocking exclusive lock and holds
it through both private and external reporting, so another promotion cannot
race a partially completed activation. The lock file remains in the protected
project directory between attempts. Before activation it rechecks paused
writers, database revisions, and the existing
PostgreSQL/proxy container identities. It then runs `docker compose up` for
`backend`, `mom`, and `frontend` only, with `--no-deps`, `--no-build`, and
`--pull never`. It requires one healthy container for each candidate service,
unchanged PostgreSQL/proxy identities, no bundled bot or maintenance writer,
and unchanged database revisions. A failed pre-activation check restores the
two original files. Once activation has started, it leaves the files and stage
evidence in place for separately confirmed recovery; new writes may already
have occurred, so it never attempts automatic image or database rollback.

The promotion script is reviewed code only. It is not installed on the VPS or
exposed by the forced SSH command. It has not switched production images. Its
operator invocation and rollback rehearsal must be reviewed alongside the
production acceptance and recovery phases before any live deployment.

The read-only `scripts/portable-vps-release-accept.py` phase requires an accepted
promotion report and the exact preserved old configuration. An operator-owned
private pilot configuration must name the staged candidate's API, frontend, and
Mom image digests, the original PostgreSQL/proxy digests, the exact source SHAs
in version metadata, and the approved public HTTPS origin. The script holds the
project promotion lock, checks the current container identities/configuration
and both database revisions, runs the bounded portable observer, verifies the
frontend marker, tests anonymous rejection and a fresh authenticated session at
`/api/auth/me`, and probes Mom's private sidecar authentication from the backend
container. The sidecar probe uses the backend container's existing key in memory
and returns only booleans and a version; it never emits the member roster. The
operator provides the **session cookie value only** in a separate root-owned
0600 file outside Git and evidence. Do not place it on a command line or in a
report. Use a short-lived test account session and delete that file after
acceptance. The command requires `--confirm-readonly-acceptance` for the
reviewed target. An unsuccessful acceptance is STOP; it never restarts services,
changes images, sends a Discord message, or attempts recovery.

The private acceptance baseline records the exact installed images, source
SHAs, versions, container IDs, and configuration hashes. The separate report
contains fixed pass/fail checks only. This phase is reviewed repository code,
not installed on the VPS or exposed to the GitHub deploy key. It has not run
against production. It does not establish independent monitor notification
delivery or a full Discord write acceptance. Those remain separate release
gates, with production Discord writes requiring their own authorization.

The confirmed pause records each writer container's Docker `FinishedAt` time
in root-private stage evidence. Migration, promotion, acceptance, and recovery
share the same project lock; migration holds it through its final reports.

The separately confirmed `scripts/portable-vps-release-recover.py` has an
`inspect` phase and a `restore-preactivation` phase. Both require the exact
staged candidate, pause plan, accepted promotion readiness baseline, and
original protected configuration preserved by the promotion attempt. The
inspection is read-only. It stops if any stage for the project started a
migration or Compose activation, if a writer's stop time differs from the
accepted pause, if database revisions changed, or if the original
frontend and PostgreSQL/proxy foundation no longer match the preflight. A
passing inspection is evidence only; the restore requires a separate operator
invocation with `--confirm-restore <stage-run-id>` and a fresh private report.
The restore rechecks the boundary, then atomically restores only `stack.env`
and `runtime/mom-image.json` from the preserved bytes. It verifies the original
preflight baseline afterward and leaves backend and Mom stopped. It does not
restart writers, roll back a database, or change images. A partial restore or
any post-activation failure records STOP for manual reconciliation. Stage and
external reports contain booleans only, and a project lock prevents a parallel
promotion. This script is repository code only; it is not installed on the VPS
or exposed through the GitHub deploy key.

1. **Preflight integration:** expose the reviewed read-only candidate gate
   through a phase-scoped host command, bind later phases to its baseline, and
   rehearse the complete configuration path without activating services.
2. **Backup:** run the already reviewed production backup job as a distinct
   confirmed phase. Require a fresh complete local and off-host readback report
   and the independent restore evidence before migration or promotion. Timer
   activity alone does not satisfy this gate.
3. **Migrate:** only if the candidate explicitly names a reviewed compatible
   migration, run the exact Siege and Mom maintenance services after backup
   acceptance. Do not infer migration permission from a new image digest.
4. **Promote:** atomically preserve the old protected `stack.env` and Mom image
   approval record, switch only the approved image digests, then run targeted
   Compose activation with one production Mom gateway. Never start the bundled
   bot on the production identity.
5. **Accept:** verify public HTTPS health/version/auth, backend and frontend
   source identities, Mom gateway connection and completed scheduler work,
   sidecar authentication, backup/monitor status, and unaffected host services.
   Record exact installed digests/source/configuration and sanitized evidence.
   Production Discord write tests remain independently authorized.
6. **Recover:** before new writes or migration, a separately confirmed image
   and configuration restore can use the preserved baseline. After a migration
   or new writes, stop and use reviewed database reconciliation and recovery;
   never automatically roll back the database or reenable Azure deployment.

The preflight, backup, migration, promote, accept, and recover operations need
separate phase-scoped host commands, explicit confirmations, bounded input,
sanitized private reports, and narrow rollback behavior. Review their host
dependencies and remote-access impact before installing them. Rehearse with an
isolated Discord identity and target before requesting a distinct production
release approval. Completion is tracked by [Siege #583](https://github.com/glitchwerks/rsl-siege-manager/issues/583),
[Mom #387](https://github.com/glitchwerks/rsl-mom-bot/issues/387), and
[coordination #25](https://github.com/glitchwerks/rsl-mom-apps/issues/25).

## Live migration revision baseline

Before approving any database migration, compare the exact staged manifest to
the revisions in both running databases. The read-only
`scripts/portable-vps-migration-baseline.py` command requires a root-private
manifest, its SHA-256 digest from the approved stage, and the protected
`/srv/rsl-siege` project directory. It invokes `alembic current` inside the
existing `backend` and `mom` containers through the local Docker socket. Both
revisions must exactly match the manifest's `from_revision` values, including
when that database has `mode: none`. Any missing, multiple, unexpected, or
unreadable revision stops the gate. The report is a new root-private JSON file
containing booleans only.

This baseline does not pause writers, establish a fresh backup, execute
migrations, pull images, or change the running stack. A passing report is
evidence for a later deployment phase, not authorization to promote. Before a
schema change, separately verify a fresh successful encrypted off-host backup
and restore proof from the existing backup workflow after writers are paused.
Keep the backup and migration phases independently gated; a mismatch or failed
migration leaves writers stopped for manual recovery.

## Backup and restore evidence gate

The read-only `scripts/portable-vps-backup-evidence.py` command checks three
private reports from the existing backup system: the managed job, off-host
recovery, and isolated restore. Run it as the account that owns the evidence
(normally `rsl-backup`) with `--backup-report`, `--recover-report`,
`--restore-report`, and a fresh `--report` path in a private directory. It
requires the successful managed backup's full encrypted remote readback and
monitor acknowledgement, a finish time within two hours, and successful
off-host recovery and both database restore controls for that **same copy**.
The recovery and restore proofs must follow the backup. The new report contains
only boolean checks and is created privately without overwriting earlier
evidence.

This command does not run a backup or restore, pause writers, or authorize a
schema change. A future migration phase must separately prove that the writers
were paused before its release backup, bind this evidence to the staged
candidate, and recheck freshness immediately before migration. A historical
restore proof for a different copy fails this release gate even when it remains
valid as a retention anchor.

`scripts/portable-vps-release-recovery-proof.py` performs that binding after
the separately confirmed pause and managed backup phases. It requires their
accepted stage reports, the exact pause plan digest, and the selected backup
run UUID. It rechecks the unchanged candidate preflight baseline and stopped
writers, reruns the writer-before-backup ordering gate, and verifies fresh
off-host recovery and isolated restore reports against the selected managed
backup copy. The root process reads owner-private backup reports with checked
ownership, permissions, and symlink protection. It then rechecks the paused
state and proof file hashes. A root-private stage baseline records only the
candidate/plan digests, selected run UUID, and report paths/hashes; the external
report contains booleans only. This is read-only release evidence and does not
run a migration or authorize promotion. A later migration phase must recheck
the proof and live schema revisions before a separately confirmed schema change.

`scripts/portable-vps-release-migration-ready.py` is that read-only readiness
gate. It requires the accepted recovery proof baseline for the same candidate,
pause plan, and selected backup. Since the backend and Mom containers are
stopped, it queries `public.alembic_version` in the `siege` and `mom_bot`
databases through the still-running PostgreSQL container. The query uses the
container's mounted administrator secret and emits only the exact revision or
STOP; it does not print the password or database content. Both revisions must
match the manifest's `from_revision`, including a database with `mode: none`.
The gate rechecks the fresh same-copy proof and revisions before recording a
root-private baseline of the candidate, plan, proof digest, PostgreSQL container
ID, current revisions, and manifest migration order. Its external report is
boolean-only. This gate performs no migration or promotion. A later confirmed
migration phase must recheck this baseline immediately before any schema write.

`scripts/portable-vps-release-migrate.py` runs one explicitly confirmed
database migration per invocation, in the manifest's order. It requires the
accepted readiness baseline digest, a fresh selected backup/restore proof,
paused original writers, exact live `from_revision` values (or the prior
step's target), and the prior step's PASS report when migrating the second
database. The candidate image must already be cached by digest. The script
validates the candidate Compose configuration and checks that the image's
single Alembic head equals the manifest's target before creating a private
start marker. It then runs only the selected maintenance service with
`docker compose run --no-deps --rm --pull never -T`, overriding its entrypoint
to Alembic and passing the explicit target revision. Docker's documented
[`run` options](https://docs.docker.com/reference/cli/docker/compose/run/)
prevent dependency startup and implicit image pulls here.

After that one command, it requires the exact target revision, the other
database's unchanged revision, no running maintenance container, and the
still-current paused backup proof. A failure or timeout records STOP and leaves
the writers stopped; the stage marker prevents an automatic retry. An operator
must inspect the resulting schema and any remaining maintenance container
before separately approving recovery or another candidate. This reviewed
script is not installed in the forced SSH command or invoked by GitHub Actions.
The CI rehearsal uses disposable services only. No production migration has
run, and promotion remains a separate phase.

## Writer pause and backup ordering gate

After a separately approved pause of the `backend` and `mom` writers, the
read-only `scripts/portable-vps-writer-pause-gate.py` verifies the order of
events. It binds to the root-private staged candidate by run ID and SHA-256,
reads the protected Compose project name, and checks the local Docker socket
for exactly one exited container for each writer service. It then reads the
private successful managed backup report under the selected protected backup
root's `runs/<run-UUID>/job.json`. The caller must provide the installed job's
reviewed owner (`root` or `rsl-backup`) and exact root path; the production
owner must be inspected before use. The gate requires
both container stop times to precede the backup start. A second Docker state
check catches a writer that restarted while evidence was inspected. Any missing,
duplicate, running, or replaced writer state stops the gate. It writes a new
root-private boolean-only report.

The check does not stop a service, run a backup, or inspect other possible
database writers. Before an actual release, inventory any external writer or
worker outside this Compose project and include it in the approved pause plan.
The gate must be rerun immediately before migration, together with the
same-copy backup and restore evidence gate. No forced SSH command currently
exposes the writer gate, and it must first be rehearsed on the isolated target.

The `VPS access contract` CI workflow also runs a disposable Docker rehearsal.
It starts two synthetic containers with a unique Compose project label, verifies
the gate refuses them while running, stops only those containers, checks the
recorded stop times against a synthetic managed-job report, and verifies a
restart stops the gate again. The runner removes only its two fixture containers
afterward. The synthetic report is never uploaded or treated as backup/restore
acceptance. This verifies Docker CLI behavior without a standing VMware pilot;
an actual isolated application and database rehearsal is still required before
production promotion.

## Confirmed writer pause and managed backup phases

`scripts/portable-vps-release-pause-backup.py` provides three independent host
phases. The confirmed pause is now exposed through a fixed restricted SSH command
and protected manual workflow. The backup phase remains separately gated. The root-private profile names the protected Compose project,
the installed backup root and owner (`root` or `rsl-backup`), and the exact
backup service/timer, unit-file SHA-256, and SHA-256 of the trimmed `systemctl
cat` output, including any drop-ins. The unit file must be root-owned,
non-writable by group/others, and match systemd's active fragment path.
`NeedDaemonReload` must be `no`, and the effective unit digest is checked
again immediately before starting the backup service. Profile
changes invalidate an earlier plan. Inspect and record the installed values
before creating this profile; the repository service template does not attest
to the production unit or account.

The **plan** phase requires the accepted read-only release preflight report and
its root-private baseline for the same staged candidate. It rechecks the
reviewed checkout, installed Compose and bind files, private runtime hashes,
Mom approval, and exact IDs, images, and health of all five installed services.
It then verifies the staged candidate digest, four unique running
Compose containers (`backend`, `mom`, `postgres`, `proxy`), health of the writers
and PostgreSQL, an active backup timer, an idle backup service, and no running
bundled bot or maintenance service. It saves the exact container IDs and
profile/candidate/preflight-baseline digests in a root-private plan, with a sanitized PASS/STOP
report. No service changes occur.

The **pause** phase requires the exact plan SHA-256 and `--confirm-pause`. It
rechecks the bound preflight baseline, running IDs, and health before reserving
an INCOMPLETE marker. It stops only
the recorded Mom and backend containers in that order, then requires those
same containers to be exited while PostgreSQL remains healthy, proxy keeps
running, and the backup timer stays active. The **backup** phase independently
requires `--confirm-backup`, the accepted pause report, the unchanged preflight
baseline with the two original writers stopped, an idle backup service,
and the reviewed unit. It starts only that unit once, requires exactly one new
managed run, and reuses the writer/backup ordering gate. Each phase writes a
new root-private boolean-only report to a caller-selected private directory
outside the Compose project. No phase automatically retries or restarts a
writer. The backup phase stores only the new run UUID in a separate private
selector for later recovery proof, after rechecking the complete paused
baseline, including frontend identity and private input hashes, so a writer,
migration container, or configuration drift during backup causes STOP.

Partial stop, command timeout, new container, unit drift, a failed backup, or
missing evidence is STOP. Preserve all markers and reports. Do not migrate or
promote after STOP. Before a migration, the existing backup/restore evidence
gate still requires recovery and isolated restore of that exact encrypted
copy. There is no automatic writer resume: an operator must first confirm no
migration or new writes occurred, recheck database revisions and original
container identities, then separately approve targeted restart of only the
stopped original services and verify app, gateway, PostgreSQL, proxy, backup
timer, and remote access. If the schema or writer state is uncertain, keep the
writers stopped for coordinated recovery. This branch supplies code and tests
only; isolated rehearsal and a separately approved production deployment are
still required.

The disposable Docker CI job exercises the pause against four uniquely labeled
synthetic containers. It verifies that Mom and backend stop while PostgreSQL
and proxy stay running, then removes only those four fixture containers. The
backup service itself is mocked in CI; this test is not backup recovery or
application acceptance evidence.
