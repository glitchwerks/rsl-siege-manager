"""Tests for the validation engine (all 16 rules) and the validate endpoint."""

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.building_type_config import BuildingTypeConfig
from app.models.enums import BuildingType, MemberRole, SiegeStatus
from app.models.siege import Siege
from app.schemas.validation import ValidationIssue, ValidationResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_siege(id=1, defense_scroll_count=5, status=SiegeStatus.planning):
    return SimpleNamespace(
        id=id,
        date=datetime.date(2026, 3, 20),
        status=status,
        defense_scroll_count=defense_scroll_count,
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


def _make_member(id=1, name="Alice", role=MemberRole.advanced, is_active=True, power=None):
    return SimpleNamespace(
        id=id, name=name, role=role, is_active=is_active, power=power, post_preferences=[]
    )


def _make_building(id=1, siege_id=1, building_type=BuildingType.stronghold, building_number=1):
    return SimpleNamespace(
        id=id,
        siege_id=siege_id,
        building_type=building_type,
        building_number=building_number,
        level=1,
        is_broken=False,
        groups=[],
        post=None,
    )


def _make_group(id=1, building_id=1, group_number=1, slot_count=3):
    return SimpleNamespace(
        id=id,
        building_id=building_id,
        group_number=group_number,
        slot_count=slot_count,
        positions=[],
    )


def _make_position(
    id=1,
    group_id=1,
    position_number=1,
    member_id=None,
    is_reserve=False,
    is_disabled=False,
    member=None,
):
    return SimpleNamespace(
        id=id,
        building_group_id=group_id,
        position_number=position_number,
        member_id=member_id,
        is_reserve=is_reserve,
        is_disabled=is_disabled,
        member=member,
    )


def _make_siege_member(
    siege_id=1,
    member_id=1,
    attack_day=1,
    has_reserve_set=True,
    attack_day_override=False,
    member=None,
):
    return SimpleNamespace(
        siege_id=siege_id,
        member_id=member_id,
        attack_day=attack_day,
        has_reserve_set=has_reserve_set,
        attack_day_override=attack_day_override,
        member=member,
    )


def _make_config(building_type, count, base_group_count=1, base_last_group_slots=2):
    return SimpleNamespace(
        building_type=building_type,
        count=count,
        base_group_count=base_group_count,
        base_last_group_slots=base_last_group_slots,
    )


def _make_post(id=1, siege_id=1, building_id=1, active_conditions=None):
    return SimpleNamespace(
        id=id,
        siege_id=siege_id,
        building_id=building_id,
        priority=0,
        description=None,
        active_conditions=active_conditions or [],
    )


def _make_condition(id=1, description="Cond"):
    return SimpleNamespace(
        id=id, description=description, stronghold_level=1, posts=[], member_preferences=[]
    )


# ---------------------------------------------------------------------------
# API fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Endpoint: 404 if siege not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_endpoint_404(client):
    from unittest.mock import MagicMock

    from app.db.session import get_db

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    async def override_get_db():
        yield mock_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with client as c:
            response = await c.post("/api/sieges/999/validate")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_validate_endpoint_returns_result(client):
    from unittest.mock import MagicMock

    from app.db.session import get_db

    result = ValidationResult(
        errors=[ValidationIssue(rule=1, message="Test error")],
        warnings=[ValidationIssue(rule=14, message="Test warning")],
    )
    siege = _make_siege()
    mock_db_result = MagicMock()
    mock_db_result.scalar_one_or_none.return_value = siege
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_db_result)

    async def override_get_db():
        yield mock_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.validation.validation_service.validate_siege", new_callable=AsyncMock
        ) as mock_svc:
            mock_svc.return_value = result
            async with client as c:
                response = await c.post("/api/sieges/1/validate")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    data = response.json()
    assert len(data["errors"]) == 1
    assert len(data["warnings"]) == 1


# ---------------------------------------------------------------------------
# Service unit tests — all 16 rules
# ---------------------------------------------------------------------------

from app.services.validation import validate_siege as svc_validate  # noqa: E402


@pytest.mark.asyncio
async def test_rule1_inactive_member_error():
    """Rule 1: assigned member who is inactive triggers an error."""
    member = _make_member(id=1, is_active=False)
    pos = _make_position(id=1, member_id=1, member=member)
    group = _make_group(id=1, slot_count=3)
    group.positions = [pos]
    building = _make_building(id=1, building_type=BuildingType.stronghold, building_number=1)
    building.groups = [group]
    siege = _make_siege()
    siege.buildings = [building]
    sm = _make_siege_member(member_id=1, attack_day=1, has_reserve_set=True, member=member)
    siege.siege_members = [sm]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule1_errors = [e for e in result.errors if e.rule == 1]
    assert len(rule1_errors) >= 1


@pytest.mark.asyncio
async def test_rule1_active_member_no_error():
    """Rule 1 pass: active member assigned — no rule 1 error."""
    member = _make_member(id=1, is_active=True)
    pos = _make_position(id=1, member_id=1, member=member)
    group = _make_group(id=1, slot_count=3)
    group.positions = [pos]
    building = _make_building(id=1, building_type=BuildingType.stronghold, building_number=1)
    building.groups = [group]
    siege = _make_siege(defense_scroll_count=5)
    siege.buildings = [building]
    sm = _make_siege_member(member_id=1, attack_day=1, has_reserve_set=True, member=member)
    siege.siege_members = [sm]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule1_errors = [e for e in result.errors if e.rule == 1]
    assert len(rule1_errors) == 0


@pytest.mark.asyncio
async def test_rule2_broken_building_assignment_counts_toward_scroll_limit():
    """Rule 2 regression (#253): assignments on broken buildings count toward scroll budget.

    A member is assigned to 4 positions on a broken building.
    compute_scroll_count (mocked to return 5) gives a limit of 3.
    All 4 assignments must count regardless of is_broken, so Rule 2
    must fire with count=4 and limit=3.
    """
    member = _make_member(id=1, is_active=True)
    # 4 positions on a broken building — exceeds the limit of 3 when counted
    positions = [
        _make_position(id=i, position_number=i, member_id=1, member=member) for i in range(1, 5)
    ]
    group = _make_group(id=1, slot_count=4)
    group.positions = positions
    broken_building = _make_building(id=1)
    broken_building.is_broken = True
    broken_building.groups = [group]

    siege = _make_siege()
    siege.buildings = [broken_building]
    sm = _make_siege_member(member_id=1, attack_day=1, has_reserve_set=True, member=member)
    siege.siege_members = [sm]

    # compute_scroll_count returns 5 → limit = 3.  Member has 4 assignments on a
    # broken building; they must all count, so Rule 2 must fire.
    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule2_errors = [e for e in result.errors if e.rule == 2]
    assert len(rule2_errors) >= 1, (
        "Assignments on broken buildings must count toward the scroll limit; "
        "Rule 2 should have fired but did not"
    )
    assert rule2_errors[0].context["count"] == 4
    assert rule2_errors[0].context["limit"] == 3


@pytest.mark.asyncio
async def test_rule2_broken_and_healthy_both_count_toward_scroll_limit():
    """Rule 2: assignments on broken buildings add to healthy-building count.

    Member has 2 assignments on a healthy building + 2 assignments on a broken building.
    compute_scroll_count (mocked to return 5) → limit = 3.
    Total count = 4 (both healthy and broken), which exceeds 3, so Rule 2 must fire.
    """
    member = _make_member(id=1, is_active=True)

    # 2 positions on a healthy building
    healthy_positions = [
        _make_position(id=i, position_number=i, member_id=1, member=member) for i in range(1, 3)
    ]
    healthy_group = _make_group(id=1, slot_count=2)
    healthy_group.positions = healthy_positions
    healthy_building = _make_building(id=1)
    healthy_building.is_broken = False
    healthy_building.groups = [healthy_group]

    # 2 positions on a broken building
    broken_positions = [
        _make_position(id=i, position_number=i - 2, member_id=1, member=member) for i in range(3, 5)
    ]
    broken_group = _make_group(id=2, slot_count=2)
    broken_group.positions = broken_positions
    broken_building = _make_building(id=2)
    broken_building.is_broken = True
    broken_building.groups = [broken_group]

    siege = _make_siege()
    siege.buildings = [healthy_building, broken_building]
    sm = _make_siege_member(member_id=1, attack_day=1, has_reserve_set=True, member=member)
    siege.siege_members = [sm]

    # compute_scroll_count returns 5 → limit = 3.
    # Total count = 4 (2 healthy + 2 broken) → exceeds limit → Rule 2 must fire.
    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule2_errors = [e for e in result.errors if e.rule == 2]
    assert len(rule2_errors) >= 1, (
        "Broken-building assignments must add to the scroll budget total; "
        "Rule 2 should have fired but did not"
    )
    assert rule2_errors[0].context["count"] == 4


@pytest.mark.asyncio
async def test_rule2_exceeds_scroll_count():
    """Rule 2: member assigned 4 times when scrolls_per_player limit is 3 → error."""
    member = _make_member(id=1, is_active=True)
    positions = [
        _make_position(id=i, position_number=i, member_id=1, member=member) for i in range(1, 5)
    ]
    group = _make_group(id=1, slot_count=4)
    group.positions = positions
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege()
    siege.buildings = [building]
    sm = _make_siege_member(member_id=1, attack_day=1, has_reserve_set=True, member=member)
    siege.siege_members = [sm]

    # _session_with_siege_and_configs mocks compute_scroll_count to return 5,
    # so scrolls_per_player(5) = 3; member with 4 assignments exceeds the limit
    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule2_errors = [e for e in result.errors if e.rule == 2]
    assert len(rule2_errors) >= 1


@pytest.mark.asyncio
async def test_rule2_within_scroll_count():
    """Rule 2 pass: member assigned once, scroll count 5 → no error."""
    member = _make_member(id=1, is_active=True)
    pos = _make_position(id=1, member_id=1, member=member)
    group = _make_group(id=1, slot_count=3)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege(defense_scroll_count=5)
    siege.buildings = [building]
    sm = _make_siege_member(member_id=1, attack_day=1, has_reserve_set=True, member=member)
    siege.siege_members = [sm]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule2_errors = [e for e in result.errors if e.rule == 2]
    assert len(rule2_errors) == 0


@pytest.mark.asyncio
async def test_rule3_invalid_building_number():
    """Rule 3: building_number=0 for stronghold → friendly label, no id= leak."""
    building = _make_building(id=1, building_type=BuildingType.stronghold, building_number=0)
    building.groups = []
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule3_errors = [e for e in result.errors if e.rule == 3]
    assert len(rule3_errors) >= 1
    msg = rule3_errors[0].message
    assert "id=" not in msg, f"Message must not expose raw id, got: {msg!r}"
    assert "stronghold" not in msg, f"Message must not expose raw enum, got: {msg!r}"
    assert "Stronghold" in msg, f"Message must show friendly label, got: {msg!r}"
    assert "0" in msg, f"Message must include the invalid number, got: {msg!r}"
    # context dict must still carry machine-readable handles
    assert rule3_errors[0].context["building_id"] == 1
    assert rule3_errors[0].context["building_type"] == "stronghold"


@pytest.mark.asyncio
async def test_rule3_valid_building_number():
    """Rule 3 pass: building_number=1 for stronghold."""
    building = _make_building(id=1, building_type=BuildingType.stronghold, building_number=1)
    building.groups = []
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule3_errors = [e for e in result.errors if e.rule == 3]
    assert len(rule3_errors) == 0


@pytest.mark.asyncio
async def test_rule4_invalid_group_number():
    """Rule 4: group_number=11 exceeds the maximum of 10 → error.

    Level-6 strongholds have 10 groups; group_number=11 is always invalid.
    """
    pos = _make_position(id=1)
    group = _make_group(id=1, group_number=11)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule4_errors = [e for e in result.errors if e.rule == 4]
    assert len(rule4_errors) >= 1


@pytest.mark.asyncio
async def test_rule4_group_number_zero_is_invalid():
    """Rule 4: group_number=0 is below the minimum of 1 → error."""
    pos = _make_position(id=1)
    group = _make_group(id=1, group_number=0)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule4_errors = [e for e in result.errors if e.rule == 4]
    assert len(rule4_errors) >= 1


@pytest.mark.asyncio
async def test_rule4_group_number_10_is_valid():
    """Rule 4 pass: group_number=10 is valid for a level-6 stronghold.

    The DB CHECK constraint allows group_number in [1, 10].  The validator
    must align with the DB: group_number=10 must not produce a Rule 4 error.
    Regression test for GitHub issue #495.
    """
    pos = _make_position(id=1)
    group = _make_group(id=1, group_number=10)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule4_errors = [e for e in result.errors if e.rule == 4]
    assert len(rule4_errors) == 0, (
        "group_number=10 is valid for a level-6 stronghold (10 groups); "
        f"Rule 4 must not fire, but got: {rule4_errors}"
    )


@pytest.mark.asyncio
async def test_rule4_valid_group_number():
    """Rule 4 pass: group_number=1."""
    pos = _make_position(id=1)
    group = _make_group(id=1, group_number=1)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule4_errors = [e for e in result.errors if e.rule == 4]
    assert len(rule4_errors) == 0


@pytest.mark.asyncio
async def test_rule5_position_number_exceeds_slot_count():
    """Rule 5: position_number=4 with slot_count=3 → error."""
    pos = _make_position(id=1, position_number=4)
    group = _make_group(id=1, slot_count=3)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule5_errors = [e for e in result.errors if e.rule == 5]
    assert len(rule5_errors) >= 1


@pytest.mark.asyncio
async def test_rule5_valid_position_number():
    """Rule 5 pass: position_number=2, slot_count=3."""
    pos = _make_position(id=1, position_number=2)
    group = _make_group(id=1, slot_count=3)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule5_errors = [e for e in result.errors if e.rule == 5]
    assert len(rule5_errors) == 0


@pytest.mark.asyncio
async def test_rule6_invalid_attack_day():
    """Rule 6: attack_day=3 → error."""
    sm = _make_siege_member(member_id=1, attack_day=3, has_reserve_set=True)
    siege = _make_siege()
    siege.siege_members = [sm]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule6_errors = [e for e in result.errors if e.rule == 6]
    assert len(rule6_errors) >= 1


@pytest.mark.asyncio
async def test_rule6_valid_attack_day():
    """Rule 6 pass: attack_day=2."""
    sm = _make_siege_member(member_id=1, attack_day=2, has_reserve_set=True)
    siege = _make_siege()
    siege.siege_members = [sm]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule6_errors = [e for e in result.errors if e.rule == 6]
    assert len(rule6_errors) == 0


@pytest.mark.asyncio
async def test_rule7_post_has_multiple_groups():
    """Rule 7: post building with 2 groups → error with correct message."""
    building = _make_building(id=1, building_type=BuildingType.post, building_number=1)
    g1 = _make_group(id=1, group_number=1)
    g1.positions = []
    g2 = _make_group(id=2, group_number=2)
    g2.positions = []
    building.groups = [g1, g2]
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule7_errors = [e for e in result.errors if e.rule == 7]
    assert len(rule7_errors) >= 1
    # Message uses "Post N" — not the internal building.id primary key.
    assert rule7_errors[0].message == "Post 1 has 2 groups (expected 1)"
    # context dict is unchanged — building_id is still the machine-readable handle.
    assert rule7_errors[0].context["building_id"] == 1


@pytest.mark.asyncio
async def test_rule7_post_has_exactly_one_group():
    """Rule 7 pass: post building with 1 group."""
    building = _make_building(id=1, building_type=BuildingType.post, building_number=1)
    g1 = _make_group(id=1, group_number=1)
    g1.positions = []
    building.groups = [g1]
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule7_errors = [e for e in result.errors if e.rule == 7]
    assert len(rule7_errors) == 0


@pytest.mark.asyncio
async def test_rule8_disabled_with_member():
    """Rule 8: disabled position with member_id → error."""
    member = _make_member(id=1, is_active=True)
    pos = _make_position(id=1, member_id=1, is_disabled=True, member=member)
    group = _make_group(id=1)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule8_errors = [e for e in result.errors if e.rule == 8]
    assert len(rule8_errors) >= 1


@pytest.mark.asyncio
async def test_rule8_reserve_with_member():
    """Rule 8: reserve position with member → error."""
    member = _make_member(id=1, is_active=True)
    pos = _make_position(id=1, member_id=1, is_reserve=True, member=member)
    group = _make_group(id=1)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule8_errors = [e for e in result.errors if e.rule == 8]
    assert len(rule8_errors) >= 1


@pytest.mark.asyncio
async def test_rule8_valid_state():
    """Rule 8 pass: position with member, not reserve, not disabled."""
    member = _make_member(id=1, is_active=True)
    pos = _make_position(id=1, member_id=1, member=member)
    group = _make_group(id=1)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule8_errors = [e for e in result.errors if e.rule == 8]
    assert len(rule8_errors) == 0


@pytest.mark.asyncio
async def test_rule9_wrong_building_count():
    """Rule 9: 0 strongholds when config expects 1 → error."""
    siege = _make_siege()
    siege.buildings = []  # no buildings at all

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule9_errors = [e for e in result.errors if e.rule == 9]
    assert len(rule9_errors) >= 1


@pytest.mark.asyncio
async def test_rule9_correct_building_count():
    """Rule 9 pass: exactly the right number of each building type."""
    siege = _make_siege()
    # Build exactly what the configs expect
    buildings = []
    for bt, count in [
        (BuildingType.stronghold, 1),
        (BuildingType.mana_shrine, 2),
        (BuildingType.magic_tower, 4),
        (BuildingType.defense_tower, 5),
        (BuildingType.post, 18),
    ]:
        for i in range(1, count + 1):
            b = _make_building(id=len(buildings) + 1, building_type=bt, building_number=i)
            b.groups = []
            buildings.append(b)
    siege.buildings = buildings

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule9_errors = [e for e in result.errors if e.rule == 9]
    assert len(rule9_errors) == 0


@pytest.mark.asyncio
async def test_rule10_empty_unresolved_slot():
    """Rule 10: unassigned, non-disabled, non-reserve position → warning."""
    pos = _make_position(id=1, member_id=None, is_disabled=False, is_reserve=False)
    group = _make_group(id=1)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule10_warnings = [w for w in result.warnings if w.rule == 10]
    assert len(rule10_warnings) >= 1


@pytest.mark.asyncio
async def test_rule10_message_uses_position_name():
    """Rule 10 message uses friendly building label, not raw enum value."""
    pos = _make_position(
        id=3090, position_number=2, member_id=None, is_disabled=False, is_reserve=False
    )
    group = _make_group(id=1, group_number=3)
    group.positions = [pos]
    building = _make_building(id=1, building_type=BuildingType.stronghold, building_number=1)
    building.groups = [group]
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule10_warnings = [w for w in result.warnings if w.rule == 10]
    assert len(rule10_warnings) >= 1
    msg = rule10_warnings[0].message
    assert "id=" not in msg, f"Message must not expose raw id, got: {msg!r}"
    assert "stronghold" not in msg, f"Message must not expose raw enum, got: {msg!r}"
    assert "Stronghold" in msg, f"Message must show friendly label, got: {msg!r}"
    assert "Group 3" in msg, f"Message must contain group number, got: {msg!r}"
    assert "Position 2" in msg, f"Message must contain position number, got: {msg!r}"
    # context dict must still carry machine-readable handles
    assert rule10_warnings[0].context["building_type"] == "stronghold"


@pytest.mark.asyncio
async def test_rule10_no_warning_when_disabled():
    """Rule 10 pass: disabled empty position → no rule 10 warning."""
    pos = _make_position(id=1, member_id=None, is_disabled=True, is_reserve=False)
    group = _make_group(id=1)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule10_warnings = [w for w in result.warnings if w.rule == 10]
    assert len(rule10_warnings) == 0


@pytest.mark.asyncio
async def test_rule11_member_pref_no_match():
    """Rule 11: member has preferences but none match active conditions → warning."""
    cond_a = _make_condition(id=1)
    cond_b = _make_condition(id=2)
    member = _make_member(id=1, is_active=True)
    member.post_preferences = [cond_a]  # prefers cond 1

    post = _make_post(building_id=1, active_conditions=[cond_b])  # active is cond 2 — no match

    building = _make_building(id=1, building_type=BuildingType.post, building_number=1)
    g = _make_group(id=1)
    pos = _make_position(id=1, member_id=1, member=member)
    g.positions = [pos]
    building.groups = [g]
    building.post = post

    siege = _make_siege()
    siege.buildings = [building]
    sm = _make_siege_member(member_id=1, attack_day=1, has_reserve_set=True, member=member)
    siege.siege_members = [sm]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule11_warnings = [w for w in result.warnings if w.rule == 11]
    assert len(rule11_warnings) >= 1


@pytest.mark.asyncio
async def test_rule11_no_warning_when_no_preferences():
    """Rule 11 pass: member has no preferences → skip warning."""
    cond_b = _make_condition(id=2)
    member = _make_member(id=1, is_active=True)
    member.post_preferences = []  # no preferences

    post = _make_post(building_id=1, active_conditions=[cond_b])

    building = _make_building(id=1, building_type=BuildingType.post, building_number=1)
    g = _make_group(id=1)
    pos = _make_position(id=1, member_id=1, member=member)
    g.positions = [pos]
    building.groups = [g]
    building.post = post

    siege = _make_siege()
    siege.buildings = [building]
    sm = _make_siege_member(member_id=1, attack_day=1, has_reserve_set=True, member=member)
    siege.siege_members = [sm]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule11_warnings = [w for w in result.warnings if w.rule == 11]
    assert len(rule11_warnings) == 0


@pytest.mark.asyncio
async def test_rule13_missing_attack_day_assigned_member():
    """Rule 13: assigned member with no attack_day → error."""
    member = _make_member(id=1, is_active=True)
    pos = _make_position(id=1, member_id=1, member=member)
    group = _make_group(id=1)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege(defense_scroll_count=5)
    siege.buildings = [building]
    sm = _make_siege_member(member_id=1, attack_day=None, has_reserve_set=True, member=member)
    siege.siege_members = [sm]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule13_errors = [e for e in result.errors if e.rule == 13]
    assert len(rule13_errors) >= 1


@pytest.mark.asyncio
async def test_rule13_missing_attack_day_unassigned_member():
    """Rule 13: siege member not assigned to any position but has no attack_day → error."""
    member = _make_member(id=1, is_active=True)
    # No positions reference this member — they are on the roster but unassigned
    siege = _make_siege(defense_scroll_count=5)
    siege.buildings = []
    sm = _make_siege_member(member_id=1, attack_day=None, has_reserve_set=True, member=member)
    siege.siege_members = [sm]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule13_errors = [e for e in result.errors if e.rule == 13]
    assert len(rule13_errors) >= 1


@pytest.mark.asyncio
async def test_rule13_attack_day_set():
    """Rule 13 pass: all siege members have attack_day set → no rule 13 error."""
    member = _make_member(id=1, is_active=True)
    pos = _make_position(id=1, member_id=1, member=member)
    group = _make_group(id=1)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege(defense_scroll_count=5)
    siege.buildings = [building]
    sm = _make_siege_member(member_id=1, attack_day=1, has_reserve_set=True, member=member)
    siege.siege_members = [sm]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule13_errors = [e for e in result.errors if e.rule == 13]
    assert len(rule13_errors) == 0


@pytest.mark.asyncio
async def test_rule14_fewer_than_10_day2():
    """Rule 14: 5 Day 2 attackers → warning."""
    siege = _make_siege()
    siege.buildings = []
    siege.siege_members = [
        _make_siege_member(member_id=i, attack_day=2, has_reserve_set=True) for i in range(1, 6)
    ]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule14_warnings = [w for w in result.warnings if w.rule == 14]
    assert len(rule14_warnings) == 1


@pytest.mark.asyncio
async def test_rule14_ten_or_more_day2():
    """Rule 14 pass: 10 Day 2 attackers → no warning."""
    siege = _make_siege()
    siege.buildings = []
    siege.siege_members = [
        _make_siege_member(member_id=i, attack_day=2, has_reserve_set=True) for i in range(1, 11)
    ]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule14_warnings = [w for w in result.warnings if w.rule == 14]
    assert len(rule14_warnings) == 0


@pytest.mark.asyncio
async def test_rule15_hh_no_reserve():
    """Rule 15: HH member with has_reserve_set=None → warning."""
    member = _make_member(id=1, is_active=True, role=MemberRole.heavy_hitter)
    pos = _make_position(id=1, member_id=1, member=member)
    group = _make_group(id=1)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege(defense_scroll_count=5)
    siege.buildings = [building]
    sm = _make_siege_member(member_id=1, attack_day=1, has_reserve_set=None, member=member)
    siege.siege_members = [sm]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule15_warnings = [w for w in result.warnings if w.rule == 15]
    assert len(rule15_warnings) >= 1


@pytest.mark.asyncio
async def test_rule15_advanced_no_reserve():
    """Rule 15: Advanced Role member with has_reserve_set=None → warning."""
    member = _make_member(id=1, is_active=True, role=MemberRole.advanced)
    pos = _make_position(id=1, member_id=1, member=member)
    group = _make_group(id=1)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege(defense_scroll_count=5)
    siege.buildings = [building]
    sm = _make_siege_member(member_id=1, attack_day=1, has_reserve_set=None, member=member)
    siege.siege_members = [sm]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule15_warnings = [w for w in result.warnings if w.rule == 15]
    assert len(rule15_warnings) >= 1


@pytest.mark.asyncio
async def test_rule15_hh_reserve_configured():
    """Rule 15 pass: HH member with has_reserve_set=True → no warning."""
    member = _make_member(id=1, is_active=True, role=MemberRole.heavy_hitter)
    pos = _make_position(id=1, member_id=1, member=member)
    group = _make_group(id=1)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege(defense_scroll_count=5)
    siege.buildings = [building]
    sm = _make_siege_member(member_id=1, attack_day=1, has_reserve_set=True, member=member)
    siege.siege_members = [sm]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule15_warnings = [w for w in result.warnings if w.rule == 15]
    assert len(rule15_warnings) == 0


@pytest.mark.asyncio
async def test_rule15_non_hh_no_reserve_no_warning():
    """Rule 15 pass: medium/novice member with has_reserve_set=None → no warning."""
    member = _make_member(id=1, is_active=True, role=MemberRole.medium)
    pos = _make_position(id=1, member_id=1, member=member)
    group = _make_group(id=1)
    group.positions = [pos]
    building = _make_building(id=1)
    building.groups = [group]
    siege = _make_siege(defense_scroll_count=5)
    siege.buildings = [building]
    sm = _make_siege_member(member_id=1, attack_day=1, has_reserve_set=None, member=member)
    siege.siege_members = [sm]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule15_warnings = [w for w in result.warnings if w.rule == 15]
    assert len(rule15_warnings) == 0


@pytest.mark.asyncio
async def test_rule16_post_fewer_than_3_conditions():
    """Rule 16: post with 1 active condition → warning with correct message."""
    cond = _make_condition(id=1)
    post = _make_post(building_id=1, active_conditions=[cond])

    building = _make_building(id=1, building_type=BuildingType.post, building_number=1)
    g = _make_group(id=1)
    g.positions = []
    building.groups = [g]
    building.post = post

    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule16_warnings = [w for w in result.warnings if w.rule == 16]
    assert len(rule16_warnings) >= 1
    # Message uses "Post N" — not the internal building.id primary key.
    assert rule16_warnings[0].message == (
        "Post 1 has only 1 active conditions (minimum recommended: 3)"
    )
    # context dict is unchanged — building_id is still the machine-readable handle.
    assert rule16_warnings[0].context["building_id"] == 1


@pytest.mark.asyncio
async def test_rule16_post_has_3_conditions():
    """Rule 16 pass: post with 3 active conditions → no rule 16 warning."""
    conditions = [_make_condition(id=i) for i in range(1, 4)]
    post = _make_post(building_id=1, active_conditions=conditions)

    building = _make_building(id=1, building_type=BuildingType.post, building_number=1)
    g = _make_group(id=1)
    g.positions = []
    building.groups = [g]
    building.post = post

    siege = _make_siege()
    siege.buildings = [building]

    session = _session_with_siege_and_configs(siege)
    result = await svc_validate(session, 1)
    rule16_warnings = [w for w in result.warnings if w.rule == 16]
    assert len(rule16_warnings) == 0


# ---------------------------------------------------------------------------
# Helpers for session mocking
# ---------------------------------------------------------------------------


def _default_configs() -> list[BuildingTypeConfig]:
    return [
        _make_config(BuildingType.stronghold, 1, base_group_count=4),
        _make_config(BuildingType.mana_shrine, 2, base_group_count=2),
        _make_config(BuildingType.magic_tower, 4, base_group_count=1),
        _make_config(BuildingType.defense_tower, 5, base_group_count=1),
        _make_config(BuildingType.post, 18, base_group_count=1, base_last_group_slots=1),
    ]


def _session_with_siege_and_configs(siege: Siege):
    from unittest.mock import MagicMock

    call_count = 0

    async def fake_execute(stmt):
        nonlocal call_count
        result = MagicMock()
        if call_count == 0:
            # First call: siege query
            result.scalar_one_or_none.return_value = siege
        elif call_count == 1:
            # Second call: compute_scroll_count → result.scalar() returns int
            result.scalar.return_value = 5
        else:
            # Third call: config query
            result.scalars.return_value.all.return_value = _default_configs()
        call_count += 1
        return result

    session = AsyncMock()
    session.execute = fake_execute
    return session
