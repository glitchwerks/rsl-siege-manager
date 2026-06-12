from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.building import Building
from app.models.building_group import BuildingGroup
from app.models.enums import SiegeStatus
from app.models.member import Member
from app.models.member_post_preference import member_post_preference
from app.models.position import Position
from app.models.post_condition import PostCondition
from app.models.siege import Siege
from app.models.siege_member import SiegeMember
from app.schemas.member import MemberCreate, MemberPreferencesUpdate, MemberUpdate


async def list_members(session: AsyncSession, is_active: bool | None) -> list[Member]:
    stmt = select(Member)
    if is_active is not None:
        stmt = stmt.where(Member.is_active == is_active)
    stmt = stmt.order_by(Member.name)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_member(session: AsyncSession, member_id: int) -> Member:
    result = await session.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


async def create_member(session: AsyncSession, data: MemberCreate) -> Member:
    existing = await session.execute(select(Member).where(Member.name == data.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="A member with this name already exists")
    active_count = await session.scalar(
        select(func.count()).select_from(Member).where(Member.is_active == True)  # noqa: E712
    )
    if active_count >= 30:
        raise HTTPException(
            status_code=409,
            detail="Cannot create member: the 30-active-member limit has been reached",
        )
    member = Member(**data.model_dump())
    session.add(member)
    await session.flush()

    planning_sieges = await session.execute(
        select(Siege).where(Siege.status == SiegeStatus.planning)
    )
    for siege in planning_sieges.scalars().all():
        session.add(SiegeMember(siege_id=siege.id, member_id=member.id))

    await session.commit()
    await session.refresh(member)
    return member


async def _clear_member_from_planning_sieges(session: AsyncSession, member_id: int) -> None:
    """Clear a member's position assignments and roster rows from planning sieges.

    Removes all ``Position.member_id`` references for *member_id* that fall
    within planning sieges, and deletes the corresponding ``SiegeMember`` rows.
    Active and complete sieges are left untouched so historical records are
    preserved.

    This helper does **not** commit; the caller is responsible for committing
    after all mutations are applied.

    Args:
        session: The active async database session.
        member_id: Primary key of the member to remove from planning sieges.
    """
    # Clear position assignments in planning sieges.
    stmt = (
        select(Position)
        .join(BuildingGroup, Position.building_group_id == BuildingGroup.id)
        .join(Building, BuildingGroup.building_id == Building.id)
        .join(Siege, Building.siege_id == Siege.id)
        .where(Position.member_id == member_id)
        .where(Siege.status == SiegeStatus.planning)
    )
    result = await session.execute(stmt)
    positions = result.scalars().all()
    for position in positions:
        position.member_id = None

    # Remove the member from all planning siege rosters.
    await session.execute(
        delete(SiegeMember)
        .where(SiegeMember.member_id == member_id)
        .where(
            SiegeMember.siege_id.in_(select(Siege.id).where(Siege.status == SiegeStatus.planning))
        )
    )


async def update_member(session: AsyncSession, member_id: int, data: MemberUpdate) -> Member:
    """Update mutable fields on an existing member.

    When the update transitions the member from active to inactive, the
    planning-siege cleanup is run before committing so that stale
    ``SiegeMember`` rows and position assignments are never left behind.

    Args:
        session: The active async database session.
        member_id: Primary key of the member to update.
        data: Partial update payload; only fields present in the request are
            applied.

    Returns:
        The refreshed ``Member`` instance after the update.

    Raises:
        HTTPException: 404 if the member does not exist, 409 if reactivating
            would exceed the 30-active-member limit.
    """
    member = await get_member(session, member_id)
    updates = data.model_dump(exclude_unset=True)

    if updates.get("is_active") is True and not member.is_active:
        active_count = await session.scalar(
            select(func.count()).select_from(Member).where(Member.is_active == True)  # noqa: E712
        )
        if active_count >= 30:
            raise HTTPException(
                status_code=409,
                detail="Cannot reactivate member: the 30-active-member limit has been reached",
            )

    # Capture the transition flag BEFORE mutating member.is_active so we can
    # detect active → inactive reliably regardless of update field order.
    transitioning_to_inactive = updates.get("is_active") is False and member.is_active is True

    for field, value in updates.items():
        setattr(member, field, value)

    if transitioning_to_inactive:
        await _clear_member_from_planning_sieges(session, member_id)

    await session.commit()
    await session.refresh(member)
    return member


async def deactivate_member(session: AsyncSession, member_id: int) -> Member:
    """Deactivate a member and clean up their planning-siege presence.

    Sets ``is_active = False``, clears position assignments in planning sieges,
    and removes ``SiegeMember`` rows from planning sieges. Active and complete
    sieges are left untouched so historical records are preserved.

    Args:
        session: The active async database session.
        member_id: Primary key of the member to deactivate.

    Returns:
        The refreshed ``Member`` instance after deactivation.

    Raises:
        HTTPException: 404 if the member does not exist.
    """
    member = await get_member(session, member_id)
    member.is_active = False

    await _clear_member_from_planning_sieges(session, member_id)

    await session.commit()
    await session.refresh(member)
    return member


async def get_member_preferences(session: AsyncSession, member_id: int) -> list[PostCondition]:
    await get_member(session, member_id)
    result = await session.execute(
        select(Member).options(selectinload(Member.post_preferences)).where(Member.id == member_id)
    )
    member = result.scalar_one()
    return list(member.post_preferences)


async def set_member_preferences(
    session: AsyncSession, member_id: int, data: MemberPreferencesUpdate
) -> list[PostCondition]:
    await get_member(session, member_id)

    # Validate all post_condition_ids exist
    if data.post_condition_ids:
        result = await session.execute(
            select(PostCondition).where(PostCondition.id.in_(data.post_condition_ids))
        )
        found = {pc.id for pc in result.scalars().all()}
        missing = set(data.post_condition_ids) - found
        if missing:
            raise HTTPException(
                status_code=404,
                detail=f"Post condition IDs not found: {sorted(missing)}",
            )

    # Replace all preferences for this member
    await session.execute(
        delete(member_post_preference).where(member_post_preference.c.member_id == member_id)
    )

    for pc_id in data.post_condition_ids:
        await session.execute(
            member_post_preference.insert().values(member_id=member_id, post_condition_id=pc_id)
        )

    await session.commit()

    # Return the updated preferences
    result = await session.execute(
        select(Member).options(selectinload(Member.post_preferences)).where(Member.id == member_id)
    )
    member = result.scalar_one()
    return list(member.post_preferences)
