"""Private content must require the exact guild and a current Discord officer role."""

from dataclasses import replace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.dependencies.auth import AuthenticatedUser, get_current_user
from app.main import app
from app.services.guide_access import can_view_mom_bot_guide

USER = AuthenticatedUser(7, "Officer", False, role="novice", discord_id="987654321")
MEMBER = {
    "is_member": True,
    "discord_id": USER.discord_id,
    "role_names": ["Clan Deputies"],
}


@pytest.fixture(autouse=True)
def guide_settings(monkeypatch):
    monkeypatch.setattr(settings, "auth_disabled", False)
    monkeypatch.setattr(settings, "dev_guild_guide_preview", False)
    monkeypatch.setattr(settings, "bot_service_token", "")
    monkeypatch.setattr(settings, "discord_guild_id", "123456789")
    monkeypatch.setattr(settings, "mom_bot_guide_guild_id", "123456789")
    monkeypatch.setattr(settings, "mom_bot_guide_officer_roles", "Clan Deputies")
    monkeypatch.setattr(settings, "discord_required_role", "Clan Deputies")


@pytest.fixture
def officer_session():
    async def current_officer():
        return USER

    app.dependency_overrides[get_current_user] = current_officer
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_officer_role_not_strength_role_controls_access():
    with patch(
        "app.services.guide_access.bot_client.get_member", new=AsyncMock(return_value=MEMBER)
    ):
        assert await can_view_mom_bot_guide(USER) is True
        assert await can_view_mom_bot_guide(replace(USER, role="heavy_hitter")) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("mom_bot_guide_guild_id", ""),
        ("mom_bot_guide_guild_id", "different-guild"),
        ("auth_disabled", True),
        ("mom_bot_guide_officer_roles", ""),
    ],
)
async def test_disabled_wrong_guild_and_demo_deny_without_discord_lookup(monkeypatch, field, value):
    monkeypatch.setattr(settings, field, value)
    with patch("app.services.guide_access.bot_client.get_member", new=AsyncMock()) as lookup:
        assert await can_view_mom_bot_guide(USER) is False
        lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    [replace(USER, is_service=True), replace(USER, member_id=None), replace(USER, discord_id=None)],
)
async def test_only_real_member_sessions_qualify(user):
    with patch("app.services.guide_access.bot_client.get_member", new=AsyncMock()) as lookup:
        assert await can_view_mom_bot_guide(user) is False
        lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "member",
    [
        {**MEMBER, "role_names": ["Member", "Heavy Hitter"]},
        {**MEMBER, "role_names": "Clan Deputies"},
        {**MEMBER, "role_names": [None, {}]},
        {**MEMBER, "role_names": []},
        {**MEMBER, "is_member": False},
        {**MEMBER, "is_member": "true"},
        {**MEMBER, "discord_id": "someone-else"},
        {},
        None,
    ],
)
async def test_missing_removed_roles_and_malformed_responses_deny(member):
    with patch(
        "app.services.guide_access.bot_client.get_member", new=AsyncMock(return_value=member)
    ):
        assert await can_view_mom_bot_guide(USER) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectError("offline"), AssertionError("bad response")])
async def test_discord_lookup_failure_denies(error):
    with patch("app.services.guide_access.bot_client.get_member", new=AsyncMock(side_effect=error)):
        assert await can_view_mom_bot_guide(USER) is False


@pytest.mark.asyncio
async def test_explicit_officer_role_list_overrides_login_role(monkeypatch):
    monkeypatch.setattr(settings, "mom_bot_guide_officer_roles", " Leader, Officer ")
    with patch(
        "app.services.guide_access.bot_client.get_member",
        new=AsyncMock(return_value={**MEMBER, "role_names": ["Officer"]}),
    ):
        assert await can_view_mom_bot_guide(USER) is True
    with patch(
        "app.services.guide_access.bot_client.get_member", new=AsyncMock(return_value=MEMBER)
    ):
        assert await can_view_mom_bot_guide(USER) is False


@pytest.mark.asyncio
async def test_anonymous_direct_request_is_unauthorized():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/guides/mom-bot")
    assert response.status_code == 401
    assert "member-notify" not in response.text


@pytest.mark.asyncio
async def test_signed_in_non_officer_gets_no_content(officer_session):
    with patch(
        "app.services.guide_access.bot_client.get_member",
        new=AsyncMock(return_value={**MEMBER, "role_names": ["Member"]}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/guides/mom-bot")
    assert response.status_code == 403
    assert "member-notify" not in response.text
    assert "no-store" in response.headers["cache-control"]


@pytest.mark.asyncio
async def test_private_content_is_delivered_without_caching(officer_session):
    with patch(
        "app.services.guide_access.bot_client.get_member", new=AsyncMock(return_value=MEMBER)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/guides/mom-bot")
    assert response.status_code == 200
    assert "member-notify-add" in response.text
    assert "no-store" in response.headers["cache-control"]
    assert "Cookie" in response.headers["vary"]


@pytest.mark.asyncio
async def test_role_revoked_after_identity_check_blocks_direct_content(officer_session):
    with patch(
        "app.services.guide_access.bot_client.get_member",
        new=AsyncMock(side_effect=[MEMBER, {**MEMBER, "role_names": []}]),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            identity = await client.get("/api/auth/me")
            content = await client.get("/api/guides/mom-bot")
    assert identity.json()["guides"]["mom_bot"] is True
    assert "no-store" in identity.headers["cache-control"]
    assert content.status_code == 403
    assert "member-notify" not in content.text


@pytest.mark.asyncio
async def test_slow_optional_lookup_does_not_block_identity_or_skip_content_check(officer_session):
    import asyncio

    cancelled = asyncio.Event()
    calls = 0

    async def lookup(discord_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        return MEMBER

    with patch(
        "app.services.guide_access.bot_client.get_member", new=AsyncMock(side_effect=lookup)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            identity = await asyncio.wait_for(client.get("/api/auth/me"), timeout=1)
            assert identity.status_code == 200
            assert identity.json()["member_id"] == USER.member_id
            assert identity.json()["guides"]["mom_bot"] is False
            assert cancelled.is_set()
            content = await client.get("/api/guides/mom-bot")
    assert content.status_code == 200
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [False, True])
async def test_failed_lookup_logs_diagnostics_without_member_data(caplog, malformed):
    secret = f"private-member-payload-{USER.discord_id}"
    lookup = (
        AsyncMock(return_value={"is_member": "invalid", "private": secret})
        if malformed
        else AsyncMock(side_effect=AssertionError(secret))
    )
    with patch("app.services.guide_access.bot_client.get_member", new=lookup):
        assert await can_view_mom_bot_guide(USER) is False
    assert "Officer guide membership lookup" in caplog.text
    assert USER.discord_id not in caplog.text
    assert secret not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
async def test_developer_preview_unlocks_navigation_and_content_for_local_user(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "auth_disabled", True)
    monkeypatch.setattr(settings, "dev_guild_guide_preview", True)
    monkeypatch.setattr(settings, "mom_bot_guide_guild_id", "")
    monkeypatch.setattr(settings, "mom_bot_guide_officer_roles", "")
    with patch("app.services.guide_access.bot_client.get_member", new=AsyncMock()) as lookup:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            identity = await client.get("/api/auth/me")
            content = await client.get("/api/guides/mom-bot")
    assert identity.status_code == 200
    assert identity.json()["guides"]["mom_bot"] is True
    assert content.status_code == 200
    assert "member-notify-add" in content.text
    assert "no-store" in content.headers["cache-control"]
    lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["development", "dev"])
async def test_developer_preview_allows_signed_in_user_from_another_guild(
    monkeypatch, officer_session, environment
):
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings, "dev_guild_guide_preview", True)
    monkeypatch.setattr(settings, "mom_bot_guide_guild_id", "another-guild")
    with patch("app.services.guide_access.bot_client.get_member", new=AsyncMock()) as lookup:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            identity = await client.get("/api/auth/me")
            content = await client.get("/api/guides/mom-bot")
    assert identity.json()["guides"]["mom_bot"] is True
    assert content.status_code == 200
    lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_disabled", [False, True])
async def test_developer_preview_never_grants_service_principals_access(monkeypatch, auth_disabled):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "auth_disabled", auth_disabled)
    monkeypatch.setattr(settings, "dev_guild_guide_preview", True)
    assert await can_view_mom_bot_guide(replace(USER, is_service=True)) is False


@pytest.mark.asyncio
async def test_developer_preview_still_requires_login_when_oauth_is_enabled(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "dev_guild_guide_preview", True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/guides/mom-bot")
    assert response.status_code == 401
    assert await can_view_mom_bot_guide(replace(USER, member_id=None)) is False
    assert await can_view_mom_bot_guide(replace(USER, discord_id=None)) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["production", "staging", "Development", ""])
async def test_developer_preview_fails_closed_outside_development(monkeypatch, environment):
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings, "dev_guild_guide_preview", True)
    assert await can_view_mom_bot_guide(USER) is False
    with pytest.raises(RuntimeError, match="DEV_GUILD_GUIDE_PREVIEW=true is not permitted"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("environment,auth_disabled", [("development", True), ("dev", False)])
async def test_developer_preview_startup_warns_and_allows_development(
    monkeypatch, caplog, environment, auth_disabled
):
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings, "auth_disabled", auth_disabled)
    monkeypatch.setattr(settings, "session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "bot_service_token", "test-service-token")
    monkeypatch.setattr(settings, "dev_guild_guide_preview", True)
    async with app.router.lifespan_context(app):
        pass
    assert "browser users can preview guild guides" in caplog.text
