"""API routes for the attack-day auto-assign feature.

``preview_attack_day`` runs the assignment algorithm and stores the result
temporarily.  ``apply_attack_day`` commits the stored preview to
``SiegeMember.attack_day`` and then fans out one day-role-sync webhook
call per affected member via FastAPI ``BackgroundTasks``.

All fan-out calls share a single ``correlation_id`` generated here (one
per user action, per contract §8).  ``assigned_at`` timestamps are sourced
from PostgreSQL ``clock_timestamp()`` inside the service layer — each
``AppliedMemberEntry`` carries its own timestamp captured at mutation time
so the receiver can apply monotonic ordering (§7).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._role_sync import schedule_role_sync
from app.config import settings
from app.db.session import get_db
from app.schemas.attack_day import AttackDayApplyResult, AttackDayPreviewResult
from app.services import attack_day as attack_day_service

router = APIRouter(tags=["attack_day"])


def _role_id_for_day(day: int | None) -> int | None:
    """Return the configured Discord role snowflake for the given attack day.

    Resolves ``discord_day_1_role_id`` or ``discord_day_2_role_id`` from
    settings at request time (not inside the BackgroundTask) so the value
    is bound before the task is enqueued.  This satisfies the plan design
    decision D2 and inquisitor CHARGE 4: role-ID lookup must happen in
    the request handler, not deferred into the background task, to avoid
    reading a stale or unset settings snapshot inside a worker context.

    Args:
        day: Attack-day number (``1`` or ``2``) or ``None`` for unassign.

    Returns:
        The configured Discord role integer for days 1 and 2.
        ``None`` for any other value (including ``None`` input).
    """
    if day == 1:
        return settings.discord_day_1_role_id
    if day == 2:
        return settings.discord_day_2_role_id
    return None


@router.post(
    "/sieges/{siege_id}/members/auto-assign-attack-day",
    response_model=AttackDayPreviewResult,
)
async def preview_attack_day(
    siege_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Run the attack-day auto-assign algorithm and store the preview."""
    return await attack_day_service.preview_attack_day(db, siege_id)


@router.post(
    "/sieges/{siege_id}/members/auto-assign-attack-day/apply",
    response_model=AttackDayApplyResult,
    response_model_exclude={"applied_members"},
)
async def apply_attack_day(
    siege_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Commit the stored attack-day preview to SiegeMember records.

    After the DB commit, fans out one day-role-sync webhook call per
    affected member whose ``discord_id`` is set.  All N calls share a
    single ``correlation_id`` (one per user action, contract §8).
    Each call receives a strictly-increasing ``assigned_at`` timestamp
    so the receiver can apply monotonic ordering (contract §7).

    Members with ``discord_id=None`` are silently skipped at the sender
    layer; no HTTP call is made for them.

    The HTTP response is returned before the background tasks fire
    (fire-and-forget per the brief).
    """
    result = await attack_day_service.apply_attack_day(db, siege_id)

    # One correlation_id for the entire user action (contract §8).
    correlation_id = str(uuid.uuid4())

    for entry in result.applied_members:
        # assigned_at is sourced from PostgreSQL clock_timestamp() per
        # member inside the service layer — each entry carries its own
        # timestamp captured at the moment of mutation (contract §7).
        action = "assign" if entry.attack_day is not None else "unassign"

        # Resolve role ID at request time, not inside the BackgroundTask
        # (plan §1 D2, inquisitor CHARGE 4).  For unassign actions the
        # role ID is not needed; pass None so the bot retains existing
        # removal logic.
        discord_role_id = (
            _role_id_for_day(entry.attack_day) if action == "assign" else None
        )

        schedule_role_sync(
            background_tasks,
            discord_id=entry.discord_id,
            siege_id=siege_id,
            day_number=entry.attack_day,
            action=action,
            assigned_at=entry.assigned_at,
            correlation_id=correlation_id,
            discord_role_id=discord_role_id,
        )

    return result
