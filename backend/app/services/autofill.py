import random
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.building import Building
from app.models.building_group import BuildingGroup
from app.models.member import Member
from app.models.position import Position
from app.models.siege import Siege
from app.models.siege_member import SiegeMember
from app.schemas.autofill import AutofillApplyResult, AutofillAssignment, AutofillPreviewResult
from app.services.siege_lock import require_planning_siege
from app.services.sieges import compute_scroll_count, scrolls_per_player

PREVIEW_TTL_MINUTES = 30


def _now_utc() -> datetime:
    return datetime.now(UTC)


async def preview_autofill(session: AsyncSession, siege_id: int) -> AutofillPreviewResult:
    siege_result = await session.execute(
        select(Siege)
        .where(Siege.id == siege_id)
        .options(
            selectinload(Siege.buildings)
            .selectinload(Building.groups)
            .selectinload(BuildingGroup.positions),
            selectinload(Siege.siege_members).selectinload(SiegeMember.member),
        )
    )
    siege = siege_result.scalar_one_or_none()
    if siege is None:
        raise HTTPException(status_code=404, detail="Siege not found")
    require_planning_siege(siege)

    # 1. Collect empty, non-disabled, non-reserve positions (skip broken buildings)
    empty_positions: list[Position] = []
    for building in siege.buildings:
        if building.is_broken:
            continue
        for group in building.groups:
            for pos in group.positions:
                if not pos.is_disabled and not pos.is_reserve and pos.member_id is None:
                    empty_positions.append(pos)

    # 2. Count existing assignments per member (skip broken buildings)
    assignment_counts: dict[int, int] = defaultdict(int)
    for building in siege.buildings:
        if building.is_broken:
            continue
        for group in building.groups:
            for pos in group.positions:
                if pos.member_id is not None and not pos.is_reserve and not pos.is_disabled:
                    assignment_counts[pos.member_id] += 1

    # 3. Build list of active members from siege_members
    active_members: list[Member] = [
        sm.member for sm in siege.siege_members if sm.member is not None and sm.member.is_active
    ]

    # 4. Fisher-Yates shuffle
    shuffled = list(active_members)
    random.shuffle(shuffled)

    # 5. Fill empty positions
    assignments: list[AutofillAssignment] = []
    member_index = 0
    total_positions = await compute_scroll_count(session, siege_id)
    limit = scrolls_per_player(total_positions)

    for pos in empty_positions:
        assigned = False
        attempts = 0
        while attempts < len(shuffled):
            candidate = shuffled[member_index % len(shuffled)]
            if assignment_counts[candidate.id] < limit:
                assignment_counts[candidate.id] += 1
                assignments.append(
                    AutofillAssignment(
                        position_id=pos.id,
                        member_id=candidate.id,
                        is_reserve=False,
                    )
                )
                member_index += 1
                assigned = True
                break
            member_index += 1
            attempts += 1

        if not assigned:
            # 6. All members hit the limit — mark as reserve
            assignments.append(
                AutofillAssignment(
                    position_id=pos.id,
                    member_id=None,
                    is_reserve=True,
                )
            )

    # 7. Store preview with TTL
    # Strip timezone before storing — DB column is TIMESTAMP (naive), implicitly UTC
    expires_at = _now_utc().replace(tzinfo=None) + timedelta(minutes=PREVIEW_TTL_MINUTES)
    siege.autofill_preview = {"assignments": [a.model_dump() for a in assignments]}
    siege.autofill_preview_expires_at = expires_at
    await session.commit()

    return AutofillPreviewResult(
        assignments=assignments,
        expires_at=expires_at.isoformat(),
    )


async def apply_autofill(session: AsyncSession, siege_id: int) -> AutofillApplyResult:
    siege_result = await session.execute(select(Siege).where(Siege.id == siege_id))
    siege = siege_result.scalar_one_or_none()
    if siege is None:
        raise HTTPException(status_code=404, detail="Siege not found")
    require_planning_siege(siege)

    if siege.autofill_preview is None or siege.autofill_preview_expires_at is None:
        raise HTTPException(status_code=409, detail="No valid preview to apply, generate a new one")

    # Normalize expires_at to UTC-aware for comparison
    expires_at = siege.autofill_preview_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if _now_utc() > expires_at:
        raise HTTPException(status_code=409, detail="No valid preview to apply, generate a new one")

    raw_assignments: list[dict] = siege.autofill_preview.get("assignments", [])

    # Load all positions referenced, excluding positions from broken buildings.
    # A building may have been broken between preview generation and apply (within the
    # 30-minute TTL).  Joining to Building and filtering on is_broken means those positions
    # will simply be absent from positions_by_id, and the existing `if pos is None: continue`
    # guard below will silently skip them — no assignment lands on a broken building.
    position_ids = [a["position_id"] for a in raw_assignments]
    if position_ids:
        positions_result = await session.execute(
            select(Position)
            .join(BuildingGroup, Position.building_group_id == BuildingGroup.id)
            .join(Building, BuildingGroup.building_id == Building.id)
            .where(Position.id.in_(position_ids))
            .where(Building.is_broken == False)  # noqa: E712
        )
        positions_by_id = {p.id: p for p in positions_result.scalars().all()}
    else:
        positions_by_id = {}

    applied_count = 0
    reserve_count = 0

    for entry in raw_assignments:
        pos = positions_by_id.get(entry["position_id"])
        if pos is None:
            continue
        if entry["is_reserve"]:
            pos.member_id = None
            pos.is_reserve = True
            reserve_count += 1
        else:
            pos.member_id = entry["member_id"]
            pos.is_reserve = False
            applied_count += 1

    siege.autofill_preview = None
    siege.autofill_preview_expires_at = None

    await session.commit()

    return AutofillApplyResult(applied_count=applied_count, reserve_count=reserve_count)
