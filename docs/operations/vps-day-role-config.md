# Portable day-role configuration phases

This phase-scoped host runner prepares the configuration-only change tracked by
[coordination #16](https://github.com/glitchwerks/rsl-mom-apps/issues/16).
It is repository preparation. Do not run it on production until the isolated
VM has passed enable, disable, failure and recovery rehearsal, the full
deployment window is approved, and the protected configuration recovery
package has a verified pre-change copy. The current production flag is false;
no role ID or receiver URL is installed there. The separate
[running-state checker](vps-day-role-effective.md) verifies what the recreated
backend actually received; it does not prove a Discord role update.

## Inputs and phase boundaries

Use the reviewed checkout on the selected host. The project, runtime, decision
and evidence directories must be root-owned and private. Keep the decision
file at mode 0600 and outside Git. For enablement it contains JSON with exactly
`schema: 1`, `enabled: true`, `day_1_role_id`, `day_2_role_id`, and
`receiver_url`. The IDs must be distinct decimal Discord snowflakes and the URL
must be the approved HTTPS origin plus `/api/internal/role-sync`. For an
emergency disable, use exactly `{"schema":1,"enabled":false}`; that phase
retains the prior IDs and URL for diagnosis. Never put any candidate value on a
command line, in an issue, log, or report. Do not use production Discord
recipients for isolated rehearsal.

The runner changes only `runtime/backend.env`. It refuses duplicate or
nonliteral role settings and preserves all unrelated bytes. It validates both
the backend and the optional `migrate-siege` service because both read that
file. It compares the staged candidate with the full rendered Compose model
before recreating only the backend container. The independent Mom gateway,
database, proxy and frontend retain their exact container IDs and images.

Stage against the current healthy, matching, disabled or enabled backend:

```sh
python3 scripts/portable-vps-day-role-config.py stage \
  --project-directory /srv/rsl-siege \
  --decision-file /protected/day-role/decision.json \
  --evidence-directory /protected/day-role/evidence \
  --confirm stage
```

The command prints only PASS/STOP and a random run ID. It creates a private
stage directory under the evidence directory with exact rollback and candidate
copies of `backend.env`, the unchanged Mom and stack baselines, input hashes,
and an accepted record. Keep the directory private and off uploaded artifacts.
A stage never edits the running project or sends a webhook. Re-stage after any
STOP or baseline drift; never edit an accepted stage in place.

The protected **VPS day-role stage** workflow accepts an exact UUID as its
`stage_id` before environment approval. It chooses rehearsal or production
credentials and tailnet tags separately, and sends the target's private
`DAY_ROLE_DECISION_JSON` secret as bounded SSH stdin. The restricted host route
checks the reviewed checkout and decision digest, invokes only the stage phase,
and removes its temporary decision file. GitHub receives boolean checks and
the already visible stage ID. The existing manual command may still generate
its own UUID; `--stage-id` selects the exact ID for the protected route. This
workflow does not install the host route, approve activation, or deploy a
candidate. The host route must be installed through its separately reviewed
hash-bound update procedure after isolated rehearsal.

Before activation, complete the separately confirmed maintenance pause and
two-peer drain, managed encrypted backup, off-host recovery and isolated
same-copy restore. Write the successful maintenance `pause` report to the exact
stage directory as `pause-report.json`. If pause reported STOP because its drain
timed out, run the separate read-only `drain` phase and write its successful
report there as `drain-report.json`. The backup must start after the successful
drain report was written. The proof phase checks that report against the
current paused state file, the private backup reports, the protected backup profile,
current installed images and configuration, exact staged candidate digest, and unchanged pause history. It
records one immutable acceptance under the exact stage. A failed proof attempt
requires a new stage; do not edit evidence in place. Select the exact reports
without putting their contents in command arguments or logs:

```sh
python3 scripts/portable-vps-day-role-config.py proof \
  --project-directory /srv/rsl-siege \
  --stage-directory /protected/day-role/evidence/REPLACE-RUN-ID \
  --backup-profile /srv/rsl-siege/runtime/release-backup-profile.json \
  --maintenance-report /protected/day-role/evidence/REPLACE-RUN-ID/pause-report.json \
  --backup-report /protected/backups/runs/REPLACE-BACKUP-UUID/job.json \
  --recover-report /protected/recovery/recover.json \
  --restore-report /protected/recovery/restore.json \
  --confirm proof
```

The example paths must be replaced with the protected paths on the selected
host. The backup path must match the `backup_root` in the profile. The
protected **VPS day-role proof** workflow dispatches the same read-only host
phase with the exact stage UUID, successful `pause` or `drain` report selector,
and three private evidence paths. The restricted route rejects path traversal,
duplicate paths, an unreviewed checkout, and a repeated proof attempt. The
sanitized artifact contains the accepted proof SHA-256 to bind a later
activation; it contains no report paths, role IDs, receiver URL, or credentials.
The protected **VPS day-role preflight** workflow takes that proof SHA-256 and
records an exact, single-use baseline and public-health acceptance. Its
sanitized artifact returns `preflight_sha256`. If the installed state, pause,
proof, or public signal changes, discard the stage and start again.

Before dispatching **VPS day-role activate** or **VPS day-role recover**, an
operator must set a matching, separately approved full deployment window ID
in the protected target environment variable `REHEARSAL_VPS_DAY_ROLE_WINDOW_ID`
or `VPS_DAY_ROLE_WINDOW_ID`. The workflows fail closed when this variable is
absent or differs from the dispatch input. The protected target also needs
`REHEARSAL_VPS_PUBLIC_SIGNAL_CONFIG_PATH` or
`VPS_PUBLIC_SIGNAL_CONFIG_PATH` pointing to its private public-signal
configuration. Rehearsal and production use separate Tailscale identities,
SSH credentials, host keys, and protected environment approvals. The workflow
does not set either window variable itself.

Activation takes the exact stage UUID, proof SHA-256, preflight SHA-256, and
approved window ID. The restricted host route verifies both private records,
reserves the attempt once, invokes the reviewed runner, and returns only
boolean checks. The runner rechecks the accepted preflight just before it
stops backend. No workflow resumes writers. In the full approved window, the
equivalent host command is:

```sh
python3 scripts/portable-vps-day-role-config.py activate \
  --project-directory /srv/rsl-siege \
  --stage-directory /protected/day-role/evidence/REPLACE-RUN-ID \
  --public-config /protected/public/public-signal.json \
  --backup-profile /srv/rsl-siege/runtime/release-backup-profile.json \
  --confirm activate
```

The runner locks the project, repeats installed-state, pause and proof checks, writes
an INCOMPLETE marker, stops only backend, atomically replaces its environment
file, validates the exact Compose render, then recreates only backend with the
installed immutable image. It checks the effective running flag and IDs, all
unchanged peer identities, and public TLS/health/version. A failure stops
backend and leaves the maintenance pause in place. Do not resume writers or
retry activation from the same stage. Preserve the fixed-check result report.

## Separate recovery and acceptance

For an interrupted or failed activation, inspect the private stage and live
state first. The guarded recovery phase accepts only the original or staged
backend file, unchanged other inputs, unchanged pause history, and the same
healthy Mom identity. It restores the original backend file and recreates only
backend. It then repeats effective-state and public checks. A failed recovery
stops backend and keeps the pause; it is not permission to restart the whole
stack. Recovery refuses a stage that already passed activation because new
writes or Discord mutations may have occurred. A later kill switch is a new
disable decision and stage, not a rollback of a completed activation.
When backend is already stopped, recovery verifies its exact image and requires
three paused, zero-inflight Mom observations. If backend remains running, the
normal two-peer drain still applies.

```sh
python3 scripts/portable-vps-day-role-config.py recover \
  --project-directory /srv/rsl-siege \
  --stage-directory /protected/day-role/evidence/REPLACE-RUN-ID \
  --public-config /protected/public/public-signal.json \
  --confirm recover
```

If recovery would restore a previously enabled flag after an emergency
disable, first verify that re-enabling the old behavior is safe and that no
post-pause role mutation occurred. Only then add
`--confirm-restore-enabled-safe`. If that cannot be established, keep the
path paused and prepare a new forward repair. The runner never automatically
reconciles Discord roles or database history.

The protected **VPS day-role recover** workflow is narrower than that manual
command: it refuses stages whose old flag was enabled, requires an attempted
activation that did not pass, and never supplies
`--confirm-restore-enabled-safe`. It is a separate, single-use dispatch under
the approved window. Preserve the private host reports for diagnosis after
any STOP; do not retry the same stage.

Installing the new restricted SSH route is a separate host operation. The
reviewed `portable-vps-host-access.py update-day-role-script` phase requires
the exact currently installed script hash and saves its bytes in
`/root/portable-vps-remote.py.before-day-role-dispatch`, leaving the earlier
release-script backup untouched. Its distinct `restore-day-role-script` phase
restores only those reviewed bytes. Neither phase is run by the workflows or
authorized by merging this change. Review and execute it on the isolated VM
first, with a sanitized report, before considering a production host update.

After an accepted activation or recovery, independently run the
[effective-state checker](vps-day-role-effective.md) with a fresh private
report. Complete the isolated authenticated assign/unassign or disabled
negative-control checks under their separate approval. Refresh and verify the
encrypted configuration recovery package before resuming writers. Production
role mutation requires its own authorization or observation of a natural
event. Merging this runner does not authorize any live host or Discord change.
