# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.4.1] - 2026-06-13

Patch release: markdown rendering in the changelog dropdown, a validation fix for level-6 strongholds, and repairs to the Discord release-announcement workflow.

### Changed

- **Inline markdown in the "What's new" changelog dropdown** — bullet text now renders `**bold**`, *italic*, `` `inline code` ``, `[links](url)` (opening in a new tab), and bare `#NNN` issue references as clickable links, instead of showing the raw markdown. (#388)

### Fixed

- **Group-number validation rejected `group_number=10`** — a level-6 stronghold legitimately has 10 groups, but the validator's hardcoded upper bound flagged group 10 as out of range. The bound now agrees with the database constraint (≤ 10). (#495)

### Infrastructure

- **Discord release-announcement workflow repaired** — removed a broken `created_at == published_at` skip-guard that suppressed the initial v1.4.0 announcement, added a `workflow_dispatch` trigger so a missed announcement can be re-fired by tag, and scoped the job to the `prod` environment so it can read the `DISCORD_RELEASE_WEBHOOK_URL` secret. (#493, #497)

## [1.4.0] - 2026-06-12

Member removal from planning sieges, inactive-member indicators, and stale-roster cleanup. Infrastructure: automated idempotency verification for infra-deploy CI, Pester test coverage for certificate tooling, Discord release announcements, and dependency housekeeping.

### Added

- **Remove member from a planning siege** — officers can now remove a member from an active planning siege directly from the roster, reassigning or clearing any positions they held. (#486, #490)
- **Inactive-member indicator on siege roster rows** — roster rows for deactivated members now display a visual indicator icon so officers can identify stale assignments at a glance. (#487, #489)

### Changed

- **Bot exception-handler logs now include request endpoint and method** — enriched log context makes it straightforward to correlate bot errors with the originating API call. (#432, #491)

### Fixed

- **Stale SiegeMember rows after member deactivation** — deactivating a member now cleans up any open `SiegeMember` roster rows; inactive members no longer linger in cloned-siege rosters. (#485, #488)

### Infrastructure

- **Automated idempotency verification in infra-deploy CI** — the `infra-deploy.yml` workflow now runs an Azure what-if check after each deployment to assert the resulting state is idempotent. (#233, #471, #474, #475, #476, #477, #478, #479, #480, #481)
- **Wire `DISCORD_DAY_1/2_ROLE_ID` dev environment variables** — day-role IDs are now provisioned in the dev environment to support day-role-sync testing end-to-end. (#463, #470)
- **Discord release-announcement workflow** — publishing a GitHub Release now automatically posts a rich embed to the release-announcement channel via `notify-discord-release.yml`. (#469)
- **Pester tests for `generate-origin-pfx.ps1`** — certificate-generation script now has PowerShell unit-test coverage via Pester. (#231, #473)
- **Bump `actions/upload-artifact` 4 → 7** — CI workflow dependency update. (#484)
- **Bump `actions/checkout` 4 → 6** — CI workflow dependency update. (#483)

### Documentation

- **Consolidate backend↔bot seam reference** — the canonical backend↔bot HTTP seam reference (URL resolution, auth, deployment shapes, ingress posture, Key Vault secrets) is now consolidated in `docs/bot-seam.md`. (#454, #472)

## [1.3.0] - 2026-05-26

Day-role-sync producer (mom-bot integration) + sidecar bot support + member preferences API. See `RELEASING.md` for the release process this entry follows.

### Added

- **Day-role-sync producer** — siege-web now POSTs a `role_sync` webhook to a configured receiver (e.g. `mom-bot`) whenever a member's day-role assignment changes. Producer ships dark behind `DAY_ROLE_SYNC_ENABLED` (default `false`); enable by setting both `DAY_ROLE_SYNC_ENABLED=true` and `DAY_ROLE_SYNC_URL=<receiver-endpoint>`. Implemented via `BotClient.sync_day_role()` invoked from mutation seams through FastAPI `BackgroundTasks`. Contract bumped `v1.0 → v1.1` adding optional `discord_role_id`. Canonical contract lives in `glitchwerks/rsl-mom-apps`. (#401, #409, #410, #411, #459, #460, #461, #462)
- **Member preferences API** — `GET` / `PUT` `/me/preferences` with an `X-Acting-Discord-Id` header for actor identification on proxied calls. Closes #322. (#438)
- **Post-condition category taxonomy API** — Role / Affinity / Faction / League / Rarity / Effect / Other taxonomy is now first-class on the API. Closes #442. (#446)
- **Auto-consult telemetry** — auto-consult flow now emits structured graph + telemetry events. (#441)
- **External sidecar support** — operators running an alternate Discord bot (e.g. `mom-bot`) can now exclude the bundled bot at the infrastructure layer. Bicep: `useExternalSidecar bool = false` param in `infra/modules/container-apps.bicep`; when `true`, the bundled bot Container App is not provisioned and `DISCORD_BOT_API_URL` is sourced from the new `externalBotApiUrl` param instead. The `infra-deploy.yml` workflow accepts `useExternalSidecar` as a `workflow_dispatch` boolean input. Local dev: use `docker-compose -f docker-compose.yml -f docker-compose.sidecar-external.yml up` to start postgres + backend + frontend without the bot; the default `docker-compose up` continues to bring up the full stack including the bundled bot. The Discord singleton-token constraint (only one bot process per token) is enforced at the infrastructure layer, not just documentation. See `bot/INTERFACE.md` for the sidecar HTTP contract. (#426, closes #347)
- **Sidecar integration test suite** (`backend/tests/integration/sidecar/`) — 45
  tests covering all seven bot HTTP endpoints over a live TCP socket. The bot runs
  in a new fake mode (`BOT_TEST_MODE=fake`) via an in-memory `FakeDiscordClient`
  that requires no Discord token, injects magic trigger values for every
  exception-translation path (403/502/503), and always reports `bot_connected: true`.
  Every success-path assertion validates both status code and body shape. Auth
  coverage exercises both the 403 (missing header) and 401 (wrong token) failure
  modes across three distinct endpoints. A dedicated `Sidecar Integration Tests` CI
  job surfaces as an independent status check on every PR. This suite is the
  normative source of truth for `bot/INTERFACE.md` (Step 3 of #347). (#424)
- **`bot/INTERFACE.md`** — full HTTP contract spec for the bot service with a quick-start checklist. (#420, #433, #445)

### Changed

- **`_serialize_post.active_conditions`** now includes `condition_type` on every active condition. Downstream consumers of the posts API will see the additional key. Bundled with the post-condition taxonomy work. (#451)
- **Acting-member auth match** now uses `discord_username` with opportunistic snowflake backfill — corrects the auth match path when only the username is available; backfills the snowflake to short-circuit subsequent matches. (#453)

### Fixed

- Bot HTTP sidecar (`bot/app/http_api.py`) now translates discord.py exceptions
  to structured HTTP error responses via FastAPI global exception handlers:
  `discord.Forbidden` → 403, `discord.NotFound` → 404,
  `discord.HTTPException` (4xx) → 502, `discord.HTTPException` (5xx) /
  `asyncio.TimeoutError` → 503.  Previously these surfaced as unhandled 500s.
  (#419)
- **Bot HTTP exception-handler payloads tightened** — error response shapes are now compact and consistent. (#431)
- **Flaky XFF production-warning tests removed** in favor of throttle-state assertions. Closes #387. (#389, #414)

### Infrastructure

- **`useExternalSidecar` Bicep param + compose override** — opt-in path for deployments where the bot sidecar runs as a separate container, behind a Bicep flag with a composite-action validator. Closes #426, #434, #435 (Step 4 of #347). (#427, #436, #437)
- **`BOT_SERVICE_TOKEN` split onto its own KV secret** (`bot-service-token`) — separation from other shared secrets for tighter rotation. (#447)
- **Bot HTTP seam cleanup** — Step 1 of the sidecar-bot-support plan (#347), normalizing the bot's HTTP seam inconsistencies. (#415)
- **`bootstrap-reimport.ps1` moved into `scripts/`** — repo-hygiene move. (#391)

### Documentation

- **Day-role-sync spec** canonical home moved to `glitchwerks/rsl-mom-apps` (`contracts/sidecar-api.yaml` + `docs/`); the in-repo copy was first redirected (#457) then restored as a symmetric producer/receiver reference (#459). Producer-side env-var documentation (`DAY_ROLE_SYNC_ENABLED`, `DAY_ROLE_SYNC_URL`) landed in #410.
- **Stale `siege-rg-{env}` references** purged in favor of the current `siege-web-{env}` resource-group naming. Closes #448. (#449)
- **Sidecar-bot-support plan** ported into the repo (refs #347). (#412)
- **Demo-mode plan** added after a 3-pass inquisitor sweep. (#392)
- **`useExternalSidecar` README + bicepparam example**, plus a safe-nav inline comment in the affected template. Closes #434. (#436)

### Release-metadata note

This entry was added in a follow-up PR after the `v1.3.0` tag and GitHub Release were initially cut — the tagging step preceded the CHANGELOG/VERSION updates, which violates the discipline now codified in `RELEASING.md`. The tag was re-cut at the metadata-fix HEAD; the original tag pointed at the same code less the changelog/version surfaces. Future releases follow the `RELEASING.md` pre-tag checklist.

## [1.2.0] - 2026-05-11

### Added

- **Suggest Post Assignments** — one-click auto-assignment of members to posts based on each member's saved condition preferences. Preview the result as a diff list with type filters and an expiry countdown; apply atomically with race-fenced writes. The Posts tab shows an optimal-status chip indicating whether the current board is already best-possible. Per-post skip reasons distinguish "no eligible match" from "no conditions configured." Algorithm is deterministic across runs and never matches the same `(member, condition)` pair to two posts. (#335, #345, #348, #357, #359, #360, #362, #363, #364, #365, #366, #367, #368, #369, #381, #382)
- **Group Post Conditions by Level or Type** — switch the conditions list between Stronghold Level (the existing default) and a new Type grouping (Role / Affinity / Faction / League / Rarity / Effect / Other). Choice persists per browser. Available on the Post Priorities page, Member Detail page, and the Post Condition Assignments page inside a siege; the siege page also has a master "Toggle All" control at the top right that overrides every per-row toggle. (#370, #371, #375, #376, #377, #378)
- **Rate-limited Discord OAuth2 endpoints** — `/api/auth/login` (10/min) and `/api/auth/callback` (5/min), env-tunable via `AUTH_LOGIN_RATE_LIMIT` / `AUTH_CALLBACK_RATE_LIMIT`. Client IP is read from the leftmost validated `X-Forwarded-For` entry, with throttled production warnings on missing or non-IP headers. (#153, #352)

### Fixed

- Backend test suite is reliably green on CI's Linux + Python 3.12 runners — caplog handler-install race in `test_auth_rate_limit.py` and a dotenv leak in `test_config.py` were resolved. (#358, #372, #374, #379, #380)

### Infrastructure

- Repository renamed: `glitchwerks/siege-web` → `glitchwerks/rsl-siege-manager`; all docs, setup guides, and config updated. (#349, #361)
- Fork-PR-friendly CI: secret-dependent steps now skip when running on fork PRs. Reusable-workflow version bumps to `glitchwerks/github-actions` v2.6.2. (#350, #351, #353, #354, #355, #356)

## [1.1.0] - 2026-05-08

### Added

- In-app changelog dropdown in the top bar with a per-user red-dot indicator that surfaces `CHANGELOG.md` content; bell icon, latest entry first, mark-seen persists across sessions (#298)
- Inline duplicate-condition indicator on post-assignment radio buttons — operators see at a glance which member-condition pairs are already in use elsewhere, with the conflicting post number rendered next to the affected radio (#196)

### Fixed

- Posts tab now sorts by Post # rather than priority — list order stays stable when priorities are edited (#291)
- Removed the dead "N/A" summary tile and unreachable `has_no_assignment` code paths from the assignment board (#294)
- Removed the dead `disabled` summary counter from the board summary bar — the underlying `is_disabled` flag is preserved (drag-drop gating, image generation) but the passive count no longer earns its slot (#256)
- `bootstrap-db.ps1` now calls the canonical `scripts/seed.py` (the legacy `backend/seed.py` was incomplete); regression tests added (#293)
- Login page now has a "Back to Home" link, closing the navigation loop between the landing page and login flow (#208)
- Auto Attack Day preview popup no longer renders empty padding cells — each column iterates its own length, with assignment counts surfaced in column headers (#243)
- Validation Rules 7 and 16 now render `Post N` rather than the internal database `building.id` in user-facing warning messages (#301)

### Infrastructure

- `RUNBOOK.md` extended with a Deploys & Drift section and a hybrid-trigger decision: dev auto-triggers infra deploys on `infra/**` changes, prod remains manual to prevent drift (#289)

## [1.0.3] - 2026-05-07

### Added

- DB dependency p95 latency tile and PostgreSQL connection-failure alert added to the Application Insights workbook (#271)

### Fixed

- Validation Rule 2 (member scroll budget) now correctly counts broken-building assignments toward the limit, restoring the error message in mixed-assignment scenarios (#273)

### Infrastructure

- Bicep `assert` precondition validation for `enableCustomDomain` — misconfigurations (missing cert, wrong scope) now fail at template-evaluation time rather than mid-deploy (#287)
- Behavior-tier Bicep API bumps: PostgreSQL Flexible Server (preview → GA), Container Apps (2024-03-01 → 2025-07-01), Scheduled Query Rules (2023-12-01 → 2026-03-01); validated via what-if (#285)

## [1.0.2] - 2026-04-30

### Added

- SQLAlchemy + asyncpg instrumented via OpenTelemetry — database queries now surface as dependencies in Application Insights (#265)

### Infrastructure

- Observability runbook finalized: alert inventory, autoMitigate policy, and workbook portal links added to `RUNBOOK.md` for on-call use (#264)

## [1.0.1] - 2026-04-29

### Added

- Application Insights workbook with 5 tiles (request volume, latency p50/p95, error rate, bot restarts, image generation duration) and 4 alert rules for production health (#260)
- Scheduled weekly ACR Task (`weekly-purge`, Sundays 03:00 UTC): keeps the last 10 SHA-tagged images per repo, deletes untagged manifests older than 7 days, retains all `v*` release tags forever (#247)

### Fixed

- App Insights telemetry pipeline: `OTEL_SERVICE_NAME` is now set and FastAPI routes are explicitly instrumented, so spans reach Application Insights as expected (#248)
- Alert rules no longer auto-mitigate — autoMitigate disabled on all four alert rules, so on-call must manually acknowledge when alerts fire (Azure rejected the prior `autoMitigate: true` + action-suppression combo) (#262)

## [1.0.0] - 2026-04-17

### Added

**Siege lifecycle**
- Full siege lifecycle: create → configure buildings → assign members → validate → activate → complete
- Assignment board with per-position state (assigned / RESERVE / No Assignment / empty / disabled)
- Siege clone: copy an existing siege as a starting point for the next one
- Post management: create and manage siege posts with per-post condition tracking

**Assignment logic**
- Auto-fill algorithm using Fisher-Yates shuffle respecting `defense_scroll_count`; preview → apply flow (apply commits exactly what was previewed)
- All 16 validation rules (9 errors, 7 warnings) wired into the activation gate
- Attack day algorithm with pinned-member support and configurable 10-member Day 2 threshold
- Assignment comparison view: per-member diffs (added / removed / unchanged) between any two sieges

**Member management**
- Full CRUD for clan members with `defense_scroll_count` tracking
- Bulk assignment endpoints for efficient board population

**Discord bot integration**
- Direct-message notification batches with per-member delivery status tracked in the database
- Channel image posts: assignments PNG and reserves PNG generated from headless HTML/CSS via Playwright
- HTTP sidecar (port 8001) on the bot process; backend communicates exclusively through this API

**Image generation**
- Playwright headless HTML/CSS → PNG pipeline for assignments and reserves boards
- Images posted to a dedicated Discord channel; CDN links stored for retrieval

**Historical data**
- Excel import: one-time CLI script (openpyxl) ingests historical `.xlsm` siege files as completed siege records

**Frontend**
- React 18 + TypeScript single-page app with React Router v6 and React Query
- shadcn/ui component library with Tailwind CSS
- Member CRUD, siege management, assignment board, auto-fill UI, validation panel
- Comparison view (side-by-side diff between two sieges)
- DM notification panel with real-time delivery status polling
- Image preview and download from the assignment board

### Security

- Discord OAuth2 authentication flow: `/api/auth/login` → OAuth2 callback → JWT session cookie (HS256, 24-hour expiry)
- Guild membership enforced via bot `get_member()` lookup — non-members redirected to `/login?error=unauthorized`
- Role-based access gating: user must hold the Discord role named by `DISCORD_REQUIRED_ROLE` (default: `Clan Deputies`; exact, case-sensitive; configurable by self-hosters)
- `get_current_user` FastAPI dependency applied globally; auth bypass allowlist for `/api/auth/*`, `/api/health`, and `/api/version`
- `auth_disabled` flag available for local development without a Discord application
- Frontend `AuthContext` + `RequireAuth` wrapper guards all protected routes; 401 interceptor redirects to `/login`

### Infrastructure

- Azure Bicep IaC: full module set for ACR, Log Analytics, App Insights, PostgreSQL Flexible Server, Key Vault, and Container Apps; separate dev and prod parameter files
- Custom domain via Cloudflare Origin Cert: cert stored as PFX in Key Vault; user-assigned managed identity grants Container Apps environment `Key Vault Secrets User`; two-phase `enableCustomDomain` deploy gate prevents failure before cert upload
- `deploy.yml` CD pipeline: push to `main` auto-deploys to dev; `v*` tag push deploys to prod; builds Docker images, pushes to ACR, updates Container App revisions
- `infra-deploy.yml` manual pipeline: `workflow_dispatch`-only; runs `az deployment group create` for Bicep changes
- `RUNBOOK.md`: restart procedures, rollback, DB restore, secret rotation, log queries, and incident playbooks

### Developer Experience

- `docker-compose` local stack: postgres → backend → frontend → bot in one command
- 3500+ backend tests covering all routes, business logic, and constraint enforcement (pytest + asyncpg test DB)
- Playwright end-to-end tests covering the full siege lifecycle; 22 flaky tests resolved
- Vitest component tests for the assignment board (`BoardPage`) and notification polling (`SiegeSettingsPage`)
- CI on every PR to `main`: black + ruff + pytest (backend); ESLint + build (frontend)

[1.2.0]: https://github.com/glitchwerks/rsl-siege-manager/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/glitchwerks/rsl-siege-manager/compare/v1.0.3...v1.1.0
[1.0.3]: https://github.com/glitchwerks/rsl-siege-manager/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/glitchwerks/rsl-siege-manager/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/glitchwerks/rsl-siege-manager/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/glitchwerks/rsl-siege-manager/releases/tag/v1.0.0
