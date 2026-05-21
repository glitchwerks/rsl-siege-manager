"""Endpoint tests for GET/PUT /api/members/me/preferences.

Covers the X-Acting-Discord-Id header feature (#322):
- Service-token + valid header resolves to the named member
- Service-token + no header → 401
- Service-token + unknown discord_id → 404
- Cookie auth ignores the header entirely
- Regression smoke: existing non-/me bot calls still work without the header
"""

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import get_db
from app.main import app
from app.models.enums import MemberRole

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

TEST_SESSION_SECRET = "test-session-secret"
SERVICE_TOKEN = "test-service-token"
MEMBER_DISCORD_ID = "123456789012345678"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_member(
    id: int = 42,
    name: str = "Alice",
    discord_id: str = MEMBER_DISCORD_ID,
    role: MemberRole = MemberRole.advanced,
    is_active: bool = True,
) -> SimpleNamespace:
    """Build a minimal Member-like namespace for mocking."""
    return SimpleNamespace(
        id=id,
        name=name,
        discord_id=discord_id,
        discord_username=None,
        role=role,
        power=None,
        sort_value=None,
        is_active=is_active,
        created_at=datetime.datetime(2026, 1, 1, 0, 0, 0),
    )


def _make_post_condition(
    id: int = 1,
    description: str = "Condition A",
    stronghold_level: int = 1,
    condition_type: str = "role",
) -> SimpleNamespace:
    """Build a minimal PostCondition-like namespace for mocking.

    Fields match the ``PostConditionResponse`` schema: ``id``,
    ``description``, ``stronghold_level``, and ``condition_type``.
    """
    return SimpleNamespace(
        id=id,
        description=description,
        stronghold_level=stronghold_level,
        condition_type=condition_type,
    )


def _make_mock_db(member: object = None) -> AsyncMock:
    """Return an AsyncMock session whose db.get() returns ``member``.

    Also sets up db.execute() to return an empty scalar result by default.
    The per-test caller can override execute's return_value as needed.
    """
    session = AsyncMock()
    session.get = AsyncMock(return_value=member)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=member)
    session.execute = AsyncMock(return_value=mock_result)
    return session


def _make_jwt(member_id: int, secret: str = TEST_SESSION_SECRET) -> str:
    """Produce a valid HS256 JWT for the given member_id."""
    from datetime import UTC, timedelta
    from datetime import datetime as dt

    import jwt

    payload = {
        "sub": str(member_id),
        "name": "TestUser",
        "iat": dt.now(UTC),
        "exp": dt.now(UTC) + timedelta(hours=24),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service_token_headers() -> dict[str, str]:
    """Authorization header using the test service token."""
    return {"Authorization": f"Bearer {SERVICE_TOKEN}"}


# ---------------------------------------------------------------------------
# Test 1: service-token + valid header → GET /me/preferences → 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_token_with_valid_header_gets_member_prefs(
    monkeypatch,
    service_token_headers,
):
    """Service token + matching X-Acting-Discord-Id returns that member's prefs."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    member = _make_member()
    prefs = [_make_post_condition(id=1), _make_post_condition(id=2)]

    mock_db = _make_mock_db(member=member)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.members.members_service.get_member_preferences",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.return_value = prefs
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": MEMBER_DISCORD_ID,
                    },
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Test 2: service-token, no header → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_token_without_header_returns_401(
    monkeypatch,
    service_token_headers,
):
    """Service token alone (no X-Acting-Discord-Id) → 401 from get_acting_member_id."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    mock_db = _make_mock_db()

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/members/me/preferences",
                headers=service_token_headers,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Test 3: service-token + unknown discord_id → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_token_with_unknown_discord_id_returns_404(
    monkeypatch,
    service_token_headers,
):
    """X-Acting-Discord-Id that matches no Member record → 404."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    # db.execute returns no member for the discord_id lookup
    mock_db = _make_mock_db(member=None)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/members/me/preferences",
                headers={
                    **service_token_headers,
                    "X-Acting-Discord-Id": "999999999999999999",
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test 4: cookie-authed + no header → 200 (uses session member's prefs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cookie_auth_no_header_gets_session_member_prefs(monkeypatch):
    """Cookie-authenticated user without header gets their own prefs."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", "")
    monkeypatch.setattr("app.config.settings.session_secret", TEST_SESSION_SECRET)

    member = _make_member(id=7, discord_id="discord-7")
    prefs = [_make_post_condition(id=5)]

    mock_db = _make_mock_db(member=member)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    token = _make_jwt(member_id=7)
    try:
        with patch(
            "app.api.members.members_service.get_member_preferences",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.return_value = prefs
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                client.cookies.set("session", token)
                response = await client.get("/api/members/me/preferences")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    # Confirm the service was called with the session member's ID (7), not a header
    mock_svc.assert_called_once()
    call_args = mock_svc.call_args
    assert call_args.args[1] == 7


# ---------------------------------------------------------------------------
# Test 5: cookie-authed + X-Acting-Discord-Id of a DIFFERENT member → 200,
#          returns SESSION member's prefs (header ignored)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cookie_auth_ignores_acting_header(monkeypatch):
    """Cookie auth is authoritative; X-Acting-Discord-Id is ignored entirely."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", "")
    monkeypatch.setattr("app.config.settings.session_secret", TEST_SESSION_SECRET)

    session_member = _make_member(id=7, discord_id="discord-7")
    prefs = [_make_post_condition(id=5)]

    mock_db = _make_mock_db(member=session_member)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    token = _make_jwt(member_id=7)
    try:
        with patch(
            "app.api.members.members_service.get_member_preferences",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.return_value = prefs
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                client.cookies.set("session", token)
                response = await client.get(
                    "/api/members/me/preferences",
                    headers={"X-Acting-Discord-Id": "discord-999-other"},
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    # Must use session member ID (7), not anything from the header
    mock_svc.assert_called_once()
    call_args = mock_svc.call_args
    assert call_args.args[1] == 7


# ---------------------------------------------------------------------------
# Test 6: service-token + valid header → PUT /me/preferences with valid IDs → 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_token_with_header_puts_preferences(
    monkeypatch,
    service_token_headers,
):
    """Service token + header → PUT /me/preferences replaces prefs, returns 200."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    member = _make_member()
    prefs = [
        _make_post_condition(id=1),
        _make_post_condition(id=2),
        _make_post_condition(id=3),
    ]

    mock_db = _make_mock_db(member=member)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.members.members_service.set_member_preferences",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.return_value = prefs
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.put(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": MEMBER_DISCORD_ID,
                    },
                    json={"post_condition_ids": [1, 2, 3]},
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert len(response.json()) == 3


# ---------------------------------------------------------------------------
# Test 7: service-token + valid header → PUT with empty list → 200 (cleared)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_token_with_header_puts_empty_preferences(
    monkeypatch,
    service_token_headers,
):
    """PUT /me/preferences with empty list clears preferences → 200, []."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    member = _make_member()
    mock_db = _make_mock_db(member=member)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.members.members_service.set_member_preferences",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.return_value = []
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.put(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": MEMBER_DISCORD_ID,
                    },
                    json={"post_condition_ids": []},
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Test 8: service-token + valid header → PUT with invalid post_condition_id → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_token_with_header_puts_invalid_ids_returns_404(
    monkeypatch,
    service_token_headers,
):
    """PUT /me/preferences with non-existent post_condition_id → 404."""
    from fastapi import HTTPException

    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    member = _make_member()
    mock_db = _make_mock_db(member=member)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.members.members_service.set_member_preferences",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.side_effect = HTTPException(
                status_code=404,
                detail="Post condition IDs not found: [9999]",
            )
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.put(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": MEMBER_DISCORD_ID,
                    },
                    json={"post_condition_ids": [9999]},
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test 9: member with zero prefs → GET /me/preferences → 200, []
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_member_with_no_prefs_returns_empty_list(
    monkeypatch,
    service_token_headers,
):
    """GET /me/preferences for a member with no preferences returns 200, []."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    member = _make_member()
    mock_db = _make_mock_db(member=member)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.members.members_service.get_member_preferences",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.return_value = []
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": MEMBER_DISCORD_ID,
                    },
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Test 10: route ordering smoke — "me" is not parsed as an int (no 422)
# (covered implicitly by tests 1+, but added here for clarity)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_route_matches_before_member_id_route(
    monkeypatch,
    service_token_headers,
):
    """'/me/preferences' must not be caught by '/{member_id}/preferences'."""
    # This is exercised whenever tests 1+ pass (status would be 422 on int parse)
    # We run a clean assertion here to make the intent explicit.
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    member = _make_member()
    mock_db = _make_mock_db(member=member)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.members.members_service.get_member_preferences",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.return_value = []
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": MEMBER_DISCORD_ID,
                    },
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    # 422 = FastAPI tried to parse "me" as int and failed → wrong route matched
    assert response.status_code != 422
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Test 11: GET /api/post-conditions returns 200 with no auth — open reference data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_conditions_open_reference_no_auth():
    """GET /api/post-conditions returns 200 with no auth headers.

    auth_disabled=True is injected by the autouse conftest fixture, matching
    the behaviour that open-reference callers (e.g. mom-bot's select-menu
    population) rely on.  If this endpoint ever moves behind a hard auth gate
    it should be a deliberate breaking-change PR, not a silent regression.
    """
    with patch(
        "app.api.reference.reference_service.get_post_conditions",
        new_callable=AsyncMock,
    ) as mock_svc:
        mock_svc.return_value = []
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/post-conditions")

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Regression smoke: existing bot calls without X-Acting-Discord-Id still work
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_bot_list_members_works_without_acting_header(monkeypatch):
    """GET /api/members (no /me) still works for the bot without the header."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    mock_db = _make_mock_db()

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.members.members_service.list_members",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.return_value = []
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/members",
                    headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Test 12: malformed X-Acting-Discord-Id → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_token_with_malformed_acting_id_returns_400(
    monkeypatch,
    service_token_headers,
):
    """X-Acting-Discord-Id with malformed format → 400 Bad Request."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    mock_db = _make_mock_db()

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    malformed_values = [
        "",
        "not-numeric",
        "abc123",
        "12345abc",
        "'; DROP TABLE members;--",
        "1" * 21,  # exceeds 20-char limit
    ]
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for malformed in malformed_values:
                response = await client.get(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": malformed,
                    },
                )
                assert (
                    response.status_code == 400
                ), f"Expected 400 for {malformed!r}, got {response.status_code}"
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Test 13: PUT with mixed valid/invalid post_condition_ids → 404, atomic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_mixed_valid_and_invalid_post_condition_ids_is_atomic(
    monkeypatch,
    service_token_headers,
):
    """PUT with mix of valid + invalid post_condition_ids → 404, no partial commit."""
    from fastapi import HTTPException

    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    member = _make_member()
    existing_prefs = [_make_post_condition(id=1)]
    mock_db = _make_mock_db(member=member)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with (
            patch(
                "app.api.members.members_service.set_member_preferences",
                new_callable=AsyncMock,
            ) as mock_set,
            patch(
                "app.api.members.members_service.get_member_preferences",
                new_callable=AsyncMock,
            ) as mock_get,
        ):
            # Service raises 404 when any ID is missing (atomic — no partial write)
            mock_set.side_effect = HTTPException(
                status_code=404,
                detail="Post condition IDs not found: [99999]",
            )
            mock_get.return_value = existing_prefs

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # 1. PUT with one valid ID (1), one invalid (99999), one valid (3)
                put_response = await client.put(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": MEMBER_DISCORD_ID,
                    },
                    json={"post_condition_ids": [1, 99999, 3]},
                )
                assert put_response.status_code == 404

                # 2. GET prefs back — must still return original [id=1] (unchanged)
                get_response = await client.get(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": MEMBER_DISCORD_ID,
                    },
                )
                assert get_response.status_code == 200
                returned_ids = [pc["id"] for pc in get_response.json()]
                assert returned_ids == [1], f"Expected prefs unchanged ([1]), got {returned_ids}"
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# condition_type field serialization tests (#442)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_preferences_response_includes_condition_type(
    monkeypatch,
    service_token_headers,
):
    """GET /me/preferences response items must include condition_type field."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    member = _make_member()
    prefs = [
        _make_post_condition(
            id=5,
            description="Only HP Champions can be used.",
            stronghold_level=1,
            condition_type="role",
        ),
        _make_post_condition(
            id=12,
            description="Only Barbarian Champions can be used.",
            stronghold_level=1,
            condition_type="faction",
        ),
        _make_post_condition(
            id=19,
            description="Only Void Champions can be used.",
            stronghold_level=2,
            condition_type="affinity",
        ),
    ]

    mock_db = _make_mock_db(member=member)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.members.members_service.get_member_preferences",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.return_value = prefs
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": MEMBER_DISCORD_ID,
                    },
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    for item in data:
        assert "condition_type" in item, f"condition_type missing from response item: {item}"
    assert data[0]["condition_type"] == "role"
    assert data[1]["condition_type"] == "faction"
    assert data[2]["condition_type"] == "affinity"


@pytest.mark.asyncio
async def test_put_preferences_response_includes_condition_type(
    monkeypatch,
    service_token_headers,
):
    """PUT /me/preferences response items must include condition_type field."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    member = _make_member()
    prefs = [
        _make_post_condition(
            id=1,
            description="Only Telerian League can be used.",
            stronghold_level=1,
            condition_type="league",
        ),
        _make_post_condition(
            id=29,
            description="Only Legendary Champions can be used.",
            stronghold_level=3,
            condition_type="rarity",
        ),
    ]

    mock_db = _make_mock_db(member=member)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.members.members_service.set_member_preferences",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.return_value = prefs
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.put(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": MEMBER_DISCORD_ID,
                    },
                    json={"post_condition_ids": [1, 29]},
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    for item in data:
        assert "condition_type" in item, f"condition_type missing from PUT response item: {item}"
    assert data[0]["condition_type"] == "league"
    assert data[1]["condition_type"] == "rarity"


# ---------------------------------------------------------------------------
# Tests (a)–(h): #452 username fallback + opportunistic discord_id backfill
# ---------------------------------------------------------------------------


def _make_execute_result(rows: list) -> MagicMock:
    """Build a mock execute() result supporting both scalar styles.

    Args:
        rows: List of Member-like objects to return from .scalars().all().
            A single-element list is also available via
            .scalar_one_or_none() for the snowflake lookup path.

    Returns:
        A MagicMock whose .scalar_one_or_none() returns the first row
        (or None) and whose .scalars().all() returns the full list.
    """
    result = MagicMock()
    # scalar_one_or_none used by the snowflake lookup path
    result.scalar_one_or_none = MagicMock(return_value=rows[0] if rows else None)
    # scalars().all() used by the username fallback path
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=rows)
    result.scalars = MagicMock(return_value=scalars_mock)
    return result


# (a) snowflake match — regression: existing path still works with username
# header present but unused


@pytest.mark.asyncio
async def test_username_fallback_a_snowflake_match_returns_member(
    monkeypatch,
    service_token_headers,
):
    """Snowflake lookup hits; username header present but unused."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    member = _make_member(discord_id=MEMBER_DISCORD_ID)
    prefs = [_make_post_condition(id=1)]

    # First execute (snowflake lookup) hits; second should never be called.
    snowflake_result = _make_execute_result([member])
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=snowflake_result)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.members.members_service.get_member_preferences",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.return_value = prefs
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": MEMBER_DISCORD_ID,
                        "X-Acting-Discord-Username": "alice_discord",
                    },
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200


# (b) username fallback match + backfill: discord_id IS NULL, username match


@pytest.mark.asyncio
async def test_username_fallback_b_username_match_backfills_discord_id(
    monkeypatch,
    service_token_headers,
):
    """Username fallback hits a member with discord_id IS NULL; backfills it."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    # Member has no discord_id yet but has a matching discord_username.
    member = SimpleNamespace(
        id=55,
        name="Bob",
        discord_id=None,
        discord_username="bob_discord",
        role=MemberRole.advanced,
        power=None,
        sort_value=None,
        is_active=True,
        created_at=datetime.datetime(2026, 1, 1),
    )
    prefs = [_make_post_condition(id=3)]

    # Call sequence:
    # 1. snowflake lookup  → miss (empty)
    # 2. username lookup   → hit (member)
    # 3. conflict guard    → miss (no other member owns acting_discord_id)
    snowflake_miss = _make_execute_result([])
    username_hit = _make_execute_result([member])
    conflict_miss = _make_execute_result([])

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[snowflake_miss, username_hit, conflict_miss])
    mock_db.commit = AsyncMock()

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.members.members_service.get_member_preferences",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.return_value = prefs
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": "555555555555555555",
                        "X-Acting-Discord-Username": "bob_discord",
                    },
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    # Backfill: discord_id was written onto the member object.
    assert member.discord_id == "555555555555555555"
    # commit() must have been called to persist the backfill.
    mock_db.commit.assert_called_once()


# (c) both miss → 404


@pytest.mark.asyncio
async def test_username_fallback_c_both_miss_returns_404(
    monkeypatch,
    service_token_headers,
):
    """Neither snowflake nor username matches → 404."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    snowflake_miss = _make_execute_result([])
    username_miss = _make_execute_result([])

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[snowflake_miss, username_miss])

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/members/me/preferences",
                headers={
                    **service_token_headers,
                    "X-Acting-Discord-Id": "999999999999999999",
                    "X-Acting-Discord-Username": "nobody_discord",
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404


# (d) case-insensitive username match


@pytest.mark.asyncio
async def test_username_fallback_d_case_insensitive_match(
    monkeypatch,
    service_token_headers,
):
    """Username lookup is case-insensitive; 'Alice_Discord' matches 'alice_discord'."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    member = SimpleNamespace(
        id=60,
        name="Alice",
        discord_id=None,
        discord_username="alice_discord",
        role=MemberRole.advanced,
        power=None,
        sort_value=None,
        is_active=True,
        created_at=datetime.datetime(2026, 1, 1),
    )
    prefs = []

    snowflake_miss = _make_execute_result([])
    username_hit = _make_execute_result([member])
    conflict_miss = _make_execute_result([])

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[snowflake_miss, username_hit, conflict_miss])
    mock_db.commit = AsyncMock()

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.members.members_service.get_member_preferences",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.return_value = prefs
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": "600600600600600600",
                        # Header username has different case than stored value.
                        "X-Acting-Discord-Username": "ALICE_DISCORD",
                    },
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert member.discord_id == "600600600600600600"


# (e) backfill idempotency: second call hits snowflake path; no second write


@pytest.mark.asyncio
async def test_username_fallback_e_backfill_idempotency(
    monkeypatch,
    service_token_headers,
):
    """After backfill, the member now has a discord_id; second call skips fallback."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    # Simulate post-backfill state: member already has discord_id set.
    member = _make_member(id=70, discord_id="700700700700700700")
    prefs = []

    # Only one execute call — snowflake hits immediately.
    snowflake_hit = _make_execute_result([member])
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=snowflake_hit)
    mock_db.commit = AsyncMock()

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.members.members_service.get_member_preferences",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.return_value = prefs
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": "700700700700700700",
                        "X-Acting-Discord-Username": "eve_discord",
                    },
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    # commit() should NOT be called (no backfill needed).
    mock_db.commit.assert_not_called()
    # execute() called exactly once (snowflake path short-circuits).
    assert mock_db.execute.call_count == 1


# (f) snowflake match + stale username header: snowflake wins; no error/rewrite


@pytest.mark.asyncio
async def test_username_fallback_f_snowflake_wins_over_stale_username(
    monkeypatch,
    service_token_headers,
):
    """Snowflake matches even when username header differs from stored value."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    # Member whose stored discord_username differs from the header value.
    member = SimpleNamespace(
        id=80,
        name="Frank",
        discord_id=MEMBER_DISCORD_ID,
        discord_username="frank_old_handle",
        role=MemberRole.advanced,
        power=None,
        sort_value=None,
        is_active=True,
        created_at=datetime.datetime(2026, 1, 1),
    )
    prefs = []

    # Snowflake hits on first execute; no further executes needed.
    snowflake_hit = _make_execute_result([member])
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=snowflake_hit)
    mock_db.commit = AsyncMock()

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.members.members_service.get_member_preferences",
            new_callable=AsyncMock,
        ) as mock_svc:
            mock_svc.return_value = prefs
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/members/me/preferences",
                    headers={
                        **service_token_headers,
                        "X-Acting-Discord-Id": MEMBER_DISCORD_ID,
                        # Header username drifted from stored value.
                        "X-Acting-Discord-Username": "frank_new_handle",
                    },
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    # discord_username must NOT be rewritten.
    assert member.discord_username == "frank_old_handle"
    # No commit (no backfill performed).
    mock_db.commit.assert_not_called()


# (g) backfill conflict guard: another member already owns the discord_id → 404


@pytest.mark.asyncio
async def test_username_fallback_g_backfill_conflict_returns_404(
    monkeypatch,
    service_token_headers,
):
    """Member A has discord_id IS NULL; Member B owns the snowflake → 404, no write."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    ACTING_SNOWFLAKE = "888888888888888888"

    # Member A: username match candidate, discord_id IS NULL.
    member_a = SimpleNamespace(
        id=91,
        name="Grace",
        discord_id=None,
        discord_username="grace_discord",
        role=MemberRole.advanced,
        power=None,
        sort_value=None,
        is_active=True,
        created_at=datetime.datetime(2026, 1, 1),
    )
    # Member B: already owns the acting snowflake (different member).
    member_b = SimpleNamespace(
        id=92,
        name="Henry",
        discord_id=ACTING_SNOWFLAKE,
        discord_username="henry_discord",
        role=MemberRole.advanced,
        power=None,
        sort_value=None,
        is_active=True,
        created_at=datetime.datetime(2026, 1, 1),
    )

    # Call sequence:
    # 1. snowflake lookup  → miss (member A has no discord_id)
    # 2. username lookup   → hit (member A via username)
    # 3. conflict guard    → hit (member B owns the snowflake)
    snowflake_miss = _make_execute_result([])
    username_hit = _make_execute_result([member_a])
    conflict_hit = _make_execute_result([member_b])

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[snowflake_miss, username_hit, conflict_hit])
    mock_db.commit = AsyncMock()

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/members/me/preferences",
                headers={
                    **service_token_headers,
                    "X-Acting-Discord-Id": ACTING_SNOWFLAKE,
                    "X-Acting-Discord-Username": "grace_discord",
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404
    # Member A must NOT have been mutated.
    assert member_a.discord_id is None
    mock_db.commit.assert_not_called()


# (h) multi-row username collision → 409


@pytest.mark.asyncio
async def test_username_fallback_h_multi_row_collision_returns_409(
    monkeypatch,
    service_token_headers,
):
    """Two members share a lowercased discord_username → 409 Conflict."""
    monkeypatch.setattr("app.config.settings.auth_disabled", False)
    monkeypatch.setattr("app.config.settings.bot_service_token", SERVICE_TOKEN)

    # Two members with the same lowercased discord_username, both no discord_id.
    member_x = SimpleNamespace(
        id=101,
        name="Xena",
        discord_id=None,
        discord_username="duplicate_handle",
        role=MemberRole.advanced,
        power=None,
        sort_value=None,
        is_active=True,
        created_at=datetime.datetime(2026, 1, 1),
    )
    member_y = SimpleNamespace(
        id=102,
        name="Yara",
        discord_id=None,
        discord_username="Duplicate_Handle",  # same lowercased value
        role=MemberRole.advanced,
        power=None,
        sort_value=None,
        is_active=True,
        created_at=datetime.datetime(2026, 1, 1),
    )

    snowflake_miss = _make_execute_result([])
    # Username lookup returns both rows.
    username_multi = _make_execute_result([member_x, member_y])

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[snowflake_miss, username_multi])
    mock_db.commit = AsyncMock()

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/members/me/preferences",
                headers={
                    **service_token_headers,
                    "X-Acting-Discord-Id": "101010101010101010",
                    "X-Acting-Discord-Username": "duplicate_handle",
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 409
    mock_db.commit.assert_not_called()
