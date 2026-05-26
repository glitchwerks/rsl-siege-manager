# Releasing siege-web

Authoritative release process for the repo's `v*` tags. Last updated in the `v1.3.0` follow-up PR — see the bottom of `CHANGELOG.md`'s `[1.3.0]` entry for the incident that prompted it.

## What "release" means here

A release is a single repo-level `v<MAJOR>.<MINOR>.<PATCH>` tag on `main`. The tag triggers `deploy.yml`, which builds (or reuses the already-built) per-service images at that SHA and deploys all three Container Apps in `siege-web-prod`:

- `siege-web-api-prod` ← `siegeacrprod.azurecr.io/siege-api:<sha>`
- `siege-web-bot-prod` ← `siegeacrprod.azurecr.io/siege-bot:<sha>`
- `siege-web-frontend-prod` ← `siegeacrprod.azurecr.io/siege-frontend:<sha>`

Releases are **lockstep across all three services** for now. Per-component semver discipline is documented in `docs/superpowers/plans/2026-05-08-component-versioning.md` (referenced as the "component-versioning plan" below); the repo-level `v*` tag is a separate concept and is what this doc governs.

## What a release surface to users

A release is "visible" via several distinct mechanisms — all must agree:

| Surface | Source of truth | How it surfaces |
| --- | --- | --- |
| Git tag | `git tag` on a specific SHA | `git describe`, GitHub Releases page |
| GitHub Release | `gh release create` | https://github.com/glitchwerks/rsl-siege-manager/releases |
| **In-app changelog dropdown** (bell icon in top bar) | `CHANGELOG.md` heading `## [X.Y.Z] - YYYY-MM-DD` | Frontend bundle (build-time) + `/api/changelog` |
| **`/api/version` endpoint** | `backend/VERSION`, `bot/VERSION`, `frontend/package.json#version` | `SystemPage.tsx`, support tooling |
| Container image SHA tag | `git rev-parse main` at tag time | Azure Container App revision UI |

**The failure mode the v1.3.0 release exposed** was tagging + cutting a GitHub Release without touching `CHANGELOG.md` or the three VERSION sources. The tag and image existed, but the in-app changelog still showed `[Unreleased]` and `/api/version` still reported `backend=1.0.1 bot=1.0.1 frontend=1.0.0`. Users saw no v1.3.0 in the app even though the deploy succeeded.

## Pre-tag checklist

Run through this entire list **before** running `git tag`. The order matters: the changelog + version updates must be a commit on `main` that the tag will point at.

### 1. Determine the new version

The repo tag follows semver applied at the release-cycle level:

- **MAJOR** — any in-cycle PR contained a breaking change (per the component-versioning plan's Q2 rules) on any of the three services.
- **MINOR** — any in-cycle PR added user-visible functionality on any of the three services.
- **PATCH** — bug-fix-only cycle.

When in doubt, prefer the higher bump. The repo-level version is consumer-facing; conservatism here is cheap.

### 2. Audit `[Unreleased]` against the actual diff

Every PR merged since the previous tag should have a corresponding entry under `## [Unreleased]` in `CHANGELOG.md`. In practice, many PRs land without updating the changelog. **The release-cutter is responsible for filling the gap before tagging.**

Run:

```powershell
git fetch --tags origin
git log v<previous>..main --oneline
```

For every PR title that does not have a matching `[Unreleased]` entry, either:

- Add an entry summarizing the change (drafting from the PR body), or
- Decide the PR has no user-visible impact and explicitly skip — but write down which PRs you skipped in the release PR's body so the next release-cutter can audit.

### 3. Promote `[Unreleased]` to the new version

In the same commit that fills any gaps from step 2:

- Replace `## [Unreleased]` with `## [Unreleased]\n\n## [<new-version>] - <YYYY-MM-DD>` (preserve an empty `[Unreleased]` heading at the top so the next cycle has somewhere to land entries).
- Use the current date in ISO 8601 (`YYYY-MM-DD`).
- Sub-section order: `### Added`, `### Changed`, `### Fixed`, `### Infrastructure`, `### Documentation`. Omit empty subsections.

### 4. Bump the three VERSION sources

Update all three to the new repo version:

- `backend/VERSION` — plain-text file, single line `<X.Y.Z>\n`.
- `bot/VERSION` — same format.
- `frontend/package.json` — `"version": "<X.Y.Z>"`. Also update the matching root-level `"version"` in `frontend/package-lock.json` (lines near the top — the lockfile mirrors `package.json`'s root version in two places). Do NOT run `npm install` to "regenerate" the lockfile just to bump the version; edit the two version fields directly.

**Note on per-component discipline:** the component-versioning plan defines independent per-component versions. The repo `v*` tag is a separate axis. For the v1.3.0 release the three component versions were bumped in lockstep with the repo tag (pragmatic alignment after a retroactive cleanup). Going forward, per-PR bumps per the plan are the steady-state rule, and at release time the cutter verifies all three components are at version `>= previous-release-version`. Component versions are allowed to drift from each other and from the repo tag — see the plan's Q3 for the rationale.

### 5. Open a release PR

Branch name: `chore/release-v<X.Y.Z>` (or `release/v<X.Y.Z>`).

PR title: `chore(release): v<X.Y.Z>`.

PR body must contain:

- The release notes (same content that will go in the GitHub Release body)
- A list of any PRs from `v<previous>..HEAD` that did NOT get a CHANGELOG entry, with one-line justification each
- `Closes #<release-tracking-issue>` for the release-tracking issue

Merge the PR via squash-merge after CI is green and reviews are addressed (per the repo's standard PR discipline in `CLAUDE.md`).

### 6. Tag and release

After the release PR is merged:

```powershell
# From your local checkout
git fetch origin --tags
git checkout main
git pull origin main

# Confirm HEAD is the merged release PR's squash commit
git log -1

# Annotated tag (use the version + headline)
git tag -a v<X.Y.Z> -m "v<X.Y.Z> - <headline>"
git push origin v<X.Y.Z>
```

Pushing the tag triggers `deploy.yml` automatically; you do not need to dispatch the workflow manually.

Then create the GitHub Release:

```powershell
gh release create v<X.Y.Z> `
  --title "v<X.Y.Z> - <headline>" `
  --notes-file .tmp/release-notes-v<X.Y.Z>.md
```

The notes file is the same content as the `[X.Y.Z]` section of `CHANGELOG.md`; extracting it ahead of time and stashing in `.tmp/` is the convention.

### 7. Verify the release surfaced everywhere

After `deploy.yml` finishes (3 jobs: Deploy API / Deploy Bot / Deploy Frontend, all "prod"):

- `curl https://<frontend-fqdn>/api/version` returns the new versions for all three components
- The frontend changelog dropdown shows the new entry as latest (bell icon may need a hard refresh to invalidate the cached bundle)
- The Container App revision UI for all three apps in `siege-web-prod` shows the new image SHA tag and `Healthy / Running / 100% traffic`

## What auto-deploy does (and does not) do

`deploy.yml` triggers on `push` to a tag matching `v*`. It:

- Skips `Run Tests` (those ran on the main push that produced the SHA)
- Skips `Build & Push` if the image at the tagged SHA already exists in ACR (common — the image was built on the main push)
- Builds and pushes if not already present
- Deploys all three Container Apps to the new image

It does **not**:

- Update CHANGELOG.md / VERSION files retroactively (this is the whole reason this doc exists)
- Run any infra-deploy step (`infra-deploy.yml` is a separate `workflow_dispatch` workflow that you trigger explicitly when Bicep changes)
- Promote dev → prod with any gating (the tag IS the promotion)

## Hotfixes

A hotfix is a `vX.Y.<Z+1>` release branched off a release tag, not main. Until this workflow is built out, hotfix PRs use the same pre-tag checklist above with the comparison baseline shifted from "previous release" to "current release that's broken" — and bump only the PATCH digit. See the component-versioning plan's "Hotfix branches" section under Q3.

## Reverting a release

If a release ships broken:

- **Container App revision swap** is the fastest revert — `az containerapp revision activate` the previous revision and set its traffic weight to 100%. Reversible in under a minute, no git operations.
- For a permanent revert that requires code changes, ship a `vX.Y.<Z+1>` patch release through the normal pre-tag checklist with the revert PR included in the diff.

Do **not** force-move a published tag. If a tag was just pushed (minutes ago) and nobody has consumed it, force-moving is recoverable (the v1.3.0 follow-up did this). Once 24h have passed or the release has been shared externally, treat the tag as immutable and use a patch release for any correction.

## Tooling targets (future)

This is the manual process for v1.3.0+. Items worth automating:

- A `scripts/cut-release.ps1` that takes `<X.Y.Z>` and `<headline>`, walks the checklist, opens the PR for you.
- A CHANGELOG-gap CI check that fails the release PR if `git log v<previous>..HEAD --oneline` contains PR numbers not referenced in the `[Unreleased]` block.
- A `/api/version` probe in `deploy.yml`'s post-deploy step that fails the run if `api/version` and the tagged version don't match (closes the failure mode that caused this doc to exist).

Each of these is its own issue when picked up.
