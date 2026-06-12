"""HTTP API sidecar for the Siege Bot.

Exposes internal endpoints consumed by the backend service to send Discord
DMs, post channel messages, and post images.  Authentication is via a shared
Bearer token (BOT_API_KEY).

Discord exception translation
------------------------------
Any discord.py exception that escapes a route handler is caught by the
global exception handlers registered below.  Raw exception details
(status, text) are logged at WARNING level for debugging but never
exposed in response bodies.  The mapping is:

  discord.Forbidden (403 from Discord)          → HTTP 403
  discord.NotFound  (404 from Discord)          → HTTP 404
  discord.HTTPException with status < 500       → HTTP 502 (upstream error)
  discord.HTTPException with status >= 500      → HTTP 503 (unavailable)
  asyncio.TimeoutError                          → HTTP 503 (unavailable)

``discord.Forbidden`` and ``discord.NotFound`` are subclasses of
``discord.HTTPException``, so they are registered with separate, more-specific
handlers and FastAPI resolves them in MRO order.

Note: per-endpoint ``ValueError → 404`` handling is retained in each route
handler rather than promoted to a global handler, because ``ValueError`` is a
broad built-in that could mask programming errors if caught globally.

Error envelope policy
----------------------
All translated responses use the shape ``{"detail": "<generic message>"}``.
Raw Discord exception details (``exc.text``, ``exc.status``) are **never**
exposed in response bodies — they may contain channel names, permission
names, role names, or other implementation detail.  Handlers log the raw
context at WARNING level for server-side debugging.  New handlers MUST
conform to this shape.
"""

import asyncio
import logging
import os
import secrets
from pathlib import Path
from typing import Literal

import discord
from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile, status
from fastapi import Path as FastAPIPath
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import settings
from app.discord_client import SiegeBot
from app.fake_discord import is_broken_shape_mode

logger = logging.getLogger(__name__)

_VERSION_FILE = Path(__file__).parent.parent / "VERSION"

app = FastAPI(title="Siege Bot HTTP API", version="0.1.0")


# ---------------------------------------------------------------------------
# Discord exception → HTTP translation (global handlers)
# ---------------------------------------------------------------------------


@app.exception_handler(discord.Forbidden)
async def _handle_discord_forbidden(request: Request, exc: discord.Forbidden) -> JSONResponse:
    """Translate discord.Forbidden to HTTP 403.

    Raised when the bot lacks channel permissions or a user's DMs are
    closed.  Raw ``exc.text`` is logged server-side but excluded from
    the response body per the module's error envelope policy.

    Args:
        request: The incoming FastAPI request; method and path are
            included in the WARNING log for operator diagnostics.
        exc: The discord.Forbidden exception instance.

    Returns:
        JSONResponse with status 403 and a generic detail message.
    """
    logger.warning(
        "Discord Forbidden on %s %s: status=%s text=%r",
        request.method,
        request.url.path,
        exc.status,
        exc.text,
    )
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"detail": "Discord permission denied"},
    )


@app.exception_handler(discord.NotFound)
async def _handle_discord_not_found(request: Request, exc: discord.NotFound) -> JSONResponse:
    """Translate discord.NotFound to HTTP 404.

    Raised when the target channel, message, or user does not exist.
    Shares the 404 response shape with the existing ValueError path.
    Raw ``exc.text`` is logged server-side but excluded from the
    response body per the module's error envelope policy.

    Args:
        request: The incoming FastAPI request; method and path are
            included in the WARNING log for operator diagnostics.
        exc: The discord.NotFound exception instance.

    Returns:
        JSONResponse with status 404 and a generic detail message.
    """
    logger.warning(
        "Discord NotFound on %s %s: status=%s text=%r",
        request.method,
        request.url.path,
        exc.status,
        exc.text,
    )
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": "Discord resource not found"},
    )


@app.exception_handler(discord.HTTPException)
async def _handle_discord_http_exception(
    request: Request, exc: discord.HTTPException
) -> JSONResponse:
    """Translate discord.HTTPException to 502 or 503.

    discord.Forbidden and discord.NotFound are subclasses of this class;
    they are handled by their own more-specific handlers above and will
    NOT reach this handler.

    Status mapping:
      - exc.status < 500  → 502 Bad Gateway (upstream Discord 4xx)
      - exc.status >= 500 → 503 Service Unavailable (upstream Discord 5xx)

    Raw ``exc.status`` and ``exc.text`` are logged server-side but
    excluded from response bodies per the module's error envelope policy.

    Args:
        request: The incoming FastAPI request; method and path are
            included in the WARNING log for operator diagnostics.
        exc: The discord.HTTPException instance.

    Returns:
        JSONResponse with status 502 or 503 and a generic detail message.
    """
    logger.warning(
        "Discord HTTPException on %s %s: status=%s text=%r",
        request.method,
        request.url.path,
        exc.status,
        exc.text,
    )
    if exc.status < 500:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": "Upstream Discord error"},
        )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Discord temporarily unavailable"},
    )


@app.exception_handler(asyncio.TimeoutError)
async def _handle_timeout(request: Request, exc: asyncio.TimeoutError) -> JSONResponse:
    """Translate asyncio.TimeoutError to HTTP 503.

    Raised when a Discord API call exceeds its configured timeout.
    The exception carries no sensitive detail; the log entry is included
    for consistency with the module's error envelope policy.

    Args:
        request: The incoming FastAPI request; method and path are
            included in the WARNING log for operator diagnostics.
        exc: The asyncio.TimeoutError instance.

    Returns:
        JSONResponse with status 503 and a generic detail message.
    """
    logger.warning("Discord timeout on %s %s: %r", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Discord temporarily unavailable"},
    )


_bearer_scheme = HTTPBearer()

_bot: SiegeBot | None = None


def set_bot(bot: SiegeBot) -> None:
    global _bot
    _bot = bot


def _get_bot() -> SiegeBot:
    if _bot is None or not _bot.is_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bot is not connected",
        )
    return _bot


def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> None:
    """Validate the Bearer token against the configured bot API key."""
    if not secrets.compare_digest(credentials.credentials, settings.bot_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


class NotifyRequest(BaseModel):
    """Request body for POST /api/notify."""

    username: str
    message: str


class PostMessageRequest(BaseModel):
    """Request body for POST /api/post-message."""

    channel_name: str
    message: str


class RoleSyncRequest(BaseModel):
    """Request body for POST /api/role-sync (v1.1 day-role-sync contract).

    All required fields map directly to the payload schema defined in
    ``docs/webhooks/day-role-sync.md`` §2.  Optional fields are absent in
    v1.0-conforming producers and MUST be tolerated per the contract.

    Attributes:
        discord_id: Discord snowflake ID of the member (opaque string).
        siege_id: Primary key of the siege record; used for correlation.
        action: ``"assign"`` or ``"unassign"`` — no other values are legal.
        assigned_at: ISO-8601 UTC timestamp of the assignment change.
        correlation_id: UUID v4 scoping the fan-out batch (§8).
        day_number: Attack-day number (optional; absent for unassign-only).
        discord_role_id: Discord snowflake of the role to toggle (v1.1,
            optional).  When absent the endpoint returns
            ``status="skipped"`` per the locked design decision.
    """

    discord_id: str
    siege_id: int
    action: Literal["assign", "unassign"]
    assigned_at: str
    correlation_id: str
    day_number: int | None = None
    discord_role_id: str | None = None


@app.get("/api/version")
async def version() -> dict[str, str]:
    """Return the bot version — no authentication required.

    Returns ``1.0.1+42.abc1234`` when BUILD_NUMBER and GIT_SHA are present
    (i.e. in a CI-built image), or just the bare semver in local development.
    """
    try:
        semver = _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        semver = "unknown"
    build_number = os.environ.get("BUILD_NUMBER", "unknown")
    git_sha = os.environ.get("GIT_SHA", "unknown")
    if build_number != "unknown" and git_sha != "unknown":
        ver = f"{semver}+{build_number}.{git_sha[:7]}"
    else:
        ver = semver
    return {"version": ver}


@app.get("/api/health")
async def health() -> dict:
    """Health check — no authentication required.

    In ``fake_broken_shape`` mode (Option B breakage) returns only
    ``{"status": "healthy"}`` — ``bot_connected`` is intentionally omitted
    so the engineered-break meta-tests can confirm the shape assertion would
    fail against a missing/renamed key.
    """
    if is_broken_shape_mode():
        # Option B: drop ``bot_connected`` entirely.
        return {"status": "healthy"}
    return {"status": "healthy", "bot_connected": _bot is not None and _bot.is_ready()}


@app.post("/api/notify")
async def notify(
    body: NotifyRequest,
    _: None = Depends(verify_api_key),
) -> dict[str, str]:
    """Send a DM notification to a guild member.

    In ``fake_broken_shape`` mode (Option B breakage) returns
    ``{"status": "ok"}`` instead of ``{"status": "sent"}`` so the
    engineered-break meta-test can confirm the exact-equality assertion
    would fail.
    """
    bot = _get_bot()
    try:
        await bot.send_dm(body.username, body.message)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if is_broken_shape_mode():
        # Option B: return wrong status value to break the shape assertion.
        return {"status": "ok"}
    return {"status": "sent"}


@app.post("/api/post-message")
async def post_message(
    body: PostMessageRequest,
    _: None = Depends(verify_api_key),
) -> dict[str, str]:
    """Post a text message to a guild channel."""
    bot = _get_bot()
    try:
        await bot.post_message(body.channel_name, body.message)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"status": "sent"}


@app.post("/api/post-image")
async def post_image(
    file: UploadFile,
    channel_name: str = Form(...),
    _: None = Depends(verify_api_key),
) -> dict[str, str]:
    """Post an image to a guild channel."""
    bot = _get_bot()
    image_bytes = await file.read()
    try:
        url = await bot.post_image(channel_name, image_bytes, file.filename or "image.png")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"status": "sent", "url": url}


@app.post("/api/role-sync")
async def role_sync(
    body: RoleSyncRequest,
    _: None = Depends(verify_api_key),
) -> dict:
    """Receive a day-role-sync event and apply or remove the Discord role.

    Implements the receiver side of the v1.1 day-role-sync contract
    (``docs/webhooks/day-role-sync.md``).  The endpoint is stateless:
    it executes the role toggle on every call; idempotency is delegated
    to Discord's ``add_roles``/``remove_roles`` API which are idempotent
    at the Discord level.

    When ``discord_role_id`` is absent the endpoint returns a
    ``status="skipped"`` response with a WARNING log and does NOT call
    the seam.  This is the producer-side v1.0 → v1.1 transition path.
    The producer's ``_handle_sync_response`` treats ``skipped`` as success
    (no retry, no producer-side failure).

    Args:
        body: Validated ``RoleSyncRequest`` from the request body.
        _: Bearer-token dependency; raises 401 on failure.

    Returns:
        JSON body conforming to §3 of the contract:
        ``{"status", "added", "removed"}`` always present;
        ``"reason"`` present when status is not ``"applied"``.

    Raises:
        HTTPException: 503 if the bot is not connected.
        HTTPException: 404 if the member or role is not found (ValueError).
        discord.NotFound: Propagated to the global 404 handler.
        discord.Forbidden: Propagated to the global 403 handler.
        discord.HTTPException: Propagated to the global 502/503 handlers.
    """
    if body.discord_role_id is None:
        logger.warning(
            "role-sync skipped — discord_role_id absent " "(discord_id=%s, correlation_id=%s)",
            body.discord_id,
            body.correlation_id,
        )
        return {
            "status": "skipped",
            "added": [],
            "removed": [],
            "reason": "discord_role_id absent — no local map fallback",
        }

    bot = _get_bot()
    try:
        applied_status, role_name = await bot.apply_day_role(
            discord_id=body.discord_id,
            role_id=body.discord_role_id,
            action=body.action,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    if body.action == "assign":
        added = [role_name]
        removed: list[str] = []
    else:
        added = []
        removed = [role_name]

    return {
        "status": applied_status,
        "added": added,
        "removed": removed,
    }


@app.get("/api/members")
async def get_members(
    _: None = Depends(verify_api_key),
) -> list[dict]:
    """Retrieve guild member list."""
    bot = _get_bot()
    return await bot.get_members()


@app.get("/api/members/{discord_user_id}")
async def get_guild_member(
    discord_user_id: str = FastAPIPath(..., pattern=r"^\d+$"),
    _: None = Depends(verify_api_key),
) -> dict:
    """Look up a single guild member by Discord user ID.

    Args:
        discord_user_id: Discord snowflake ID (numeric string only).
            FastAPI validates this against ``^\\d+$`` and returns 422
            before the handler runs if the value contains non-digit
            characters.
        _: Bearer-token dependency; raises 401/403 on failure.

    Returns:
        A dict with ``is_member: bool`` as the discriminator.  When
        ``is_member`` is ``False``, all other fields are ``None``.
        When ``is_member`` is ``True``, all other fields are populated.

    Raises:
        HTTPException: 503 if the guild object is not available or
            Discord returns an unexpected error.
    """
    guild = _bot.get_guild(int(settings.discord_guild_id)) if _bot is not None else None
    if guild is None:
        raise HTTPException(status_code=503, detail="Guild not available")
    try:
        member = await guild.fetch_member(int(discord_user_id))
    except discord.NotFound:
        return {
            "is_member": False,
            "discord_id": None,
            "username": None,
            "display_name": None,
            "roles": None,
            "role_names": None,
        }
    # discord.HTTPException is intentionally not caught here — the global handler
    # translates it to 502/503 with a generic detail message per the error
    # envelope policy (#422).  The NotFound branch above is per-endpoint business
    # logic (200 with is_member=false), not error translation.
    return {
        "is_member": True,
        "discord_id": str(member.id),
        "username": member.name,
        "display_name": member.display_name,
        "roles": [str(r.id) for r in member.roles if r.name != "@everyone"],
        "role_names": [r.name for r in member.roles if r.name != "@everyone"],
    }
