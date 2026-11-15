# Portable VPS maintenance control

Issue #640 adds a shared, nonsecret `control/state` file to the portable
deployment. The backend reads it on every affected HTTP request. The paired
rotation procedure must use the phase-scoped
`scripts/portable-vps-maintenance-control.py` runner after Mom #395 implements
the same control file and `/api/maintenance/status` contract. Until then,
`pause` fails its two-peer drain and leaves the state paused.

This is preparation only. Do not run it on the live VPS until the full
deployment is approved. Repository changes do not authorize a host change.

## Phases

Each phase takes `--project-directory` and a fresh private `--report` path.
The runner requires root ownership, a protected project and report directory,
and exact regular `control/state` and `control/history.json` files. Reports contain only fixed checks
and status; credentials and response bodies never enter evidence. The state
file is changed by an atomic replacement and directory sync, and the existing
release lock serializes it with promotion operations.

1. `inspect` checks the control state without mutation.
2. `pause --confirm pause` changes `active` to `paused`, then waits for three
   successive zero in-flight observations from **both** running peers. It
   reports STOP if either peer lacks the status contract or the 20-second
   bounded drain expires. It records the earliest pause boundary before closing
   admission. A retry preserves that boundary after interruption. A failed
   drain leaves the state paused.
3. `drain` retries only the read-only two-peer drain after a STOP. It does not
   change the control state.
4. `resume --confirm resume` requires one fresh successful bearer activation
   report for each direction, stale-bearer rejection, public health, and a
   later successful independent public-monitor delivery report. It repeats
   the two-peer drain before writing `active`. Missing or stale evidence
   leaves the state paused. It records the resume boundary before reopening
   admission. Mom uses that boundary and the active state file's modification time
   to identify member activity that may have missed gateway events.

The backend and Mom status probes run inside their exact Compose containers.
Each process uses its own service token from its environment. The host runner
captures only `paused` and `inflight` and never prints tokens.

If pause or resume returns STOP, preserve its private report and leave the
state paused while diagnosing the failed check. A failed resume does not
restart services or alter the database. Escalate to the separately reviewed
paired recovery phase if the bearer activation itself failed. Before an
operator can resume, confirm the independent receiver actually delivered the
monitoring notification to the infrastructure channel; the report confirms
   receiver acknowledgement, not a human Slack receipt. If resume reports
   DANGER, inspect the control state immediately: the runner could not verify
   that admission was restored to paused after a failed state transition.

## Backend work inventory

The portable backend starts no scheduler or independent background worker in
its application lifespan. All database writes and outbound Discord requests
originate from HTTP routes. The assignment and attack-day routes enqueue
day-role-sync webhooks through FastAPI `BackgroundTasks`; the notifications
route enqueues member DMs the same way. Those tasks remain part of the ASGI
response call, so the outer maintenance middleware retains its in-flight
count until the tasks finish. Authentication may backfill a member Discord ID
even for a GET request; therefore the only authenticated GET exempted from
pause is the deliberately invalid acting-ID bearer probe. Health, version,
and service-authenticated maintenance status are read-only. The portable
Uvicorn command pins one worker so the in-flight count covers every request
in the process. The bot's gateway handlers and schedules are separately
guarded by Mom #395.
