# Implementation Plan: Raid Shadow Legends Siege Assignment Web App

## Resolved Design Decisions

All open questions from the initial planning session have been resolved. These decisions inform every phase below.

| Decision | Answer |
|---|---|
| Database | Azure Database for PostgreSQL (Flexible Server) |
| Hosting | Azure Container Apps + Azure Container Registry (ACR) |
| CI/CD | GitHub Actions → Docker build → push to ACR → deploy to Container Apps |
| Authentication | Discord OAuth2 with JWT session cookies — guild membership + required Discord role is the authorization boundary (see `docs/superpowers/plans/discord-auth-plan.md`; role configured via `DISCORD_REQUIRED_ROLE`, default `"Clan Deputies"`) |
| Discord Bot | Full rewrite: discord.py + FastAPI HTTP sidecar, deployed as its own container |
| Board API response shape | Nested hierarchy (buildings → groups → positions) |
| Validation Rule 10 (RESERVE balance) | Warn on any slot that is empty, not disabled, and not marked RESERVE |
| Validation Rule 11 (post preference mismatch) | Skip if member has no preferences OR post has no active conditions |
| Attack day — pinned members | Count toward Day 2 threshold when algorithm runs |
| Auto-fill preview/apply | Store preview result in DB; apply commits exactly what was shown |
| Image generation | Playwright (headless HTML/CSS → PNG) |
| Async DM batch tracking | DB job table (NotificationBatch + NotificationBatchResult) |
| UI component library | shadcn/ui + Tailwind CSS |
| Assignment comparison view | Side-by-side table (old siege left, new siege right, per member) |
| Excel import | One-time backend CLI script — no UI or API endpoint |

### BuildingTypeConfig Reference Values

| Building Type | Count | Base Group Count | Base Last Group Slots |
|---|---|---|---|
| Stronghold | 1 | 4 | 3 |
| Mana Shrine | 2 | 2 | 3 |
| Magic Tower | 4 | 1 | 2 |
| Defense Tower | 5 | 1 | 2 |
| Post | 18 | 1 | 1 |

---

## Critical Path

```
Phase 0 (Foundation)
  → Phase 1 (Schema)
    → Phase 2 (Core API)
      → Phase 3 (Board API)
        → Phase 4 (Business Logic)
          → Phase 6 (Frontend Core)
            → Phase 7 (Frontend Discord & Comparison)
              → Phase 9 (Hardening & Launch)

Phase 5 (Discord Bot + Image Generation) ── runs parallel to Phase 6
Phase 8 (Excel Import Script) ── can run any time after Phase 3; defer post-launch if needed
```

---

## Phase 0: Foundation and Environment Setup

**Goal:** Establish the development environment, tooling, repository structure, and infrastructure so every subsequent phase has a clean place to land.

### Steps

1. Create monorepo structure:
   ```
   /backend     — FastAPI application
   /frontend    — React SPA
   /bot         — Discord bot rewrite
   /infra       — Azure infrastructure config (Bicep or Terraform)
   /scripts     — One-off scripts (Excel import, etc.)
   ```
2. Set up Python virtual environment with core dependencies: FastAPI, SQLAlchemy, Alembic, asyncpg, pytest, black, ruff
3. Set up Node environment: React, Vite, TypeScript, React Router v6, React Query, Tailwind CSS, shadcn/ui, ESLint, Prettier
4. Create `.env.example` documenting all required config:
   - `DATABASE_URL`
   - `DISCORD_BOT_API_URL`
   - `DISCORD_BOT_API_KEY`
   - `DISCORD_GUILD_ID`
5. Write `docker-compose.yml` for local development: PostgreSQL, backend, frontend, bot
6. Write Dockerfiles for backend, frontend, and bot
7. Configure Alembic for database migrations
8. Set up GitHub Actions CI pipeline: lint, test, and Docker build checks on every PR
9. Provision Azure resources:
   - Azure Container Registry (ACR)
   - Azure Container Apps environment
   - Azure Database for PostgreSQL Flexible Server (dev instance)
   - Azure Key Vault for secrets
10. Configure Discord OAuth2 app credentials and JWT session cookie settings (see `docs/superpowers/plans/discord-auth-plan.md`)
11. Write a minimal FastAPI app with `GET /health` that confirms DB connectivity
12. Write a minimal React app that fetches and displays the health endpoint
13. Validate the full local stack starts with one command (`docker compose up`)

### Done When
- Developer can clone the repo, run `docker compose up`, and see the health endpoint respond in the browser
- CI passes on a trivial PR
- Azure dev environment is reachable
- All config keys are documented; app fails fast with a clear error if any are missing

---

## Phase 1: Database Schema and Reference Data

**Goal:** Establish the complete, production-ready database schema with all tables, constraints, indexes, and seed data. Every subsequent phase depends on this being correct and stable.

### Steps

1. Alembic migration: `Member` table
2. Alembic migration: `Siege` table (status as PostgreSQL enum)
3. Alembic migration: `Building` table (building_type as PostgreSQL enum)
4. Alembic migration: `BuildingGroup` table
5. Alembic migration: `Position` table (with state consistency check constraints)
6. Alembic migration: `SiegeMember` table
7. Alembic migration: `Post` table
8. Alembic migration: `PostCondition` reference table
9. Alembic migration: `PostActiveCondition` join table
10. Alembic migration: `MemberPostPreference` join table
11. Alembic migration: `BuildingTypeConfig` reference table
12. Alembic migration: `NotificationBatch` table (id, siege_id, status, created_at)
13. Alembic migration: `NotificationBatchResult` table (batch_id, member_id, discord_username, success, error, sent_at)
14. Review all FK cascade behaviors (ON DELETE CASCADE vs. RESTRICT) against design doc intent
15. Seed script: all 36 PostConditions (18 Level 1, 10 Level 2, 8 Level 3) from design doc section 7.3
16. Seed script: BuildingTypeConfig rows using confirmed values above
17. Run full migration and seed against local and dev Azure databases
18. Write schema constraint tests: confirm null violations, enum violations, PK/FK violations, and position state constraints all fire correctly

### Done When
- `alembic upgrade head` runs clean on a fresh database
- All 36 post conditions and all 5 BuildingTypeConfig rows are seeded and queryable
- Constraint tests pass, confirming DB-level enforcement is correct
- A second developer can clone and reach the same schema state from scratch

---

## Phase 2: Core Backend — CRUD and Reference Endpoints

**Goal:** Build the API layer for reference data, member management, and siege/building configuration. These are the stable, non-algorithmic endpoints that all UI work depends on.

### Steps

1. Define base Pydantic v2 response and error envelope schemas (snake_case JSON throughout)
2. Implement DB async session dependency injection (`FastAPI Depends` + asyncpg)
3. Reference endpoints:
   - `GET /api/post-conditions` (with optional `stronghold_level` filter)
   - `GET /api/building-types`
   - `GET /api/member-roles`
4. Member endpoints:
   - `GET /api/members` (with optional `is_active` filter)
   - `POST /api/members`
   - `GET /api/members/{id}`
   - `PUT /api/members/{id}`
   - `DELETE /api/members/{id}` — soft delete (`is_active = false`); remove assignments from planning sieges only
   - `GET /api/members/{id}/preferences`
   - `PUT /api/members/{id}/preferences` — replace full preference set
5. Siege endpoints:
   - `GET /api/sieges` (with optional `status` filter)
   - `POST /api/sieges`
   - `GET /api/sieges/{id}`
   - `PUT /api/sieges/{id}` — metadata only (date, defense_scroll_count)
   - `DELETE /api/sieges/{id}` — planning status only
6. Building endpoints:
   - `GET /api/sieges/{id}/buildings`
   - `POST /api/sieges/{id}/buildings` — auto-creates BuildingGroups and Positions from BuildingTypeConfig
   - `PUT /api/sieges/{id}/buildings/{building_id}` — level, broken status
   - `DELETE /api/sieges/{id}/buildings/{building_id}`
   - `POST /api/sieges/{id}/buildings/{building_id}/groups`
   - `DELETE /api/sieges/{id}/buildings/{building_id}/groups/{group_id}`
7. SiegeMember endpoints:
   - `GET /api/sieges/{id}/members`
   - `PUT /api/sieges/{id}/members/{member_id}` — attack day, reserve status, override flag
8. Write unit tests for all endpoints (happy path + key error cases)
9. Write integration tests against a real test database

### Done When
- All reference, member, siege, and building CRUD endpoints pass tests
- Building creation auto-generates correct BuildingGroup and Position rows per BuildingTypeConfig
- Error responses are consistent and match the defined error envelope
- No business logic (validation, auto-fill, algorithms) is in this phase

---

## Phase 3: Assignment Board Backend

**Goal:** Implement the assignment board read/write endpoints, siege lifecycle transitions, clone logic, and post management. This is the most complex data-mutation phase.

### Steps

1. Implement `GET /api/sieges/{id}/board` — nested response: buildings → groups → positions with member info and position state
2. Implement `PUT /api/sieges/{id}/positions/{position_id}` — assign member, remove, set RESERVE, set No Assignment, clear
3. Implement `POST /api/sieges/{id}/assignments/bulk` — bulk position updates
4. Implement `POST /api/sieges/{id}/activate`:
   - Runs full validation (Phase 4 will implement this — stub it here as always-pass until Phase 4)
   - Transitions status planning → active
   - Freezes building layout
   - Only one active siege permitted at a time
5. Implement `POST /api/sieges/{id}/complete` — transitions active → complete, fully locks siege
6. Implement `POST /api/sieges/{id}/clone`:
   - Deep copy: buildings, groups, positions, member assignments (active members only)
   - Copy post priority and descriptions
   - Copy SiegeMember records (attack day, reserve status) as starting values
   - Clear: post active conditions, assignments for inactive members
   - New siege status always `planning`, date unset
7. Post endpoints:
   - `GET /api/sieges/{id}/posts`
   - `PUT /api/sieges/{id}/posts/{post_id}` — priority, description
   - `PUT /api/sieges/{id}/posts/{post_id}/conditions` — set 0–3 active conditions
8. Write comprehensive tests for clone logic (inactive member clearing, condition clearing, position fidelity)
9. Write tests for lifecycle transition guards (delete blocked on non-planning, activate blocked on validation errors)

### Done When
- Full assignment board is readable and mutable via API
- Lifecycle transitions enforce their rules
- Clone produces a correct deep copy with confirmed clearing behavior
- Post management endpoints work
- Board response shape is stable — frontend can begin work against it

---

## Phase 4: Business Logic — Validation, Auto-Fill, Comparison, and Attack Day

**Goal:** Implement all algorithmic and rule-based features. These are the highest-complexity backend features and represent the core of what replaced the VBA macros.

### Steps

1. **Validation engine** — implement all 16 rules:

   *Errors (block activation):*
   1. All assigned members must be active
   2. No member assigned more than `defense_scroll_count` times
   3. Building numbers within type-specific range
   4. Group numbers 1–10 (level-6 strongholds have 10 groups)
   5. Position numbers 1 to `slot_count`
   6. Attack day must be 1 or 2
   7. Post buildings have exactly 1 group
   8. Position state consistency (disabled/reserve/member exclusivity)
   9. Building count per type matches BuildingTypeConfig

   *Warnings (informational):*

   10. Any slot that is not assigned, not disabled, and not marked RESERVE
   11. Member with preferences assigned to a post where none match active conditions (skip if member has no preferences OR post has no conditions)
   12. Empty positions remain (not assigned, not RESERVE, not disabled)
   13. Assigned members with no attack day set
   14. Fewer than 10 Day 2 attackers
   15. Assigned members with `has_reserve_set = NULL`
   16. Posts with fewer than 3 active conditions configured

2. Implement `POST /api/sieges/{id}/validate`
3. Wire validation into `POST /api/sieges/{id}/activate` — remove stub from Phase 3
4. **Auto-fill algorithm:**
   - Fisher-Yates shuffle of active members
   - Fill empty positions (not disabled, not already assigned, not RESERVE) respecting `defense_scroll_count`
   - Mark remaining unfilled positions as RESERVE
   - Store proposed assignments in DB with a TTL (not applied yet)
5. Implement `POST /api/sieges/{id}/auto-fill` — generate and store preview, return proposed assignments
6. Implement `POST /api/sieges/{id}/auto-fill/apply` — commit the stored preview; return 409 if preview has expired
7. **Assignment comparison:**
   - Build per-member position sets keyed by `(building_type, building_number, group_number, position_number)`
   - Compute added / removed / unchanged per member
   - Exclude RESERVE positions entirely
8. Implement `GET /api/sieges/{id}/compare` — compare with most recent completed siege
9. Implement `GET /api/sieges/{id}/compare/{other_id}` — compare with specific siege
10. **Attack day algorithm:**
    - Lock overridden members (`attack_day_override = TRUE`) — do not change them
    - Seed Day 2 count from overridden members already on Day 2
    - Assign all non-overridden Heavy Hitters and Advanced to Day 2
    - If count < 10: promote top non-overridden Medium members by power descending
    - If still < 10: promote top non-overridden Novice members by power descending
    - Assign remaining non-overridden members to Day 1
11. Implement `POST /api/sieges/{id}/members/auto-assign-attack-day` — preview
12. Implement `POST /api/sieges/{id}/members/auto-assign-attack-day/apply`
13. Write unit tests for all 16 validation rules (pass and fail case per rule)
14. Write unit tests for auto-fill (scroll count never exceeded, RESERVE assigned for leftover slots, preview/apply consistency)
15. Write unit tests for comparison (all three change types, RESERVE exclusion)
16. Write unit tests for attack day algorithm (boundary conditions at the 10-member threshold, pinned member counting)

### Done When
- Validation returns correct structured results for all 16 rules
- Auto-fill never violates scroll count; apply commits exactly the previewed assignments
- Comparison produces correct per-member diffs with RESERVE excluded
- Attack day algorithm meets threshold correctly with pinned members counted
- Every rule and algorithm has unit test coverage

---

## Phase 5: Discord Bot Rewrite and Image Generation

**Goal:** Rewrite the Discord bot as a clean, maintainable service with an HTTP API, and implement server-side PNG image generation. Runs in parallel with Phase 6.

### Steps

**Discord Bot Rewrite:**
1. Create new bot project structure in `/bot`: `discord_client.py`, `http_api.py`, `main.py`
2. Implement `discord.py` client: guild connection, DM sending, channel message/image posting, member listing
3. Implement FastAPI HTTP sidecar with the following endpoints:
   - `POST /api/notify` — send DM to a discord username
   - `POST /api/post-message` — post text to a named channel
   - `POST /api/post-image` — post image file to a named channel (multipart)
   - `GET /api/members` — list guild members (username, nickname, global_name, id)
   - `GET /api/health` — confirm bot is connected
4. Implement shared API key authentication (`Authorization: Bearer <key>`) on all endpoints
5. Run discord.py event loop and FastAPI in the same process using asyncio
6. Write the bot Dockerfile
7. Write integration tests using a Discord Bot mock/stub (no real Discord calls in CI)

**Image Generation:**
8. Spike: generate a sample assignments PNG and reserves PNG using Playwright — evaluate output quality before full implementation
9. Implement HTML/CSS template for the assignments image: building grid, group rows, position cells with member names, color-coded headers by building type
10. Implement HTML/CSS template for the reserves image: member list with attack day, reserve status, defense count
11. Implement Playwright rendering service: renders template with real siege data → screenshots → returns PNG bytes
12. Add Playwright to the backend Dockerfile (install browsers during image build)
13. Implement `POST /api/sieges/{id}/generate-images` — generate and return both images
14. Test image output: valid PNG, correct dimensions, content visually verified

**Web API Integration:**
15. Implement Discord Bot API client in the web API (HTTP client wrapper with auth header, timeout, retry)
16. Implement `POST /api/sieges/{id}/notify`:
    - Enforce status restriction (planning or active only)
    - Create NotificationBatch + NotificationBatchResult rows
    - Trigger FastAPI BackgroundTask to send DMs
    - Return batch_id + pending status
17. Implement BackgroundTask: iterate members, call Discord Bot `/api/notify`, update result rows
18. Implement `GET /api/sieges/{id}/notify/{batch_id}` — read batch + result rows, return status
19. Implement `POST /api/sieges/{id}/post-to-channel`:
    - Generate images (step 13)
    - Post images to `clan-siege-assignment-images` via Discord Bot
    - Post text summary to `clan-siege-assignments` via Discord Bot

### Done When
- Bot rewrite handles DM sending, channel posting, and member listing cleanly
- HTTP API endpoints are tested against a mock Discord client
- DM batch sends work end-to-end in staging against the real bot
- Channel post with images works
- Image output is visually verified by the planner
- CI uses a mock bot — no real Discord calls in automated tests

---

## Phase 6: Frontend — Core UI

**Goal:** Build the React SPA covering member management, siege management, the assignment board, and all primary planner workflows.

### Steps

**Setup:**
1. Configure React Router v6 with route structure:
   - `/members` — member list
   - `/members/:id` — member detail / edit
   - `/sieges` — siege list
   - `/sieges/new` — create siege
   - `/sieges/:id` — siege settings
   - `/sieges/:id/board` — assignment board
   - `/sieges/:id/posts` — post management
   - `/sieges/:id/members` — siege member management (attack day, reserve)
2. Set up typed API client (Axios or fetch wrapper) with base URL from environment
3. Configure React Query for server state

**Members:**
4. Member list page: table with role, power, active status; filter by role/active
5. Member create/edit form: all fields (name, discord_username, role, power, sort_value)
6. Post preferences editor: multi-select from 36 conditions grouped by stronghold level
7. Member deactivate action with confirmation dialog

**Sieges:**
8. Siege list page: table with date, status badges; create button
9. Siege create form: date picker, defense scroll count input
10. Siege settings page: metadata editing, building layout management
11. Building management UI: add/remove buildings by type and number, set level, mark broken/repaired
12. Siege lifecycle controls: Activate button, Complete button, Delete button (planning only), Clone button

**Assignment Board:**
13. Assignment board grid: building columns (color-coded by type), group sections, position cells
14. Position cell states: member name / RESERVE / No Assignment / empty (distinct visual treatment for each)
15. Member selector: searchable dropdown populated from the siege's SiegeMember list
16. Position context actions: assign, mark RESERVE, mark No Assignment, clear
17. Board summary panel: total slots, assigned count, RESERVE count, empty count, disabled count
18. Post preference match indicator: highlight when assigned member's preferences match post conditions
19. Auto-fill controls: Preview button → modal showing proposed assignments → Confirm / Cancel
20. Validation panel: display errors (red) and warnings (yellow) returned from API; run on demand

**Posts and Members:**
21. Post management page: list all 18 posts, set conditions (multi-select up to 3), priority, description
22. Siege member page: table with attack day, reserve status, override toggle; run auto-assign button
23. Write component tests for assignment board cell interactions
24. Write integration tests for member CRUD flow

### Done When
- Planner can complete a full siege workflow end-to-end: create → configure buildings → assign members → validate → activate
- Assignment board renders correctly for a siege with all 30 buildings
- Auto-fill preview and apply work without page reload
- Validation errors and warnings are clearly displayed and actionable
- No direct DB or Discord calls from the frontend

---

## Phase 7: Frontend — Discord and Comparison

**Goal:** Add the Discord-facing UI features and the assignment comparison view.

### Steps

1. Assignment comparison view:
   - Siege selector: choose two sieges to compare (defaults to current vs. most recent completed)
   - Side-by-side table: member rows, old siege positions (left), new siege positions (right)
   - Color-coded cells: added (green), removed (red), unchanged (gray)
   - Summary row: total members changed, total positions added/removed
2. DM notification panel:
   - "Notify Members" button with confirmation
   - Progress indicator while polling batch status
   - Per-member delivery result: green checkmark (sent), red X (failed, with error), yellow warning (no Discord username)
3. Channel post action: "Post to Discord" button with confirmation, success/failure feedback
4. Image preview and download: show generated assignments and reserves images, download buttons
5. Write component tests for notification polling behavior (mock the polling endpoint)

### Done When
- Planner can send DMs and see per-member delivery status update via polling
- Channel post sends images to Discord successfully
- Comparison view shows accurate diffs between two sieges
- Images are previewable and downloadable from the UI

---

## Phase 8: Excel Import Script (One-Time Migration)

**Goal:** Migrate historical `.xlsm` siege files into the database as completed siege records.

**Note:** This is a one-time tool. It can be deferred until after launch without impacting any other feature. The script runs from the command line pointed at the production database.

### Steps

1. Audit 2–3 real `.xlsm` files and document the exact sheet/column structure before writing any code
2. Implement parser using `openpyxl`: read Members, Assignments, Reserves sheets
3. Implement import logic: map parsed rows to Member, Siege, Building, BuildingGroup, Position, SiegeMember records
4. Handle RESERVE cell values: set `is_reserve = TRUE` instead of creating a member
5. Handle known limitations with sensible defaults:
   - Building level: default to 1
   - Broken status: default to false
   - "No Assignment" cell styles: import as empty positions (document this limitation in script output)
6. Script accepts a file path or directory (batch import all files in a folder)
7. Script outputs a summary: records created, members matched/created, warnings, errors
8. Test against real historical files
9. Run migration in staging, verify record counts and spot-check assignments against original Excel files

### Done When
- At least 3 real historical siege files import without errors
- Imported records appear correctly in the assignment board UI
- All known limitations are logged clearly in the script output

---

## Phase 9: Hardening, Testing, and Pre-Launch

**Goal:** Make the application production-ready: end-to-end testing, performance validation, operational tooling, and final deployment.

### Steps

**Testing:**
1. Write end-to-end tests with Playwright covering the full siege lifecycle (create → assign → validate → activate → complete)
2. Performance test the assignment board with a full 30-building siege and 30-member roster — target: board loads in under 2 seconds
3. Validate image generation performance — target: both images generated in under 5 seconds
4. Validate DM batch delivery and failure handling (what happens when the Discord Bot is unreachable — should degrade gracefully with error status on batch results)

**Operations:**
5. Add structured logging throughout the API (request ID, siege_id, member_id on relevant log lines)
6. Configure Azure Application Insights for error monitoring and request tracing
7. Verify Azure Key Vault integration for all secrets (`DATABASE_URL`, `DISCORD_BOT_API_KEY`, etc.)
8. Configure automated PostgreSQL backups (Azure Flexible Server has this built in — confirm it's enabled and retention is set)
9. Write a runbook documenting: how to restart containers, roll back a bad deployment, restore from backup, rotate secrets

**Launch:**
10. Provision production Azure environment (separate from dev)
11. Configure GitHub Actions deployment pipeline: merge to `main` → build images → push to ACR → deploy to production Container Apps
12. Full walkthrough with the siege planner — collect and address any critical feedback before go-live
13. Deploy to production
14. Run smoke tests against production
15. Monitor error rates and logs for 48 hours post-launch

### Done When
- End-to-end tests pass in CI against staging
- Planner has signed off on the full workflow
- Production is live, monitored via Application Insights, and backed up
- Runbook exists and has been validated

---

## Containers Summary

Three containers, each deployed as a separate Azure Container App:

| Container | Contents | Exposes |
|---|---|---|
| `siege-api` | FastAPI backend, Playwright (for image gen) | Port 8000 (internal) |
| `siege-frontend` | React SPA served by Nginx | Port 80 (public) |
| `siege-bot` | discord.py + FastAPI HTTP sidecar | Port 8001 (internal only) |

The frontend container's Nginx config proxies `/api/*` to the `siege-api` container. The bot container is only reachable from `siege-api` — it is not exposed publicly.

---

## Tech Stack Summary

| Layer | Technology |
|---|---|
| Backend API | Python 3.12, FastAPI, SQLAlchemy (async), Alembic, asyncpg |
| Frontend | React 18, TypeScript, Vite, React Router v6, React Query, shadcn/ui, Tailwind CSS |
| Discord Bot | Python 3.12, discord.py, FastAPI |
| Database | Azure Database for PostgreSQL Flexible Server |
| Image Generation | Playwright (headless Chromium) |
| Hosting | Azure Container Apps |
| Container Registry | Azure Container Registry (ACR) |
| Authentication | Discord OAuth2 with JWT session cookies |
| Secrets | Azure Key Vault |
| Monitoring | Azure Application Insights |
| CI/CD | GitHub Actions |
