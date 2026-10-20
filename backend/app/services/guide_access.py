"""Server-verified access to the guild-specific officer guide."""

import httpx

from app.config import settings
from app.dependencies.auth import AuthenticatedUser
from app.services.bot_client import bot_client


async def can_view_mom_bot_guide(user: AuthenticatedUser) -> bool:
    """Require a real session, the explicitly configured guild, and a live officer role.

    Strength categories in Member.role are intentionally irrelevant. Development
    bypass users and service tokens never unlock this guide. A failed or malformed
    Discord lookup denies access rather than trusting a stale login-time role.
    """
    target = settings.mom_bot_guide_guild_id.strip()
    if (
        settings.auth_disabled
        or user.is_service
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
        member = await bot_client.get_member(user.discord_id)
    except (httpx.HTTPError, AssertionError, ValueError, TypeError):
        return False

    if not isinstance(member, dict):
        return False
    role_names = member.get("role_names")
    return (
        member.get("is_member") is True
        and member.get("discord_id") == user.discord_id
        and isinstance(role_names, list)
        and any(isinstance(role, str) and role in officer_roles for role in role_names)
    )
