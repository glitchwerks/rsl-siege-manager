from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.building import Building
from app.models.building_group import BuildingGroup
from app.models.building_type_config import BuildingTypeConfig
from app.models.enums import BUILDING_TYPE_LABELS, BuildingType, MemberRole
from app.models.member import Member
from app.models.position import Position
from app.models.post import Post
from app.models.post_condition import PostCondition  # noqa: F401
from app.models.siege import Siege
from app.models.siege_member import SiegeMember
from app.schemas.validation import ValidationIssue, ValidationResult
from app.services.sieges import compute_scroll_count, scrolls_per_player


async def validate_siege(session: AsyncSession, siege_id: int) -> ValidationResult:
    siege_result = await session.execute(
        select(Siege)
        .where(Siege.id == siege_id)
        .options(
            selectinload(Siege.buildings)
            .selectinload(Building.groups)
            .selectinload(BuildingGroup.positions)
            .selectinload(Position.member)
            .selectinload(Member.post_preferences),
            selectinload(Siege.buildings)
            .selectinload(Building.post)
            .selectinload(Post.active_conditions),
            selectinload(Siege.siege_members).selectinload(SiegeMember.member),
        )
    )
    siege = siege_result.scalar_one_or_none()
    if siege is None:
        return ValidationResult(errors=[], warnings=[])

    # Compute the live per-player scroll limit from actual position count
    total_positions = await compute_scroll_count(session, siege_id)
    scroll_limit = scrolls_per_player(total_positions)

    # Load building type configs keyed by type
    config_result = await session.execute(select(BuildingTypeConfig))
    configs: dict[BuildingType, BuildingTypeConfig] = {
        c.building_type: c for c in config_result.scalars().all()
    }

    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    # Collect all positions and their context
    all_positions: list[tuple[Position, BuildingGroup, Building]] = []
    for building in siege.buildings:
        for group in building.groups:
            for position in group.positions:
                all_positions.append((position, group, building))

    # Scroll accounting notes:
    #
    #   - SCROLL LIMIT (compute_scroll_count in sieges.py) includes ALL buildings regardless
    #     of is_broken.  It sums theoretical capacity by building type + level from game data,
    #     never consulting Position records.  The limit is a stable planning baseline that
    #     should only shift when buildings are added, removed, or levelled — none of which
    #     can happen once a siege is active (update_building in buildings.py rejects all
    #     building changes, including breaking, when the siege is active or complete).
    #
    #   - SCROLL BUDGET usage (assignments_by_member, Rule 2) includes ALL buildings
    #     regardless of is_broken.  Both the limit and the per-member assignment count
    #     treat broken buildings identically — a member assigned to a broken building
    #     still consumes a scroll slot from their allowance.
    #
    #   - COUNTING rules that are NOT scroll-related (Rule 15 reserve-set check) use
    #     all_assigned_member_ids, which INCLUDES broken buildings — every assigned member
    #     is accountable for configuring their reserve set regardless of building state.
    #   - NOISE rules (Rule 10 empty-slot warnings) skip broken buildings —
    #     empty slots on broken buildings are not actionable.
    #   - STRUCTURAL rules (Rules 1, 3, 4, 5, 7, 8, 9, 11) still check broken buildings —
    #     a broken building can still have invalid configuration or invalid assignments.

    # Scroll-budget usage counter: includes ALL buildings (broken or healthy) so that
    # the per-member assignment count matches what the scroll limit already charges for.
    # Note: other rules (e.g. Rule 15) use all_assigned_member_ids which also includes
    # broken buildings, because those rules are not scroll-related.
    assignments_by_member: dict[int, int] = defaultdict(int)
    for pos, group, building in all_positions:
        if pos.member_id is not None and not pos.is_reserve and not pos.is_disabled:
            assignments_by_member[pos.member_id] += 1

    # Build siege_member lookup by member_id
    sm_by_member: dict[int, SiegeMember] = {sm.member_id: sm for sm in siege.siege_members}

    # ------------------------------------------------------------------ #
    # ERRORS
    # ------------------------------------------------------------------ #

    # Rule 1: All assigned members must be active
    for pos, group, building in all_positions:
        if pos.member_id is not None and pos.member is not None and not pos.member.is_active:
            errors.append(
                ValidationIssue(
                    rule=1,
                    message=f"Assigned member '{pos.member.name}' is not active",
                    context={"member_id": pos.member_id, "position_id": pos.id},
                )
            )

    # Rule 2: No member assigned more than defense_scroll_count times
    for member_id, count in assignments_by_member.items():
        if count > scroll_limit:
            member_name = "Unknown"
            for pos, _, _ in all_positions:
                if pos.member_id == member_id and pos.member is not None:
                    member_name = pos.member.name
                    break
            errors.append(
                ValidationIssue(
                    rule=2,
                    message=(
                        f"Member '{member_name}' is assigned {count} times "
                        f"but scroll limit is {scroll_limit}"
                    ),
                    context={"member_id": member_id, "count": count, "limit": scroll_limit},
                )
            )

    # Rule 3: Building numbers within type-specific range
    for building in siege.buildings:
        config = configs.get(building.building_type)
        if config is not None:
            if building.building_number < 1 or building.building_number > config.count:
                errors.append(
                    ValidationIssue(
                        rule=3,
                        message=(
                            f"{BUILDING_TYPE_LABELS[building.building_type]} has number "
                            f"{building.building_number} outside valid range [1, {config.count}]"
                        ),
                        context={
                            "building_id": building.id,
                            "building_type": building.building_type,
                        },
                    )
                )

    # Rule 4: Group numbers 1–10
    # Upper bound matches the DB CHECK constraint on building_group.group_number
    # (group_number >= 1 AND group_number <= 10) — level-6 strongholds have 10
    # groups, so 10 is the true global maximum.  See: models/building_group.py.
    _GROUP_NUMBER_MAX = 10
    for pos, group, building in all_positions:
        if group.group_number < 1 or group.group_number > _GROUP_NUMBER_MAX:
            errors.append(
                ValidationIssue(
                    rule=4,
                    message=(
                        f"Group id={group.id} has group_number "
                        f"{group.group_number} outside [1, {_GROUP_NUMBER_MAX}]"
                    ),
                    context={"group_id": group.id, "building_id": building.id},
                )
            )

    # Rule 5: Position numbers 1 to slot_count
    for pos, group, building in all_positions:
        if pos.position_number < 1 or pos.position_number > group.slot_count:
            errors.append(
                ValidationIssue(
                    rule=5,
                    message=(
                        f"Position id={pos.id} has position_number {pos.position_number} "
                        f"outside [1, {group.slot_count}]"
                    ),
                    context={"position_id": pos.id, "group_id": group.id},
                )
            )

    # Rule 6: Attack day must be 1 or 2 if set
    for sm in siege.siege_members:
        if sm.attack_day is not None and sm.attack_day not in (1, 2):
            name = sm.member.name if sm.member else "Unknown"
            errors.append(
                ValidationIssue(
                    rule=6,
                    message=f"Member '{name}' has invalid attack_day {sm.attack_day}",
                    context={"member_id": sm.member_id, "attack_day": sm.attack_day},
                )
            )

    # Rule 7: Post buildings have exactly 1 group
    for building in siege.buildings:
        if building.building_type == BuildingType.post:
            if len(building.groups) != 1:
                errors.append(
                    ValidationIssue(
                        rule=7,
                        message=(
                            f"Post {building.building_number} "
                            f"has {len(building.groups)} groups (expected 1)"
                        ),
                        context={"building_id": building.id},
                    )
                )

    # Rule 8: Position state consistency
    for pos, group, building in all_positions:
        if pos.is_disabled and (pos.member_id is not None or pos.is_reserve):
            errors.append(
                ValidationIssue(
                    rule=8,
                    message=(
                        f"Position id={pos.id} is disabled but also "
                        f"has member_id or is_reserve=True"
                    ),
                    context={"position_id": pos.id},
                )
            )
        elif pos.is_reserve and pos.member_id is not None:
            errors.append(
                ValidationIssue(
                    rule=8,
                    message=f"Position id={pos.id} is marked reserve but has a member assigned",
                    context={"position_id": pos.id},
                )
            )

    # Rule 9: Building count per type matches BuildingTypeConfig.count
    buildings_by_type: dict[BuildingType, list[Building]] = defaultdict(list)
    for building in siege.buildings:
        buildings_by_type[building.building_type].append(building)

    for building_type, config in configs.items():
        actual = len(buildings_by_type.get(building_type, []))
        if actual != config.count:
            errors.append(
                ValidationIssue(
                    rule=9,
                    message=(
                        f"Building type '{building_type}' has {actual} buildings "
                        f"but expected {config.count}"
                    ),
                    context={
                        "building_type": building_type,
                        "expected": config.count,
                        "actual": actual,
                    },
                )
            )

    # ------------------------------------------------------------------ #
    # WARNINGS
    # ------------------------------------------------------------------ #

    # Rule 10/12: Empty unresolved slots (not assigned, not disabled, not reserve).
    # Broken buildings are skipped: their surviving positions are intentionally empty
    # (the building is out of action) and flagging them produces unactionable warnings.
    for pos, group, building in all_positions:
        if building.is_broken:
            continue
        if pos.member_id is None and not pos.is_disabled and not pos.is_reserve:
            warnings.append(
                ValidationIssue(
                    rule=10,
                    message=(
                        f"{BUILDING_TYPE_LABELS[building.building_type]} "
                        f"{building.building_number} "
                        f"Group {group.group_number} Position {pos.position_number} "
                        f"is unassigned, not disabled, and not marked reserve"
                    ),
                    context={
                        "position_id": pos.id,
                        "building_id": building.id,
                        "building_type": building.building_type,
                        "building_number": building.building_number,
                        "group_number": group.group_number,
                        "position_number": pos.position_number,
                    },
                )
            )

    # Rule 11: Member with preferences assigned to a post where none match active conditions
    # Only fires if member has preferences AND post has active conditions AND none match
    for building in siege.buildings:
        if building.building_type != BuildingType.post:
            continue
        post = building.post
        if post is None or len(post.active_conditions) == 0:
            continue
        active_condition_ids = {c.id for c in post.active_conditions}

        for pos, group, bld in all_positions:
            if bld.id != building.id:
                continue
            if pos.member_id is None or pos.member is None:
                continue
            member = pos.member
            if len(member.post_preferences) == 0:
                continue
            pref_condition_ids = {c.id for c in member.post_preferences}
            if not pref_condition_ids.intersection(active_condition_ids):
                warnings.append(
                    ValidationIssue(
                        rule=11,
                        message=(
                            f"Member '{member.name}' is assigned to Post "
                            f"{building.building_number} but none of their "
                            f"preferences match active conditions"
                        ),
                        context={"member_id": member.id, "building_id": building.id},
                    )
                )

    # Rule 13: Any siege member with no attack day set
    for sm in siege.siege_members:
        if sm.attack_day is None:
            name = sm.member.name if sm.member else "Unknown"
            errors.append(
                ValidationIssue(
                    rule=13,
                    message=f"Member '{name}' has no attack day assigned",
                    context={"member_id": sm.member_id},
                )
            )

    # Rule 14: Fewer than 10 Day 2 attackers
    day2_count = sum(1 for sm in siege.siege_members if sm.attack_day == 2)
    if day2_count < 10:
        warnings.append(
            ValidationIssue(
                rule=14,
                message=f"Only {day2_count} Day 2 attackers assigned (minimum recommended: 10)",
                context={"day2_count": day2_count},
            )
        )

    # Rule 15: HH/Advanced Role members without a reserve flag configured.
    # Only Heavy Hitter and Advanced Role members are required to have the reserve
    # toggle set; other roles are not subject to this requirement.
    # Uses all_assigned_member_ids (includes broken buildings) because reserve-set
    # configuration is not scroll-related — a member assigned only to broken buildings
    # must still have has_reserve_set configured.
    _HH_ADVANCED_ROLES = {MemberRole.heavy_hitter, MemberRole.advanced}
    all_assigned_member_ids: set[int] = {
        pos.member_id
        for pos, group, building in all_positions
        if pos.member_id is not None and not pos.is_reserve and not pos.is_disabled
    }
    for member_id in all_assigned_member_ids:
        sm = sm_by_member.get(member_id)
        if sm is None:
            continue
        member_role = sm.member.role if sm.member else None
        if member_role not in _HH_ADVANCED_ROLES:
            continue
        if not sm.has_reserve_set:
            name = sm.member.name if sm.member else "Unknown"
            warnings.append(
                ValidationIssue(
                    rule=15,
                    message=(
                        f"Member '{name}' is a Heavy Hitter/Advanced Role "
                        f"but has no reserve configured"
                    ),
                    context={"member_id": member_id},
                )
            )

    # Rule 16: Posts with fewer than 3 active conditions configured
    for building in siege.buildings:
        if building.building_type != BuildingType.post:
            continue
        post = building.post
        condition_count = len(post.active_conditions) if post is not None else 0
        if condition_count < 3:
            warnings.append(
                ValidationIssue(
                    rule=16,
                    message=(
                        f"Post {building.building_number} "
                        f"has only {condition_count} active conditions "
                        f"(minimum recommended: 3)"
                    ),
                    context={"building_id": building.id, "condition_count": condition_count},
                )
            )

    return ValidationResult(errors=errors, warnings=warnings)
