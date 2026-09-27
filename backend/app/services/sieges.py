from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.building import Building
from app.models.building_group import BuildingGroup
from app.models.building_type_config import BuildingTypeConfig
from app.models.enums import BuildingType, SiegeStatus
from app.models.member import Member
from app.models.position import Position
from app.models.post import Post
from app.models.post_priority_config import PostPriorityConfig
from app.models.siege import Siege
from app.models.siege_member import SiegeMember
from app.schemas.siege import SiegeCreate, SiegeUpdate
from app.services.building_capacity import get_team_count
from app.services.siege_lock import lock_planning_siege


def scrolls_per_player(total_positions: int) -> int:
    """Return the per-player scroll limit for a siege.

    Matches the UI formula: 4 scrolls when there are 90+ total positions,
    3 scrolls otherwise.  Single source of truth for validation and auto-fill.
    """
    return 4 if total_positions >= 90 else 3


async def compute_scroll_count(session: AsyncSession, siege_id: int) -> int:
    """Compute total scroll count from the theoretical capacity of every building.

    The count is derived from ``get_team_count(building.building_type, building.level)``
    summed across all buildings for the siege.  Position records are never consulted,
    and ``Building.is_broken`` is deliberately ignored: a broken building still occupies
    its structural slot in the siege layout, so its theoretical capacity must remain in
    the denominator.  The scroll limit should only change when buildings are added,
    removed, or levelled — all of which are locked once a siege is active.

    Stability invariant: this count cannot change during an active siege because
    ``update_building`` in ``buildings.py`` rejects all building mutations (including
    level changes and breaking/unbreaking) whenever the siege status is ``active`` or
    ``complete``.  That gate is the single guard that makes this value stable for the
    duration of a live siege.
    """
    buildings_result = await session.execute(select(Building).where(Building.siege_id == siege_id))
    buildings = buildings_result.scalars().all()
    return sum(get_team_count(b.building_type, b.level) for b in buildings)


async def list_sieges(session: AsyncSession, status: SiegeStatus | None) -> list[Siege]:
    stmt = select(Siege)
    if status is not None:
        stmt = stmt.where(Siege.status == status)
    stmt = stmt.order_by(Siege.date.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_siege(session: AsyncSession, siege_id: int) -> Siege:
    result = await session.execute(select(Siege).where(Siege.id == siege_id))
    siege = result.scalar_one_or_none()
    if siege is None:
        raise HTTPException(status_code=404, detail="Siege not found")
    return siege


async def create_siege(session: AsyncSession, data: SiegeCreate) -> Siege:
    siege = Siege(
        date=data.date,
        status=SiegeStatus.planning,
        defense_scroll_count=0,
    )
    session.add(siege)
    await session.flush()  # get siege.id before creating members

    # Create SiegeMember records for all active members
    active_members_result = await session.execute(
        select(Member).where(Member.is_active == True)  # noqa: E712
    )
    active_members = active_members_result.scalars().all()
    for member in active_members:
        session.add(
            SiegeMember(
                siege_id=siege.id,
                member_id=member.id,
                attack_day=None,
                has_reserve_set=None,
                attack_day_override=False,
            )
        )

    # Seed buildings from BuildingTypeConfig
    configs_result = await session.execute(select(BuildingTypeConfig))
    configs = configs_result.scalars().all()
    for config in configs:
        for num in range(1, config.count + 1):
            building = Building(
                siege_id=siege.id,
                building_type=config.building_type,
                building_number=num,
                level=1,
                is_broken=False,
            )
            session.add(building)
            await session.flush()

            for group_num in range(1, config.base_group_count + 1):
                is_last = group_num == config.base_group_count
                slot_count = config.base_last_group_slots if is_last else 3
                group = BuildingGroup(
                    building_id=building.id,
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

            if config.building_type == BuildingType.post:
                # Look up global priority for this post number
                ppc_result = await session.execute(
                    select(PostPriorityConfig).where(PostPriorityConfig.post_number == num)
                )
                ppc = ppc_result.scalar_one_or_none()
                session.add(
                    Post(
                        siege_id=siege.id,
                        building_id=building.id,
                        priority=ppc.priority if ppc else 2,
                        description=ppc.description if ppc else None,
                    )
                )

    await session.commit()
    await session.refresh(siege)
    return siege


async def update_siege(session: AsyncSession, siege_id: int, data: SiegeUpdate) -> Siege:
    siege = await lock_planning_siege(
        session, siege_id, detail="Only planning sieges can be updated"
    )
    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(siege, field, value)
    await session.commit()
    await session.refresh(siege)
    return siege


async def delete_siege(session: AsyncSession, siege_id: int) -> None:
    siege = await lock_planning_siege(
        session, siege_id, detail="Only planning sieges can be deleted"
    )
    await session.delete(siege)
    await session.commit()
