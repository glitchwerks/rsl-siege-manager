"""Integration test: full siege lifecycle — create → validate → assign → activate → complete."""

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.enums import BuildingType, MemberRole, SiegeStatus
from app.services import lifecycle as lifecycle_service
from app.services import validation as validation_service

# ---------------------------------------------------------------------------
# Helpers — build a minimal valid siege graph
# ---------------------------------------------------------------------------


def _make_siege(status=SiegeStatus.planning):
    return SimpleNamespace(
        id=1,
        date=datetime.date(2026, 4, 1),
        status=status,
        defense_scroll_count=5,
        created_at=datetime.datetime(2026, 1, 1),
        updated_at=datetime.datetime(2026, 1, 1),
        autofill_preview=None,
        autofill_preview_expires_at=None,
        attack_day_preview=None,
        attack_day_preview_expires_at=None,
        buildings=[],
        siege_members=[],
        posts=[],
    )


def _make_member(id=1, name="Alice", is_active=True):
    return SimpleNamespace(
        id=id,
        name=name,
        role=MemberRole.advanced,
        is_active=is_active,
        power=None,
        post_preferences=[],
    )


def _make_position(
    id, position_number=1, member_id=None, member=None, is_reserve=False, is_disabled=False
):
    return SimpleNamespace(
        id=id,
        position_number=position_number,
        member_id=member_id,
        member=member,
        is_reserve=is_reserve,
        is_disabled=is_disabled,
        building_group_id=1,
    )


def _make_group(id=1, group_number=1, slot_count=3, positions=None):
    g = SimpleNamespace(id=id, group_number=group_number, slot_count=slot_count, building_id=1)
    g.positions = positions or []
    return g


def _make_building(id, building_type=BuildingType.stronghold, building_number=1, groups=None):
    b = SimpleNamespace(
        id=id,
        building_type=building_type,
        building_number=building_number,
        level=1,
        is_broken=False,
        post=None,
    )
    b.groups = groups or []
    return b


def _make_siege_member(member_id, attack_day=1, has_reserve_set=True, member=None):
    return SimpleNamespace(
        siege_id=1,
        member_id=member_id,
        attack_day=attack_day,
        has_reserve_set=has_reserve_set,
        attack_day_override=False,
        member=member,
    )


def _make_building_type_config(building_type, count, base_group_count=1, base_last_group_slots=2):
    return SimpleNamespace(
        building_type=building_type,
        count=count,
        base_group_count=base_group_count,
        base_last_group_slots=base_last_group_slots,
    )


def _default_configs():
    return [
        _make_building_type_config(BuildingType.stronghold, 1, base_group_count=4),
        _make_building_type_config(BuildingType.mana_shrine, 2, base_group_count=2),
        _make_building_type_config(BuildingType.magic_tower, 4, base_group_count=1),
        _make_building_type_config(BuildingType.defense_tower, 5, base_group_count=1),
        _make_building_type_config(
            BuildingType.post, 18, base_group_count=1, base_last_group_slots=1
        ),
    ]


def _build_valid_siege_graph():
    """Build a siege with exactly the right building counts and assigned active members."""
    members = [_make_member(id=i, name=f"Member{i}") for i in range(1, 11)]

    # Build exactly the number of each building type the config expects
    buildings = []
    building_id = 1

    # 1 stronghold
    stronghold = _make_building(
        id=building_id, building_type=BuildingType.stronghold, building_number=1
    )
    g = _make_group(id=1, group_number=1, slot_count=3)
    g.positions = []
    stronghold.groups = [g]
    buildings.append(stronghold)
    building_id += 1

    # 2 mana shrines
    for bn in range(1, 3):
        b = _make_building(
            id=building_id, building_type=BuildingType.mana_shrine, building_number=bn
        )
        b.groups = []
        buildings.append(b)
        building_id += 1

    # 4 magic towers
    for bn in range(1, 5):
        b = _make_building(
            id=building_id, building_type=BuildingType.magic_tower, building_number=bn
        )
        b.groups = []
        buildings.append(b)
        building_id += 1

    # 5 defense towers
    for bn in range(1, 6):
        b = _make_building(
            id=building_id, building_type=BuildingType.defense_tower, building_number=bn
        )
        b.groups = []
        buildings.append(b)
        building_id += 1

    # 18 posts
    for bn in range(1, 19):
        b = _make_building(id=building_id, building_type=BuildingType.post, building_number=bn)
        g = _make_group(id=building_id * 10, group_number=1, slot_count=1)
        g.positions = []
        b.groups = [g]
        buildings.append(b)
        building_id += 1

    # 10 siege members — Day 1 and Day 2 (5 each to start)
    siege_members = []
    for i, m in enumerate(members):
        day = 1 if i < 5 else 2
        siege_members.append(
            _make_siege_member(member_id=m.id, attack_day=day, has_reserve_set=True, member=m)
        )

    return buildings, siege_members, members


# ---------------------------------------------------------------------------
# Session mock factory
# ---------------------------------------------------------------------------


def _make_session(siege):
    """Return a mock async session that serves the siege + configs across multiple execute calls."""
    call_count = 0

    async def fake_execute(stmt):
        nonlocal call_count
        result = MagicMock()
        if call_count == 0:
            # First call: siege query (used by validate_siege)
            result.scalar_one_or_none.return_value = siege
        elif call_count == 1:
            # Second call: compute_scroll_count → returns an integer
            result.scalar.return_value = 5
        else:
            # Subsequent calls: config query or repeated siege queries
            result.scalar_one_or_none.return_value = siege
            result.scalars.return_value.all.return_value = _default_configs()
        call_count += 1
        return result

    session = AsyncMock()
    session.execute = fake_execute
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Full lifecycle integration test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_siege_lifecycle():
    """Happy-path: validate (errors) → assign → validate (0 errors) → activate → complete."""

    # --- Step 1: Create a planning siege (no buildings yet) ---
    empty_siege = _make_siege(status=SiegeStatus.planning)

    session = _make_session(empty_siege)
    result = await validation_service.validate_siege(session, 1)

    # Should have errors because building counts are wrong (0 of each type)
    assert len(result.errors) > 0
    rule9_errors = [e for e in result.errors if e.rule == 9]
    assert len(rule9_errors) > 0, "Expected Rule 9 errors (wrong building count)"

    # --- Step 2: Add buildings, groups, positions, siege members ---
    buildings, siege_members, members = _build_valid_siege_graph()
    configured_siege = _make_siege(status=SiegeStatus.planning)
    configured_siege.buildings = buildings
    configured_siege.siege_members = siege_members

    # --- Step 3: Assign one member to stronghold group position ---
    member1 = members[0]
    pos1 = _make_position(id=1, position_number=1, member_id=member1.id, member=member1)
    configured_siege.buildings[0].groups[0].positions = [pos1]

    # --- Step 4: Validate the fully configured siege ---
    call_count = 0

    async def configured_execute(stmt):
        nonlocal call_count
        result = MagicMock()
        if call_count == 0:
            result.scalar_one_or_none.return_value = configured_siege
        elif call_count == 1:
            # compute_scroll_count
            result.scalar.return_value = 5
        else:
            result.scalar_one_or_none.return_value = configured_siege
            result.scalars.return_value.all.return_value = _default_configs()
        call_count += 1
        return result

    configured_session = AsyncMock()
    configured_session.execute = configured_execute
    configured_session.commit = AsyncMock()
    configured_session.refresh = AsyncMock()

    result2 = await validation_service.validate_siege(configured_session, 1)

    # With correct building counts and ≥10 Day-2 members, no hard errors expected
    # (we have 5 Day 2 members so Rule 14 warning fires, but no errors)
    assert len(result2.errors) == 0, f"Unexpected errors: {result2.errors}"
    assert any(w.rule == 14 for w in result2.warnings), "Expected Rule 14 warning (< 10 Day 2)"

    # --- Step 5: Activate the siege (service-level, mock session) ---
    active_siege = _make_siege(status=SiegeStatus.active)
    active_siege.buildings = buildings
    active_siege.siege_members = siege_members

    activate_call_count = 0

    async def activate_execute(stmt):
        nonlocal activate_call_count
        r = MagicMock()
        if activate_call_count == 0:
            # Siege lookup
            assert stmt._for_update_arg is not None
            r.scalar_one_or_none.return_value = configured_siege
        elif activate_call_count == 1:
            # Active siege check (looking for another active siege) — none found
            r.scalar_one_or_none.return_value = None
        elif activate_call_count == 2:
            # validate_siege siege query
            r.scalar_one_or_none.return_value = configured_siege
        elif activate_call_count == 3:
            # validate_siege: compute_scroll_count
            r.scalar.return_value = 5
        else:
            # validate_siege config query
            r.scalar_one_or_none.return_value = None
            r.scalars.return_value.all.return_value = _default_configs()
        activate_call_count += 1
        return r

    activate_session = AsyncMock()
    activate_session.execute = activate_execute
    activate_session.commit = AsyncMock()

    async def refresh_sets_active(obj):
        obj.status = SiegeStatus.active

    activate_session.refresh = refresh_sets_active

    result_siege = await lifecycle_service.activate_siege(activate_session, 1)
    assert result_siege.status == SiegeStatus.active

    # --- Step 6: Complete the siege ---
    completeable_siege = _make_siege(status=SiegeStatus.active)

    async def complete_execute(stmt):
        r = MagicMock()
        r.scalar_one_or_none.return_value = completeable_siege
        return r

    complete_session = AsyncMock()
    complete_session.execute = complete_execute
    complete_session.commit = AsyncMock()

    async def refresh_sets_complete(obj):
        obj.status = SiegeStatus.complete

    complete_session.refresh = refresh_sets_complete

    completed = await lifecycle_service.complete_siege(complete_session, 1)
    assert completed.status == SiegeStatus.complete
