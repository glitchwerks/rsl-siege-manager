"""Tests for day-role-sync webhook wiring into mutation seams.

Covers issue #399 acceptance criteria — verifies that
``update_siege_member`` and ``apply_attack_day`` schedule the correct
outbound webhook calls via FastAPI BackgroundTasks, and that
``add_siege_member`` emits nothing.

Test strategy: use ``respx`` to intercept real httpx transport calls
so the full stack (BotClient → httpx → mock receiver) is exercised
without patching internals.  ``monkeypatch.setattr`` on
``app.config.settings`` propagates to all production paths because
``BotClient.sync_day_role`` reads ``settings`` at call time (not at
import time).

All eight acceptance criteria from the brief:
1.  Happy path single-member assign (update_siege_member, day=1)
2.  Happy path single-member unassign (update_siege_member, day=None)
3.  Fan-out shared correlation_id (apply_attack_day, N=3 members)
4.  discord_id=None filter end-to-end (fan-out, one member skipped)
5.  Feature-gate off → zero HTTP calls (all mutation seams)
6.  add_siege_member no-op (no webhook, even with discord_id set)
7.  assigned_at monotonicity in fan-out (strictly increasing)
8.  assigned_at millisecond precision (regex + format check)
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from app.main import app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SYNC_URL = "https://mom-bot.test/api/internal/role-sync"
_DISCORD_ID_A = "111111111111111111"
_DISCORD_ID_B = "222222222222222222"
_DISCORD_ID_C = "333333333333333333"
_SIEGE_ID = 7
_MEMBER_ID = 42

# Canonical receiver response for "applied".
_APPLIED_BODY = {"status": "applied", "added": ["Day 1"], "removed": []}
_UNASSIGN_APPLIED_BODY = {"status": "applied", "added": [], "removed": ["Day 1"]}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_siege_member(
    siege_id: int = _SIEGE_ID,
    member_id: int = _MEMBER_ID,
    attack_day: int | None = None,
    discord_id: str | None = _DISCORD_ID_A,
    attack_day_override: bool = False,
) -> SimpleNamespace:
    """Return a minimal SiegeMember-shaped object for service mocks."""
    member = SimpleNamespace(
        id=member_id,
        name=f"Member{member_id}",
        discord_id=discord_id,
        discord_username=None,
        role="advanced",
        power_level=None,
        is_active=True,
    )
    return SimpleNamespace(
        siege_id=siege_id,
        member_id=member_id,
        attack_day=attack_day,
        has_reserve_set=True,
        attack_day_override=attack_day_override,
        member=member,
    )


def _make_added_siege_member(
    siege_id: int = _SIEGE_ID,
    member_id: int = _MEMBER_ID,
    discord_id: str | None = _DISCORD_ID_A,
) -> SimpleNamespace:
    """Return a SiegeMember as returned by add_siege_member (no day yet)."""
    return _make_siege_member(
        siege_id=siege_id,
        member_id=member_id,
        attack_day=None,
        discord_id=discord_id,
    )


@pytest.fixture
def http_client():
    """HTTPX AsyncClient backed by the FastAPI ASGI app."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _enable_sync(monkeypatch, url: str = _SYNC_URL) -> None:
    """Turn the feature gate on and point it at the mock receiver."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", url)


def _parse_assigned_at(payload: dict) -> datetime:
    """Parse the assigned_at field from a captured request payload."""
    raw: str = payload["assigned_at"]
    # Normalise trailing Z to +00:00 for fromisoformat compatibility.
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# AC1 — Happy path single-member assign (day_number present)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_siege_member_assign_emits_one_webhook(monkeypatch, http_client):
    """AC1: update_siege_member setting day=1 emits exactly one assign call.

    Verifies payload shape: correct action, discord_id, day_number, and a
    non-empty correlation_id.
    """
    _enable_sync(monkeypatch)

    sm_after = _make_siege_member(attack_day=1)
    _assigned_at = datetime(2026, 5, 14, 12, 0, 0, 123456, tzinfo=UTC)

    with (
        patch(
            "app.api.siege_members.siege_members_service.update_siege_member",
            new_callable=AsyncMock,
        ) as mock_update,
        respx.mock() as transport,
    ):
        route = transport.post(_SYNC_URL).mock(return_value=httpx.Response(200, json=_APPLIED_BODY))
        mock_update.return_value = (sm_after, _assigned_at)

        async with http_client as c:
            response = await c.put(
                f"/api/sieges/{_SIEGE_ID}/members/{_MEMBER_ID}",
                json={"attack_day": 1},
            )

    assert response.status_code == 200
    assert route.call_count == 1, "Expected exactly one webhook call"

    body = json.loads(route.calls[0].request.content)
    assert body["action"] == "assign"
    assert body["discord_id"] == _DISCORD_ID_A
    assert body["day_number"] == 1
    assert body["siege_id"] == _SIEGE_ID
    assert body["correlation_id"], "correlation_id must be non-empty"


# ---------------------------------------------------------------------------
# AC2 — Happy path single-member unassign (day_number absent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_siege_member_unassign_emits_one_webhook(monkeypatch, http_client):
    """AC2: update_siege_member clearing day emits action='unassign'.

    The day_number field must be absent when action='unassign' per the
    BotClient contract.
    """
    _enable_sync(monkeypatch)

    sm_after = _make_siege_member(attack_day=None)
    _assigned_at = datetime(2026, 5, 14, 12, 0, 0, 123456, tzinfo=UTC)

    with (
        patch(
            "app.api.siege_members.siege_members_service.update_siege_member",
            new_callable=AsyncMock,
        ) as mock_update,
        respx.mock() as transport,
    ):
        route = transport.post(_SYNC_URL).mock(
            return_value=httpx.Response(200, json=_UNASSIGN_APPLIED_BODY)
        )
        mock_update.return_value = (sm_after, _assigned_at)

        async with http_client as c:
            response = await c.put(
                f"/api/sieges/{_SIEGE_ID}/members/{_MEMBER_ID}",
                json={"attack_day": None},
            )

    assert response.status_code == 200
    assert route.call_count == 1

    body = json.loads(route.calls[0].request.content)
    assert body["action"] == "unassign"
    assert "day_number" not in body


# ---------------------------------------------------------------------------
# AC3 — Fan-out: shared correlation_id across N=3 members
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_attack_day_fanout_shared_correlation_id(monkeypatch, http_client):
    """AC3: apply_attack_day with 3 members emits 3 calls with one correlation_id.

    All three payloads must carry the same correlation_id (one per user
    action per contract §8).
    """
    _enable_sync(monkeypatch)

    from app.schemas.attack_day import AppliedMemberEntry, AttackDayApplyResult

    _base_ts = datetime(2026, 5, 14, 12, 0, 0, 0, tzinfo=UTC)
    apply_result = AttackDayApplyResult(
        applied_count=3,
        applied_members=[
            AppliedMemberEntry(
                member_id=1,
                attack_day=1,
                discord_id=_DISCORD_ID_A,
                assigned_at=_base_ts,
            ),
            AppliedMemberEntry(
                member_id=2,
                attack_day=2,
                discord_id=_DISCORD_ID_B,
                assigned_at=_base_ts + timedelta(microseconds=100),
            ),
            AppliedMemberEntry(
                member_id=3,
                attack_day=1,
                discord_id=_DISCORD_ID_C,
                assigned_at=_base_ts + timedelta(microseconds=200),
            ),
        ],
    )

    with (
        patch(
            "app.api.attack_day.attack_day_service.apply_attack_day",
            new_callable=AsyncMock,
        ) as mock_apply,
        respx.mock() as transport,
    ):
        route = transport.post(_SYNC_URL).mock(return_value=httpx.Response(200, json=_APPLIED_BODY))
        mock_apply.return_value = apply_result

        async with http_client as c:
            response = await c.post(f"/api/sieges/{_SIEGE_ID}/members/auto-assign-attack-day/apply")

    assert response.status_code == 200
    assert route.call_count == 3, "Expected one webhook call per affected member"

    payloads = [json.loads(call.request.content) for call in route.calls]
    correlation_ids = [p["correlation_id"] for p in payloads]
    assert (
        len(set(correlation_ids)) == 1
    ), f"All fan-out calls must share one correlation_id, got: {correlation_ids}"


# ---------------------------------------------------------------------------
# AC4 — discord_id=None filter end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_attack_day_skips_member_with_no_discord_id(monkeypatch, http_client):
    """AC4: Fan-out skips members with discord_id=None; others still get calls.

    N=2 members; member 1 has no discord_id → 1 webhook call, not 2.
    """
    _enable_sync(monkeypatch)

    from app.schemas.attack_day import AppliedMemberEntry, AttackDayApplyResult

    _base_ts = datetime(2026, 5, 14, 12, 0, 0, 0, tzinfo=UTC)
    apply_result = AttackDayApplyResult(
        applied_count=2,
        applied_members=[
            AppliedMemberEntry(
                member_id=10,
                attack_day=1,
                discord_id=None,  # must be skipped
                assigned_at=_base_ts,
            ),
            AppliedMemberEntry(
                member_id=11,
                attack_day=2,
                discord_id=_DISCORD_ID_B,  # must get a call
                assigned_at=_base_ts + timedelta(microseconds=100),
            ),
        ],
    )

    with (
        patch(
            "app.api.attack_day.attack_day_service.apply_attack_day",
            new_callable=AsyncMock,
        ) as mock_apply,
        respx.mock() as transport,
    ):
        route = transport.post(_SYNC_URL).mock(return_value=httpx.Response(200, json=_APPLIED_BODY))
        mock_apply.return_value = apply_result

        async with http_client as c:
            response = await c.post(f"/api/sieges/{_SIEGE_ID}/members/auto-assign-attack-day/apply")

    assert response.status_code == 200
    assert route.call_count == 1, "Only the member with a discord_id should generate a webhook call"
    body = json.loads(route.calls[0].request.content)
    assert body["discord_id"] == _DISCORD_ID_B


# ---------------------------------------------------------------------------
# AC5 — Feature-gate off → zero HTTP calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feature_gate_off_update_emits_no_webhook(monkeypatch, http_client):
    """AC5a: DAY_ROLE_SYNC_ENABLED=false → update_siege_member emits zero calls."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", False)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    sm_after = _make_siege_member(attack_day=1)
    _assigned_at = datetime(2026, 5, 14, 12, 0, 0, 0, tzinfo=UTC)

    with (
        patch(
            "app.api.siege_members.siege_members_service.update_siege_member",
            new_callable=AsyncMock,
        ) as mock_update,
        respx.mock(assert_all_called=False) as transport,
    ):
        mock_update.return_value = (sm_after, _assigned_at)

        async with http_client as c:
            response = await c.put(
                f"/api/sieges/{_SIEGE_ID}/members/{_MEMBER_ID}",
                json={"attack_day": 1},
            )

    assert response.status_code == 200
    assert transport.calls.call_count == 0, "Feature gate off must suppress all calls"


@pytest.mark.asyncio
async def test_feature_gate_off_apply_emits_no_webhook(monkeypatch, http_client):
    """AC5b: DAY_ROLE_SYNC_ENABLED=false → apply_attack_day emits zero calls."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", False)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    from app.schemas.attack_day import AppliedMemberEntry, AttackDayApplyResult

    apply_result = AttackDayApplyResult(
        applied_count=1,
        applied_members=[
            AppliedMemberEntry(
                member_id=1,
                attack_day=1,
                discord_id=_DISCORD_ID_A,
                assigned_at=datetime(2026, 5, 14, 12, 0, 0, 0, tzinfo=UTC),
            ),
        ],
    )

    with (
        patch(
            "app.api.attack_day.attack_day_service.apply_attack_day",
            new_callable=AsyncMock,
        ) as mock_apply,
        respx.mock(assert_all_called=False) as transport,
    ):
        mock_apply.return_value = apply_result

        async with http_client as c:
            response = await c.post(f"/api/sieges/{_SIEGE_ID}/members/auto-assign-attack-day/apply")

    assert response.status_code == 200
    assert transport.calls.call_count == 0


# ---------------------------------------------------------------------------
# AC6 — add_siege_member is a documented no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_siege_member_emits_no_webhook(monkeypatch, http_client):
    """AC6: add_siege_member must not trigger any webhook call.

    Adding a member does not assign them to a day yet, so there is no role
    to sync.  This is a documented no-op even if discord_id is set.
    """
    _enable_sync(monkeypatch)

    added_sm = _make_added_siege_member()

    with (
        patch(
            "app.api.siege_members.siege_members_service.add_siege_member",
            new_callable=AsyncMock,
        ) as mock_add,
        respx.mock(assert_all_called=False) as transport,
    ):
        mock_add.return_value = added_sm

        async with http_client as c:
            response = await c.post(
                f"/api/sieges/{_SIEGE_ID}/members",
                json={"member_id": _MEMBER_ID},
            )

    assert response.status_code == 201
    assert (
        transport.calls.call_count == 0
    ), "add_siege_member must not emit any webhook — member has no day yet"


# ---------------------------------------------------------------------------
# AC7 — assigned_at strict monotonicity in fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_attack_day_fanout_assigned_at_strictly_increasing(monkeypatch, http_client):
    """AC7: Each payload in the fan-out has a strictly larger assigned_at.

    Within a single bulk apply, two payloads for different members must
    have distinct, strictly-increasing assigned_at values (contract §7).
    """
    _enable_sync(monkeypatch)

    from app.schemas.attack_day import AppliedMemberEntry, AttackDayApplyResult

    _base_ts = datetime(2026, 5, 14, 12, 0, 0, 0, tzinfo=UTC)
    apply_result = AttackDayApplyResult(
        applied_count=3,
        applied_members=[
            AppliedMemberEntry(
                member_id=i,
                attack_day=1,
                discord_id=str(100_000_000_000_000_000 + i),
                # Each entry is 1 ms later than the previous so the
                # millisecond-precision serialization preserves strict
                # monotonicity in the captured payloads (contract §7).
                assigned_at=_base_ts + timedelta(milliseconds=i - 1),
            )
            for i in range(1, 4)
        ],
    )

    captured_payloads: list[dict] = []

    with (
        patch(
            "app.api.attack_day.attack_day_service.apply_attack_day",
            new_callable=AsyncMock,
        ) as mock_apply,
        respx.mock() as transport,
    ):
        transport.post(_SYNC_URL).mock(return_value=httpx.Response(200, json=_APPLIED_BODY))
        mock_apply.return_value = apply_result

        async with http_client as c:
            await c.post(f"/api/sieges/{_SIEGE_ID}/members/auto-assign-attack-day/apply")

        # Capture calls inside the context manager before respx clears state.
        captured_payloads = [json.loads(call.request.content) for call in transport.calls]

    assert len(captured_payloads) == 3

    timestamps = [_parse_assigned_at(p) for p in captured_payloads]
    for i in range(1, len(timestamps)):
        assert timestamps[i] > timestamps[i - 1], (
            f"assigned_at[{i}]={timestamps[i]} must be strictly greater than "
            f"assigned_at[{i-1}]={timestamps[i-1]}"
        )


# ---------------------------------------------------------------------------
# AC5c — no attack_day in payload → no webhook (service returns None timestamp)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_siege_member_no_attack_day_emits_no_webhook(monkeypatch, http_client):
    """AC5c: Patching non-day fields must not trigger any webhook call.

    When the update payload does not include ``attack_day``, the service
    returns ``(sm, None)`` for ``assigned_at``.  The API handler must detect
    this and skip ``schedule_role_sync`` entirely — zero HTTP calls even when
    the feature gate is on.
    """
    _enable_sync(monkeypatch)

    sm_after = _make_siege_member(attack_day=1)  # day already set on the record

    with (
        patch(
            "app.api.siege_members.siege_members_service.update_siege_member",
            new_callable=AsyncMock,
        ) as mock_update,
        respx.mock(assert_all_called=False) as transport,
    ):
        # Service returns None for assigned_at — attack_day was not in the patch.
        mock_update.return_value = (sm_after, None)

        async with http_client as c:
            response = await c.put(
                f"/api/sieges/{_SIEGE_ID}/members/{_MEMBER_ID}",
                json={"has_reserve_set": True},  # no attack_day field
            )

    assert response.status_code == 200
    assert (
        transport.calls.call_count == 0
    ), "Patching non-day fields must not emit a webhook — no day-role-sync needed"


# ---------------------------------------------------------------------------
# AC8 — assigned_at millisecond precision
# ---------------------------------------------------------------------------

_MS_PRECISION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


@pytest.mark.asyncio
async def test_update_siege_member_assigned_at_millisecond_precision(monkeypatch, http_client):

    """AC8: assigned_at serialized as ISO-8601 UTC with exactly millisecond precision.

    The required form is ``YYYY-MM-DDTHH:MM:SS.sssZ`` (contract §2).
    """
    _enable_sync(monkeypatch)

    sm_after = _make_siege_member(attack_day=1)
    # Use a datetime with microsecond precision (as returned by clock_timestamp()).
    # BotClient serializes with timespec="milliseconds", so exactly 3 fractional
    # digits appear in the wire format regardless of source precision.
    _assigned_at = datetime(2026, 5, 14, 12, 0, 0, 123456, tzinfo=UTC)

    with (
        patch(
            "app.api.siege_members.siege_members_service.update_siege_member",
            new_callable=AsyncMock,
        ) as mock_update,
        respx.mock() as transport,
    ):
        route = transport.post(_SYNC_URL).mock(return_value=httpx.Response(200, json=_APPLIED_BODY))
        mock_update.return_value = (sm_after, _assigned_at)

        async with http_client as c:
            await c.put(
                f"/api/sieges/{_SIEGE_ID}/members/{_MEMBER_ID}",
                json={"attack_day": 1},
            )

    body = json.loads(route.calls[0].request.content)
    assigned_at_str: str = body["assigned_at"]
    assert _MS_PRECISION_RE.match(assigned_at_str), (
        f"assigned_at must match {_MS_PRECISION_RE.pattern!r}, " f"got {assigned_at_str!r}"
    )


# ---------------------------------------------------------------------------
# AC19 — discord_role_id kwarg propagates schedule_role_sync → BotClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac19_discord_role_id_propagates_to_bot_client(monkeypatch):
    """AC19: discord_role_id kwarg forwarded from schedule_role_sync to BotClient.sync_day_role."""
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
        # Execute background tasks synchronously.
        for task in bt.tasks:
            await task()

    mock_sync.assert_called_once()
    _, kwargs = mock_sync.call_args
    assert kwargs.get("discord_role_id") == 12345
