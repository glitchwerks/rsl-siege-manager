from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.building import Building
from app.models.building_group import BuildingGroup
from app.models.building_type_config import BuildingTypeConfig
from app.models.enums import BuildingType
from app.models.position import Position
from app.models.post import Post
from app.models.post_priority_config import PostPriorityConfig
from app.schemas.building import BuildingCreate, BuildingUpdate, GroupCreate
from app.services.building_capacity import get_team_count
from app.services.siege_lock import lock_planning_siege


async def _rebuild_groups_for_level(
    session: AsyncSession,
    building_id: int,
    building_type: BuildingType,
    level: int,
) -> None:
    """Rebuild groups and positions so they match the level-appropriate configuration.

    Called both from level-change updates and from unbreaking a building.
    Does nothing for post buildings (always exactly 1 group).
    """
    if building_type == BuildingType.post:
        return

    target_teams = get_team_count(building_type, level)
    if target_teams % 3 == 0:
        target_groups = target_teams // 3
        last_slots = 3
    else:
        target_groups = target_teams // 3 + 1
        last_slots = target_teams % 3

    # Get current groups ordered by group_number
    groups_result = await session.execute(
        select(BuildingGroup)
        .where(BuildingGroup.building_id == building_id)
        .order_by(BuildingGroup.group_number)
    )
    current_groups = list(groups_result.scalars().all())
    current_count = len(current_groups)

    if target_groups > current_count:
        # Expand previous last group to 3 slots if it was trimmed
        if current_groups:
            prev_last = current_groups[-1]
            if prev_last.slot_count < 3:
                for pos_num in range(prev_last.slot_count + 1, 4):
                    session.add(
                        Position(
                            building_group_id=prev_last.id,
                            position_number=pos_num,
                        )
                    )
                prev_last.slot_count = 3

        # Add new groups
        for g in range(current_count + 1, target_groups + 1):
            is_last = g == target_groups
            slot_count = last_slots if is_last else 3
            new_group = BuildingGroup(
                building_id=building_id,
                group_number=g,
                slot_count=slot_count,
            )
            session.add(new_group)
            await session.flush()
            for pos_num in range(1, slot_count + 1):
                session.add(
                    Position(
                        building_group_id=new_group.id,
                        position_number=pos_num,
                    )
                )

    elif target_groups < current_count:
        # Remove excess groups
        for group in current_groups[target_groups:]:
            await session.delete(group)

    # Adjust last group's slot count to match target
    await session.flush()
    last_result = await session.execute(
        select(BuildingGroup)
        .where(BuildingGroup.building_id == building_id)
        .order_by(BuildingGroup.group_number.desc())
    )
    actual_last = last_result.scalars().first()
    if actual_last and actual_last.slot_count != last_slots:
        if actual_last.slot_count < last_slots:
            for pos_num in range(actual_last.slot_count + 1, last_slots + 1):
                session.add(
                    Position(
                        building_group_id=actual_last.id,
                        position_number=pos_num,
                    )
                )
        else:
            excess = await session.execute(
                select(Position)
                .where(Position.building_group_id == actual_last.id)
                .where(Position.position_number > last_slots)
            )
            for pos in excess.scalars().all():
                await session.delete(pos)
        actual_last.slot_count = last_slots


async def _get_building_type_config(
    session: AsyncSession, building_type: BuildingType
) -> BuildingTypeConfig:
    result = await session.execute(
        select(BuildingTypeConfig).where(BuildingTypeConfig.building_type == building_type)
    )
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(
            status_code=400, detail=f"No configuration found for building type: {building_type}"
        )
    return config


async def _get_building(session: AsyncSession, siege_id: int, building_id: int) -> Building:
    result = await session.execute(
        select(Building).where(
            Building.id == building_id,
            Building.siege_id == siege_id,
        )
    )
    building = result.scalar_one_or_none()
    if building is None:
        raise HTTPException(status_code=404, detail="Building not found")
    return building


async def _create_groups_and_positions(
    session: AsyncSession,
    building_id: int,
    base_group_count: int,
    base_last_group_slots: int,
) -> None:
    """Auto-create BuildingGroups and Positions from config."""
    for group_num in range(1, base_group_count + 1):
        is_last = group_num == base_group_count
        slot_count = base_last_group_slots if is_last else 3
        group = BuildingGroup(
            building_id=building_id,
            group_number=group_num,
            slot_count=slot_count,
        )
        session.add(group)
        await session.flush()
        for pos_num in range(1, slot_count + 1):
            session.add(
                Position(
                    building_group_id=group.id,
                    position_number=pos_num,
                )
            )


async def list_buildings(session: AsyncSession, siege_id: int) -> list[Building]:
    result = await session.execute(select(Building).where(Building.siege_id == siege_id))
    return list(result.scalars().all())


async def add_building(session: AsyncSession, siege_id: int, data: BuildingCreate) -> Building:
    await lock_planning_siege(
        session,
        siege_id,
        detail="Buildings can only be added to planning sieges",
    )

    config = await _get_building_type_config(session, data.building_type)

    # Validate building_number is within allowed range for this type
    if data.building_number < 1 or data.building_number > config.count:
        raise HTTPException(
            status_code=400,
            detail=(
                f"building_number must be between 1 and {config.count} "
                f"for type {data.building_type}"
            ),
        )

    # Validate building_type + building_number uniqueness within the siege
    existing = await session.execute(
        select(Building).where(
            Building.siege_id == siege_id,
            Building.building_type == data.building_type,
            Building.building_number == data.building_number,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Building {data.building_type} #{data.building_number} "
                "already exists in this siege"
            ),
        )

    # Validate total count won't exceed config limit
    count_result = await session.execute(
        select(Building).where(
            Building.siege_id == siege_id,
            Building.building_type == data.building_type,
        )
    )
    current_count = len(count_result.scalars().all())
    if current_count >= config.count:
        raise HTTPException(
            status_code=400,
            detail=(f"Cannot add more than {config.count} buildings of type {data.building_type}"),
        )

    building = Building(
        siege_id=siege_id,
        building_type=data.building_type,
        building_number=data.building_number,
        level=data.level,
        is_broken=False,
    )
    session.add(building)
    await session.flush()

    await _create_groups_and_positions(
        session,
        building.id,
        config.base_group_count,
        config.base_last_group_slots,
    )

    # Auto-create Post record for post buildings
    if data.building_type == BuildingType.post:
        # Look up global priority for this post number (mirrors create_siege logic)
        ppc_result = await session.execute(
            select(PostPriorityConfig).where(PostPriorityConfig.post_number == data.building_number)
        )
        ppc = ppc_result.scalar_one_or_none()
        session.add(
            Post(
                siege_id=siege_id,
                building_id=building.id,
                priority=ppc.priority if ppc else 2,
                description=ppc.description if ppc else None,
            )
        )

    await session.commit()
    await session.refresh(building)
    return building


async def update_building(
    session: AsyncSession, siege_id: int, building_id: int, data: BuildingUpdate
) -> Building:
    await lock_planning_siege(
        session,
        siege_id,
        detail="Building layout is locked — siege is active or complete",
    )
    # This gate is the stability invariant for compute_scroll_count in sieges.py:
    # by rejecting all building mutations (level changes, breaking, unbreaking) once
    # a siege is active or complete, we guarantee the scroll count cannot shift
    # under an in-progress siege.
    building = await _get_building(session, siege_id, building_id)

    if data.is_broken is not None:
        building.is_broken = data.is_broken
        if data.is_broken:
            # Revert groups/positions to base configuration
            config = await _get_building_type_config(session, building.building_type)

            # Load all groups ordered by group_number
            groups_result = await session.execute(
                select(BuildingGroup)
                .where(BuildingGroup.building_id == building_id)
                .order_by(BuildingGroup.group_number)
            )
            groups = list(groups_result.scalars().all())

            # Delete groups beyond base_group_count
            for group in groups[config.base_group_count :]:
                await session.delete(group)

            # Update last group's slot_count and trim excess positions
            remaining_groups = groups[: config.base_group_count]
            if remaining_groups:
                last_group = remaining_groups[-1]
                last_group.slot_count = config.base_last_group_slots
                await session.flush()

                # Delete positions beyond base_last_group_slots in last group
                positions_result = await session.execute(
                    select(Position)
                    .where(Position.building_group_id == last_group.id)
                    .where(Position.position_number > config.base_last_group_slots)
                )
                for pos in positions_result.scalars().all():
                    await session.delete(pos)
        else:
            # Restoring from broken: rebuild to level-appropriate configuration
            await _rebuild_groups_for_level(
                session, building_id, building.building_type, building.level
            )

    if data.level is not None and data.level != building.level:
        building.level = data.level
        await _rebuild_groups_for_level(session, building_id, building.building_type, data.level)

    await session.commit()
    await session.refresh(building)
    return building


async def delete_building(session: AsyncSession, siege_id: int, building_id: int) -> None:
    await lock_planning_siege(
        session,
        siege_id,
        detail="Building layout is locked — siege is active or complete",
    )
    building = await _get_building(session, siege_id, building_id)
    await session.delete(building)
    await session.commit()


async def add_group(
    session: AsyncSession, siege_id: int, building_id: int, data: GroupCreate
) -> BuildingGroup:
    await lock_planning_siege(
        session,
        siege_id,
        detail="Building layout is locked — siege is active or complete",
    )

    building = await _get_building(session, siege_id, building_id)

    if building.building_type == BuildingType.post:
        raise HTTPException(
            status_code=400,
            detail="Post buildings always have exactly one group",
        )

    existing_group = await session.execute(
        select(BuildingGroup).where(
            BuildingGroup.building_id == building_id,
            BuildingGroup.group_number == data.group_number,
        )
    )
    if existing_group.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Group number {data.group_number} already exists on this building",
        )

    group = BuildingGroup(
        building_id=building_id,
        group_number=data.group_number,
        slot_count=data.slot_count,
    )
    session.add(group)
    await session.flush()

    for pos_num in range(1, data.slot_count + 1):
        session.add(
            Position(
                building_group_id=group.id,
                position_number=pos_num,
            )
        )

    await session.commit()
    await session.refresh(group)
    return group


async def delete_group(
    session: AsyncSession, siege_id: int, building_id: int, group_id: int
) -> None:
    await lock_planning_siege(
        session,
        siege_id,
        detail="Building layout is locked — siege is active or complete",
    )

    await _get_building(session, siege_id, building_id)

    result = await session.execute(
        select(BuildingGroup).where(
            BuildingGroup.id == group_id,
            BuildingGroup.building_id == building_id,
        )
    )
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")

    # Check it's not the only group
    all_groups_result = await session.execute(
        select(BuildingGroup).where(BuildingGroup.building_id == building_id)
    )
    all_groups = all_groups_result.scalars().all()
    if len(all_groups) <= 1:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the only group on a building",
        )

    await session.delete(group)
    await session.commit()
