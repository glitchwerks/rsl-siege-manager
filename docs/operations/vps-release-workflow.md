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
`7f654cbb57b3e185207edf9fc70a04601f1125a0`. It verifies the two CI runs
and the three SHA-tagged GHCR image digests. The candidate checkout is data only:
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

The host rechecks the digest and manifest shape, then creates exactly one
root-private `/srv/rsl-siege/evidence/release-stages/<run-id>-<attempt>/`
directory containing `candidate.json` and a boolean-only `report.json`. An
existing run directory, changed digest, duplicate JSON key, malformed manifest,
or unsafe path is a STOP. The runner preserves a separate sanitized stage report.
Staging does not alter `stack.env`, pull images, run preflight against runtime
configuration, execute backup/migration, or touch Compose services. Keep the
candidate and reports for the later preflight/promotion phases.

The host-side forced-command script must be installed separately from a reviewed
commit. Its `update-script` phase requires the exact currently installed
read-only script hash, preserves a root-private copy at
`/opt/rsl-siege-manager/scripts/portable-vps-remote.py.before-release-stage`,
and atomically changes only that script. Keep an admin SSH session and provider
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

## Promotion phases to implement and rehearse

1. **Preflight:** compare the staged candidate with protected runtime
   configuration, preserve the installed source/image/configuration baseline,
   and run the portable configuration preflight without activating services.
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
