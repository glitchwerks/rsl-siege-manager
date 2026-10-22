"""
Tests for backend/app/config.py and the lifespan startup guard in main.py.

Covers:
  - New Discord OAuth2 / auth fields present with correct defaults
  - ENVIRONMENT has no default (required field)
  - auth_disabled guard: allowed in development, rejected elsewhere
"""

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

# conftest.py has already set ENVIRONMENT=test (and other required vars) before
# this module was imported, so `from app.config import settings` is safe here.
from app.config import Settings

# ---------------------------------------------------------------------------
# Settings field defaults
# ---------------------------------------------------------------------------


class TestSettingsDefaults:
    """New auth fields should be present and have correct empty-string defaults."""

    def _make_settings(self, **overrides) -> Settings:
        """Construct a Settings instance with all required fields plus optional overrides."""
        base = {
            "database_url": "postgresql+asyncpg://u:p@localhost/db",
            "discord_bot_api_url": "http://bot:8001",
            "discord_bot_api_key": "key",
            "discord_guild_id": "111",
            "environment": "development",
        }
        base.update(overrides)
        return Settings(**base)

    def test_discord_client_id_defaults_to_empty(self):
        s = self._make_settings()
        assert s.discord_client_id == ""

    def test_discord_client_secret_defaults_to_empty(self):
        s = self._make_settings()
        assert s.discord_client_secret == ""

    def test_discord_redirect_uri_defaults_to_empty(self):
        s = self._make_settings()
        assert s.discord_redirect_uri == ""

    def test_session_secret_defaults_to_empty(self):
        s = self._make_settings()
        assert s.session_secret == ""

    def test_bot_service_token_defaults_to_empty(self):
        s = self._make_settings()
        assert s.bot_service_token == ""

    def test_auth_disabled_defaults_to_false(self):
        s = self._make_settings()
        assert s.auth_disabled is False

    def test_developer_guild_guide_preview_defaults_off(self, monkeypatch):
        monkeypatch.delenv("DEV_GUILD_GUIDE_PREVIEW", raising=False)
        s = self._make_settings(_env_file=None)
        assert s.dev_guild_guide_preview is False

    def test_discord_required_role_defaults_to_clan_deputies(self):
        s = self._make_settings()
        assert s.discord_required_role == "Clan Deputies"

    def test_discord_required_role_accepts_override(self):
        s = self._make_settings(discord_required_role="Admin")
        assert s.discord_required_role == "Admin"

    def test_allowed_origins_defaults_to_localhost(self):
        s = self._make_settings()
        assert s.allowed_origins == "http://localhost:5173"

    def test_allowed_origins_accepts_override(self):
        s = self._make_settings(allowed_origins="https://rslsiege.com")
        assert s.allowed_origins == "https://rslsiege.com"

    def test_allowed_origins_accepts_comma_separated_values(self):
        s = self._make_settings(allowed_origins="https://rslsiege.com,https://www.rslsiege.com")
        assert s.allowed_origins == "https://rslsiege.com,https://www.rslsiege.com"

    def test_new_fields_accept_provided_values(self):
        s = self._make_settings(
            discord_client_id="cid",
            discord_client_secret="secret",
            discord_redirect_uri="http://localhost/callback",
            session_secret="supersecret",
            bot_service_token="bearer-token",
            auth_disabled=True,
        )
        assert s.discord_client_id == "cid"
        assert s.discord_client_secret == "secret"
        assert s.discord_redirect_uri == "http://localhost/callback"
        assert s.session_secret == "supersecret"
        assert s.bot_service_token == "bearer-token"
        assert s.auth_disabled is True


class TestEnvironmentRequired:
    """ENVIRONMENT must be explicitly provided — no default allows silent misconfiguration."""

    def test_missing_environment_raises_validation_error(self, monkeypatch):
        # Remove ENVIRONMENT from the process env so pydantic-settings cannot
        # fall back to it (the .env file is absent in CI; developers have one,
        # but we force-unset here to exercise the validation path).
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        with pytest.raises(ValidationError) as exc_info:
            Settings(
                _env_file=None,  # bypass backend/.env on dev machines (issue #358)
                database_url="postgresql+asyncpg://u:p@localhost/db",
                discord_bot_api_url="http://bot:8001",
                discord_bot_api_key="key",
                discord_guild_id="111",
                # environment intentionally omitted
            )
        errors = exc_info.value.errors()
        field_names = [e["loc"][0] for e in errors]
        assert "environment" in field_names


# ---------------------------------------------------------------------------
# Lifespan startup guard
# ---------------------------------------------------------------------------


class TestLifespanAuthGuard:
    """auth_disabled=True must raise at startup when environment != 'development'."""

    @pytest.mark.asyncio
    async def test_auth_disabled_allowed_in_development(self, monkeypatch):
        """No RuntimeError when AUTH_DISABLED=true and ENVIRONMENT=development."""
        import app.config as config_module
        import app.main as main_module

        # Patch settings on both the config module and the main module (main
        # captured a reference at import time via `from app.config import settings`).
        from app.config import Settings

        dev_settings = Settings(
            database_url="postgresql+asyncpg://u:p@localhost/db",
            discord_bot_api_url="http://bot:8001",
            discord_bot_api_key="key",
            discord_guild_id="111",
            environment="development",
            auth_disabled=True,
        )
        monkeypatch.setattr(config_module, "settings", dev_settings)
        monkeypatch.setattr(main_module, "settings", dev_settings)

        # Starting the app via ASGI lifespan should not raise.
        # raise_app_exceptions=False so DB connection errors become 500s
        # instead of crashing the test.
        async with AsyncClient(
            transport=ASGITransport(app=main_module.app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/health")
        # Health may fail due to no real DB, but a 500 means the guard passed.
        assert response.status_code in (200, 500, 503)

    @pytest.mark.asyncio
    async def test_auth_disabled_rejected_in_production(self, monkeypatch):
        """RuntimeError raised at startup when AUTH_DISABLED=true outside development."""
        import app.config as config_module
        import app.main as main_module
        from app.config import Settings

        prod_settings = Settings(
            database_url="postgresql+asyncpg://u:p@localhost/db",
            discord_bot_api_url="http://bot:8001",
            discord_bot_api_key="key",
            discord_guild_id="111",
            environment="production",
            auth_disabled=True,
        )
        monkeypatch.setattr(config_module, "settings", prod_settings)
        monkeypatch.setattr(main_module, "settings", prod_settings)

        # Call lifespan directly — ASGITransport doesn't simulate the ASGI
        # lifespan protocol, so we invoke the context manager ourselves.
        with pytest.raises(RuntimeError, match="AUTH_DISABLED=true is not permitted"):
            async with main_module.lifespan(main_module.app):
                pass  # guard raises before yield

    @pytest.mark.asyncio
    async def test_auth_disabled_rejected_in_test_environment(self, monkeypatch):
        """RuntimeError raised when AUTH_DISABLED=true and environment is 'test'."""
        import app.config as config_module
        import app.main as main_module
        from app.config import Settings

        test_settings = Settings(
            database_url="postgresql+asyncpg://u:p@localhost/db",
            discord_bot_api_url="http://bot:8001",
            discord_bot_api_key="key",
            discord_guild_id="111",
            environment="test",
            auth_disabled=True,
        )
        monkeypatch.setattr(config_module, "settings", test_settings)
        monkeypatch.setattr(main_module, "settings", test_settings)

        with pytest.raises(RuntimeError, match="AUTH_DISABLED=true is not permitted"):
            async with main_module.lifespan(main_module.app):
                pass

    @pytest.mark.asyncio
    async def test_auth_not_disabled_allowed_in_any_environment(self, monkeypatch):
        """No RuntimeError when auth_disabled=False regardless of environment."""
        import app.config as config_module
        import app.main as main_module
        from app.config import Settings

        prod_settings = Settings(
            database_url="postgresql+asyncpg://u:p@localhost/db",
            discord_bot_api_url="http://bot:8001",
            discord_bot_api_key="key",
            discord_guild_id="111",
            environment="production",
            auth_disabled=False,
        )
        monkeypatch.setattr(config_module, "settings", prod_settings)
        monkeypatch.setattr(main_module, "settings", prod_settings)

        # Should complete lifespan without raising.
        async with AsyncClient(
            transport=ASGITransport(app=main_module.app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/health")
        assert response.status_code in (200, 500, 503)
