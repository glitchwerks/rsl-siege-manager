"""API routes for siege-member management.

Handles listing, adding, and updating members within a siege.  The
``update_siege_member`` route schedules a fire-and-forget day-role-sync
webhook call via FastAPI ``BackgroundTasks`` whenever the ``attack_day``
field changes.  ``add_siege_member`` is a documented no-op for webhook
emission — newly added members have no day assigned yet, so there is no
role to sync.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._role_sync import schedule_role_sync
from app.config import settings
from app.db.session import get_db
from app.schemas.siege_member import (
    MemberPreferenceSummary,
    SiegeMemberResponse,
    SiegeMemberUpdate,
)
from app.services import siege_members as siege_members_service

router = APIRouter(tags=["siege_members"])


class SiegeMemberCreate(BaseModel):
    """Request body for adding a member to a siege."""

    member_id: int


@router.get(
    "/sieges/{siege_id}/members/preferences",
    response_model=list[MemberPreferenceSummary],
)
async def get_siege_member_preferences(
    siege_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Return member preference summaries for all members in the siege."""
    return await siege_members_service.get_siege_member_preferences(db, siege_id)


@router.get("/sieges/{siege_id}/members", response_model=list[SiegeMemberResponse])
async def list_siege_members(
    siege_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Return all SiegeMember records for the given siege."""
    return await siege_members_service.list_siege_members(db, siege_id)


@router.post("/sieges/{siege_id}/members", response_model=SiegeMemberResponse, status_code=201)
async def add_siege_member(
    siege_id: int,
    data: SiegeMemberCreate,
    db: AsyncSession = Depends(get_db),
):
    """Add a member to the siege roster.

    No day-role-sync webhook is emitted here.  A newly added member has
    no attack day yet, so there is no role assignment to propagate.  The
    webhook is only fired from ``update_siege_member`` (day change) and
    ``apply_attack_day`` (bulk apply).
    """
    return await siege_members_service.add_siege_member(db, siege_id, data.member_id)


@router.put("/sieges/{siege_id}/members/{member_id}", response_model=SiegeMemberResponse)
async def update_siege_member(
    siege_id: int,
    member_id: int,
    data: SiegeMemberUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Update a siege member's fields (attack_day, override, reserve flag).

    When ``attack_day`` is present in the request body, a day-role-sync
    webhook call is scheduled as a background task (fire-and-forget) after
    the DB mutation commits.  The webhook is skipped when:

    - ``DAY_ROLE_SYNC_ENABLED`` is ``false`` (feature gate).
    - The member has no linked Discord account (``discord_id`` is ``None``).
    """
    siege_member, assigned_at = await siege_members_service.update_siege_member(
        db, siege_id, member_id, data
    )

    # Schedule webhook only when attack_day was part of the update payload.
    # If the caller patched only ``has_reserve_set`` or ``attack_day_override``,
    # there is no day-assignment change to propagate.
    if "attack_day" in data.model_fields_set:
        correlation_id = str(uuid.uuid4())
        # assigned_at is sourced from PostgreSQL clock_timestamp() inside
        # the service layer — no API-layer timestamp generation needed.
        new_day = siege_member.attack_day
        if new_day is not None:
            action = "assign"
        else:
            action = "unassign"

        # Resolve role ID at request time, not inside the BackgroundTask
        # (plan §1 D2, inquisitor CHARGE 4).  For unassign actions the
        # role ID is not needed; pass None so the bot retains existing
        # removal logic.
        discord_role_id = (
            settings.discord_day_1_role_id
            if new_day == 1
            else settings.discord_day_2_role_id
            if new_day == 2
            else None
        )

        schedule_role_sync(
            background_tasks,
            discord_id=(
                siege_member.member.discord_id if siege_member.member is not None else None
            ),
            siege_id=siege_id,
            day_number=new_day,
            action=action,
            assigned_at=assigned_at,
            correlation_id=correlation_id,
            discord_role_id=discord_role_id,
        )

    return siege_member
