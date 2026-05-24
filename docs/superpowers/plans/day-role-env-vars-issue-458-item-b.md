---
title: "day-role env-var config + sync_day_role kwarg (issue #458 item B revised + item C tail)"
issue: 458
contract_version: "1.1"
coord_ref: "glitchwerks/rsl-mom-apps#9"
supersedes: "day-role-map-issue-458-item-b.md (deleted 2026-05-24 after inquisitor review)"
touches:
  - backend/app/config.py
  - backend/app/main.py
  - backend/app/services/bot_client.py
  - backend/app/api/_role_sync.py
  - backend/app/api/attack_day.py
  - backend/app/api/siege_members.py
  - backend/tests/test_bot_client_sync_day_role.py
  - backend/tests/test_role_sync_wiring.py
  - .env.example
  - .env.deploy.example
  - docs/webhooks/day-role-sync.md
skills_relevant:
  - python
  - test-driven-development
---

# Day-Role Env-Var Config + sync_day_role Kwarg Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Closes siege-web#458 item B (revised) + item C tail.
Supersedes `day-role-map-issue-458-item-b.md`, deleted 2026-05-24 after scope reduction by inquisitor review.
References coord issue `glitchwerks/rsl-mom-apps#9`.

**Goal:** Plumb two env-var role IDs through config → request handler → `schedule_role_sync()` → `BotClient.sync_day_role()` so assign payloads can carry `discord_role_id` per contract v1.1, without any DB table, migration, model, REST endpoint, or frontend change.

**Architecture:** Pure config-and-wiring change. `DISCORD_DAY_1_ROLE_ID` / `DISCORD_DAY_2_ROLE_ID` are read at startup into `Settings`. Request handlers resolve the correct ID from `settings` before scheduling the background task and pass it as a new `discord_role_id` kwarg down through `schedule_role_sync()` to `BotClient.sync_day_role()`. The BotClient includes the field in the payload only when `action == "assign"` and the kwarg is non-None, mirroring the existing `day_number` gating at `bot_client.py:265-267`.

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings, httpx, pytest, respx

---

## §0. Why this supersedes the prior plan

The deleted plan (`day-role-map-issue-458-item-b.md`) proposed a `day_role_map` DB table, an Alembic migration, a Pydantic schema, an admin REST router, and a frontend management page. Inquisitor review on 2026-05-24 raised two blocking charges that made that approach untenable:

**CHARGE 1 (config-not-data):** Day-role IDs are deployment configuration, not application data. They map guild-specific Discord snowflakes that are set once per environment and never edited by end users. A DB table buys CRUD overhead with no benefit a pair of env vars would not cover more simply.

**CHARGE 4 (session-per-task pool exhaustion):** The prior plan fetched role IDs inside a `BackgroundTask` using a fresh `AsyncSessionLocal()`. No existing BackgroundTask in this codebase opens its own session; doing so first-of-its-kind risked pool exhaustion under fan-out (a 30-member day = 30 concurrent sessions held open during HTTPS POSTs to mom-bot). Reading from `settings` inside the request handler — before scheduling — eliminates the session entirely.

Both charges were resolved in the same direction: read from `settings`, pass as a kwarg, no DB involvement.

---

## §1. Design Decisions

**D1. Config, not data.**
Add `discord_day_1_role_id: int | None = None` and `discord_day_2_role_id: int | None = None` to the `Settings` class in `backend/app/config.py`. Both default to `None` so existing deployments without these vars continue to start and emit v1.0-shape payloads (no `discord_role_id` field). The env var names are `DISCORD_DAY_1_ROLE_ID` and `DISCORD_DAY_2_ROLE_ID` — pydantic-settings maps them automatically via its env-var convention.

**D2. Lookup happens in the request handler.**
`attack_day.py` and `siege_members.py` resolve `discord_role_id` from `settings.discord_day_N_role_id` before calling `schedule_role_sync()`. The resolved value (`int | None`) is passed as a kwarg. This avoids any DB access inside a `BackgroundTask` and keeps the background task stateless. Citation: existing fan-out loop in `backend/app/api/attack_day.py:71-85`; single-member path in `backend/app/api/siege_members.py:98-118`.

**D3. `BotClient.sync_day_role()` signature change.**
Add `discord_role_id: int | None = None` as a keyword-only argument after the existing parameters. When `action == "assign"` and `discord_role_id is not None`, include `"discord_role_id": str(discord_role_id)` in the `§2` payload dict. This mirrors the existing `day_number` gating at `backend/app/services/bot_client.py:265-267`:

```python
# Include day_number only for "assign" actions per issue #323 AC.
if action == "assign" and day_number is not None:
    payload["day_number"] = day_number
```

The string cast follows Discord's snowflake convention (large integers exceed JSON numeric precision in some parsers). Contract v1.1 (`glitchwerks/rsl-mom-apps` — `contracts/day-role-sync.md`) specifies the field as a string. Receivers MUST tolerate absence per contract v1.1, so omitting on `unassign` or when `None` is safe.

**D4. Startup log line.**
Inside the `lifespan` context manager in `backend/app/main.py:47-65`, after the existing auth guards, add a WARNING if `DAY_ROLE_SYNC_ENABLED=true` and either or both role ID vars are unset. Two conditions:
- Both unset → WARNING: system runs but emits v1.0-shape payloads (no `discord_role_id`).
- Exactly one set → WARNING: partial config is suspicious; name which var is missing.

This fires at startup, not per-request, so it costs nothing at runtime.

**D5. No mom-bot fallback assumption.**
This plan makes no assumptions about mom-bot's internal day_role_map. Once these env vars are set, `discord_role_id` in the payload becomes the authoritative source for assign actions. Contract v1.1 receivers tolerate absence, so the transition is backward-compatible.

**D6. Auth not applicable.**
No admin endpoints are introduced. No auth surface changes.

---

## §2. File Map

| File | Change |
|------|--------|
| `backend/app/config.py` | Add `discord_day_1_role_id` and `discord_day_2_role_id` settings |
| `backend/app/main.py` | Add startup WARNING in `lifespan()` for partial/missing role ID config |
| `backend/app/services/bot_client.py` | Add `discord_role_id` kwarg to `sync_day_role()`; include in payload when `action=="assign"` and non-None |
| `backend/app/api/_role_sync.py` | Add `discord_role_id` kwarg to `schedule_role_sync()`; forward to BotClient |
| `backend/app/api/attack_day.py` | Resolve role ID from settings before fan-out loop; pass to `schedule_role_sync()` |
| `backend/app/api/siege_members.py` | Resolve role ID from settings before scheduling; pass to `schedule_role_sync()` |
| `backend/tests/test_bot_client_sync_day_role.py` | Add AC16, AC17, AC18 |
| `backend/tests/test_role_sync_wiring.py` | Add AC19 |
| `.env.example` | Add `DISCORD_DAY_1_ROLE_ID` and `DISCORD_DAY_2_ROLE_ID` under the day-role sync block |
| `.env.deploy.example` | Same addition |
| `docs/webhooks/day-role-sync.md` | Add §11 note naming bundled siege-bot as canonical reference impl, mom-bot as second conforming impl |

---

## §3. Step-by-Step Checklist

### Task 1: Add env-var settings to config

**Files:**
- Modify: `backend/app/config.py`

- [ ] **Step 1.1: Add the two settings fields**

  Open `backend/app/config.py`. After the existing `day_role_sync_url` field (line 33), add:

  ```python
  # Discord role IDs for day-role sync (contract v1.1).
  # When set, assign payloads include "discord_role_id" per the contract.
  # Default None → v1.0-shape payload (field omitted). Both vars must be set
  # for full functionality; partial config logs a WARNING at startup (see main.py).
  discord_day_1_role_id: int | None = None
  discord_day_2_role_id: int | None = None
  ```

- [ ] **Step 1.2: Verify the settings class loads**

  ```bash
  cd backend
  ./.venv/Scripts/python.exe -c "from app.config import settings; print(settings.discord_day_1_role_id, settings.discord_day_2_role_id)"
  ```

  Expected output: `None None`

- [ ] **Step 1.3: Commit**

  ```bash
  git add backend/app/config.py
  git commit -m "feat(config): add DISCORD_DAY_1_ROLE_ID and DISCORD_DAY_2_ROLE_ID settings"
  ```

---

### Task 2: Add AC16, AC17, AC18 tests for BotClient (TDD — write before implementation)

**Files:**
- Modify: `backend/tests/test_bot_client_sync_day_role.py`

- [ ] **Step 2.1: Read the existing test module**

  Read `backend/tests/test_bot_client_sync_day_role.py` fully to understand the helper fixture pattern before adding new tests. The existing tests use a `_make_client()` helper and `respx.mock` for HTTP interception.

- [ ] **Step 2.2: Add AC16 — discord_role_id present on assign**

  Append to the test file after the existing AC15 test:

  ```python
  # ---------------------------------------------------------------------------
  # AC16 — discord_role_id kwarg present → included in assign payload as string
  # ---------------------------------------------------------------------------

  @pytest.mark.anyio
  async def test_ac16_discord_role_id_included_on_assign(monkeypatch):
      """AC16: sync_day_role(action="assign", discord_role_id=999) → payload["discord_role_id"] == "999"."""
      monkeypatch.setattr("app.services.bot_client.settings.day_role_sync_enabled", True)
      monkeypatch.setattr("app.services.bot_client.settings.day_role_sync_url", _SYNC_URL)

      captured: dict = {}

      async def capture_handler(request):
          captured.update(request.content and __import__("json").loads(request.content))
          return httpx.Response(200, json={"status": "applied"})

      with respx.mock:
          respx.post(_SYNC_URL).mock(side_effect=capture_handler)
          client = BotClient()
          result = await client.sync_day_role(
              discord_id=111222333444555666,
              siege_id=42,
              day_number=1,
              action="assign",
              assigned_at=_ASSIGNED_AT,
              correlation_id=_CORRELATION_ID,
              discord_role_id=999,
          )

      assert result is True
      assert captured.get("discord_role_id") == "999"
  ```

- [ ] **Step 2.3: Add AC17 — discord_role_id=None omits field on assign**

  ```python
  # ---------------------------------------------------------------------------
  # AC17 — discord_role_id=None → field absent from assign payload
  # ---------------------------------------------------------------------------

  @pytest.mark.anyio
  async def test_ac17_discord_role_id_absent_when_none(monkeypatch):
      """AC17: sync_day_role(action="assign", discord_role_id=None) → payload has no "discord_role_id" key."""
      monkeypatch.setattr("app.services.bot_client.settings.day_role_sync_enabled", True)
      monkeypatch.setattr("app.services.bot_client.settings.day_role_sync_url", _SYNC_URL)

      captured: dict = {}

      async def capture_handler(request):
          captured.update(__import__("json").loads(request.content))
          return httpx.Response(200, json={"status": "applied"})

      with respx.mock:
          respx.post(_SYNC_URL).mock(side_effect=capture_handler)
          client = BotClient()
          result = await client.sync_day_role(
              discord_id=111222333444555666,
              siege_id=42,
              day_number=1,
              action="assign",
              assigned_at=_ASSIGNED_AT,
              correlation_id=_CORRELATION_ID,
              discord_role_id=None,
          )

      assert result is True
      assert "discord_role_id" not in captured
  ```

- [ ] **Step 2.4: Add AC18 — discord_role_id omitted on unassign even when kwarg set**

  ```python
  # ---------------------------------------------------------------------------
  # AC18 — unassign with discord_role_id kwarg → field still omitted from payload
  # ---------------------------------------------------------------------------

  @pytest.mark.anyio
  async def test_ac18_discord_role_id_absent_on_unassign(monkeypatch):
      """AC18: sync_day_role(action="unassign", discord_role_id=999) → payload has no "discord_role_id" key.

      Mirrors day_number gating: the field is assign-only per contract v1.1.
      """
      monkeypatch.setattr("app.services.bot_client.settings.day_role_sync_enabled", True)
      monkeypatch.setattr("app.services.bot_client.settings.day_role_sync_url", _SYNC_URL)

      captured: dict = {}

      async def capture_handler(request):
          captured.update(__import__("json").loads(request.content))
          return httpx.Response(200, json={"status": "applied"})

      with respx.mock:
          respx.post(_SYNC_URL).mock(side_effect=capture_handler)
          client = BotClient()
          result = await client.sync_day_role(
              discord_id=111222333444555666,
              siege_id=42,
              day_number=None,
              action="unassign",
              assigned_at=_ASSIGNED_AT,
              correlation_id=_CORRELATION_ID,
              discord_role_id=999,
          )

      assert result is True
      assert "discord_role_id" not in captured
  ```

- [ ] **Step 2.5: Run the new tests — expect FAIL (kwarg not yet on BotClient)**

  ```bash
  cd backend
  ./.venv/Scripts/python.exe -m pytest tests/test_bot_client_sync_day_role.py::test_ac16_discord_role_id_included_on_assign tests/test_bot_client_sync_day_role.py::test_ac17_discord_role_id_absent_when_none tests/test_bot_client_sync_day_role.py::test_ac18_discord_role_id_absent_on_unassign -v
  ```

  Expected: three FAIL with `TypeError: sync_day_role() got an unexpected keyword argument 'discord_role_id'`

- [ ] **Step 2.6: Commit the failing tests**

  ```bash
  git add backend/tests/test_bot_client_sync_day_role.py
  git commit -m "test(bot_client): add AC16-18 for discord_role_id kwarg (TDD — failing)"
  ```

---

### Task 3: Implement discord_role_id kwarg in BotClient

**Files:**
- Modify: `backend/app/services/bot_client.py`

- [ ] **Step 3.1: Update the sync_day_role signature**

  In `backend/app/services/bot_client.py`, find the `sync_day_role` method signature at line 134. Add `discord_role_id: int | None = None` as the last keyword-only parameter:

  ```python
  async def sync_day_role(
      self,
      *,
      discord_id: int | None,
      siege_id: int,
      day_number: int | None,
      action: Literal["assign", "unassign"],
      assigned_at: datetime,
      correlation_id: str,
      discord_role_id: int | None = None,
  ) -> bool:
  ```

- [ ] **Step 3.2: Add discord_role_id to the payload block**

  After the existing `day_number` gating block at lines 265-267:

  ```python
  # Include day_number only for "assign" actions per issue #323 AC.
  if action == "assign" and day_number is not None:
      payload["day_number"] = day_number
  ```

  Add immediately after:

  ```python
  # Include discord_role_id only for "assign" actions per contract v1.1.
  # Mirrors day_number gating above. String cast follows Discord snowflake
  # convention (large int64 values lose precision in some JSON parsers).
  if action == "assign" and discord_role_id is not None:
      payload["discord_role_id"] = str(discord_role_id)
  ```

- [ ] **Step 3.3: Update the method docstring**

  In the Args section of the `sync_day_role` docstring, add:

  ```
  discord_role_id: Discord role snowflake to assign. Included in the
      payload only when ``action="assign"`` and non-None (contract v1.1
      §2). Omitted on unassign and when ``None`` (v1.0-shape payload).
      Defaults to ``None`` so callers without role ID config produce
      backward-compatible payloads.
  ```

- [ ] **Step 3.4: Run AC16-18 — expect PASS**

  ```bash
  cd backend
  ./.venv/Scripts/python.exe -m pytest tests/test_bot_client_sync_day_role.py::test_ac16_discord_role_id_included_on_assign tests/test_bot_client_sync_day_role.py::test_ac17_discord_role_id_absent_when_none tests/test_bot_client_sync_day_role.py::test_ac18_discord_role_id_absent_on_unassign -v
  ```

  Expected: three PASS

- [ ] **Step 3.5: Run full BotClient test suite — no regressions**

  ```bash
  cd backend
  ./.venv/Scripts/python.exe -m pytest tests/test_bot_client_sync_day_role.py -v
  ```

  Expected: all 18 tests PASS (AC1-AC15 plus the three new ones)

- [ ] **Step 3.6: Commit**

  ```bash
  git add backend/app/services/bot_client.py
  git commit -m "feat(bot_client): add discord_role_id kwarg to sync_day_role (contract v1.1)"
  ```

---

### Task 4: Add AC19 test for kwarg propagation (TDD — write before wiring)

**Files:**
- Modify: `backend/tests/test_role_sync_wiring.py`

- [ ] **Step 4.1: Read the existing test_role_sync_wiring.py**

  Read `backend/tests/test_role_sync_wiring.py` fully to understand how it mocks `bot_client.sync_day_role` and what helpers it uses before adding AC19.

- [ ] **Step 4.2: Add AC19 — kwarg propagates handler → schedule → BotClient**

  Append to `backend/tests/test_role_sync_wiring.py`:

  ```python
  # ---------------------------------------------------------------------------
  # AC19 — discord_role_id kwarg propagates schedule_role_sync → BotClient
  # ---------------------------------------------------------------------------

  @pytest.mark.anyio
  async def test_ac19_discord_role_id_propagates_to_bot_client(monkeypatch):
      """AC19: schedule_role_sync(discord_role_id=12345, ...) forwards the kwarg to BotClient.sync_day_role."""
      from datetime import UTC, datetime
      from unittest.mock import AsyncMock, patch

      from fastapi import BackgroundTasks

      from app.api._role_sync import schedule_role_sync

      mock_sync = AsyncMock(return_value=True)

      monkeypatch.setattr("app.api._role_sync.settings.day_role_sync_enabled", True)

      with patch("app.api._role_sync.bot_client.sync_day_role", mock_sync):
          bt = BackgroundTasks()
          schedule_role_sync(
              bt,
              discord_id="111222333444555666",
              siege_id=1,
              day_number=1,
              action="assign",
              assigned_at=datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC),
              correlation_id="test-corr-id",
              discord_role_id=12345,
          )
          # Execute background tasks synchronously
          for task in bt.tasks:
              await task()

      mock_sync.assert_called_once()
      _, kwargs = mock_sync.call_args
      assert kwargs.get("discord_role_id") == 12345
  ```

- [ ] **Step 4.3: Run AC19 — expect FAIL**

  ```bash
  cd backend
  ./.venv/Scripts/python.exe -m pytest tests/test_role_sync_wiring.py::test_ac19_discord_role_id_propagates_to_bot_client -v
  ```

  Expected: FAIL — `schedule_role_sync()` does not yet accept `discord_role_id`

- [ ] **Step 4.4: Commit failing test**

  ```bash
  git add backend/tests/test_role_sync_wiring.py
  git commit -m "test(role_sync): add AC19 kwarg propagation test (TDD — failing)"
  ```

---

### Task 5: Add discord_role_id kwarg to schedule_role_sync

**Files:**
- Modify: `backend/app/api/_role_sync.py`

- [ ] **Step 5.1: Update the schedule_role_sync signature**

  In `backend/app/api/_role_sync.py`, find `def schedule_role_sync(` at line 53. Add `discord_role_id: int | None = None` after `correlation_id`:

  ```python
  def schedule_role_sync(
      background_tasks: BackgroundTasks,
      *,
      discord_id: str | None,
      siege_id: int,
      day_number: int | None,
      action: Literal["assign", "unassign"],
      assigned_at: datetime,
      correlation_id: str,
      discord_role_id: int | None = None,
  ) -> None:
  ```

- [ ] **Step 5.2: Forward the kwarg to BotClient in add_task call**

  Find the `background_tasks.add_task(...)` call at line 114. Add `discord_role_id=discord_role_id` to the kwargs:

  ```python
  background_tasks.add_task(
      bot_client.sync_day_role,
      discord_id=discord_id,  # type: ignore[arg-type]
      siege_id=siege_id,
      day_number=day_number,
      action=action,
      assigned_at=assigned_at,
      correlation_id=correlation_id,
      discord_role_id=discord_role_id,
  )
  ```

- [ ] **Step 5.3: Update the docstring Args section**

  Add to the Args block:

  ```
  discord_role_id: Discord role snowflake resolved by the request handler
      from ``settings.discord_day_N_role_id``.  Forwarded verbatim to
      ``BotClient.sync_day_role()``.  Pass ``None`` when the env var is
      unset — the payload will omit ``discord_role_id`` (v1.0-shape).
  ```

- [ ] **Step 5.4: Run AC19 — expect PASS**

  ```bash
  cd backend
  ./.venv/Scripts/python.exe -m pytest tests/test_role_sync_wiring.py::test_ac19_discord_role_id_propagates_to_bot_client -v
  ```

  Expected: PASS

- [ ] **Step 5.5: Run full role_sync_wiring suite — no regressions**

  ```bash
  cd backend
  ./.venv/Scripts/python.exe -m pytest tests/test_role_sync_wiring.py -v
  ```

  Expected: all tests PASS

- [ ] **Step 5.6: Commit**

  ```bash
  git add backend/app/api/_role_sync.py
  git commit -m "feat(role_sync): add discord_role_id kwarg; forward to BotClient"
  ```

---

### Task 6: Resolve role ID in request handlers

**Files:**
- Modify: `backend/app/api/attack_day.py`
- Modify: `backend/app/api/siege_members.py`

- [ ] **Step 6.1: Update attack_day.py — add settings import and resolve before fan-out**

  In `backend/app/api/attack_day.py`, add `settings` to the imports:

  ```python
  from app.config import settings
  ```

  Then in `apply_attack_day`, resolve the role ID once before the fan-out loop (after line 69, before the `for entry in result.applied_members:` loop):

  ```python
  # Resolve discord_role_id from settings once per request, before scheduling.
  # Reading settings here (not inside the BackgroundTask) avoids opening a DB
  # session inside a background task — no AsyncSessionLocal in a BackgroundTask
  # is the established pattern in this codebase (see docs/superpowers/plans/
  # day-role-env-vars-issue-458-item-b.md §0, inquisitor CHARGE 4).
  def _role_id_for_day(day: int | None) -> int | None:
      if day == 1:
          return settings.discord_day_1_role_id
      if day == 2:
          return settings.discord_day_2_role_id
      return None
  ```

  Note: define `_role_id_for_day` at module level (not inside the handler) to keep the handler clean and make the helper testable. Then in the handler body:

  ```python
  for entry in result.applied_members:
      action = "assign" if entry.attack_day is not None else "unassign"
      discord_role_id = _role_id_for_day(entry.attack_day) if action == "assign" else None

      schedule_role_sync(
          background_tasks,
          discord_id=entry.discord_id,
          siege_id=siege_id,
          day_number=entry.attack_day,
          action=action,
          assigned_at=entry.assigned_at,
          correlation_id=correlation_id,
          discord_role_id=discord_role_id,
      )
  ```

- [ ] **Step 6.2: Update siege_members.py — resolve before scheduling**

  In `backend/app/api/siege_members.py`, add `settings` to the imports:

  ```python
  from app.config import settings
  ```

  In `update_siege_member`, inside the `if "attack_day" in data.model_fields_set:` block, resolve the role ID before calling `schedule_role_sync`:

  ```python
  if "attack_day" in data.model_fields_set:
      correlation_id = str(uuid.uuid4())
      new_day = siege_member.attack_day
      if new_day is not None:
          action = "assign"
          discord_role_id = (
              settings.discord_day_1_role_id if new_day == 1
              else settings.discord_day_2_role_id if new_day == 2
              else None
          )
      else:
          action = "unassign"
          discord_role_id = None

      schedule_role_sync(
          background_tasks,
          discord_id=(
              siege_member.member.discord_id if siege_member.member is not None else None
          ),
          siege_id=siege_id,
          day_number=new_day,
          action=action,
          assigned_at=assigned_at,
          correlation_id=correlation_id,
          discord_role_id=discord_role_id,
      )
  ```

- [ ] **Step 6.3: Run the full test suite — no regressions**

  ```bash
  cd backend
  ./.venv/Scripts/python.exe -m pytest -v
  ```

  Expected: all tests PASS (including the full attack_day and siege_members integration tests if they exist)

- [ ] **Step 6.4: Commit**

  ```bash
  git add backend/app/api/attack_day.py backend/app/api/siege_members.py
  git commit -m "feat(api): resolve discord_role_id from settings in attack_day and siege_members handlers"
  ```

---

### Task 7: Add startup WARNING for partial or missing role ID config

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 7.1: Add WARNING logic to the lifespan context manager**

  In `backend/app/main.py`, inside the `lifespan` async context manager (currently lines 47-65), add the role ID config check after the existing auth guards and before `yield`:

  ```python
  # Warn when DAY_ROLE_SYNC_ENABLED=true but role ID env vars are not fully set.
  if settings.day_role_sync_enabled:
      day1 = settings.discord_day_1_role_id
      day2 = settings.discord_day_2_role_id
      if day1 is None and day2 is None:
          logger.warning(
              "DAY_ROLE_SYNC_ENABLED=true but neither DISCORD_DAY_1_ROLE_ID nor "
              "DISCORD_DAY_2_ROLE_ID is set — assign payloads will omit "
              "discord_role_id (v1.0-shape). Set both vars to enable v1.1 payloads."
          )
      elif day1 is None or day2 is None:
          missing = "DISCORD_DAY_1_ROLE_ID" if day1 is None else "DISCORD_DAY_2_ROLE_ID"
          logger.warning(
              "DAY_ROLE_SYNC_ENABLED=true but %s is unset — partial role ID "
              "config is likely a misconfiguration. Assign payloads for the "
              "unconfigured day will omit discord_role_id.",
              missing,
          )
  ```

- [ ] **Step 7.2: Verify startup log fires in dev**

  Start the backend with `DAY_ROLE_SYNC_ENABLED=true` and both role ID vars unset. Look for the WARNING line in startup output:

  ```bash
  cd backend
  DAY_ROLE_SYNC_ENABLED=true ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
  ```

  Expected log line containing: `"assign payloads will omit discord_role_id (v1.0-shape)"`

  Stop the server (Ctrl-C).

- [ ] **Step 7.3: Commit**

  ```bash
  git add backend/app/main.py
  git commit -m "feat(startup): warn when DAY_ROLE_SYNC_ENABLED but role ID env vars are unset"
  ```

---

### Task 8: Update example env files

**Files:**
- Modify: `.env.example`
- Modify: `.env.deploy.example`

- [ ] **Step 8.1: Add the two vars to .env.example**

  In `.env.example`, find the day-role sync block at the bottom:

  ```
  # Day-role sync webhook (see docs/webhooks/day-role-sync.md §9 for the full contract).
  # Kill switch — keep false until a conforming receiver is deployed and smoke-tested.
  DAY_ROLE_SYNC_ENABLED=false
  DAY_ROLE_SYNC_URL=
  ```

  Append after `DAY_ROLE_SYNC_URL=`:

  ```
  # Discord role IDs for day-role sync (contract v1.1). Set to the numeric Discord
  # role snowflake for each attack day. When unset, assign payloads omit
  # discord_role_id (v1.0-shape, backward-compatible). Both must be set for full
  # v1.1 functionality; partial config logs a WARNING at startup.
  # DISCORD_DAY_1_ROLE_ID=
  # DISCORD_DAY_2_ROLE_ID=
  ```

- [ ] **Step 8.2: Add the same vars to .env.deploy.example**

  Read `.env.deploy.example` to find where the day-role sync block lives, then add the same two commented vars after `DAY_ROLE_SYNC_URL`.

- [ ] **Step 8.3: Commit**

  ```bash
  git add .env.example .env.deploy.example
  git commit -m "docs(env): add DISCORD_DAY_1_ROLE_ID and DISCORD_DAY_2_ROLE_ID to env examples"
  ```

---

### Task 9: Update docs/webhooks/day-role-sync.md pointer file

**Files:**
- Modify: `docs/webhooks/day-role-sync.md`

Note: this file is a pointer to the canonical contract in `glitchwerks/rsl-mom-apps`. The update adds a §11 note about reference implementations — it does not duplicate contract text.

- [ ] **Step 9.1: Add §11 reference implementation note**

  Append to `docs/webhooks/day-role-sync.md`:

  ```markdown
  ## Reference Implementations

  **Canonical producer:** `glitchwerks/rsl-siege-manager` (this repo) — `backend/app/services/bot_client.py` implements the producer side and is the authoritative reference for how to build a contract-conforming sender.

  **Second conforming producer:** `glitchwerks/mom-bot` — implements the same contract as a bundled sidecar. Its implementation is a second conforming producer, not the canonical reference.

  See coord issue `glitchwerks/rsl-mom-apps#9` for the v1.1 contract change that introduced `discord_role_id`.
  ```

- [ ] **Step 9.2: Commit**

  ```bash
  git add docs/webhooks/day-role-sync.md
  git commit -m "docs(webhooks): add reference implementation note for v1.1 (siege-bot canonical, mom-bot second)"
  ```

---

### Task 10: Final verification

- [ ] **Step 10.1: Run the full backend test suite**

  ```bash
  cd backend
  ./.venv/Scripts/python.exe -m pytest -v
  ```

  Expected: all tests PASS. Note the exact count — it should be previous count + 4 (AC16, AC17, AC18, AC19).

- [ ] **Step 10.2: Smoke check with env vars set**

  Set vars in your shell, start the backend, and confirm no WARNING appears (clean config):

  ```bash
  DAY_ROLE_SYNC_ENABLED=true DISCORD_DAY_1_ROLE_ID=123456789 DISCORD_DAY_2_ROLE_ID=987654321 \
    ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
  ```

  Expected: no WARNING log for role IDs. Stop the server.

- [ ] **Step 10.3: Open PR**

  Push the branch and open a PR targeting `main`. PR body must include:
  - `Closes #458` (item B revised + item C tail)
  - `refs glitchwerks/rsl-mom-apps#9`
  - The "Files NOT modified" list from §4 so reviewers see the scope reduction explicitly.

---

## §4. Tests (ACs in full)

Extend `backend/tests/test_bot_client_sync_day_role.py`:

**AC16** — `sync_day_role(action="assign", day_number=1, discord_role_id=999)` fires with a payload containing `"discord_role_id": "999"` (string; Discord snowflake convention). The existing `discord_id` and other fields are also present.

**AC17** — `sync_day_role(action="assign", day_number=1, discord_role_id=None)` fires with a payload that does NOT contain a `"discord_role_id"` key. The payload is otherwise v1.0-shape.

**AC18** — `sync_day_role(action="unassign", day_number=None, discord_role_id=999)` fires with a payload that does NOT contain `"discord_role_id"`, even though the kwarg was non-None. Mirrors the `day_number` assign-only gating at `bot_client.py:265-267`.

Add to `backend/tests/test_role_sync_wiring.py`:

**AC19** — `schedule_role_sync(discord_role_id=12345, ...)` (with `DAY_ROLE_SYNC_ENABLED=true`) calls `BotClient.sync_day_role` with `discord_role_id=12345` in kwargs. Verified by asserting `mock_sync.call_args` contains the kwarg with the exact integer value.

---

## §5. Out of Scope (was in prior plan — inquisitor-driven reduction)

The following items were in `day-role-map-issue-458-item-b.md` and are explicitly removed from this plan per inquisitor CHARGES 1 and 4 (2026-05-24):

- **No Alembic migration** — no DB schema change of any kind
- **No new SQLAlchemy model** — `DayRoleMap` and equivalents do not exist
- **No new Pydantic schema** — no `DayRoleMapEntry`, `DayRoleMapResponse`, etc.
- **No new API router** — no `/api/admin/day-role-map` endpoint or any admin REST surface
- **No new frontend page** — no `DayRoleMapPage`, no nav entry, no route in the React app
- **No frontend test file changes** — Vitest/React Testing Library tests are untouched

This is not a deferral. These items are out of scope for this feature entirely. Day-role IDs are deployment config set by the operator; they have no user-facing management surface.

---

## §6. Rollout

1. Set `DISCORD_DAY_1_ROLE_ID` and `DISCORD_DAY_2_ROLE_ID` to the correct Discord role snowflakes in the deploy environment (Azure Container Apps secrets or environment variables, alongside the existing `DAY_ROLE_SYNC_ENABLED` and `DAY_ROLE_SYNC_URL`).
2. Restart the backend container. The startup log should show no WARNING for role IDs.
3. Smoke test: trigger one assign action (via the attack-day apply endpoint or a manual siege member update). Capture the outbound payload to mom-bot (wire log or mom-bot receiver log) and verify it contains `"discord_role_id": "<expected-snowflake-string>"`.
4. If the payload is missing `discord_role_id`, check that the env vars are set and that the action was `"assign"` (not `"unassign"`). Unassign payloads intentionally omit the field.
5. No database migration, data backfill, or feature-flag toggle is needed beyond `DAY_ROLE_SYNC_ENABLED` (which should already be `true` if the webhook is in use).
