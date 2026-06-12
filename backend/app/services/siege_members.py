from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import SiegeStatus
from app.models.member import Member
from app.models.siege_member import SiegeMember
from app.schemas.siege_member import MemberPreferenceSummary, SiegeMemberUpdate
from app.services.sieges import get_siege


async def get_siege_member_preferences(
    session: AsyncSession, siege_id: int
) -> list[MemberPreferenceSummary]:
    result = await session.execute(
        select(SiegeMember)
        .where(SiegeMember.siege_id == siege_id)
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
    """Return all active SiegeMember rows for *siege_id*.

    Members whose ``is_active`` flag is ``False`` are excluded as a
    defense-in-depth guard against stale roster rows (issue #485).

    Args:
        session: Async SQLAlchemy session.
        siege_id: Primary key of the siege to query.

    Returns:
        List of ``SiegeMember`` instances with ``member`` eagerly loaded,
        filtered to active members only.
    """
    result = await session.execute(
        select(SiegeMember)
        .join(Member, SiegeMember.member_id == Member.id)
        .where(SiegeMember.siege_id == siege_id)
        .where(Member.is_active.is_(True))
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
