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

Before activation, complete the separately confirmed maintenance pause and
two-peer drain, managed encrypted backup, off-host recovery and isolated
same-copy restore. Write the successful maintenance `pause` report to the exact
stage directory as `pause-report.json`. If pause reported STOP because its drain
timed out, run the separate read-only `drain` phase and write its successful
report there as `drain-report.json`. The backup must start after the successful
drain report was written. The proof phase checks that report against the
current paused state file, the private backup reports, the protected backup profile,
current installed images and configuration, and unchanged pause history. It
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
host. The backup path must match the `backup_root` in the profile. Record the
installed digest and public signal configuration. In the full approved window,
activate the accepted stage using its exact private directory:

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

After an accepted activation or recovery, independently run the
[effective-state checker](vps-day-role-effective.md) with a fresh private
report. Complete the isolated authenticated assign/unassign or disabled
negative-control checks under their separate approval. Refresh and verify the
encrypted configuration recovery package before resuming writers. Production
role mutation requires its own authorization or observation of a natural
event. Merging this runner does not authorize any live host or Discord change.
