from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.building import Building
from app.models.building_group import BuildingGroup
from app.models.enums import SiegeStatus
from app.models.member import Member
from app.models.position import Position
from app.models.siege import Siege
from app.models.siege_member import SiegeMember
from app.schemas.siege_member import MemberPreferenceSummary, SiegeMemberUpdate
from app.services.sieges import get_siege


async def get_siege_member_preferences(
    session: AsyncSession, siege_id: int
) -> list[MemberPreferenceSummary]:
    """Return member preference summaries for all roster members of *siege_id*.

    Applies the same planning-scoped active-member filter as
    ``list_siege_members``: inactive members are excluded only when the siege
    is in ``planning`` status. For ``active`` and ``complete`` sieges all
    roster rows are returned, preserving the historical record (see Fix 1 in
    ``deactivate_member`` which removes rows only from planning sieges).

    Args:
        session: Async SQLAlchemy session.
        siege_id: Primary key of the siege to query.

    Returns:
        List of ``MemberPreferenceSummary`` instances ordered by member ID.
    """
    result = await session.execute(
        select(SiegeMember)
        .join(Siege, SiegeMember.siege_id == Siege.id)
        .join(Member, SiegeMember.member_id == Member.id)
        .where(SiegeMember.siege_id == siege_id)
        .where((Siege.status != SiegeStatus.planning) | (Member.is_active.is_(True)))
        .options(selectinload(SiegeMember.member).selectinload(Member.post_preferences))
        .order_by(SiegeMember.member_id)
    )
    siege_members = list(result.scalars().all())
    return [
        MemberPreferenceSummary(
            member_id=sm.member.id,
            member_name=sm.member.name,
            preferences=list(sm.member.post_preferences),
        )
        for sm in siege_members
    ]


async def list_siege_members(session: AsyncSession, siege_id: int) -> list[SiegeMember]:
    """Return SiegeMember rows for *siege_id*, respecting history preservation.

    Both fixes for issue #485 work together here:

    - **Fix 1** (``deactivate_member``): removes ``SiegeMember`` rows from
      planning sieges on deactivation, so stale rows never accumulate there.
    - **Fix 2** (this function): defense-in-depth read filter that excludes
      inactive members, but *only* for ``planning`` sieges. For ``active`` and
      ``complete`` sieges every roster row is returned so that historical
      records of who participated remain intact even if those members are
      later deactivated.

    Args:
        session: Async SQLAlchemy session.
        siege_id: Primary key of the siege to query.

    Returns:
        List of ``SiegeMember`` instances with ``member`` eagerly loaded.
        For planning sieges, inactive members are excluded. For active and
        complete sieges, all roster rows are included.
    """
    result = await session.execute(
        select(SiegeMember)
        .join(Siege, SiegeMember.siege_id == Siege.id)
        .join(Member, SiegeMember.member_id == Member.id)
        .where(SiegeMember.siege_id == siege_id)
        .where((Siege.status != SiegeStatus.planning) | (Member.is_active.is_(True)))
        .options(selectinload(SiegeMember.member))
    )
    return list(result.scalars().all())


async def add_siege_member(session: AsyncSession, siege_id: int, member_id: int) -> SiegeMember:
    siege = await get_siege(session, siege_id)
    if siege.status != SiegeStatus.planning:
        raise HTTPException(
            status_code=400, detail="Members can only be added during the planning phase"
        )

    # Verify the member exists and is active
    member_result = await session.execute(select(Member).where(Member.id == member_id))
    member = member_result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if not member.is_active:
        raise HTTPException(status_code=400, detail="Only active members can be added to a siege")

    # Check not already in siege
    existing_result = await session.execute(
        select(SiegeMember).where(
            SiegeMember.siege_id == siege_id,
            SiegeMember.member_id == member_id,
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Member is already in this siege")

    siege_member = SiegeMember(siege_id=siege_id, member_id=member_id)
    session.add(siege_member)
    await session.commit()
    await session.refresh(siege_member)
    # Eagerly load member for the response schema
    result = await session.execute(
        select(SiegeMember)
        .where(SiegeMember.siege_id == siege_id, SiegeMember.member_id == member_id)
        .options(selectinload(SiegeMember.member))
    )
    return result.scalar_one()


async def remove_siege_member(
    session: AsyncSession,
    siege_id: int,
    member_id: int,
) -> tuple[str | None, int | None, datetime | None]:
    """Remove a member from a single planning siege.

    Unassigns all of that member's positions within THIS siege, then deletes
    their ``SiegeMember`` roster row.  Only permitted while the siege is in
    ``planning`` status — mirrors the guard used by ``add_siege_member``.

    Captures ``discord_id`` and ``attack_day`` **before** deleting the row
    so the caller (DELETE route) can emit a day-role-sync unassign webhook
    when the member had an attack day assigned.  Sources ``assigned_at``
    from PostgreSQL ``clock_timestamp()`` when ``attack_day`` is not ``None``
    (contract §7 monotonic clock requirement), matching the approach used by
    ``update_siege_member``.

    Args:
        session: Async SQLAlchemy session.
        siege_id: Primary key of the siege to remove the member from.
        member_id: Primary key of the member to remove.

    Returns:
        A ``(discord_id, prior_attack_day, assigned_at)`` tuple.
        ``discord_id`` is the member's Discord snowflake string or ``None``.
        ``prior_attack_day`` is the attack-day value before deletion, or
        ``None`` if the member had no day assigned.
        ``assigned_at`` is a UTC-aware ``clock_timestamp()`` value when
        ``prior_attack_day`` was not ``None``, else ``None``.

    Raises:
        HTTPException 400: Siege is not in planning status.
        HTTPException 404: No SiegeMember row found for (siege_id, member_id).
    """
    siege = await get_siege(session, siege_id)
    if siege.status != SiegeStatus.planning:
        raise HTTPException(
            status_code=400,
            detail="Members can only be removed during the planning phase",
        )

    result = await session.execute(
        select(SiegeMember)
        .where(
            SiegeMember.siege_id == siege_id,
            SiegeMember.member_id == member_id,
        )
        .options(selectinload(SiegeMember.member))
    )
    siege_member = result.scalar_one_or_none()
    if siege_member is None:
        raise HTTPException(status_code=404, detail="Member is not in this siege")

    # Capture day-role-sync data BEFORE deletion so the route can emit an
    # unassign webhook for members who had an attack day (P2a).
    prior_attack_day: int | None = siege_member.attack_day
    discord_id: str | None = (
        siege_member.member.discord_id if siege_member.member is not None else None
    )
    assigned_at: datetime | None = None
    if prior_attack_day is not None:
        raw_ts: datetime = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        assigned_at = raw_ts.astimezone(UTC)

    # Clear this member's position assignments within this siege only.
    # Join path: Position → BuildingGroup → Building (siege_id filter here).
    # Also clear matched_condition_id — it is only meaningful when a member
    # is assigned; leaving it stale after removal mirrors the behaviour of
    # update_position (board.py:160) and bulk_update_positions (board.py:229).
    await session.execute(
        update(Position)
        .where(
            Position.member_id == member_id,
            Position.building_group_id.in_(
                select(BuildingGroup.id).where(
                    BuildingGroup.building_id.in_(
                        select(Building.id).where(Building.siege_id == siege_id)
                    )
                )
            ),
        )
        .values(member_id=None, matched_condition_id=None)
    )

    await session.delete(siege_member)
    await session.commit()

    return discord_id, prior_attack_day, assigned_at


async def update_siege_member(
    session: AsyncSession,
    siege_id: int,
    member_id: int,
    data: SiegeMemberUpdate,
) -> tuple[SiegeMember, datetime | None]:
    """Apply a partial update to a SiegeMember row.

    When ``attack_day`` is present in the update payload, sources
    ``assigned_at`` from PostgreSQL ``clock_timestamp()`` at the moment of
    the DB mutation (contract §7).  When ``attack_day`` is absent, the
    timestamp query is skipped entirely — no round-trip wasted on fields
    that don't affect day-role-sync.

    Args:
        session: Async SQLAlchemy session.
        siege_id: Primary key of the siege.
        member_id: Primary key of the member within the siege.
        data: Partial update payload; only fields present in the request
            body are written.

    Returns:
        A ``(SiegeMember, datetime | None)`` tuple.  The datetime is a
        UTC-aware ``clock_timestamp()`` value when ``attack_day`` was
        updated, or ``None`` when it was not part of the payload.

    Raises:
        HTTPException 400: Siege is complete, or ``attack_day`` is invalid.
        HTTPException 404: SiegeMember record not found.
    """
    siege = await get_siege(session, siege_id)
    if siege.status == SiegeStatus.complete:
        raise HTTPException(
            status_code=400, detail="Siege is complete — member data is fully locked"
        )

    result = await session.execute(
        select(SiegeMember).where(
            SiegeMember.siege_id == siege_id,
            SiegeMember.member_id == member_id,
        )
    )
    siege_member = result.scalar_one_or_none()
    if siege_member is None:
        raise HTTPException(status_code=404, detail="SiegeMember record not found")

    if data.attack_day is not None and data.attack_day not in (1, 2):
        raise HTTPException(status_code=400, detail="attack_day must be 1 or 2")

    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(siege_member, field, value)

    # Only query clock_timestamp() when attack_day is changing — that is the
    # only field that triggers a day-role-sync webhook.  Patching other fields
    # (has_reserve_set, attack_day_override, etc.) does not need a timestamp.
    assigned_at: datetime | None = None
    if "attack_day" in updates:
        raw_ts: datetime = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        assigned_at = raw_ts.astimezone(UTC)

    await session.commit()
    await session.refresh(siege_member, attribute_names=["member"])
    return siege_member, assigned_at
