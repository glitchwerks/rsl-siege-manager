from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    discord_bot_api_url: str
    discord_bot_api_key: str
    discord_guild_id: str
    discord_siege_channel: str = "clan-siege-assignments"
    discord_siege_images_channel: str = "clan-siege-assignment-images"

    # ENVIRONMENT must be explicitly set — no default so misconfigured deployments fail fast.
    environment: str

    # Discord OAuth2 — empty defaults so existing envs without these vars still start.
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = ""

    # HS256 signing key for JWTs — empty default; rotate in production.
    session_secret: str = ""

    # Bearer token for bot→backend calls; empty string disables the check.
    bot_service_token: str = ""

    # Day-role sync webhook feature gate and receiver URL.
    # DAY_ROLE_SYNC_ENABLED defaults to False; set to True only after a
    # conforming receiver is deployed and smoke-tested (see docs/webhooks/
    # day-role-sync.md §9 and issue #323).
    day_role_sync_enabled: bool = False
    day_role_sync_url: str | None = None

    # Discord role IDs for day-role sync (contract v1.1).
    # When set, assign payloads include "discord_role_id" per the contract.
    # Default None → v1.0-shape payload (field omitted). Both vars must be set
    # for full functionality; partial config logs a WARNING at startup (see main.py).
    discord_day_1_role_id: int | None = None
    discord_day_2_role_id: int | None = None

    # Dev-only auth bypass — startup guard rejects True outside development.
    auth_disabled: bool = False

    # Discord role required to log in. Members without this role are rejected
    # at OAuth callback with an insufficient_role error.
    discord_required_role: str = "Clan Deputies"

    # Comma-separated list of origins allowed by CORS middleware.
    # Default covers local dev frontend. In production set to your public domain,
    # e.g. "https://rslsiege.com" or "https://rslsiege.com,https://www.rslsiege.com".
    allowed_origins: str = "http://localhost:5173"

    # Rate-limit strings for the auth endpoints.  These accept any slowapi-
    # parseable rate string, e.g. "10/minute", "5/second", "100/hour".
    # See: https://limits.readthedocs.io/en/stable/string-notation.html
    auth_login_rate_limit: str = "10/minute"
    auth_callback_rate_limit: str = "5/minute"


settings = Settings()

JWT_ALGORITHM = "HS256"
