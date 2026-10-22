"""Discord OAuth2 authentication endpoints."""

import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import JWT_ALGORITHM, settings
from app.db.session import get_db
from app.dependencies.auth import AuthenticatedUser, get_current_user
from app.models.member import Member
from app.rate_limit import limiter
from app.services.bot_client import bot_client
from app.services.guide_access import can_view_mom_bot_guide

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Optional navigation must not hold up identity or protected website routes.
GUIDE_CAPABILITY_TIMEOUT_SECONDS = 0.25


class AuthError:
    """Error codes returned in login redirect query parameters."""

    INVALID_STATE = "invalid_state"
    SERVICE_UNAVAILABLE = "service_unavailable"
    UNAUTHORIZED = "unauthorized"
    INSUFFICIENT_ROLE = "insufficient_role"


DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_OAUTH_AUTHORIZE = "https://discord.com/oauth2/authorize"
DISCORD_OAUTH_TOKEN = f"{DISCORD_API_BASE}/oauth2/token"


async def _exchange_code_for_token(code: str) -> str:
    """Exchange an authorization code for a Discord access token."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            DISCORD_OAUTH_TOKEN,
            data={
                "client_id": settings.discord_client_id,
                "client_secret": settings.discord_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.discord_redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        return response.json()["access_token"]


async def _get_discord_user(access_token: str) -> dict:
    """Fetch the authenticated Discord user's profile (identify scope)."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{DISCORD_API_BASE}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        return response.json()


async def _check_guild_membership(discord_id: str) -> dict:
    """Check guild membership via the bot sidecar. Raises on connection failure."""
    return await bot_client.get_member(discord_id)


@router.get("/login")
@limiter.limit(lambda: settings.auth_login_rate_limit)
async def login(request: Request, response: Response) -> dict:
    """Initiate Discord OAuth2 flow.

    Generates a CSRF state token, stores it in a short-lived cookie, and
    returns the Discord authorization URL for the frontend to redirect to.

    Rate-limited per client IP via AUTH_LOGIN_RATE_LIMIT (default 10/minute).
    The limit is read lazily from ``settings`` so env-var overrides and test
    monkeypatches take effect without reloading the module.  The ``request``
    parameter is required by slowapi's decorator.
    """
    state = secrets.token_hex(32)
    params = urlencode(
        {
            "client_id": settings.discord_client_id,
            "redirect_uri": settings.discord_redirect_uri,
            "response_type": "code",
            "scope": "identify",
            "state": state,
        }
    )
    url = f"{DISCORD_OAUTH_AUTHORIZE}?{params}"
    response.set_cookie(
        key="oauth_state",
        value=state,
        httponly=True,
        max_age=300,
        samesite="lax",
        secure=settings.environment != "development",
    )
    return {"url": url}


@router.get("/callback")
@limiter.limit(lambda: settings.auth_callback_rate_limit)
async def callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Handle Discord OAuth2 callback.

    Validates CSRF state, exchanges the authorization code for an access token,
    fetches the Discord user profile, verifies guild membership via the bot
    sidecar, matches to a Member record, and issues a signed JWT session cookie.

    Rate-limited per client IP via AUTH_CALLBACK_RATE_LIMIT (default 5/minute).
    The limit is read lazily from ``settings`` so env-var overrides and test
    monkeypatches take effect without reloading the module.  The ``request``
    parameter is required by slowapi's decorator.
    """
    # 1. Validate state (CSRF)
    stored_state = request.cookies.get("oauth_state", "")
    if not stored_state or not secrets.compare_digest(stored_state, state):
        logger.warning("auth_invalid_state")
        return _error_redirect(AuthError.INVALID_STATE)

    # 2. Exchange code for access token
    try:
        access_token = await _exchange_code_for_token(code)
    except httpx.HTTPError:
        logger.error("auth_token_exchange_failed", exc_info=True)
        return _error_redirect(AuthError.SERVICE_UNAVAILABLE)

    # 3. Get Discord user profile
    try:
        discord_user = await _get_discord_user(access_token)
    except httpx.HTTPError:
        logger.error("auth_discord_user_fetch_failed", exc_info=True)
        return _error_redirect(AuthError.SERVICE_UNAVAILABLE)

    discord_id = discord_user["id"]

    # 4. Verify guild membership via bot sidecar
    try:
        guild_check = await _check_guild_membership(discord_id)
    except httpx.HTTPError:
        logger.error("auth_guild_check_failed", extra={"discord_id": discord_id}, exc_info=True)
        return _error_redirect(AuthError.SERVICE_UNAVAILABLE)

    if not guild_check.get("is_member"):
        logger.warning("auth_guild_check_rejected", extra={"discord_id": discord_id})
        return _error_redirect(AuthError.UNAUTHORIZED)

    # 4b. Verify required Discord role
    role_names: list[str] = guild_check.get("role_names", [])
    if settings.discord_required_role not in role_names:
        logger.warning(
            "auth_role_check_rejected",
            extra={"discord_id": discord_id, "required_role": settings.discord_required_role},
        )
        return _error_redirect(AuthError.INSUFFICIENT_ROLE)

    # 5. Match member by discord_id only — no username fallback
    result = await db.execute(select(Member).where(Member.discord_id == discord_id))
    member = result.scalar_one_or_none()
    if not member:
        logger.warning("auth_member_not_found", extra={"discord_id": discord_id})
        return _error_redirect(AuthError.UNAUTHORIZED)

    # 6. Issue JWT — 24-hour expiry
    now = datetime.now(UTC)
    token_payload = {
        "sub": str(member.id),
        "name": member.name,
        "iat": now,
        "exp": now + timedelta(hours=24),
    }
    token = jwt.encode(token_payload, settings.session_secret, algorithm=JWT_ALGORITHM)

    # 7. Set session cookie and redirect to app root
    redirect = RedirectResponse(url="/", status_code=302)
    redirect.set_cookie(
        key="session",
        value=token,
        httponly=True,
        max_age=82800,  # 23h — safety margin so cookie expires before JWT
        samesite="lax",
        secure=settings.environment != "development",
    )
    redirect.delete_cookie(key="oauth_state")
    return redirect


@router.post("/logout")
async def logout(response: Response) -> dict:
    """Clear the session cookie, ending the user's session."""
    response.delete_cookie(key="session")
    return {"status": "logged_out"}


@router.get("/me")
async def me(
    response: Response,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Return identity information for the currently authenticated caller."""
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "member_id": current_user.member_id,
        "name": current_user.name,
        "role": current_user.role,
        "discord_id": current_user.discord_id,
        "guides": {
            "mom_bot": await can_view_mom_bot_guide(
                current_user, timeout=GUIDE_CAPABILITY_TIMEOUT_SECONDS
            )
        },
    }


def _error_redirect(error: str) -> RedirectResponse:
    """Return a redirect to the login page with an error query parameter."""
    return RedirectResponse(url=f"/login?error={error}", status_code=302)
