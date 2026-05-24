"""Tests for the bot HTTP API endpoints."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

import app.http_api as http_api_module
from app.http_api import RoleSyncRequest, app

API_KEY = "test-key"
AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture(autouse=True)
def patch_api_key(monkeypatch):
    """Override the bot_api_key setting for all tests."""
    monkeypatch.setattr(http_api_module.settings, "bot_api_key", API_KEY)


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _make_mock_bot(ready: bool = True) -> MagicMock:
    bot = MagicMock()
    bot.is_ready.return_value = ready
    bot.send_dm = AsyncMock()
    bot.post_message = AsyncMock()
    bot.post_image = AsyncMock()
    bot.get_members = AsyncMock(return_value=[])
    bot.apply_day_role = AsyncMock(return_value=("applied", "Day 1"))
    return bot


# ---------------------------------------------------------------------------
# GET /version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_version_returns_200(client):
    """GET /api/version responds 200 with a 'version' key."""
    async with client as c:
        response = await c.get("/api/version")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data


@pytest.mark.asyncio
async def test_version_old_path_returns_404(client):
    """GET /version (old path) must return 404 — route was moved to /api/version."""
    async with client as c:
        response = await c.get("/version")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_version_bare_semver_in_local_dev(client, monkeypatch, tmp_path):
    """When BUILD_NUMBER / GIT_SHA are absent the version is the bare semver."""
    version_file = tmp_path / "VERSION"
    version_file.write_text("2.3.4")
    monkeypatch.delenv("BUILD_NUMBER", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)

    original = http_api_module._VERSION_FILE
    http_api_module._VERSION_FILE = version_file
    try:
        async with client as c:
            response = await c.get("/api/version")
    finally:
        http_api_module._VERSION_FILE = original

    assert response.status_code == 200
    assert response.json()["version"] == "2.3.4"


@pytest.mark.asyncio
async def test_version_includes_build_suffix_when_env_vars_set(client, monkeypatch, tmp_path):
    """When BUILD_NUMBER and GIT_SHA are set, version is 'semver+build.sha7'."""
    version_file = tmp_path / "VERSION"
    version_file.write_text("1.0.1")
    monkeypatch.setenv("BUILD_NUMBER", "42")
    monkeypatch.setenv("GIT_SHA", "abc1234567890")

    original = http_api_module._VERSION_FILE
    http_api_module._VERSION_FILE = version_file
    try:
        async with client as c:
            response = await c.get("/api/version")
    finally:
        http_api_module._VERSION_FILE = original

    assert response.status_code == 200
    assert response.json()["version"] == "1.0.1+42.abc1234"


@pytest.mark.asyncio
async def test_version_unknown_when_version_file_missing(client, monkeypatch, tmp_path):
    """When the VERSION file is absent, semver falls back to 'unknown'."""
    monkeypatch.delenv("BUILD_NUMBER", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)

    original = http_api_module._VERSION_FILE
    http_api_module._VERSION_FILE = tmp_path / "NONEXISTENT"
    try:
        async with client as c:
            response = await c.get("/api/version")
    finally:
        http_api_module._VERSION_FILE = original

    assert response.status_code == 200
    assert response.json()["version"] == "unknown"


@pytest.mark.asyncio
async def test_version_bare_semver_when_env_vars_are_unknown_literal(client, monkeypatch, tmp_path):
    """Env vars explicitly set to 'unknown' should yield bare semver (no suffix)."""
    version_file = tmp_path / "VERSION"
    version_file.write_text("1.2.3")
    monkeypatch.setenv("BUILD_NUMBER", "unknown")
    monkeypatch.setenv("GIT_SHA", "unknown")

    original = http_api_module._VERSION_FILE
    http_api_module._VERSION_FILE = version_file
    try:
        async with client as c:
            response = await c.get("/api/version")
    finally:
        http_api_module._VERSION_FILE = original

    assert response.status_code == 200
    assert response.json()["version"] == "1.2.3"


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_no_bot(client):
    http_api_module._bot = None
    async with client as c:
        response = await c.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["bot_connected"] is False


@pytest.mark.asyncio
async def test_health_with_bot_connected(client):
    http_api_module._bot = _make_mock_bot(ready=True)
    async with client as c:
        response = await c.get("/api/health")
    assert response.status_code == 200
    assert response.json()["bot_connected"] is True
    http_api_module._bot = None


# ---------------------------------------------------------------------------
# POST /api/notify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_success(client):
    bot = _make_mock_bot()
    http_api_module._bot = bot
    async with client as c:
        response = await c.post(
            "/api/notify",
            json={"username": "testuser", "message": "Hello!"},
            headers=AUTH_HEADER,
        )
    assert response.status_code == 200
    assert response.json() == {"status": "sent"}
    bot.send_dm.assert_awaited_once_with("testuser", "Hello!")
    http_api_module._bot = None


@pytest.mark.asyncio
async def test_notify_bot_not_ready_returns_503(client):
    http_api_module._bot = None
    async with client as c:
        response = await c.post(
            "/api/notify",
            json={"username": "testuser", "message": "Hello!"},
            headers=AUTH_HEADER,
        )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_notify_member_not_found_returns_404(client):
    bot = _make_mock_bot()
    bot.send_dm.side_effect = ValueError("Member 'ghost' not found in guild")
    http_api_module._bot = bot
    async with client as c:
        response = await c.post(
            "/api/notify",
            json={"username": "ghost", "message": "Hi"},
            headers=AUTH_HEADER,
        )
    assert response.status_code == 404
    assert "ghost" in response.json()["detail"]
    http_api_module._bot = None


# ---------------------------------------------------------------------------
# POST /api/post-message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_message_success(client):
    bot = _make_mock_bot()
    http_api_module._bot = bot
    async with client as c:
        response = await c.post(
            "/api/post-message",
            json={"channel_name": "general", "message": "Siege ready!"},
            headers=AUTH_HEADER,
        )
    assert response.status_code == 200
    assert response.json() == {"status": "sent"}
    bot.post_message.assert_awaited_once_with("general", "Siege ready!")
    http_api_module._bot = None


@pytest.mark.asyncio
async def test_post_message_bot_not_ready_returns_503(client):
    http_api_module._bot = None
    async with client as c:
        response = await c.post(
            "/api/post-message",
            json={"channel_name": "general", "message": "Hi"},
            headers=AUTH_HEADER,
        )
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/post-image
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_image_success(client):
    """POST /api/post-image accepts channel_name as a multipart form field."""
    bot = _make_mock_bot()
    bot.post_image.return_value = "https://cdn.discordapp.com/attachments/123/board.png"
    http_api_module._bot = bot
    async with client as c:
        response = await c.post(
            "/api/post-image",
            data={"channel_name": "siege-images"},
            files={"file": ("board.png", b"fake-png-bytes", "image/png")},
            headers=AUTH_HEADER,
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "sent"
    assert data["url"] == "https://cdn.discordapp.com/attachments/123/board.png"
    bot.post_image.assert_awaited_once_with("siege-images", b"fake-png-bytes", "board.png")
    http_api_module._bot = None


@pytest.mark.asyncio
async def test_post_image_channel_name_as_query_param_is_rejected(client):
    """POST /api/post-image with channel_name as query param must fail (422)."""
    bot = _make_mock_bot()
    bot.post_image.return_value = "https://cdn.discordapp.com/attachments/123/board.png"
    http_api_module._bot = bot
    async with client as c:
        response = await c.post(
            "/api/post-image?channel_name=siege-images",
            files={"file": ("board.png", b"fake-png-bytes", "image/png")},
            headers=AUTH_HEADER,
        )
    # FastAPI will return 422 because channel_name is not in form body
    assert response.status_code == 422
    http_api_module._bot = None


@pytest.mark.asyncio
async def test_post_image_bot_not_ready_returns_503(client):
    http_api_module._bot = None
    async with client as c:
        response = await c.post(
            "/api/post-image",
            data={"channel_name": "siege-images"},
            files={"file": ("board.png", b"fake-png-bytes", "image/png")},
            headers=AUTH_HEADER,
        )
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/members
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_members_returns_list(client):
    members = [
        {"id": "123", "username": "alice", "display_name": "Alice"},
        {"id": "456", "username": "bob", "display_name": "Bob"},
    ]
    bot = _make_mock_bot()
    bot.get_members.return_value = members
    http_api_module._bot = bot
    async with client as c:
        response = await c.get("/api/members", headers=AUTH_HEADER)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["username"] == "alice"
    http_api_module._bot = None


@pytest.mark.asyncio
async def test_get_members_bot_not_ready_returns_503(client):
    http_api_module._bot = None
    async with client as c:
        response = await c.get("/api/members", headers=AUTH_HEADER)
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Discord exception translation — global handler (#419)
# ---------------------------------------------------------------------------
#
# Each block below covers one row of the translation table.  We vary the
# endpoint under test so the suite demonstrates the handler fires globally
# (not just for one call site).
#
# discord.Forbidden and discord.NotFound are subclasses of
# discord.HTTPException, so they must be caught before the generic
# HTTPException branch.
#
# Helper: build a minimal discord.HTTPException-like object.  The real
# constructors require a aiohttp.ClientResponse, so we mock the .status
# attribute directly.
# ---------------------------------------------------------------------------


def _discord_http_exc(status: int, text: str = "") -> discord.HTTPException:
    """Return a discord.HTTPException instance with a given HTTP status.

    Constructs via MagicMock to avoid requiring a live aiohttp response.
    The .status attribute is the one inspected by the exception handler.

    Args:
        status: Integer HTTP status code to attach to the exception.
        text: Optional response body text (maps to exc.text in discord.py).

    Returns:
        A discord.HTTPException (or subclass) instance with .status set.
    """
    response = MagicMock()
    response.status = status
    response.reason = "test"
    exc = discord.HTTPException(response, text)
    return exc


# -- discord.Forbidden → 403 -------------------------------------------------


@pytest.mark.asyncio
async def test_notify_discord_forbidden_returns_403(client):
    """discord.Forbidden from send_dm must translate to HTTP 403."""
    bot = _make_mock_bot()
    response = MagicMock()
    response.status = 403
    response.reason = "Forbidden"
    bot.send_dm.side_effect = discord.Forbidden(response, "Missing Permissions")
    http_api_module._bot = bot
    async with client as c:
        resp = await c.post(
            "/api/notify",
            json={"username": "alice", "message": "hi"},
            headers=AUTH_HEADER,
        )
    http_api_module._bot = None
    assert resp.status_code == 403
    assert "permission denied" in resp.json()["detail"].lower()


# -- discord.NotFound → 404 --------------------------------------------------


@pytest.mark.asyncio
async def test_post_message_discord_not_found_returns_404(client):
    """discord.NotFound from post_message must translate to HTTP 404."""
    bot = _make_mock_bot()
    response = MagicMock()
    response.status = 404
    response.reason = "Not Found"
    bot.post_message.side_effect = discord.NotFound(response, "Unknown Channel")
    http_api_module._bot = bot
    async with client as c:
        resp = await c.post(
            "/api/post-message",
            json={"channel_name": "no-such-channel", "message": "hi"},
            headers=AUTH_HEADER,
        )
    http_api_module._bot = None
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# -- discord.HTTPException (status < 500) → 502 ------------------------------


@pytest.mark.asyncio
async def test_post_image_discord_http_4xx_returns_502(client):
    """discord.HTTPException with status 429 (rate limit) must translate to 502."""
    bot = _make_mock_bot()
    bot.post_image.side_effect = _discord_http_exc(429, "You are being rate limited")
    http_api_module._bot = bot
    async with client as c:
        resp = await c.post(
            "/api/post-image",
            data={"channel_name": "siege-images"},
            files={"file": ("board.png", b"fake-png", "image/png")},
            headers=AUTH_HEADER,
        )
    http_api_module._bot = None
    assert resp.status_code == 502
    assert "Discord" in resp.json()["detail"]


# -- discord.HTTPException (status >= 500) → 503 -----------------------------


@pytest.mark.asyncio
async def test_notify_discord_http_5xx_returns_503(client):
    """discord.HTTPException with status 500 must translate to HTTP 503."""
    bot = _make_mock_bot()
    bot.send_dm.side_effect = _discord_http_exc(500, "Internal Server Error")
    http_api_module._bot = bot
    async with client as c:
        resp = await c.post(
            "/api/notify",
            json={"username": "alice", "message": "hi"},
            headers=AUTH_HEADER,
        )
    http_api_module._bot = None
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"].lower()


# -- asyncio.TimeoutError → 503 ----------------------------------------------


@pytest.mark.asyncio
async def test_post_message_timeout_returns_503(client):
    """asyncio.TimeoutError from post_message must translate to HTTP 503."""
    bot = _make_mock_bot()
    bot.post_message.side_effect = asyncio.TimeoutError()  # noqa: UP041
    http_api_module._bot = bot
    async with client as c:
        resp = await c.post(
            "/api/post-message",
            json={"channel_name": "general", "message": "hi"},
            headers=AUTH_HEADER,
        )
    http_api_module._bot = None
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"].lower()


# -- discord.Forbidden edge case: text=None → must not expose "None" ----------


@pytest.mark.asyncio
async def test_notify_discord_forbidden_with_text_none_returns_403(client):
    """discord.Forbidden with text=None must return 403 without "None" in body.

    Regression catch: the original handler used f"...: {exc.text}", which
    would render as "Discord permission denied: None" when the Discord API
    omits a text field.  The generic envelope must never include the literal
    string "None".
    """
    bot = _make_mock_bot()
    response = MagicMock()
    response.status = 403
    response.reason = "Forbidden"
    bot.send_dm.side_effect = discord.Forbidden(response, None)
    http_api_module._bot = bot
    async with client as c:
        resp = await c.post(
            "/api/notify",
            json={"username": "alice", "message": "hi"},
            headers=AUTH_HEADER,
        )
    http_api_module._bot = None
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Discord permission denied"
    assert "None" not in resp.json()["detail"]  # defense-in-depth regression catch


# -- discord.Forbidden logs raw text server-side, not in response -------------


@pytest.mark.asyncio
async def test_notify_discord_forbidden_logs_raw_text(client, caplog):
    """Raw exc.text must appear in server logs, not in the response body.

    Validates the info-disclosure fix: sensitive Discord context (channel
    names, permission names, role names) is logged at WARNING level for
    operator debugging but never returned to the caller.
    """
    secret = "Missing Permissions on #secret-channel"
    bot = _make_mock_bot()
    response = MagicMock()
    response.status = 403
    response.reason = "Forbidden"
    bot.send_dm.side_effect = discord.Forbidden(response, secret)
    http_api_module._bot = bot

    caplog.set_level(logging.WARNING, logger="app.http_api")
    async with client as c:
        resp = await c.post(
            "/api/notify",
            json={"username": "alice", "message": "hi"},
            headers=AUTH_HEADER,
        )

    http_api_module._bot = None
    # Raw text must NOT leak into the response body.
    assert "secret-channel" not in resp.json()["detail"]
    # Raw text MUST appear in the warning log.
    assert any("secret-channel" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# RoleSyncRequest model
# ---------------------------------------------------------------------------


def test_role_sync_request_full_payload():
    """RoleSyncRequest parses a complete v1.1 payload with discord_role_id."""
    req = RoleSyncRequest(
        discord_id="111000111000111001",
        siege_id=42,
        action="assign",
        assigned_at="2026-05-14T13:52:18.247Z",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        day_number=1,
        discord_role_id="999000999000999001",
    )
    assert req.discord_id == "111000111000111001"
    assert req.action == "assign"
    assert req.discord_role_id == "999000999000999001"
    assert req.day_number == 1


def test_role_sync_request_minimal_payload():
    """RoleSyncRequest accepts payload without optional discord_role_id/day_number."""
    req = RoleSyncRequest(
        discord_id="111000111000111001",
        siege_id=42,
        action="unassign",
        assigned_at="2026-05-14T13:52:18.247Z",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    )
    assert req.discord_role_id is None
    assert req.day_number is None


def test_role_sync_request_invalid_action_raises():
    """RoleSyncRequest rejects action values outside assign/unassign."""
    with pytest.raises((ValidationError, ValueError)):
        RoleSyncRequest(
            discord_id="111000111000111001",
            siege_id=42,
            action="delete",
            assigned_at="2026-05-14T13:52:18.247Z",
            correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        )


def test_role_sync_request_missing_required_field_raises():
    """RoleSyncRequest raises when discord_id is absent."""
    with pytest.raises((ValidationError, TypeError)):
        RoleSyncRequest(
            siege_id=42,
            action="assign",
            assigned_at="2026-05-14T13:52:18.247Z",
            correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        )


# ---------------------------------------------------------------------------
# POST /api/role-sync
# ---------------------------------------------------------------------------

_ROLE_SYNC_PAYLOAD = {
    "discord_id": "111000111000111001",
    "siege_id": 42,
    "action": "assign",
    "assigned_at": "2026-05-14T13:52:18.247Z",
    "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "day_number": 1,
    "discord_role_id": "888000888000888001",
}


@pytest.mark.asyncio
async def test_role_sync_assign_with_role_id_returns_applied(client):
    """AC-d1: assign with discord_role_id → 200 {"status": "applied", "added": [...], "removed": []}."""
    bot = _make_mock_bot()
    bot.apply_day_role = AsyncMock(return_value=("applied", "Day 1"))
    http_api_module._bot = bot
    async with client as c:
        response = await c.post(
            "/api/role-sync",
            json=_ROLE_SYNC_PAYLOAD,
            headers=AUTH_HEADER,
        )
    http_api_module._bot = None
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "applied"
    assert data["added"] == ["Day 1"]
    assert data["removed"] == []
    bot.apply_day_role.assert_awaited_once_with(
        discord_id="111000111000111001",
        role_id="888000888000888001",
        action="assign",
    )


@pytest.mark.asyncio
async def test_role_sync_assign_without_role_id_returns_skipped(client, caplog):
    """AC-d2: assign with discord_role_id absent → 200 skipped + WARNING log."""
    bot = _make_mock_bot()
    http_api_module._bot = bot
    payload = {k: v for k, v in _ROLE_SYNC_PAYLOAD.items() if k != "discord_role_id"}

    caplog.set_level(logging.WARNING, logger="app.http_api")
    async with client as c:
        response = await c.post(
            "/api/role-sync",
            json=payload,
            headers=AUTH_HEADER,
        )
    http_api_module._bot = None
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "skipped"
    assert "discord_role_id" in data.get("reason", "")
    bot.apply_day_role.assert_not_awaited()
    assert any(
        "discord_role_id" in r.message.lower() for r in caplog.records
    )


@pytest.mark.asyncio
async def test_role_sync_unassign_with_role_id_returns_applied(client):
    """AC-d3: unassign with discord_role_id → 200 {"status": "applied", "added": [], "removed": [...]}."""
    bot = _make_mock_bot()
    bot.apply_day_role = AsyncMock(return_value=("applied", "Day 1"))
    http_api_module._bot = bot
    payload = {**_ROLE_SYNC_PAYLOAD, "action": "unassign"}
    async with client as c:
        response = await c.post(
            "/api/role-sync",
            json=payload,
            headers=AUTH_HEADER,
        )
    http_api_module._bot = None
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "applied"
    assert data["added"] == []
    assert data["removed"] == ["Day 1"]


@pytest.mark.asyncio
async def test_role_sync_missing_auth_returns_401(client):
    """AC-d4: missing/wrong Bearer token → 401."""
    async with client as c:
        response = await c.post(
            "/api/role-sync",
            json=_ROLE_SYNC_PAYLOAD,
            headers={"Authorization": "Bearer wrong-key"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_role_sync_member_not_found_returns_404(client):
    """AC-d5: member not in guild → 404."""
    bot = _make_mock_bot()
    response = MagicMock()
    response.status = 404
    response.reason = "Not Found"
    bot.apply_day_role = AsyncMock(
        side_effect=discord.NotFound(response, "Unknown Member")
    )
    http_api_module._bot = bot
    async with client as c:
        response_http = await c.post(
            "/api/role-sync",
            json=_ROLE_SYNC_PAYLOAD,
            headers=AUTH_HEADER,
        )
    http_api_module._bot = None
    assert response_http.status_code == 404


@pytest.mark.asyncio
async def test_role_sync_role_not_found_returns_404(client):
    """AC-d6: role_id not in guild → 404."""
    bot = _make_mock_bot()
    bot.apply_day_role = AsyncMock(
        side_effect=ValueError("Role '888000888000888001' not found in guild")
    )
    http_api_module._bot = bot
    async with client as c:
        response = await c.post(
            "/api/role-sync",
            json=_ROLE_SYNC_PAYLOAD,
            headers=AUTH_HEADER,
        )
    http_api_module._bot = None
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_role_sync_malformed_payload_returns_422(client):
    """AC-d7: missing required field discord_id → 422 (FastAPI validation)."""
    bot = _make_mock_bot()
    http_api_module._bot = bot
    payload = {k: v for k, v in _ROLE_SYNC_PAYLOAD.items() if k != "discord_id"}
    async with client as c:
        response = await c.post(
            "/api/role-sync",
            json=payload,
            headers=AUTH_HEADER,
        )
    http_api_module._bot = None
    assert response.status_code == 422
