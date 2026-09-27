from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.building import Building
from app.models.building_group import BuildingGroup
from app.models.enums import BuildingType, SiegeStatus
from app.models.position import Position
from app.models.post import Post
from app.models.post_priority_config import PostPriorityConfig
from app.models.siege import Siege
from app.models.siege_member import SiegeMember
from app.services import validation as validation_service


async def activate_siege(session: AsyncSession, siege_id: int) -> Siege:
    """Transition a planning siege to active status.

    Raises:
        404 if siege not found.
        400 if not in planning status.
        400 if another siege is already active.

    Note: The readiness check here is a STUB for Phase 3.
    Phase 4 will replace this with the full 16-rule validation engine.
    The stub passes if the siege has at least one building configured.
    """
    result = await session.execute(select(Siege).where(Siege.id == siege_id).with_for_update())
    siege = result.scalar_one_or_none()
    if siege is None:
        raise HTTPException(status_code=404, detail="Siege not found")

    if siege.status != SiegeStatus.planning:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot activate a siege with status '{siege.status}'",
        )

    # Check for another active siege
    active_result = await session.execute(
        select(Siege).where(Siege.status == SiegeStatus.active).where(Siege.id != siege_id)
    )
    if active_result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="Another siege is already active")

    # Run the full validation engine — errors block activation, warnings are informational
    validation_result = await validation_service.validate_siege(session, siege_id)
    if validation_result.errors:
        raise HTTPException(
            status_code=400,
            detail=[e.model_dump() for e in validation_result.errors],
        )

    siege.status = SiegeStatus.active
    await session.commit()
    await session.refresh(siege)
    return siege


async def complete_siege(session: AsyncSession, siege_id: int) -> Siege:
    """Transition an active siege to complete status.

    Raises:
        404 if siege not found.
        400 if not in active status.
    """
    result = await session.execute(select(Siege).where(Siege.id == siege_id))
    siege = result.scalar_one_or_none()
    if siege is None:
        raise HTTPException(status_code=404, detail="Siege not found")

    if siege.status != SiegeStatus.active:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot complete a siege with status '{siege.status}'",
        )

    siege.status = SiegeStatus.complete
    await session.commit()
    await session.refresh(siege)
    return siege


async def reopen_siege(session: AsyncSession, siege_id: int) -> Siege:
    """Transition a completed siege back to planning status.

    Raises:
        404 if siege not found.
        400 if not in complete status.
    """
    result = await session.execute(select(Siege).where(Siege.id == siege_id))
    siege = result.scalar_one_or_none()
    if siege is None:
        raise HTTPException(status_code=404, detail="Siege not found")

    if siege.status != SiegeStatus.complete:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reopen a siege with status '{siege.status}'",
        )

    siege.status = SiegeStatus.planning
    await session.commit()
    await session.refresh(siege)
    return siege


async def clone_siege(session: AsyncSession, siege_id: int) -> Siege:
    """Deep-copy a siege into a new planning siege.

    Rules:
    - New siege: same defense_scroll_count, status=planning, date=None.
    - Buildings, groups, and positions are deep-copied (no shared FKs).
    - Active member assignments are preserved; inactive member slots are cleared.
    - Reserve positions are copied as-is (member_id=None preserved).
    - Posts are copied (priority + description) but active_conditions are cleared
      (the game assigns new conditions each siege).
    - SiegeMember rows are copied for members who are still active.

    Raises:
        404 if source siege not found.
    """
    # Load source siege with all nested data
    source_result = await session.execute(
        select(Siege)
        .where(Siege.id == siege_id)
        .options(
            selectinload(Siege.buildings)
            .selectinload(Building.groups)
            .selectinload(BuildingGroup.positions)
            .selectinload(Position.member),
            selectinload(Siege.buildings).selectinload(Building.post),
            selectinload(Siege.siege_members).selectinload(SiegeMember.member),
        )
    )
    source = source_result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Siege not found")

    # 1. Create new Siege
    new_siege = Siege(
        date=None,
        status=SiegeStatus.planning,
        defense_scroll_count=source.defense_scroll_count,
    )
    session.add(new_siege)
    await session.flush()  # obtain new_siege.id

    # 2. Copy buildings
    for src_building in source.buildings:
        new_building = Building(
            siege_id=new_siege.id,
            building_type=src_building.building_type,
            building_number=src_building.building_number,
            level=src_building.level,
            is_broken=src_building.is_broken,
        )
        session.add(new_building)
        await session.flush()  # obtain new_building.id

        # 2a. Copy groups
        for src_group in src_building.groups:
            new_group = BuildingGroup(
                building_id=new_building.id,
                group_number=src_group.group_number,
                slot_count=src_group.slot_count,
            )
            session.add(new_group)
            await session.flush()  # obtain new_group.id

            # 2b. Copy positions
            for src_pos in src_group.positions:
                if src_pos.is_reserve:
                    # Reserve positions: copy as-is (no member)
                    new_pos = Position(
                        building_group_id=new_group.id,
                        position_number=src_pos.position_number,
                        member_id=None,
                        is_reserve=True,
                        is_disabled=src_pos.is_disabled,
                    )
                elif (
                    src_pos.member_id is not None
                    and src_pos.member is not None
                    and src_pos.member.is_active
                ):
                    # Active member — preserve assignment
                    new_pos = Position(
                        building_group_id=new_group.id,
                        position_number=src_pos.position_number,
                        member_id=src_pos.member_id,
                        is_reserve=False,
                        is_disabled=src_pos.is_disabled,
                    )
                else:
                    # Inactive member or unassigned — clear member
                    new_pos = Position(
                        building_group_id=new_group.id,
                        position_number=src_pos.position_number,
                        member_id=None,
                        is_reserve=src_pos.is_reserve,
                        is_disabled=src_pos.is_disabled,
                    )
                session.add(new_pos)

        # 2c. Copy post if building type is post
        if src_building.building_type == BuildingType.post and src_building.post is not None:
            src_post = src_building.post
            # Look up priority from config by post_number rather than copying the source
            # priority verbatim — the source siege may predate PostPriorityConfig seeds and
            # carry stale priority=0 values that would otherwise propagate to every clone.
            ppc_result = await session.execute(
                select(PostPriorityConfig).where(
                    PostPriorityConfig.post_number == src_building.building_number
                )
            )
            ppc = ppc_result.scalar_one_or_none()
            new_post = Post(
                siege_id=new_siege.id,
                building_id=new_building.id,
                priority=ppc.priority if ppc else 2,
                description=src_post.description,
                # active_conditions intentionally NOT copied — game assigns new conditions
            )
            session.add(new_post)

    # 3. Copy SiegeMember rows for still-active members
    for src_sm in source.siege_members:
        if src_sm.member is not None and src_sm.member.is_active:
            new_sm = SiegeMember(
                siege_id=new_siege.id,
                member_id=src_sm.member_id,
                attack_day=src_sm.attack_day,
                has_reserve_set=src_sm.has_reserve_set,
                attack_day_override=src_sm.attack_day_override,
            )
            session.add(new_sm)

    await session.commit()
    await session.refresh(new_siege)
    return new_siege
