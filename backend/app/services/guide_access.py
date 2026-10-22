"""Server-verified access to the guild-specific officer guide."""

import asyncio
import logging

import httpx

from app.config import settings
from app.dependencies.auth import AuthenticatedUser
from app.services.bot_client import bot_client

logger = logging.getLogger(__name__)


async def can_view_mom_bot_guide(user: AuthenticatedUser, *, timeout: float | None = None) -> bool:
    """Check normal guild access or an explicitly enabled developer preview.

    Strength categories in Member.role are intentionally irrelevant. Service tokens
    never unlock this guide. Development preview explicitly bypasses guild/role
    checks for browser users; otherwise a failed or malformed lookup denies access.
    """
    if user.is_service:
        return False
    if settings.dev_guild_guide_preview:
        return settings.environment in {"development", "dev"} and (
            settings.auth_disabled or (user.member_id is not None and bool(user.discord_id))
        )

    target = settings.mom_bot_guide_guild_id.strip()
    if (
        settings.auth_disabled
        or user.member_id is None
        or not user.discord_id
        or not target
        or target != settings.discord_guild_id
    ):
        return False

    configured_roles = settings.mom_bot_guide_officer_roles
    officer_roles = {name.strip() for name in configured_roles.split(",") if name.strip()}
    if not officer_roles:
        return False

    try:
        async with asyncio.timeout(timeout):
            member = await bot_client.get_member(user.discord_id)
    except (TimeoutError, httpx.HTTPError, AssertionError, ValueError, TypeError) as error:
        # Exception messages and tracebacks may contain Discord IDs or member payloads.
        logger.warning("Officer guide membership lookup failed (%s)", type(error).__name__)
        return False

    if isinstance(member, dict) and member.get("is_member") is False:
        return False
    if (
        not isinstance(member, dict)
        or member.get("is_member") is not True
        or member.get("discord_id") != user.discord_id
        or not isinstance(member.get("role_names"), list)
        or not all(isinstance(role, str) for role in member["role_names"])
    ):
        logger.warning("Officer guide membership lookup returned malformed data")
        return False
    return any(role in officer_roles for role in member["role_names"])
