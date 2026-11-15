# Releasing Siege Manager

The current production deployment is the guarded VPS stack. Pushing a `v*`
tag does **not** deploy it: the former Azure `deploy.yml` and
`infra-deploy.yml` workflows are disabled. The old procedure is preserved as
[historical Azure release guidance](docs/operations/azure-release-historical.md).
Do not re-enable either workflow as part of a VPS release.

## Prepare a release

1. Review the changes since the previous tag and update `CHANGELOG.md`. Keep
   an empty `[Unreleased]` section for the next cycle.
2. Apply the component version rules to `backend/VERSION`, `bot/VERSION`, and
   `frontend/package.json` (and the lockfile when frontend changes). A repo
   release tag does not require every component to change version. Check that
   `/api/version` will report the intended versions and build SHA.
3. Merge a reviewed release metadata PR to `main`. Once the release gates
   pass, create the annotated tag from that exact release commit. The tag is
   release metadata; it does not replace any VPS promotion gate.

The repository tag follows semantic versioning for the release cycle: a
breaking change requires a major bump, a user-visible feature a minor bump,
and a fix-only cycle a patch bump. Component versioning is independent; see
the [component-versioning plan](docs/superpowers/plans/2026-05-08-component-versioning.md).

## Deploy to the VPS

The [guarded VPS release workflow](docs/operations/vps-release-workflow.md)
is the authoritative operator procedure. Its candidate is a reviewed
coordination-repository manifest containing immutable image digests, source
commits, CI provenance, and explicit database migration decisions. The
workflows require separate protected approvals for access, staging, writer
pause, backup, migration, promotion, acceptance, and recovery. A successful
tag, image publish, readiness check, or stage check does not promote images.

Before any production promotion, complete the isolated rehearsal, independent
monitoring delivery tests, backup and restore proof, migration readiness, and
all release gates recorded in that runbook. Production promotion remains
unscheduled until those gates pass. After promotion, verify public/API health,
exact image digests, component versions and build SHA, database revisions,
and Slack and fallback email delivery. Then publish the GitHub Release with
notes from the matching changelog section. Publication triggers the active
Discord release notification workflow; verify that notification. Preserve
the sanitized reports and private rollback evidence according to the runbook.

For the frozen Azure registry, follow
[registry retention](docs/operations/registry-retention.md). Its workflow is
disabled; no cleanup or deletion is part of a VPS release.
