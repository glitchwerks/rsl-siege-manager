# Guarded production registry retention

Ticket: [#553](https://github.com/glitchwerks/rsl-siege-manager/issues/553).

Production registry `siegeacrprod` uses a daily GitHub Actions cleanup at
04:17 UTC, after the replacement is explicitly activated. GitHub schedules can
be delayed; this is not a hard seven-day maximum storage age. The operator runs
without installing packages or Azure CLI extensions.

## Retention policy

The scope is exactly `siege-api`, `siege-bot`, and `siege-frontend` in the existing
production registry. Delete a manifest only when it was last updated more than
seven days ago and none of these protections apply:

- An image tag or digest appears in the current template or any retained
  Container App revision, active or inactive, including init-container images, in production, development, or
  the standalone bot resource group.
- It is among the newest five manifests in its repository or the newest five
  release-tagged (`v*`) manifests.
- The manifest is locked against deletion or writing.

Age is based on the precise last-updated timestamp, not calendar dates. At the
seven-day boundary the image stays. Shared layers mean manifest sizes cannot
be summed to predict reclaimed storage. Included Basic storage is a billing
allowance; growth-alert budgets are configured separately.

Nested manifest indexes, unexpected repositories, unhealthy or changing
applications, modified candidate metadata, and more than 100 candidates stop
the entire preflight. During deletion, a conflicting change stops further
writes and leaves a report of attempted and confirmed deletions. No automatic
retry or broad purge occurs after an uncertain write.

## Activation and evidence

Merge the workflow and operator before retiring the legacy task. Run
`Production Registry Retention` manually with `phase=preflight` on main first.
The job refuses execution from branches or tags other than main.
This proves the existing production environment's `AZURE_CREDENTIALS` can read
all relevant applications/revisions and registry metadata. Do not broaden
permissions silently if this fails.

Next run the workflow with `phase=activate`. This takes a fresh baseline,
checks that no legacy cleanup is running, disables only the legacy task status,
and verifies its other configuration. Activation does not delete images.
The daily schedule and a manual `phase=apply` both refuse deletion until the
legacy task is disabled. A scheduled run never disables it itself. Production
Bicep records `acrLegacyPurgeEnabled=false`; development keeps its prior task.

Activation and deletion each use an explicit `--confirm`. Schedule execution
is standing authorization for the fixed policy after activation. Every run
uploads a sanitized report for 30 days, including retained/deleted manifest
metadata, hashes, service checks, and before/after bytes. Reports contain no
credentials, environment variables, or workload logs. Failed GitHub Actions
runs provide the failure signal; no new Slack or email integration is added.
Restricted bot ingress permits control-plane checks only; its private
functional health is not exercised. Public web health uses the existing
unauthenticated endpoint and bounded retries for transport failures.

For a local operator, use distinct report paths and the same phases:

```sh
python3 scripts/registry-retention.py preflight --report /tmp/retention-report.json
python3 scripts/registry-retention.py activate --report /tmp/retention-report.json --confirm
python3 scripts/registry-retention.py apply --report /tmp/retention-report.json --confirm
python3 scripts/registry-retention.py verify --report /tmp/retention-report.json
```

Preflight is valid for 20 minutes. Do not run local maintenance during any
application/infrastructure deployment or registry write. GitHub production
promotion, production infrastructure deployment, and cleanup share the
`production-registry-maintenance` concurrency group with a non-replacing queue.
[GitHub supports queued concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
with `queue: max` since May 7, 2026. Development builds retain their existing paths; any observed reference or
registry drift stops cleanup. Manual Azure operations and other repositories
are outside that queue, so operators must exclude them during maintenance.

## Recovery

Deletion is permanent. Current and retained rollback images remain available;
reconstructing deleted historical images requires rebuilding their source
commits and is not a guaranteed byte-for-byte recovery. Stop and inspect the
report after a failure; never blindly resume an incomplete deletion list.

To pause replacement cleanup, disable its GitHub workflow. Before re-enabling
the legacy purge, review its proposed candidates: it does not inspect deployed
revisions. Scheduling recovery only, using the saved activation baseline:

```sh
python3 scripts/registry-retention.py rollback-task --report /tmp/retention-report.json --confirm
```

The rollback refuses unrelated task changes and restores its original status;
it does not recover deleted images. Set `acrLegacyPurgeEnabled=true` only as a
separately approved infrastructure change when intentionally returning to that
legacy policy. Keep reports outside Git.
