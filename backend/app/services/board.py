from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.building import Building
from app.models.building_group import BuildingGroup
from app.models.member import Member
from app.models.position import Position
from app.models.siege import Siege
from app.schemas.board import PositionUpdate
from app.services.siege_lock import require_planning_siege


async def get_board(session: AsyncSession, siege_id: int) -> dict:
    """Return the full nested board structure for a siege.

    Raises 404 if the siege does not exist.
    Eager-loads: buildings → groups → positions → member.
    Buildings are ordered by building_type then building_number;
    groups by group_number; positions by position_number.
    """
    result = await session.execute(
        select(Siege)
        .where(Siege.id == siege_id)
        .options(
            selectinload(Siege.buildings)
            .selectinload(Building.groups)
            .selectinload(BuildingGroup.positions)
            .selectinload(Position.member)
        )
    )
    siege = result.scalar_one_or_none()
    if siege is None:
        raise HTTPException(status_code=404, detail="Siege not found")

    _BUILDING_TYPE_ORDER = {
        "stronghold": 0,
        "mana_shrine": 1,
        "magic_tower": 2,
        "defense_tower": 3,
        "post": 4,
    }
    buildings_sorted = sorted(
        siege.buildings,
        key=lambda b: (_BUILDING_TYPE_ORDER.get(b.building_type, 99), b.building_number),
    )

    buildings_out = []
    for building in buildings_sorted:
        groups_sorted = sorted(building.groups, key=lambda g: g.group_number)
        groups_out = []
        for group in groups_sorted:
            positions_sorted = sorted(group.positions, key=lambda p: p.position_number)
            positions_out = [
                {
                    "id": pos.id,
                    "position_number": pos.position_number,
                    "member_id": pos.member_id,
                    "member_name": pos.member.name if pos.member is not None else None,
                    "is_reserve": pos.is_reserve,
                    "is_disabled": pos.is_disabled,
                    "matched_condition_id": pos.matched_condition_id,
                }
                for pos in positions_sorted
            ]
            groups_out.append(
                {
                    "id": group.id,
                    "group_number": group.group_number,
                    "slot_count": group.slot_count,
                    "positions": positions_out,
                }
            )
        buildings_out.append(
            {
                "id": building.id,
                "building_type": building.building_type,
                "building_number": building.building_number,
                "level": building.level,
                "is_broken": building.is_broken,
                "groups": groups_out,
            }
        )

    return {"siege_id": siege_id, "buildings": buildings_out}


async def _get_siege_for_position(session: AsyncSession, siege_id: int) -> Siege:
    result = await session.execute(select(Siege).where(Siege.id == siege_id))
    siege = result.scalar_one_or_none()
    if siege is None:
        raise HTTPException(status_code=404, detail="Siege not found")
    return siege


def _validate_position_state(data_member_id, data_is_reserve: bool, data_is_disabled: bool) -> None:
    """Validate the logical consistency of position flag combinations."""
    if data_is_disabled:
        if data_member_id is not None or data_is_reserve:
            raise HTTPException(
                status_code=400,
                detail="A disabled position cannot have a member or be marked as reserve",
            )
    if data_is_reserve and data_member_id is not None:
        raise HTTPException(
            status_code=400,
            detail="A reserve position cannot have a member assigned",
        )


async def _validate_member_active(session: AsyncSession, member_id: int) -> Member:
    result = await session.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if member is None or not member.is_active:
        raise HTTPException(status_code=400, detail="Member not found or not active")
    return member


async def update_position(
    session: AsyncSession, siege_id: int, position_id: int, data: PositionUpdate
) -> Position:
    """Update a single position's assignment.

    Raises:
        404 if position not found or doesn't belong to this siege.
        400 if siege is not in planning.
        400 on invalid flag combinations or member constraints.
    """
    # Verify siege exists and is editable.
    siege_result = await session.execute(select(Siege).where(Siege.id == siege_id))
    siege = siege_result.scalar_one_or_none()
    if siege is None:
        raise HTTPException(status_code=404, detail="Siege not found")
    require_planning_siege(siege)

    # Fetch position and verify it belongs to this siege
    stmt = (
        select(Position)
        .join(BuildingGroup, Position.building_group_id == BuildingGroup.id)
        .join(Building, BuildingGroup.building_id == Building.id)
        .where(Position.id == position_id)
        .where(Building.siege_id == siege_id)
    )
    result = await session.execute(stmt)
    position = result.scalar_one_or_none()
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")

    _validate_position_state(data.member_id, data.is_reserve, data.is_disabled)

    if data.member_id is not None:
        await _validate_member_active(session, data.member_id)

    position.member_id = data.member_id
    position.is_reserve = data.is_reserve
    position.is_disabled = data.is_disabled
    if data.member_id is None:
        position.matched_condition_id = None
    else:
        position.matched_condition_id = data.matched_condition_id

    await session.commit()
    await session.refresh(position)
    return position


async def bulk_update_positions(
    session: AsyncSession, siege_id: int, updates: list[dict]
) -> list[Position]:
    """Apply multiple position updates in a single transaction.

    Each update dict must have: position_id, member_id, is_reserve, is_disabled.
    Raises:
        400 if siege is not in planning.
        404/400 per the same rules as update_position.
    """
    # Verify siege exists and is editable.
    siege_result = await session.execute(select(Siege).where(Siege.id == siege_id))
    siege = siege_result.scalar_one_or_none()
    if siege is None:
        raise HTTPException(status_code=404, detail="Siege not found")
    require_planning_siege(siege)

    # Load all positions for the siege in one query
    all_positions_result = await session.execute(
        select(Position)
        .join(BuildingGroup, Position.building_group_id == BuildingGroup.id)
        .join(Building, BuildingGroup.building_id == Building.id)
        .where(Building.siege_id == siege_id)
    )
    positions_by_id = {p.id: p for p in all_positions_result.scalars().all()}

    # Validate all member IDs up front
    member_ids = {u["member_id"] for u in updates if u.get("member_id") is not None}
    active_member_ids: set[int] = set()
    if member_ids:
        members_result = await session.execute(select(Member).where(Member.id.in_(member_ids)))
        members = members_result.scalars().all()
        for m in members:
            if not m.is_active:
                raise HTTPException(
                    status_code=400,
                    detail=f"Member {m.id} is not active",
                )
            active_member_ids.add(m.id)
        missing = member_ids - active_member_ids
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Members not found: {missing}",
            )

    updated: list[Position] = []
    for u in updates:
        pos_id = u["position_id"]
        member_id = u.get("member_id")
        is_reserve = bool(u.get("is_reserve", False))
        is_disabled = bool(u.get("is_disabled", False))

        position = positions_by_id.get(pos_id)
        if position is None:
            raise HTTPException(status_code=404, detail=f"Position {pos_id} not found")

        _validate_position_state(member_id, is_reserve, is_disabled)

        matched_condition_id = u.get("matched_condition_id") if member_id is not None else None
        position.member_id = member_id
        position.is_reserve = is_reserve
        position.is_disabled = is_disabled
        position.matched_condition_id = matched_condition_id
        updated.append(position)

    await session.commit()
    for pos in updated:
        await session.refresh(pos)
    return updated
