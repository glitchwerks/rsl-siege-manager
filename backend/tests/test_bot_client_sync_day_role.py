"""Tests for BotClient.sync_day_role() — outbound day-role-sync webhook client.

Covers all 12 acceptance criteria from issue #323:
1.  DAY_ROLE_SYNC_ENABLED=false → True, zero HTTP calls
2.  discord_id=None → True, zero HTTP calls, INFO log
3.  DAY_ROLE_SYNC_ENABLED=true + URL unset → False, WARNING log
4.  Happy path 200 applied → True, correct payload, Bearer header
5.  Happy path 200 partial → True, WARNING log
6.  Happy path 200 skipped → True
7.  Happy path 200 failed → False
8.  4xx → False, NO retry (exactly 1 call)
9.  5xx → 5xx → False, exactly 2 calls, retry-exhaustion WARN log
10. 5xx → 200 applied → True, exactly 2 calls, same correlation_id
11. action="assign" includes day_number; action="unassign" omits day_number
12. assigned_at serializes with millisecond precision

PR #409 review feedback additions:
13. Non-HTTPS DAY_ROLE_SYNC_URL → False, ERROR log (contract §4)
14. assigned_at with non-UTC tzinfo → ValueError
15. assigned_at naive (no tzinfo) → ValueError
"""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from app.services.bot_client import BotClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SYNC_URL = "https://mom-bot.test/api/internal/role-sync"
# _BEARER_KEY must match DISCORD_BOT_API_KEY set in conftest.py
_BEARER_KEY = "test-key"
_CORRELATION_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_ASSIGNED_AT = datetime(2026, 5, 14, 13, 52, 18, 247000, tzinfo=UTC)
_DISCORD_ID = 123456789012345678
_SIEGE_ID = 42
_DAY_NUMBER = 1

# Canonical "applied" response body.
_APPLIED_BODY = {
    "status": "applied",
    "added": ["Day 1"],
    "removed": [],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bot_client() -> BotClient:
    """Return a BotClient instance with the test bearer key pre-configured."""
    return BotClient()


async def _call_set(client: BotClient) -> bool:
    """Invoke sync_day_role with action='assign' and standard test fixtures."""
    return await client.sync_day_role(
        discord_id=_DISCORD_ID,
        siege_id=_SIEGE_ID,
        day_number=_DAY_NUMBER,
        action="assign",
        assigned_at=_ASSIGNED_AT,
        correlation_id=_CORRELATION_ID,
    )


async def _call_clear(client: BotClient) -> bool:
    """Invoke sync_day_role with action='unassign' and standard test fixtures."""
    return await client.sync_day_role(
        discord_id=_DISCORD_ID,
        siege_id=_SIEGE_ID,
        day_number=None,
        action="unassign",
        assigned_at=_ASSIGNED_AT,
        correlation_id=_CORRELATION_ID,
    )


# ---------------------------------------------------------------------------
# AC1 — feature flag disabled → True, zero HTTP calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_day_role_disabled_returns_true_no_http(monkeypatch):
    """AC1: When DAY_ROLE_SYNC_ENABLED is false, return True without any HTTP call."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", False)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    with respx.mock(assert_all_called=False) as mock_transport:
        result = await _call_set(_make_bot_client())

    assert result is True
    # respx.mock with assert_all_called=False: we verify no routes were hit
    assert mock_transport.calls.call_count == 0


# ---------------------------------------------------------------------------
# AC2 — discord_id=None → True, zero HTTP calls, INFO log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_day_role_none_discord_id_returns_true_no_http(monkeypatch, caplog):
    """AC2: discord_id=None short-circuits before any HTTP call."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    import logging

    with caplog.at_level(logging.INFO, logger="app.services.bot_client"):
        with respx.mock(assert_all_called=False) as mock_transport:
            result = await _make_bot_client().sync_day_role(
                discord_id=None,
                siege_id=_SIEGE_ID,
                day_number=_DAY_NUMBER,
                action="assign",
                assigned_at=_ASSIGNED_AT,
                correlation_id=_CORRELATION_ID,
            )

    assert result is True
    assert mock_transport.calls.call_count == 0
    assert any(
        _CORRELATION_ID in record.message
        for record in caplog.records
        if record.levelno == logging.INFO
    )


# ---------------------------------------------------------------------------
# AC3 — enabled=true but URL unset → False, WARNING log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_day_role_url_unset_returns_false_warns(monkeypatch, caplog):
    """AC3: URL missing while flag is true → False and WARNING logged."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", None)

    import logging

    with caplog.at_level(logging.WARNING, logger="app.services.bot_client"):
        result = await _call_set(_make_bot_client())

    assert result is False
    assert any(record.levelno == logging.WARNING for record in caplog.records)


# ---------------------------------------------------------------------------
# AC4 — happy path 200 applied → True, correct payload, Bearer header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_day_role_applied_returns_true(monkeypatch):
    """AC4: 200 applied response returns True with correct payload and auth header."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    with respx.mock() as mock_transport:
        route = mock_transport.post(_SYNC_URL).mock(
            return_value=httpx.Response(200, json=_APPLIED_BODY)
        )

        result = await _call_set(_make_bot_client())

    assert result is True
    assert route.called
    assert route.call_count == 1

    request = route.calls[0].request

    # Verify Authorization header.
    assert request.headers["authorization"] == f"Bearer {_BEARER_KEY}"

    # Verify Content-Type.
    assert "application/json" in request.headers["content-type"]

    # Verify payload fields exactly (§2 shape).
    import json

    body = json.loads(request.content)
    assert body["discord_id"] == str(_DISCORD_ID)
    assert body["siege_id"] == _SIEGE_ID
    assert body["day_number"] == _DAY_NUMBER
    assert body["action"] == "assign"
    assert body["correlation_id"] == _CORRELATION_ID
    assert "assigned_at" in body


# ---------------------------------------------------------------------------
# AC5 — happy path 200 partial → True, WARNING log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_day_role_partial_returns_true_warns(monkeypatch, caplog):
    """AC5: 200 partial response returns True and emits a WARNING log."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    import logging

    partial_body = {
        "status": "partial",
        "added": ["Day 1"],
        "removed": [],
        "reason": "remove_of_other_day_failed_403",
    }

    with caplog.at_level(logging.WARNING, logger="app.services.bot_client"):
        with respx.mock() as mock_transport:
            mock_transport.post(_SYNC_URL).mock(return_value=httpx.Response(200, json=partial_body))
            result = await _call_set(_make_bot_client())

    assert result is True
    assert any(record.levelno == logging.WARNING for record in caplog.records)


# ---------------------------------------------------------------------------
# AC6 — happy path 200 skipped → True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_day_role_skipped_returns_true(monkeypatch):
    """AC6: 200 skipped (e.g. already_has_role) response returns True."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    skipped_body = {
        "status": "skipped",
        "added": [],
        "removed": [],
        "reason": "already_has_role",
    }

    with respx.mock() as mock_transport:
        mock_transport.post(_SYNC_URL).mock(return_value=httpx.Response(200, json=skipped_body))
        result = await _call_set(_make_bot_client())

    assert result is True


# ---------------------------------------------------------------------------
# AC7 — happy path 200 failed → False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_day_role_failed_returns_false(monkeypatch):
    """AC7: 200 failed response returns False."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    failed_body = {
        "status": "failed",
        "added": [],
        "removed": [],
        "reason": "discord_api_error",
    }

    with respx.mock() as mock_transport:
        mock_transport.post(_SYNC_URL).mock(return_value=httpx.Response(200, json=failed_body))
        result = await _call_set(_make_bot_client())

    assert result is False


# ---------------------------------------------------------------------------
# AC8 — 4xx → False, NO retry (exactly 1 call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_day_role_4xx_no_retry(monkeypatch):
    """AC8: A 4xx response returns False with exactly 1 HTTP call (no retry)."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    with respx.mock() as mock_transport:
        route = mock_transport.post(_SYNC_URL).mock(
            return_value=httpx.Response(400, json={"detail": "bad request"})
        )
        result = await _call_set(_make_bot_client())

    assert result is False
    assert route.call_count == 1


# ---------------------------------------------------------------------------
# AC9 — 5xx → 5xx → False, exactly 2 calls, retry-exhaustion WARN log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_day_role_5xx_retry_exhausted(monkeypatch, caplog):
    """AC9: Two consecutive 5xx → False, 2 calls total, WARN log on exhaustion."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    import logging

    with caplog.at_level(logging.WARNING, logger="app.services.bot_client"):
        with respx.mock() as mock_transport:
            route = mock_transport.post(_SYNC_URL).mock(
                side_effect=[
                    httpx.Response(503, json={"detail": "unavailable"}),
                    httpx.Response(503, json={"detail": "unavailable"}),
                ]
            )

            # Patch asyncio.sleep to skip the 500ms backoff in tests.
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await _call_set(_make_bot_client())

    assert result is False
    assert route.call_count == 2
    # Retry-exhaustion WARN log must include correlation_id.
    assert any(
        _CORRELATION_ID in record.message and record.levelno == logging.WARNING
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# AC10 — 5xx → 200 applied → True, 2 calls, same correlation_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_day_role_5xx_then_200_returns_true(monkeypatch):
    """AC10: 5xx then 200 applied → True, 2 calls, correlation_id unchanged."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    import json

    with respx.mock() as mock_transport:
        route = mock_transport.post(_SYNC_URL).mock(
            side_effect=[
                httpx.Response(503, json={"detail": "unavailable"}),
                httpx.Response(200, json=_APPLIED_BODY),
            ]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await _call_set(_make_bot_client())

    assert result is True
    assert route.call_count == 2

    # Verify both calls sent the same correlation_id.
    correlation_ids = [json.loads(call.request.content)["correlation_id"] for call in route.calls]
    assert correlation_ids[0] == correlation_ids[1] == _CORRELATION_ID


# ---------------------------------------------------------------------------
# AC11 — action="assign" includes day_number; action="unassign" omits day_number
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_day_role_set_includes_day_number(monkeypatch):
    """AC11a: action='assign' payload includes day_number field."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    import json

    with respx.mock() as mock_transport:
        route = mock_transport.post(_SYNC_URL).mock(
            return_value=httpx.Response(200, json=_APPLIED_BODY)
        )
        await _call_set(_make_bot_client())

    body = json.loads(route.calls[0].request.content)
    assert "day_number" in body
    assert body["day_number"] == _DAY_NUMBER


@pytest.mark.asyncio
async def test_sync_day_role_clear_omits_day_number(monkeypatch):
    """AC11b: action='unassign' payload omits day_number field."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    import json

    unassign_body = {"status": "applied", "added": [], "removed": ["Day 1"]}

    with respx.mock() as mock_transport:
        route = mock_transport.post(_SYNC_URL).mock(
            return_value=httpx.Response(200, json=unassign_body)
        )
        await _call_clear(_make_bot_client())

    body = json.loads(route.calls[0].request.content)
    assert "day_number" not in body
    assert body["action"] == "unassign"


# ---------------------------------------------------------------------------
# AC12 — assigned_at serializes with millisecond precision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_day_role_assigned_at_millisecond_precision(monkeypatch):
    """AC12: assigned_at is serialized as ISO-8601 UTC with millisecond precision."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    import json

    with respx.mock() as mock_transport:
        route = mock_transport.post(_SYNC_URL).mock(
            return_value=httpx.Response(200, json=_APPLIED_BODY)
        )
        await _call_set(_make_bot_client())

    body = json.loads(route.calls[0].request.content)
    assigned_at_str: str = body["assigned_at"]

    # Must be UTC (ends with Z or +00:00).
    assert assigned_at_str.endswith("Z") or assigned_at_str.endswith("+00:00")

    # Must have at least millisecond precision (dot followed by ≥3 digits).
    import re

    assert re.search(
        r"\.\d{3,}", assigned_at_str
    ), f"assigned_at lacks millisecond precision: {assigned_at_str!r}"

    # Verify the actual value round-trips correctly.
    assert "2026-05-14T13:52:18.247" in assigned_at_str


# ---------------------------------------------------------------------------
# PR #409 review — AC13: HTTPS enforcement (contract §4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_day_role_non_https_url_returns_false_errors(monkeypatch, caplog):
    """AC13: Non-HTTPS DAY_ROLE_SYNC_URL returns False and emits ERROR log.

    Contract §4 requires HTTPS.  The ERROR log must include the offending
    scheme but not the full URL to avoid leaking a misconfigured target.
    """
    import logging

    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr(
        "app.config.settings.day_role_sync_url",
        "http://insecure-receiver.internal/role-sync",
    )

    with caplog.at_level(logging.ERROR, logger="app.services.bot_client"):
        with respx.mock(assert_all_called=False) as mock_transport:
            result = await _call_set(_make_bot_client())

    assert result is False
    assert mock_transport.calls.call_count == 0
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error_records, "Expected an ERROR log for non-HTTPS URL"
    # Scheme must appear in log; full URL must not.
    assert any("http" in r.message for r in error_records)
    assert not any("insecure-receiver.internal" in r.message for r in error_records)
    assert any(_CORRELATION_ID in r.message for r in error_records)


# ---------------------------------------------------------------------------
# PR #409 review — AC14/15: UTC validation for assigned_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_day_role_non_utc_tzinfo_raises_value_error(monkeypatch):
    """AC14: assigned_at with a non-UTC tzinfo raises ValueError immediately.

    This is a programming error by the caller — raise rather than return False.
    """
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    # UTC+5 — valid aware datetime but not UTC.
    non_utc_dt = datetime(2026, 5, 14, 13, 52, 18, tzinfo=timezone(timedelta(hours=5)))

    with pytest.raises(ValueError, match="UTC"):
        await _make_bot_client().sync_day_role(
            discord_id=_DISCORD_ID,
            siege_id=_SIEGE_ID,
            day_number=_DAY_NUMBER,
            action="assign",
            assigned_at=non_utc_dt,
            correlation_id=_CORRELATION_ID,
        )


@pytest.mark.asyncio
async def test_sync_day_role_naive_datetime_raises_value_error(monkeypatch):
    """AC15: assigned_at with no tzinfo (naive datetime) raises ValueError.

    A naive datetime would produce an incorrect ISO-8601 string because
    isoformat() omits the offset entirely — the payload would be malformed.
    """
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    naive_dt = datetime(2026, 5, 14, 13, 52, 18)  # no tzinfo

    with pytest.raises(ValueError, match="UTC"):
        await _make_bot_client().sync_day_role(
            discord_id=_DISCORD_ID,
            siege_id=_SIEGE_ID,
            day_number=_DAY_NUMBER,
            action="assign",
            assigned_at=naive_dt,
            correlation_id=_CORRELATION_ID,
        )


# ---------------------------------------------------------------------------
# AC16 — discord_role_id present on assign → payload includes "discord_role_id"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac16_discord_role_id_included_on_assign(monkeypatch):
    """AC16: discord_role_id=999 on assign → payload has "discord_role_id": "999"."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    import json

    with respx.mock() as mock_transport:
        route = mock_transport.post(_SYNC_URL).mock(
            return_value=httpx.Response(200, json=_APPLIED_BODY)
        )
        result = await _make_bot_client().sync_day_role(
            discord_id=_DISCORD_ID,
            siege_id=_SIEGE_ID,
            day_number=_DAY_NUMBER,
            action="assign",
            assigned_at=_ASSIGNED_AT,
            correlation_id=_CORRELATION_ID,
            discord_role_id=999,
        )

    assert result is True
    body = json.loads(route.calls[0].request.content)
    assert body["discord_role_id"] == "999"


# ---------------------------------------------------------------------------
# AC17 — discord_role_id=None on assign → payload omits "discord_role_id"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac17_discord_role_id_absent_when_none(monkeypatch):
    """AC17: discord_role_id=None on assign → "discord_role_id" absent from payload."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    import json

    with respx.mock() as mock_transport:
        route = mock_transport.post(_SYNC_URL).mock(
            return_value=httpx.Response(200, json=_APPLIED_BODY)
        )
        result = await _make_bot_client().sync_day_role(
            discord_id=_DISCORD_ID,
            siege_id=_SIEGE_ID,
            day_number=_DAY_NUMBER,
            action="assign",
            assigned_at=_ASSIGNED_AT,
            correlation_id=_CORRELATION_ID,
            discord_role_id=None,
        )

    assert result is True
    body = json.loads(route.calls[0].request.content)
    assert "discord_role_id" not in body


# ---------------------------------------------------------------------------
# AC18 — discord_role_id on unassign → payload omits "discord_role_id"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac18_discord_role_id_absent_on_unassign(monkeypatch):
    """AC18: discord_role_id=999 on unassign → "discord_role_id" absent from payload."""
    monkeypatch.setattr("app.config.settings.day_role_sync_enabled", True)
    monkeypatch.setattr("app.config.settings.day_role_sync_url", _SYNC_URL)

    import json

    unassign_body = {"status": "applied", "added": [], "removed": ["Day 1"]}

    with respx.mock() as mock_transport:
        route = mock_transport.post(_SYNC_URL).mock(
            return_value=httpx.Response(200, json=unassign_body)
        )
        result = await _make_bot_client().sync_day_role(
            discord_id=_DISCORD_ID,
            siege_id=_SIEGE_ID,
            day_number=None,
            action="unassign",
            assigned_at=_ASSIGNED_AT,
            correlation_id=_CORRELATION_ID,
            discord_role_id=999,
        )

    assert result is True
    body = json.loads(route.calls[0].request.content)
    assert "discord_role_id" not in body
