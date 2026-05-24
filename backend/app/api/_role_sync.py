"""Internal helper: schedule outbound day-role-sync webhook calls.

This module encapsulates all caller-side invariants for the day-role-sync
webhook contract defined in ``docs/webhooks/day-role-sync.md``.

It is intentionally thin — all HTTP mechanics, retry logic, and response
parsing live in ``BotClient.sync_day_role()``.  This layer handles only
the caller-side concerns:

- Feature gate (``DAY_ROLE_SYNC_ENABLED``) checked before scheduling.
- ``discord_id=None`` filter with an INFO log.
- ``correlation_id`` is generated once per user action by the caller;
  this module never generates it.

``assigned_at`` is sourced from PostgreSQL ``clock_timestamp()`` inside
the service layer (``apply_attack_day`` / ``update_siege_member``) and
passed through as a parameter.  This satisfies contract §7 (monotonic
clock source) without module-level state.

Usage::

    from app.api._role_sync import schedule_role_sync
    import uuid

    correlation_id = str(uuid.uuid4())

    for entry in result.applied_members:
        schedule_role_sync(
            background_tasks,
            discord_id=entry.discord_id,
            siege_id=siege_id,
            day_number=entry.attack_day,
            action="assign",
            assigned_at=entry.assigned_at,
            correlation_id=correlation_id,
        )
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import BackgroundTasks

from app.config import settings
from app.services.bot_client import bot_client

logger = logging.getLogger(__name__)


def schedule_role_sync(
    background_tasks: BackgroundTasks,
    *,
    discord_id: str | None,
    siege_id: int,
    day_number: int | None,
    action: Literal["assign", "unassign"],
    assigned_at: datetime,
    correlation_id: str,
    discord_role_id: int | None = None,
) -> None:
    """Schedule a fire-and-forget webhook call via FastAPI BackgroundTasks.

    The HTTP response to the operator is sent before the webhook fires.
    This function applies the feature gate and discord_id filter so that
    ``BotClient.sync_day_role`` receives only valid, gate-enabled calls.

    Args:
        background_tasks: FastAPI ``BackgroundTasks`` from the route handler.
        discord_id: Discord snowflake string for the member.  Pass ``None``
            or empty string to skip (member has no linked Discord account).
            Logged at INFO when skipped.
        siege_id: Primary key of the siege record.
        day_number: Attack-day number (``1`` or ``2``).  Required when
            ``action="assign"``; should be ``None`` when
            ``action="unassign"`` (client omits it from the payload).
        action: ``"assign"`` — member placed on a day.
            ``"unassign"`` — member removed from a day.
        assigned_at: UTC-aware datetime for the assignment change.  Must be
            strictly greater than the previous call's value for the same
            ``discord_id`` within a fan-out batch.  Source this from
            PostgreSQL ``clock_timestamp()`` inside the service layer so
            the monotonicity invariant is met without module-level state.
        correlation_id: UUID v4 generated once per user action.  Shared
            across all calls in a fan-out batch (contract §8).
        discord_role_id: Optional Discord role snowflake integer.  When
            provided, forwarded to ``BotClient.sync_day_role`` so the bot
            can target a specific role rather than deriving it from the
            day number.  ``None`` (default) leaves role resolution to the
            bot.

    Returns:
        None.  The call is always fire-and-forget.
    """
    if not settings.day_role_sync_enabled:
        # Feature gate closed — suppress all scheduling silently.
        # BotClient.sync_day_role() also checks this flag, but checking here
        # avoids even enqueuing the background task.
        return

    if not discord_id:
        # discord_id is None or empty string — skip per contract §10.
        # BotClient also handles this, but filtering here avoids scheduling
        # a no-op background task and gives a caller-layer log entry.
        logger.info(
            "role_sync skipped: discord_id is None or empty "
            "(siege_id=%s, correlation_id=%s, action=%s)",
            siege_id,
            correlation_id,
            action,
        )
        return

    # ``discord_id`` on the Member model is a ``str | None``.  BotClient
    # accepts ``int | None`` in its type signature but does ``str(discord_id)``
    # internally, so passing the string directly is correct and avoids an
    # unnecessary int() cast that could raise on non-numeric strings.
    background_tasks.add_task(
        bot_client.sync_day_role,
        discord_id=discord_id,  # type: ignore[arg-type]
        siege_id=siege_id,
        day_number=day_number,
        action=action,
        assigned_at=assigned_at,
        correlation_id=correlation_id,
        discord_role_id=discord_role_id,
    )
