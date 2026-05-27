# Web Design Document: Raid Shadow Legends Siege Assignment System

## 1. System Overview

### 1.1 Purpose

This document specifies the web-based version of the Siege Assignment System for the Raid Shadow Legends clan "Masters of Magicka" (1MOM). It replaces the current workflow — Excel workbooks with VBA macros, Python CLI tools, and manual file management — with a unified web application backed by a relational database.

The current system is documented in `DESIGN_DOCUMENT.md`. This document restructures that functionality into a modern web architecture while preserving all existing capabilities and fixing known limitations.

### 1.2 What This Replaces

| Current Component | Replaced By |
|---|---|
| Excel workbooks (`.xlsm`) | Web UI assignment board + database |
| VBA macros (Validate, FillEmpties, etc.) | Server-side API logic |
| Python CLI (`run_siege`, `assignments`, etc.) | Web UI actions + API endpoints |
| File-based siege management (naming, discovery) | Database records with status tracking |
| `member_discord_map.json` | Database `Member` table with `discord_username` column |
| Manual Excel image export | Server-generated PNG images from assignment data (section 4.10) |
| Hardcoded root path (`E:\My Files\...`) | Cloud-hosted database |

### 1.3 What This Does NOT Replace

| Component | Status |
|---|---|
| **Clan Reminders** (Hydra/Chimera) | Excluded — separate microservice, separate project |
| **Discord Bot** | Remains a separate standalone service. The web app communicates with it via API. |

### 1.4 High-Level Architecture

```
┌──────────────┐       ┌──────────────┐       ┌──────────────┐
│              │       │              │       │              │
│   Web UI     │──────▶│   Web API    │──────▶│   Database   │
│   (SPA)      │◀──────│   (REST)     │◀──────│   (SQL)      │
│              │       │              │       │              │
└──────────────┘       └──────┬───────┘       └──────────────┘
                              │
                              │ HTTP
                              ▼
                       ┌──────────────┐
                       │  Discord Bot │
                       │  Service     │
                       │  (HTTP API)  │
                       └──────────────┘
```

- **Web UI** — Single-page application (SPA). This is a technical architecture term meaning the browser loads the app once and navigates between multiple views (assignment board, member list, siege settings, etc.) without full page reloads. It is not literally a single page — it has distinct screens for each functional area.
- **Web API** — RESTful backend. System of record. All business logic, validation, and data access runs here.
- **Database** — Relational SQL database. Stores all siege, member, assignment, and reference data.
- **Discord Bot Service** — Existing `discord.py` bot extended with a small HTTP API layer. The web API sends requests to it to deliver DMs, post to channels, and resolve Discord usernames.

### 1.5 User Model

The system supports a single user role: **Siege Planner**.

**Authentication mechanism:** Discord OAuth2 (Authorization Code Grant, `identify` scope). Users are redirected to Discord to authorize the app, and the backend exchanges the authorization code for an access token, then verifies guild membership via the bot sidecar. Only Discord users who are members of the configured guild **and** exist as a `Member` row in the database are permitted to sign in — unrecognized users receive an authorization error.

**Session:** A signed HS256 JWT is issued in an HttpOnly, Secure, SameSite=Lax cookie with a 24-hour TTL. The frontend wraps all protected routes in a `RequireAuth` guard that redirects unauthenticated requests to `/login`.

**Dev bypass:** Set `AUTH_DISABLED=true` in the local environment to skip OAuth for development. This flag is enforced at startup to prevent it from being set in non-development environments.

**Canonical spec:** See [`docs/superpowers/plans/discord-auth-plan.md`](./superpowers/plans/discord-auth-plan.md) for the full authentication specification.

**Deferred post-v1.0:** Role-based access control (Leader/Officer gating) is explicitly out of scope for v1.0. The `Member` model already carries a `role` field for future use. The architecture does not preclude adding member-facing access later (e.g., members viewing their own assignments), but this is not designed here.

---

## 2. Architecture

### 2.1 Component Responsibilities

#### Web UI (Front-End)

- Renders the assignment board, member list, post configuration, and validation results
- Sends all actions to the Web API — no direct database or Discord access
- Maintains UI state (current siege, selected building, drag targets) client-side
- Receives data exclusively through the Web API

#### Web API (Back-End)

- Sole point of data access — all reads and writes go through the API
- Implements all business logic: validation, auto-fill, comparison, changeset generation
- Generates Discord message content (formatted text) and sends it to the Discord Bot API for delivery
- Manages siege lifecycle (planning → active → complete)
- Serves reference data (building types, post conditions, roles)

#### Database

- Stores normalized relational data (see section 3)
- No application logic — the database is a persistence layer only
- Seeded with reference data (post conditions, building type enums) on initial deployment

#### Discord Bot Service

- Runs independently of the web application
- Exposes a small HTTP API for the web app to call
- Handles Discord authentication, rate limiting, and message delivery
- Does not access the database directly — receives all content from the web API

### 2.2 Web API Configuration

The Web API requires the following environment configuration:

| Setting | Description |
|---|---|
| `DATABASE_URL` | Connection string for the SQL database |
| `DISCORD_BOT_API_URL` | Base URL of the Discord Bot HTTP API — see [bot-seam.md § URL resolution](bot-seam.md#2-url-resolution) |
| `DISCORD_BOT_API_KEY` | Shared API key for authenticating with the Discord Bot — see [bot-seam.md § Auth](bot-seam.md#3-auth) |
| `DISCORD_GUILD_ID` | The Discord guild ID to use for all Discord operations |

### 2.3 Communication Patterns

| From | To | Protocol | Purpose |
|---|---|---|---|
| Web UI | Web API | HTTPS (REST) | All user actions, data retrieval |
| Web API | Database | SQL (connection pool) | Data persistence and queries |
| Web API | Discord Bot | HTTP (internal API) | Send DMs, post messages/images, list members |
| Discord Bot | Discord | WebSocket (discord.py) | Message delivery, guild queries |

### 2.4 Data Flow Example: Notify Members

1. Planner clicks "Notify Members" in the Web UI
2. Web UI sends `POST /api/sieges/{id}/notify` to the Web API
3. Web API:
   a. Loads the current siege and the previous siege from the database
   b. Computes per-member changesets (added/removed/unchanged positions)
   c. Formats Discord DM content for each member (same format as current system — see section 4.7)
   d. Sends each message to the Discord Bot API via `POST /api/notify`
   e. Collects delivery results (success/failure per member)
4. Web API returns notification results to the Web UI
5. Web UI displays success/failure status for each member

---

## 3. Data Model (Database Schema)

This schema normalizes the flat Excel structure into relational tables. All IDs are auto-generated primary keys (integer or UUID — implementation choice).

### 3.1 Siege

Represents a single siege event (replaces an Excel workbook file).

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | PK | Auto-generated | |
| `date` | DATE | NOT NULL | Siege date (replaces filename date extraction) |
| `status` | ENUM | `planning`, `active`, `complete` | Lifecycle state |
| `defense_scroll_count` | INT | NOT NULL, > 0 | Max times a single member can be assigned (replaces cell Q3) |
| `total_defense_slots` | INT | COMPUTED | Sum of `slot_count` across all `BuildingGroup` records for this siege's buildings. Calculated at query time, not stored. |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT NOW | |
| `updated_at` | TIMESTAMP | NOT NULL | |

**Status lifecycle:** `planning` → `active` → `complete`
- `planning` — Siege is being configured. Buildings, groups, positions, and assignments can be freely edited. Notifications can be sent (e.g., to give members advance notice of assignments).
- `active` — Siege is live and locked. Building layout is frozen; only member assignments, post conditions, and SiegeMember data can be edited. Notifications can be sent.
- `complete` — Siege is finished. Fully locked and read-only. No notifications can be sent. Historical reference.

### 3.2 Building

Represents a building instance on the siege map.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | PK | Auto-generated | |
| `siege_id` | FK → Siege | NOT NULL, ON DELETE CASCADE | |
| `building_type` | ENUM | See section 7.1 | Stronghold, Mana Shrine, Magic Tower, Defense Tower, Post |
| `building_number` | INT | 1-18, NOT NULL | Instance number on the map |
| `level` | INT | >= 1, NOT NULL, DEFAULT 1 | Current upgrade level (affects available groups) |
| `is_broken` | BOOL | NOT NULL, DEFAULT FALSE | Broken buildings revert to base form |

**Unique constraint:** `(siege_id, building_type, building_number)`

**Building count constraints per type:**

| Building Type | Fixed Count | Building Numbers |
|---|---|---|
| Stronghold | 1 | 1 |
| Mana Shrine | 2 | 1-2 |
| Magic Tower | 4 | 1-4 |
| Defense Tower | 5 | 1-5 |
| Post | 18 | 1-18 |
| **Total** | **30** | |

These counts are fixed by the game. The application enforces them — a siege cannot have more than the allowed number of buildings per type. When creating a new siege or cloning, the system should default to the full set of 30 buildings.

### 3.3 BuildingGroup

Represents an attack group within a building.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | PK | Auto-generated | |
| `building_id` | FK → Building | NOT NULL, ON DELETE CASCADE | |
| `group_number` | INT | 1-9, NOT NULL | Group within the building |
| `slot_count` | INT | 1-3, NOT NULL, DEFAULT 3 | Number of position slots in this group |

**Unique constraint:** `(building_id, group_number)`

**Slot count rule:** All groups except the last group in a building have 3 slots. The last group's slot count is variable — it depends on the building's level, but the exact formula is not fully documented. The planner configures this value when setting up or adjusting a building. The system defaults to 3 and allows manual override.

```
if group_number < total_groups_in_building:
    slot_count = 3
else:
    slot_count = configured value (1-3, defaults based on building level)
```

**Note on Posts:** Post buildings always have exactly 1 group. The application enforces this constraint.

### 3.4 Position

Represents a single defense slot within a group (replaces a cell in columns C-E of the Assignments sheet).

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | PK | Auto-generated | |
| `building_group_id` | FK → BuildingGroup | NOT NULL, ON DELETE CASCADE | |
| `position_number` | INT | 1 to `building_group.slot_count`, NOT NULL | Slot within the group |
| `member_id` | FK → Member | NULLABLE | Assigned member (NULL = empty) |
| `is_reserve` | BOOL | NOT NULL, DEFAULT FALSE | TRUE = game auto-fills this slot (see section 3.10) |
| `is_disabled` | BOOL | NOT NULL, DEFAULT FALSE | TRUE = "No Assignment" — slot cannot be filled |

**Unique constraint:** `(building_group_id, position_number)`

**State rules:**
- `is_disabled = TRUE` → `member_id` must be NULL and `is_reserve` must be FALSE
- `is_reserve = TRUE` → `member_id` must be NULL
- `member_id IS NOT NULL` → `is_reserve` must be FALSE

### 3.5 Member

Represents a clan member (replaces a row in the Members sheet).

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | PK | Auto-generated | |
| `name` | VARCHAR | NOT NULL, UNIQUE | In-game name |
| `discord_username` | VARCHAR | NULLABLE | Discord username (replaces `member_discord_map.json`) |
| `role` | ENUM | See section 7.2 | Heavy Hitter, Advanced, Medium, Novice |
| `power` | DECIMAL | NULLABLE | Player power value (used for ranking) |
| `sort_value` | INT | NULLABLE | Numeric sort value (used for ranking) |
| `is_active` | BOOL | NOT NULL, DEFAULT TRUE | Inactive members are excluded from assignment |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT NOW | |

### 3.6 SiegeMember

Per-siege participation data for each member (replaces the Reserves sheet in Excel). Attack day and reserve status are **per-siege attributes**, not global member properties — each siege generates fresh values.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `siege_id` | FK → Siege | NOT NULL, ON DELETE CASCADE | |
| `member_id` | FK → Member | NOT NULL, ON DELETE CASCADE | |
| `attack_day` | INT | 1 or 2, NULLABLE | Which day the member attacks (NULL = not yet assigned) |
| `has_reserve_set` | BOOL | NULLABLE | Whether member has configured in-game reserve defense (NULL = unknown) |
| `attack_day_override` | BOOL | NOT NULL, DEFAULT FALSE | TRUE = planner manually set attack day; FALSE = auto-assigned by algorithm |

**Primary key:** `(siege_id, member_id)`

**Auto-population:** When a siege is created or cloned, a `SiegeMember` record is created for every active member. The attack day assignment algorithm (section 4.2) runs to populate default `attack_day` values. The planner can then override individual values.

**DM format mapping:**
- `has_reserve_set = TRUE` → "Yes"
- `has_reserve_set = FALSE` → "No"
- `has_reserve_set = NULL` → "Unknown"
- `attack_day = NULL` → "Unknown"

### 3.7 BuildingTypeConfig

Reference table defining the base configuration for each building type. Used to initialize buildings when creating a siege and to determine what a building reverts to when broken.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | PK | Auto-generated | |
| `building_type` | ENUM | UNIQUE, NOT NULL | The building type |
| `count` | INT | NOT NULL | Fixed number of this building type (game rule) |
| `base_group_count` | INT | NOT NULL | Number of groups at level 1 (base form) |
| `base_last_group_slots` | INT | 1-3, NOT NULL | Slot count for the last group at level 1 |

Seeded on deployment:

| Building Type | Count | Base Groups | Base Last Group Slots |
|---|---|---|---|
| Stronghold | 1 | TBD | TBD |
| Mana Shrine | 2 | TBD | TBD |
| Magic Tower | 4 | TBD | TBD |
| Defense Tower | 5 | TBD | TBD |
| Post | 18 | 1 | 1 |

**Note:** The exact base group counts and last-group slot values for non-Post building types need to be determined from in-game data. Post is known: always 1 group with 1 slot (matching GAPS.md: "Posts can only ever have 1 group/assignment").

This table is used when:
- **Creating a siege** — Each building is initialized with its base configuration
- **Breaking a building** — Building reverts to this base configuration
- **Validating** — Building counts per type must match the `count` values

### 3.8 MemberPostPreference

Join table linking members to their preferred post conditions. These are conditions the member is well-suited to defend — the planner uses this to prioritize placement when a post's random conditions match a member's strengths.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `member_id` | FK → Member | NOT NULL, ON DELETE CASCADE | |
| `post_condition_id` | FK → PostCondition | NOT NULL, ON DELETE CASCADE | |

**Primary key:** `(member_id, post_condition_id)`

This replaces the comma-separated `PostRestrictions` column E in the Members sheet with a proper many-to-many relationship. Note the semantic change: these are **preferences**, not hard restrictions. A member can be assigned to any post regardless of their preferences — the system uses preferences to guide the planner, not to block assignments.

### 3.9 PostCondition

Reference table of all known post conditions (seeded from `post_condition_list.csv`).

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | PK | Auto-generated | |
| `description` | VARCHAR | NOT NULL, UNIQUE | Full condition text |
| `stronghold_level` | INT | 1, 2, or 3 | Which Stronghold level tier this condition belongs to |

Seeded with 36 conditions (18 Level 1 + 10 Level 2 + 8 Level 3). See section 7.3 for the full list.

### 3.10 Post

Represents a post building's per-siege configuration (priority and active conditions).

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | PK | Auto-generated | |
| `siege_id` | FK → Siege | NOT NULL, ON DELETE CASCADE | |
| `building_id` | FK → Building | NOT NULL | Must reference a building with `building_type = 'Post'` |
| `priority` | INT | NOT NULL, DEFAULT 0 | Planner-assigned importance ranking (higher = more important) |
| `description` | VARCHAR | NULLABLE | Optional text note explaining why this post is prioritized or other planner context |

**Unique constraint:** `(siege_id, building_id)`

**UNIQUE constraint on `building_id`:** Each building can have at most one Post record per siege. Since the unique constraint on `(siege_id, building_id)` already covers this, no additional constraint is needed.

**Auto-creation:** When a Building with `building_type = 'Post'` is created, the system automatically creates a corresponding `Post` record with `priority = 0` and no description. The planner then configures priority and conditions through the Post endpoints.

### 3.11 PostActiveCondition

Join table for the 3 random conditions assigned to each post each siege.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `post_id` | FK → Post | NOT NULL, ON DELETE CASCADE | |
| `post_condition_id` | FK → PostCondition | NOT NULL, ON DELETE CASCADE | |

**Primary key:** `(post_id, post_condition_id)`

Each post can have **0 to 3** active conditions per siege. Not all posts are important enough to warrant entering conditions — the planner may leave some posts blank. Validation produces a warning (not an error) if a post has fewer than 3 conditions.

### 3.12 RESERVE — Special Position State

In the current Excel system, `"RESERVE"` is a text value written into assignment cells. In the web system, RESERVE is modeled as a boolean flag on the Position record:

- `position.is_reserve = TRUE` means "let the game auto-fill this slot using members' in-game reserve defense teams"
- A RESERVE position has no assigned member (`member_id = NULL`)
- RESERVE is **not a member** — it must not appear in changesets, DM notifications, or member lookups
- The auto-fill feature (section 4.6) sets remaining empty positions to `is_reserve = TRUE` after all members have been assigned

This fixes the current bug where Python treats `"RESERVE"` as a member name (see `DESIGN_DOCUMENT.md` section 12.1, item 1).

### 3.13 Entity Relationship Summary

```
Siege ──1:N──▶ Building ──1:N──▶ BuildingGroup ──1:N──▶ Position ──N:1──▶ Member
  │                                                                          │
  ├──1:N──▶ SiegeMember ──N:1─────────────────────────────────────────────────┤
  │                                                                          │
  └──1:N──▶ Post ──M:N──▶ PostCondition ◀──M:N── MemberPostPreference ◀──────┘

BuildingTypeConfig (reference, standalone)
```

---

## 4. Feature Catalog

### 4.1 Siege Management

**Replaces:** File discovery, Excel workbook creation, manual file naming

#### Create New Siege
- Planner creates a new siege by specifying a date
- A new `Siege` record is created with status `planning`
- Building layout can be configured (see below) or cloned from a previous siege as a starting point

#### Configure Building Layout
- Add/remove buildings with type and building number (constrained to fixed counts per type — see section 3.2)
- Add/remove groups within buildings
- Positions are auto-created when a group is added: 3 slots for all groups except the last, which has a configurable slot count (see section 3.3)
- Set building level (affects how many groups are available and the last group's default slot count)

#### Building State Management

Building level determines available groups. The `BuildingTypeConfig` reference table (section 3.7) defines the base configuration for each type.

- **Upgrade** — Increase building level. Add groups/positions according to the new level's configuration.
- **Break** — Set `is_broken = TRUE`. The building reverts to its base form as defined by `BuildingTypeConfig` (base group count and base last-group slots). Groups beyond the base configuration are deleted, along with their positions. If deleted positions had member assignments, those assignments are removed (the planner is shown which members were affected).
- **Repair** — Clear `is_broken`. The building returns to its current `level` configuration. The planner must manually re-add groups and reassign members to the restored positions.
- **Set Level** — Change building level directly. This is equivalent to setting the building's group/slot configuration to match a predefined level template. Used when the planner knows the current in-game state and wants to configure the building to match.

**Active siege lock:** Once a siege transitions to `active`, its building layout is frozen. No buildings, groups, or positions can be added, removed, broken, or repaired. This matches the in-game behavior where siege state is locked once it starts.

#### Siege Lifecycle

```
planning → active → complete
    │
    └→ deleted (planning only)
```

- **Planning → Active** — Marks siege as the current active siege. Validation is run automatically before activation (warnings allowed, errors block). Only one siege can be `active` at a time. **Once active, the siege is locked:** building layout, group configuration, and position structure cannot be modified. Only member assignments, post conditions, and SiegeMember data (attack day, reserve status) can be edited. Notifications can still be sent.
- **Active → Complete** — Marks the siege as finished. Fully locked and read-only. No further notifications can be sent.
- **Planning → Deleted** — Permanently removes a planning siege. Only planning-status sieges can be deleted.
- **View Historical** — Browse and view completed sieges (read-only)

Multiple planning sieges may exist simultaneously. Only one siege can be active at a time — the active siege must be completed before another can be activated.

**Notification availability by status:**

| Status | Send DMs | Post to Channel | Edit Assignments | Edit Buildings |
|---|---|---|---|---|
| Planning | Yes | Yes | Yes | Yes |
| Active | Yes | Yes | Limited (assignments, post conditions, SiegeMember only) | No |
| Complete | No | No | No | No |

#### Clone Siege
Creates a new planning siege by deep-copying from an existing siege (typically the most recently completed one).

**What is copied:**
- Buildings, groups, and positions (full layout deep copy)
- Member assignments — all **active** members retain their prior positions
- Post priority values and descriptions
- SiegeMember records (attack day, reserve status) — copied as starting values for the new siege

**What is cleared or dropped:**
- Post active conditions — cleared (the game assigns new random conditions each siege)
- Positions assigned to **inactive** members — assignment is removed (position becomes empty)
- Siege date — planner must set the new date
- Siege status — always `planning`

**Post-clone workflow:** The planner reviews the cloned siege, adjusts building levels for any in-game upgrades, reassigns positions vacated by inactive members, enters new post conditions once the in-game siege starts, and re-runs the attack day algorithm.

### 4.2 Member Management

**Replaces:** Members sheet in Excel, `member_discord_map.json`

#### CRUD Operations
- **Create** — Add a new member: name, discord_username, role, power, sort_value
- **Read** — View member list with all attributes and their current assignment count
- **Update** — Edit any member field. Changing a name updates all references.
- **Delete** — Soft-delete via `is_active = FALSE`. Assignments in **planning** sieges are automatically removed (positions cleared). Assignments in active and complete sieges are preserved for historical accuracy. A deactivated member can be reactivated by setting `is_active = TRUE`.

#### Post Preferences
- Manage which post conditions a member is well-suited to defend via the `MemberPostPreference` join table
- Select from the seeded `PostCondition` reference list
- Preferences are **advisory, not restrictive** — a member can be assigned to any post regardless of their preferences
- The system highlights when a member's preferences match a post's active conditions, helping the planner prioritize placement
- Used by validation (section 4.5) to produce informational warnings when a member is assigned to a post where none of their preferences match

#### Attack Day Assignment
Attack day assignment uses a tiered system with a minimum threshold for Day 2 attackers.

**Role-based defaults:**

| Role | Default Attack Day | Priority Tier |
|---|---|---|
| Heavy Hitter | 2 | 1 (highest) |
| Advanced | 2 | 2 |
| Medium | 1 | 3 |
| Novice | 1 | 4 (lowest) |

**Minimum Day 2 threshold:** The system requires at least **10 Day 2 attackers**. If the number of Heavy Hitter + Advanced members is fewer than 10, the system automatically promotes the highest-power members from the next tier (Medium, then Novice if still needed) to Day 2 until the threshold is met.

**Algorithm:**
1. Assign all Heavy Hitters and Advanced members to Day 2
2. Count Day 2 members. If count >= 10, assign remaining members to Day 1.
3. If count < 10, sort Medium members by `power` descending
4. Promote top Medium members to Day 2 until count reaches 10
5. If still < 10 after all Medium members, promote top Novice members by `power` descending
6. Remaining members are assigned to Day 1

The planner can override any automatic assignment per member per siege.

#### Member Ranking
Members can be sorted/ranked by `sort_value` (primary, descending) then `power` (secondary, descending). This replaces the VBA `SelectTopNCells` macro and is used by the planner to decide assignment priority.

### 4.3 Assignment Board

**Replaces:** Assignments sheet in Excel — this is the core UI of the application

The assignment board is a visual grid that displays all buildings, groups, and positions for the active siege. The planner assigns members to positions here.

#### Layout
- Buildings are organized by type, each showing its building number
- Within each building, groups are displayed with their group number
- Each group shows its position slots (3 for most groups; the last group in a building may have fewer — see section 3.3)
- Color-coded by building type (matching Discord notification colors):

| Building Type | Color |
|---|---|
| Stronghold | Red |
| Defense Tower | Green |
| Mana Shrine | Yellow |
| Magic Tower | Blue |
| Post | White/Gray |

#### Assignment Actions
- **Assign member** — Select a member from a dropdown or drag-and-drop into a position slot
- **Remove assignment** — Clear a position (set `member_id = NULL`)
- **Mark as RESERVE** — Set `is_reserve = TRUE` (position shows "RESERVE" label instead of a member name)
- **Mark as No Assignment** — Set `is_disabled = TRUE` (position is grayed out and cannot be filled). Replaces the Excel "No Assignment" cell style.
- **Clear RESERVE / No Assignment** — Reset position to empty (available for assignment)

#### Display Information
- Each position slot shows: the assigned member name, "RESERVE", "No Assignment", or empty
- Building header shows: building type, building number, level, broken status
- Group header shows: group number
- Summary panel shows: total positions, assigned count, RESERVE count, empty count, disabled count

#### Assignment Constraints and Guidance
- A member cannot be assigned more times than `defense_scroll_count` across all positions in a siege
- When assigning a member to a post, the UI should highlight whether the member's preferred post conditions match the post's active conditions
- The UI should show an informational indicator if a member is being assigned to a post where none of their preferences match — this is guidance, not a block

### 4.4 Post Management

**Replaces:** Manual post tracking (currently not in any code — data gap in the original system)

#### Per-Siege Post Configuration
- For each post building in the siege, configure 3 active conditions from the post condition pool
- The game assigns these randomly; the planner enters them into the system after each siege starts

#### Post Priority
- Set a priority value for each post (integer, higher = more important)
- Optionally add a text description explaining the priority rationale or other planner notes
- Used by the planner to decide which posts to defend with stronger members
- Displayed on the assignment board next to post buildings

#### Post Preference Matching
- When a member is assigned to a post, the system checks whether the member's preferred post conditions overlap with the post's active conditions
- Matches are highlighted as positive indicators; non-matches produce informational warnings
- This is advisory — the planner makes the final decision
- See validation rules in section 8

### 4.5 Validation

**Replaces:** VBA `Validate` macro + Python data validation in `Position.__init__` / `SiegeAssignment.__init__`

Validation runs server-side via the Web API. It can be triggered:
- **On demand** — Planner clicks a "Validate" button
- **On siege activation** — Automatically runs when transitioning from `planning` to `active`

Results are returned to the UI and displayed inline (not on a separate sheet like the Excel Validation sheet).

#### Validation Checks

See section 8 for the full canonical list. Summary:

**Errors (block activation):** All assigned members must be active (#1), no over-assignment (#2), valid building/group/position numbers (#3-5), valid attack days (#6), post group constraints (#7), position state consistency (#8), building count limits (#9).

**Warnings (informational):** RESERVE balance (#10), post preference mismatch (#11), all positions filled (#12), attack day assigned (#13), Day 2 threshold (#14), reserve status set (#15), post conditions incomplete (#16).

#### Validation Response Format

```json
{
  "valid": true,
  "errors": [],
  "warnings": [
    {
      "check": "reserve_balance",
      "message": "Expected 12 RESERVE positions but found 10",
      "details": { "expected": 12, "actual": 10 }
    }
  ]
}
```

- **Errors** block siege activation
- **Warnings** are informational — the planner can proceed

### 4.6 Auto-Fill

**Replaces:** VBA `FillEmpties` macro

Automatically fills empty positions with members, then marks remaining positions as RESERVE.

#### Algorithm

1. Load all active members for the siege
2. Shuffle the member list (Fisher-Yates, matching current VBA behavior) for randomized assignment
3. Count existing assignments per member (positions where `member_id IS NOT NULL`)
4. For each empty position (where `member_id IS NULL`, `is_reserve = FALSE`, `is_disabled = FALSE`):
   - Assign the next member who has been assigned fewer than `defense_scroll_count` times
   - If all members are at their limit, set `is_reserve = TRUE`
5. Return the proposed assignments as a preview

#### Key Behaviors
- **Respects disabled positions** — Positions with `is_disabled = TRUE` are never touched
- **Preserves existing assignments** — Pre-assigned positions are counted but not overwritten
- **Preview before applying** — The API returns proposed changes; the planner confirms before they are saved
- **Randomization** — Each run produces a different distribution
- **Defense scroll limit** — Uses the siege's `defense_scroll_count` (replaces the hardcoded max-2 in VBA)

### 4.7 Assignment Comparison

**Replaces:** Python comparison engine (`compare_assignment_changes` in `excel.py`, `build_changeset` in `siege_utils.py`)

Compares the current siege's assignments against the previous siege to produce per-member changesets.

#### Algorithm

1. Load positions from the current siege and the most recent completed siege
2. Build per-member position sets for both sieges, keyed by `(building_type, building_number, group_number, position_number)`
3. For each member across both sets:
   - **Removed** = positions in previous siege but not in current
   - **Added** = positions in current siege but not in previous
   - **Unchanged** = positions present in both with the same member
4. **Exclude RESERVE** — Positions marked `is_reserve = TRUE` are excluded from changesets entirely. This fixes the current bug.

#### Changeset Structure

```json
{
  "member_name": {
    "added": [
      { "building": "Defense Tower", "building_number": 3, "group": 2, "position": 1 }
    ],
    "removed": [
      { "building": "Mana Shrine", "building_number": 2, "group": 1, "position": 2 }
    ],
    "unchanged": [
      { "building": "Magic Tower", "building_number": 1, "group": 4, "position": 3 }
    ]
  }
}
```

#### Visual Diff View

The UI displays the comparison results:
- Per-member expandable sections showing added (green), removed (red), and unchanged (gray) positions
- Summary counts: how many members changed, total positions added/removed
- Option to compare any two sieges (not just the most recent pair)

### 4.8 Discord Notifications

**Replaces:** `run_siege --send-dm --post-message` CLI workflow

#### Notify Members (DMs)

1. Planner clicks "Notify Members" in the UI
2. Web UI sends `POST /api/sieges/{id}/notify` to the Web API
3. Web API computes changesets (section 4.7), formats all DM content, and submits messages to the Discord Bot API
4. Web API returns immediately with a `batch_id` and `status: "pending"`
5. Web UI polls `GET /api/sieges/{id}/notify/{batchId}` for progress
6. Discord Bot delivers DMs asynchronously (with its own rate limiting)
7. Once all DMs are sent (or failed), the batch status transitions to `"completed"` with per-member results

This asynchronous pattern prevents HTTP timeouts when sending DMs to 30+ members.

**Status restriction:** Notifications can only be sent when the siege is in `planning` or `active` status. Attempting to notify on a `complete` siege returns a 400 error.

#### Post to Channel

1. Planner clicks "Post Assignments" in the UI
2. Web API generates assignment and reserves PNG images (section 4.9)
3. Web API uploads images to Discord via `POST /api/post-image` → `clan-siege-assignment-images` channel
4. Web API generates the assignment summary text with siege date header and image URLs
5. Web API posts the summary to Discord via `POST /api/post-message` → `clan-siege-assignments` channel

#### 4.8.1 DM Message Format

Preserved from the current system:

```
⚠️ **This bot is a work in progress. Please verify assignments manually if needed.** ⚠️

**[1MOM] Masters of Magicka Siege Assignment (YYYY-MM-DD)**

**Have Reserve Set:** Yes/No/Unknown
**Attack Day:** 1/2/Unknown

🛡️ ** No Change ** 🛡️
- 🟢 Defense Tower 3 / Group 2 / Pos 1
- 🔵 Magic Tower 1 / Group 4 / Pos 3

❌ ** Remove From ** ❌
- 🟡 Mana Shrine 2 / Group 1 / Pos 2

⚔️ ** Set At ** ⚔️
- 🔴 Stronghold / Group 3 / Pos 1
```

Building color coding:

| Building | Emoji |
|---|---|
| Stronghold | 🔴 |
| Defense Tower | 🟢 |
| Mana Shrine | 🟡 |
| Magic Tower | 🔵 |
| Post | ⚪ |

Position formatting rules:
- Building name + building number (if present)
- Group number (if present, omitted for Post)
- Position number (omitted for Post since posts have only 1 group with 1 effective assignment)
- Parts joined with ` / `

#### Notification Status

The UI displays delivery results:
- Green checkmark — DM sent successfully
- Red X — DM failed (with error message)
- Yellow warning — No Discord username mapped for this member

### 4.9 Assignment Image Generation

**Replaces:** Excel image export via xlwings (`range.to_png()`)

The Web API generates PNG images of the assignment board and reserves table for posting to Discord channels. This replaces the current system's dependency on xlwings COM automation for image export.

#### How It Works

1. The Web API renders the assignment data into a formatted table using a Python table/image generation library (e.g., `matplotlib`, `plotly`, or `pandas` + `dataframe_image`)
2. The table is styled to match the current assignment layout: building names, group numbers, position assignments, color coding by building type
3. The rendered table is saved as a PNG file
4. Two images are generated:
   - **Assignments image** — Full assignment board (buildings, groups, positions with member names)
   - **Reserves image** — Member list with defense count, reserve status, and attack day

#### Usage

- Generated on demand when the planner clicks "Post Assignments" to Discord
- The Web API generates the images, then sends them to the Discord Bot API via `POST /api/post-image`
- Images are also available for download from the UI

#### API Endpoint

`POST /api/sieges/{id}/generate-images` — Generate assignment and reserves PNG images for the siege. Returns the image files or URLs.

### 4.10 Historical Excel Import

**Replaces:** Nothing — this is a one-time migration tool

Supports importing existing `.xlsm` workbook files into the database as completed sieges.

#### Import Process

1. Planner uploads a `.xlsm` file through the Web UI
2. Web API extracts the date from the filename using the existing pattern: `clan_siege_(\d{2})_(\d{2})_(\d{4})`
3. Web API parses the workbook sheets:
   - **Members sheet** — Imports members (creates if not exists, updates if name matches)
   - **Assignments sheet** — Reads building/group/position assignments from `A1:E100`
   - **Reserves sheet** — Reads attack day and reserve status from `A1:D{member_count}`
4. Creates a new `Siege` record with status `complete`, populates all related tables
5. Handles RESERVE values correctly — sets `is_reserve = TRUE` on those positions instead of creating a member

#### Limitations

- Requires server-side Excel parsing library (e.g., `openpyxl`) — no COM/xlwings dependency
- VBA macros in the workbook are ignored (not executed)
- Building level and broken status are not stored in Excel and default to level 1, not broken
- Cell styles (like "No Assignment") cannot be reliably detected without COM automation — these positions import as empty

---

## 5. Discord Bot API (Integration Boundary)

The Discord bot remains a separate standalone service. The web application communicates with it through a small HTTP API. This section defines the API contract between the two services.

### 5.1 Authentication

Communication between the web API and the Discord bot uses a shared API key passed in the `Authorization` header:

```
Authorization: Bearer <shared-api-key>
```

Both services read this key from their environment configuration. This is internal service-to-service auth — not user-facing.

### 5.2 Endpoints

#### POST /api/notify

Send a DM to a Discord user.

**Request:**
```json
{
  "discord_username": "player123",
  "guild_id": "1112031869337346184",
  "message": "**[1MOM] Masters of Magicka Siege Assignment...**"
}
```

**Response:**
```json
{
  "success": true,
  "discord_username": "player123"
}
```

**Error Response:**
```json
{
  "success": false,
  "discord_username": "player123",
  "error": "User not found in guild"
}
```

#### POST /api/post-message

Post a text message to a named Discord channel.

**Request:**
```json
{
  "guild_id": "1112031869337346184",
  "channel_name": "clan-siege-assignments",
  "message": "**Siege Assignment — 2026-03-16**\n..."
}
```

**Response:**
```json
{
  "success": true,
  "message_id": "123456789"
}
```

#### POST /api/post-image

Post an image to a named Discord channel.

**Request (multipart/form-data):**
- `guild_id`: string
- `channel_name`: string
- `image`: file upload

**Response:**
```json
{
  "success": true,
  "message_id": "123456789",
  "image_url": "https://cdn.discordapp.com/attachments/..."
}
```

#### GET /api/members

List all members in a Discord guild. Used for Discord username resolution and verification.

**Query Parameters:**
- `guild_id` (required): The Discord guild ID

**Response:**
```json
{
  "members": [
    {
      "username": "player123",
      "nickname": "InGameName",
      "global_name": "Player",
      "id": "987654321"
    }
  ]
}
```

#### GET /api/health

Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "connected_guilds": ["1112031869337346184"]
}
```

### 5.3 Error Handling

The Discord Bot API uses standard HTTP status codes:
- `200` — Success
- `400` — Bad request (missing parameters)
- `401` — Invalid or missing API key
- `404` — Guild, channel, or user not found
- `429` — Rate limited (bot is throttled by Discord)
- `500` — Internal bot error
- `503` — Bot is not connected to Discord

The web API should handle these gracefully and report delivery failures per-member to the UI.

### 5.4 Rate Limiting

The Discord Bot service manages its own rate limiting against Discord's API. The web API sends requests at normal speed; the bot queues and throttles as needed. The web API should support an asynchronous notification flow:
1. Submit notifications (returns a batch ID)
2. Poll for completion status
3. Retrieve final results

This prevents HTTP timeouts when sending DMs to 30+ members.

---

## 6. Web API Endpoints

RESTful API serving the Web UI. All endpoints return JSON. All mutating operations return the updated resource.

### 6.1 Siege Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sieges` | List all sieges (with optional status filter) |
| `POST` | `/api/sieges` | Create a new siege (planning) |
| `GET` | `/api/sieges/{id}` | Get siege details |
| `PUT` | `/api/sieges/{id}` | Update siege metadata (date, defense_scroll_count) |
| `DELETE` | `/api/sieges/{id}` | Delete a planning siege |
| `POST` | `/api/sieges/{id}/activate` | Transition planning → active (runs validation first) |
| `POST` | `/api/sieges/{id}/complete` | Transition active → complete |
| `POST` | `/api/sieges/{id}/clone` | Clone siege into a new planning siege |

### 6.2 Building Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sieges/{id}/buildings` | List all buildings for a siege |
| `POST` | `/api/sieges/{id}/buildings` | Add a building |
| `PUT` | `/api/sieges/{id}/buildings/{buildingId}` | Update building (level, broken status) |
| `DELETE` | `/api/sieges/{id}/buildings/{buildingId}` | Remove a building |
| `POST` | `/api/sieges/{id}/buildings/{buildingId}/groups` | Add a group to a building |
| `DELETE` | `/api/sieges/{id}/buildings/{buildingId}/groups/{groupId}` | Remove a group |

#### Add Building Request

```json
{
  "building_type": "defense_tower",
  "building_number": 3,
  "level": 1
}
```

The system auto-creates groups and positions based on `BuildingTypeConfig` for the given type and level. Returns the created building with its groups and positions.

#### Add Group Request

```json
{
  "group_number": 4,
  "slot_count": 3
}
```

The system auto-creates `slot_count` positions for the new group. The `slot_count` defaults to 3 if omitted.

#### Update Building Request

```json
{
  "level": 3,
  "is_broken": false
}
```

Changing `level` may add or remove groups. Setting `is_broken = true` reverts to base configuration. Both fields are optional.

#### Update Post Priority/Description Request

```json
{
  "priority": 5,
  "description": "Critical — covers Stronghold approach"
}
```

Both fields are optional — omitted fields are not changed.

### 6.3 Assignment (Position) Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sieges/{id}/assignments` | Get full assignment board (all buildings → groups → positions with member info) |
| `PUT` | `/api/sieges/{id}/positions/{positionId}` | Assign a member, set RESERVE, set disabled, or clear a position |
| `POST` | `/api/sieges/{id}/assignments/bulk` | Bulk update multiple positions at once |

#### Position Update Request

```json
{
  "member_id": 42,
  "is_reserve": false,
  "is_disabled": false
}
```

To clear a position: `{ "member_id": null, "is_reserve": false, "is_disabled": false }`
To mark as RESERVE: `{ "member_id": null, "is_reserve": true, "is_disabled": false }`
To mark as disabled: `{ "member_id": null, "is_reserve": false, "is_disabled": true }`

### 6.4 Post Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sieges/{id}/posts` | List all posts with their active conditions and priorities |
| `PUT` | `/api/sieges/{id}/posts/{postId}` | Update post priority and/or description |
| `PUT` | `/api/sieges/{id}/posts/{postId}/conditions` | Set the 3 active conditions for a post |

#### Set Post Conditions Request

```json
{
  "post_condition_ids": [1, 5, 22]
}
```

Must contain 0 to 3 condition IDs. An empty array clears all conditions for the post.

### 6.5 Action Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/sieges/{id}/validate` | Run validation, return results |
| `POST` | `/api/sieges/{id}/auto-fill` | Run auto-fill, return preview (idempotent — each call generates a fresh preview with new randomization) |
| `POST` | `/api/sieges/{id}/auto-fill/apply` | Apply auto-fill directly (equivalent to preview + immediate commit) |
| `GET` | `/api/sieges/{id}/compare` | Compare with previous siege, return changesets |
| `GET` | `/api/sieges/{id}/compare/{otherId}` | Compare with a specific siege |
| `POST` | `/api/sieges/{id}/notify` | Submit DM notifications (async — returns batch ID) |
| `GET` | `/api/sieges/{id}/notify/{batchId}` | Poll notification batch status and results |
| `POST` | `/api/sieges/{id}/post-to-channel` | Post assignment summary and images to Discord channels |
| `POST` | `/api/sieges/{id}/generate-images` | Generate assignment and reserves PNG images |

#### Compare Response

Returns the changeset structure defined in section 4.7.

#### Post to Channel Request

No request body required. The API uses the configured `DISCORD_GUILD_ID` and posts to the standard channels (`clan-siege-assignment-images`, `clan-siege-assignments`).

#### Notify Response

```json
{
  "batch_id": "abc123",
  "status": "pending",
  "total_members": 30
}
```

#### Notify Batch Status Response

```json
{
  "batch_id": "abc123",
  "status": "completed",
  "results": [
    { "member_name": "Player1", "discord_username": "player1", "success": true },
    { "member_name": "Player2", "discord_username": null, "success": false, "error": "No Discord username mapped" }
  ]
}
```

### 6.6 Siege Member Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sieges/{id}/members` | List all SiegeMember records for this siege (attack day, reserve status) |
| `PUT` | `/api/sieges/{id}/members/{memberId}` | Update a member's attack day and/or reserve status for this siege |
| `POST` | `/api/sieges/{id}/members/auto-assign-attack-day` | Run the attack day assignment algorithm (section 4.2); returns preview |
| `POST` | `/api/sieges/{id}/members/auto-assign-attack-day/apply` | Apply the previewed attack day assignments |

#### SiegeMember Update Request

```json
{
  "attack_day": 2,
  "has_reserve_set": true,
  "attack_day_override": true
}
```

Setting `attack_day_override = true` prevents the auto-assignment algorithm from changing this member's attack day.

### 6.7 Member Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/members` | List all members (with optional `is_active` filter) |
| `POST` | `/api/members` | Create a new member |
| `GET` | `/api/members/{id}` | Get member details |
| `PUT` | `/api/members/{id}` | Update member |
| `DELETE` | `/api/members/{id}` | Deactivate member (`is_active = FALSE`) |
| `GET` | `/api/members/{id}/preferences` | Get member's preferred post conditions |
| `PUT` | `/api/members/{id}/preferences` | Set member's preferred post conditions (replace all) |

#### Set Member Preferences Request

```json
{
  "post_condition_ids": [1, 5, 12]
}
```

Replaces all existing preferences. An empty array clears all preferences for the member.

### 6.8 Reference Data Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/post-conditions` | List all post conditions (with optional `stronghold_level` filter) |
| `GET` | `/api/building-types` | List valid building types |
| `GET` | `/api/member-roles` | List valid member roles with default attack days |

### 6.9 Import Endpoint

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/import/excel` | Upload and import a `.xlsm` file as a completed siege |

**Request:** multipart/form-data with the `.xlsm` file

**Response:**
```json
{
  "siege_id": 15,
  "date": "2026-03-01",
  "members_imported": 30,
  "positions_imported": 141,
  "warnings": ["Could not detect 'No Assignment' cell styles — imported as empty positions"]
}
```

---

## 7. Reference Data

### 7.1 Building Types

| Enum Value | Display Name | Count | Max Groups | Notes |
|---|---|---|---|---|
| `stronghold` | Stronghold | 1 | Variable (upgradeable) | Central building, highest priority |
| `mana_shrine` | Mana Shrine | 2 | Variable (upgradeable) | Resource building |
| `magic_tower` | Magic Tower | 4 | Variable (upgradeable) | Offensive building |
| `defense_tower` | Defense Tower | 5 | Variable (upgradeable) | Defensive building |
| `post` | Post | 18 | 1 (always) | Outer defense, has post conditions |
| | **Total** | **30** | | |

Building aliases for display and import:

| Alias | Resolves To |
|---|---|
| Mana | Mana Shrine |
| Defense | Defense Tower |
| Magic | Magic Tower |

### 7.2 Member Roles

| Enum Value | Display Name | Default Attack Day |
|---|---|---|
| `heavy_hitter` | Heavy Hitter | 2 |
| `advanced` | Advanced | 2 |
| `medium` | Medium | 1 |
| `novice` | Novice | 1 |

### 7.3 Post Conditions

Seeded into the `PostCondition` table on initial deployment. 36 conditions total.

#### Stronghold Level 1 (18 conditions)

| # | Condition |
|---|---|
| 1 | Only Champions from the Telerian League can be used. |
| 2 | Only Champions from the Gaellen Pact can be used. |
| 3 | Only Champions from The Corrupted can be used. |
| 4 | Only Champions from the Nyresan Union can be used. |
| 5 | Only HP Champions can be used. |
| 6 | Only DEF Champions can be used. |
| 7 | Only Support Champions can be used. |
| 8 | Only ATK Champions can be used. |
| 9 | Only Banner Lord Champions can be used. |
| 10 | Only High Elves Champions can be used. |
| 11 | Only Sacred Order Champions can be used. |
| 12 | Only Barbarian Champions can be used. |
| 13 | Only Ogryn Tribe Champions can be used. |
| 14 | Only Lizardmen Champions can be used. |
| 15 | Only Skinwalker Champions can be used. |
| 16 | Only Orc Champions can be used. |
| 17 | All Champions are immune to Turn Meter reduction effects. |
| 18 | All Champions are immune to Turn Meter fill effects. |

#### Stronghold Level 2 (10 conditions)

| # | Condition |
|---|---|
| 19 | Only Void Champions can be used. |
| 20 | Only Force Champions can be used. |
| 21 | Only Magic Champions can be used. |
| 22 | Only Spirit Champions can be used. |
| 23 | Only Demonspawn Champions can be used. |
| 24 | Only Undead Horde Champions can be used. |
| 25 | Only Dark Elves Champions can be used. |
| 26 | Only Knights Revenant Champions can be used. |
| 27 | All Champions are immune to cooldown increasing effects. |
| 28 | All Champions are immune to cooldown decreasing effects. |

#### Stronghold Level 3 (8 conditions)

| # | Condition |
|---|---|
| 29 | Only Legendary Champions can be used. |
| 30 | Only Epic Champions can be used. |
| 31 | Only Rare Champions can be used. |
| 32 | Only Dwarves Champions can be used. |
| 33 | Only Shadowkin Champions can be used. |
| 34 | Only Sylvan Watcher Champions can be used. |
| 35 | All Champions are immune to [Sheep] debuffs. |
| 36 | Champions cannot be revived. |

### 7.4 Position Constraints

| Constraint | Value | Notes |
|---|---|---|
| Group range | 1-9 | Per building |
| Position range | 1-3 | Per group; last group in a building may have fewer slots (see section 3.3) |
| Building number range | 1-18 | Building instance on the siege map |
| Attack days | 1 or 2 | Siege runs over 2 days |
| Post groups | Always 1 | Posts can only ever have 1 group |
| Post conditions per post | 0-3 | Randomly assigned by the game each siege; planner may leave some posts unconfigured |
| Max positions per group | 3 | Determined by `slot_count` on the group (1-3); last group may have fewer |

### 7.5 Discord Channels

| Channel Name | Purpose |
|---|---|
| `clan-siege-assignment-images` | Upload destination for assignment images/views |
| `clan-siege-assignments` | Public posting of siege date header + assignment summary |

---

## 8. Validation Rules

All validation runs server-side in the Web API. Results are returned as structured JSON (see section 4.5) and displayed inline in the UI.

### 8.1 Data Integrity Rules (Errors)

These rules block siege activation and should also be enforced on individual writes where possible.

| # | Rule | Description | Origin |
|---|---|---|---|
| 1 | All assigned members active | Every `position.member_id` must reference an active `Member` record | VBA Validate |
| 2 | No over-assignment | No member may be assigned to more positions than `siege.defense_scroll_count` | VBA Validate |
| 3 | Building number valid | Building numbers must be within type-specific range (see `BuildingTypeConfig`) | Python `Position.__init__` |
| 4 | Group number valid | Group numbers must be 1-9 | Python `Position.__init__` |
| 5 | Position number valid | Position numbers must be 1 to `building_group.slot_count` | Python `Position.__init__` |
| 6 | Attack day valid | Attack day must be 1 or 2 (in `SiegeMember` records) | Python `SiegeAssignment.__init__` |
| 7 | Post group constraint | Post buildings must have exactly 1 group with `slot_count` matching `BuildingTypeConfig` | Domain rule from GAPS.md |
| 8 | Position state consistency | Disabled positions cannot have members or RESERVE; RESERVE positions cannot have members | Schema constraint |
| 9 | Building count per type | Number of buildings per type must match `BuildingTypeConfig.count` | New — game rule |

### 8.2 Business Logic Rules (Warnings)

These rules produce warnings but do not block the planner from proceeding.

| # | Rule | Description | Origin |
|---|---|---|---|
| 10 | RESERVE balance | RESERVE count should equal (total positions - assigned positions - disabled positions). Formula: `expected_empty = total_slots - assigned_count - disabled_count`; all expected-empty positions should be RESERVE | VBA Validate |
| 11 | Post preference mismatch | Members assigned to posts where none of the post's active conditions match the member's preferred post conditions. Skipped for members with no preferences defined. | New — not in any current code |
| 12 | All positions filled | No empty (unassigned, non-RESERVE, non-disabled) positions should remain | New |
| 13 | Attack day assigned | All assigned members should have an attack day set in their `SiegeMember` record | New |
| 14 | Day 2 attacker threshold | At least 10 members should be assigned to Day 2; warns if fewer | New — game strategy rule |
| 15 | Reserve status set | All assigned members should have `has_reserve_set` recorded in their `SiegeMember` record | New |
| 16 | Post conditions incomplete | Posts with fewer than 3 active conditions configured; informational only since some posts may be intentionally left unconfigured | New |

### 8.3 Post Preference Validation Detail

This is a new validation rule not present in the current system (identified as a data gap in `DESIGN_DOCUMENT.md` section 12.3, item 13).

For each member assigned to a Post building position:
1. Look up the member's preferred post conditions (`MemberPostPreference` records)
2. **If the member has no preferences defined, skip this check entirely** — many members have no preferences, and this is normal
3. If the member has preferences, look up the Post's active conditions (`PostActiveCondition` records)
4. If the post has no active conditions configured, skip this check (post is intentionally unconfigured)
5. If a match exists between the member's preferences and the post's conditions, this is a **positive indicator** — the member is well-suited for this post
6. If no match, produce a **warning**: "Member {name} has no preferred conditions matching Post {building_number}"

This is advisory only. A member's preferred post conditions describe which conditions they are strong at defending. The planner uses this to make better placement decisions, but can assign any member to any post. The warning helps the planner identify potentially suboptimal placements.

---

## 9. Technology Recommendations

These are light suggestions, not prescriptive requirements. The architecture described in this document is technology-agnostic.

### 9.1 Hosting

**Azure App Service** or equivalent PaaS:
- Hosts both the Web API and serves the Web UI static assets
- Managed SSL, scaling, deployment slots
- Alternatively: Azure Container Apps if containerized

### 9.2 Database

**Azure SQL Database** or **Azure Database for PostgreSQL**:
- Relational database fits the normalized schema well
- Managed service with automated backups
- PostgreSQL offers better enum support and JSON columns if needed

### 9.3 Backend API

**Python (FastAPI)** or **C#/.NET**:
- FastAPI: Aligns with existing Python codebase knowledge; built-in async, OpenAPI docs
- .NET: Strong Azure integration, type safety; more verbose

### 9.4 Front-End

**React**, **Vue**, or similar SPA framework:
- Component-based UI for the assignment board grid
- Drag-and-drop libraries available for position assignment
- State management for real-time validation feedback

### 9.5 Discord Bot Extension

The existing `discord.py` bot can be extended with an HTTP API layer:
- Add **Flask** or **FastAPI** running alongside the discord.py event loop
- Expose the 5 endpoints defined in section 5
- Keep the bot's existing guild connection and Discord auth
- The bot runs as a separate process/service from the web application
