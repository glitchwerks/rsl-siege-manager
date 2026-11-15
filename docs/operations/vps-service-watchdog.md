# Bounded VPS application-service recovery

This is an inert, one-shot host-side recovery runner for #579. Merging it does
not install a schedule, grant Docker access, or authorize a restart on the VPS.
It never targets PostgreSQL, the proxy, migration jobs, the Compose project, or
the host. Docker health is local; the independent receiver must still deliver
outage, recovery, and missed-signal alerts. A successful Docker restart is not
proof of application recovery; the next independent health check must verify it.

Before a separately approved activation, review the actual Compose project,
service, exact digest-pinned image, Docker access, deployment operations, and
recovery plan. Use a separate 0600 JSON config and state file in a 0700
operator-owned directory for each service. The example is a template, not a
ready configuration. Keep the state outside the deployment checkout so a
release does not reset cooldown history. Use a fresh 0600 report path in a
0700 directory for every run. Do not share a state file across services.

The only allowed service names are `backend`, `frontend`, `mom`, and `bot`. The
runner locates exactly one container with the reviewed Compose project and
service labels, then verifies its exact digest-pinned image. It makes no
recovery attempt for a missing, stopped, healthy, starting, or OOM-killed
container. A stable running `unhealthy` container must be observed twice at
least 90 seconds apart with the same full container ID and restart count. The
runner rechecks immediately before the targeted `docker container restart`.
It consumes the allowance before invoking Docker so a failed or interrupted
restart cannot cause a rapid retry. A service gets at most one attempt per six
hours and two attempts in 24 hours. Missing, stopped and OOM-killed containers,
suppressed attempts and restarted containers return `STOP` until a later healthy
observation. The separate independent alert must reach an operator; this runner
does not suppress or clear the alert.

Once the deployment operator has approved the exact config, Docker privilege,
schedule, monitoring delivery, and rollback, a single run uses:

```sh
python3 scripts/portable-vps-service-watchdog.py \
  --config /protected/watchdog/backend.json \
  --state /protected/watchdog/backend-state.json \
  --report /protected/evidence/backend-watchdog-001.json \
  --confirm-targeted-restart
```

The report contains only a fixed decision, a restart-attempt boolean, status,
and timestamp. A failed preflight emits `STOP`; incomplete or failed Docker
operations never claim a successful recovery. This script must not run during
release deployment, migration, restore, or backup recovery. The operator must
ensure the eventual schedule is paused for those phases and verify it resumes
only after the new digest is recorded in the reviewed config. Never run this
from an application container or mount the Docker socket into one.

For rollback, disable only the affected watchdog schedule and preserve its
state and evidence; continue independent health monitoring. Investigate the
unhealthy service and use the existing manual service-specific recovery
runbook. Do not reset the state or restart the database/whole stack to evade
the cooldown.
