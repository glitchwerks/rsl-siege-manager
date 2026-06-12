"""Tests for issue #486 — remove a member from a single planning siege.

Uses an in-memory SQLite database (same pattern as
tests/test_deactivate_stale_siege_member.py) so no live DB is required.

Covers:
- Happy path: roster row deleted, that siege's positions cleared.
- 404 when the SiegeMember row doesn't exist (member not in siege).
- 400 when the siege is not in planning status (active or complete).
- Isolation: OTHER sieges' rosters/positions for the same member are untouched.
- P2b: matched_condition_id cleared alongside member_id when unassigning.
"""

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — populate Base.metadata
from app.db.base import Base
from app.models.building import Building
from app.models.building_group import BuildingGroup
from app.models.enums import BuildingType, MemberRole, SiegeStatus
from app.models.member import Member
from app.models.position import Position
from app.models.post_condition import PostCondition
from app.models.siege import Siege
from app.models.siege_member import SiegeMember
from app.services.siege_members import remove_siege_member

# ---------------------------------------------------------------------------
# Engine / session fixtures (same pattern as test_deactivate_stale_siege_member)
# ---------------------------------------------------------------------------


def _enable_sqlite_fk(dbapi_conn, _connection_record):
    """Enable SQLite foreign-key enforcement."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


@pytest.fixture
async def engine():
    """Async SQLite in-memory engine with the full schema."""
    _engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    event.listen(_engine.sync_engine, "connect", _enable_sqlite_fk)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield _engine
    await _engine.dispose()


@pytest.fixture
async def session(engine):
    """Single AsyncSession per test, expire_on_commit=False for easy inspection."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _make_siege(session: AsyncSession, status: SiegeStatus) -> Siege:
    """Seed a minimal siege with one building, group and empty position."""
    siege = Siege(status=status, defense_scroll_count=5)
    session.add(siege)
    await session.flush()

    bld = Building(
        siege_id=siege.id,
        building_type=BuildingType.stronghold,
        building_number=1,
        level=1,
        is_broken=False,
    )
    session.add(bld)
    await session.flush()

    grp = BuildingGroup(building_id=bld.id, group_number=1, slot_count=3)
    session.add(grp)
    await session.flush()

    pos = Position(building_group_id=grp.id, position_number=1)
    session.add(pos)
    await session.flush()

    return siege


async def _assign_member_to_position(
    session: AsyncSession, siege: Siege, member: Member
) -> Position:
    """Assign *member* to the first position in *siege* and return the position."""
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(Siege)
        .where(Siege.id == siege.id)
        .options(
            selectinload(Siege.buildings)
            .selectinload(Building.groups)
            .selectinload(BuildingGroup.positions)
        )
    )
    full_siege = result.scalar_one()
    pos = full_siege.buildings[0].groups[0].positions[0]
    pos.member_id = member.id
    await session.flush()
    return pos


# ---------------------------------------------------------------------------
# Issue #486 — remove_siege_member tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_siege_member_deletes_roster_row(session):
    """Happy path: remove_siege_member must delete the SiegeMember row for the
    given (siege_id, member_id) pair in a planning siege.
    """
    member = Member(name="Alice", role=MemberRole.advanced, is_active=True)
    session.add(member)
    await session.flush()

    siege = await _make_siege(session, SiegeStatus.planning)
    session.add(SiegeMember(siege_id=siege.id, member_id=member.id))
    await session.commit()

    # Pre-condition: row exists
    result_before = await session.execute(
        select(SiegeMember).where(
            SiegeMember.siege_id == siege.id,
            SiegeMember.member_id == member.id,
        )
    )
    assert result_before.scalar_one_or_none() is not None, "Precondition: row must exist"

    # Act
    await remove_siege_member(session, siege.id, member.id)

    # Assert: row is gone
    result_after = await session.execute(
        select(SiegeMember).where(
            SiegeMember.siege_id == siege.id,
            SiegeMember.member_id == member.id,
        )
    )
    assert (
        result_after.scalar_one_or_none() is None
    ), "SiegeMember row must be deleted after remove_siege_member (#486)"


@pytest.mark.asyncio
async def test_remove_siege_member_clears_positions_in_that_siege(session):
    """Happy path: remove_siege_member must clear Position.member_id for the
    member's positions within THIS siege only.
    """
    member = Member(name="Bob", role=MemberRole.advanced, is_active=True)
    session.add(member)
    await session.flush()

    siege = await _make_siege(session, SiegeStatus.planning)
    session.add(SiegeMember(siege_id=siege.id, member_id=member.id))
    await session.flush()

    pos = await _assign_member_to_position(session, siege, member)
    await session.commit()

    # Confirm position is assigned
    await session.refresh(pos)
    assert pos.member_id == member.id, "Precondition: position must be assigned"

    # Act
    await remove_siege_member(session, siege.id, member.id)

    # Assert: position is cleared
    await session.refresh(pos)
    assert (
        pos.member_id is None
    ), "Position.member_id must be cleared when member removed from siege (#486)"


@pytest.mark.asyncio
async def test_remove_siege_member_raises_404_when_not_in_siege(session):
    """remove_siege_member must raise HTTP 404 when the member is not in the siege."""
    from fastapi import HTTPException

    member = Member(name="Carol", role=MemberRole.advanced, is_active=True)
    session.add(member)
    await session.flush()

    siege = await _make_siege(session, SiegeStatus.planning)
    await session.commit()

    # Member is NOT in the siege — no SiegeMember row
    with pytest.raises(HTTPException) as exc_info:
        await remove_siege_member(session, siege.id, member.id)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_remove_siege_member_raises_400_when_siege_is_active(session):
    """remove_siege_member must reject removal when the siege is active (not planning)."""
    from fastapi import HTTPException

    member = Member(name="Dave", role=MemberRole.advanced, is_active=True)
    session.add(member)
    await session.flush()

    siege = await _make_siege(session, SiegeStatus.active)
    session.add(SiegeMember(siege_id=siege.id, member_id=member.id))
    await session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await remove_siege_member(session, siege.id, member.id)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_remove_siege_member_raises_400_when_siege_is_complete(session):
    """remove_siege_member must reject removal when the siege is complete."""
    from fastapi import HTTPException

    member = Member(name="Eve", role=MemberRole.advanced, is_active=True)
    session.add(member)
    await session.flush()

    siege = await _make_siege(session, SiegeStatus.complete)
    session.add(SiegeMember(siege_id=siege.id, member_id=member.id))
    await session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await remove_siege_member(session, siege.id, member.id)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_remove_siege_member_does_not_affect_other_sieges(session):
    """remove_siege_member must NOT touch the member's roster rows or positions
    in OTHER sieges (single-siege scoping, as required by #486).
    """
    member = Member(name="Frank", role=MemberRole.advanced, is_active=True)
    session.add(member)
    await session.flush()

    # Two planning sieges: we remove from siege_a only
    siege_a = await _make_siege(session, SiegeStatus.planning)
    siege_b = await _make_siege(session, SiegeStatus.planning)

    session.add(SiegeMember(siege_id=siege_a.id, member_id=member.id))
    session.add(SiegeMember(siege_id=siege_b.id, member_id=member.id))
    await session.flush()

    pos_b = await _assign_member_to_position(session, siege_b, member)
    await session.commit()

    # Confirm siege_b position is assigned
    await session.refresh(pos_b)
    assert pos_b.member_id == member.id, "Precondition: siege_b position assigned"

    # Act: remove from siege_a only
    await remove_siege_member(session, siege_a.id, member.id)

    # Assert: siege_b SiegeMember row still exists
    result_b = await session.execute(
        select(SiegeMember).where(
            SiegeMember.siege_id == siege_b.id,
            SiegeMember.member_id == member.id,
        )
    )
    assert (
        result_b.scalar_one_or_none() is not None
    ), "SiegeMember row in OTHER siege must be preserved (#486 single-siege scope)"

    # Assert: siege_b position still assigned
    await session.refresh(pos_b)
    assert (
        pos_b.member_id == member.id
    ), "Position in OTHER siege must not be cleared (#486 single-siege scope)"


# ---------------------------------------------------------------------------
# P2b — matched_condition_id must be cleared alongside member_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_siege_member_clears_matched_condition_id(session):
    """P2b: remove_siege_member must clear matched_condition_id as well as
    member_id on the member's positions within the siege.

    When a position has both member_id and matched_condition_id set, removing
    the member must null both fields — leaving matched_condition_id stale
    would break validation logic that relies on it only being set when a
    member is assigned.
    """
    member = Member(name="Grace", role=MemberRole.advanced, is_active=True)
    session.add(member)
    await session.flush()

    siege = await _make_siege(session, SiegeStatus.planning)
    session.add(SiegeMember(siege_id=siege.id, member_id=member.id))
    await session.flush()

    # Assign the member to the position and set a matched_condition_id.
    pos = await _assign_member_to_position(session, siege, member)

    # We need a real PostCondition row for the FK; seed a minimal one.
    condition = PostCondition(
        description="Test condition — P2b",
        stronghold_level=1,
        condition_type="role",
    )
    session.add(condition)
    await session.flush()

    pos.matched_condition_id = condition.id
    await session.commit()

    # Precondition: both fields are set.
    await session.refresh(pos)
    assert pos.member_id == member.id, "Precondition: member_id must be set"
    assert (
        pos.matched_condition_id == condition.id
    ), "Precondition: matched_condition_id must be set"

    # Act
    await remove_siege_member(session, siege.id, member.id)

    # Assert: both fields cleared.
    await session.refresh(pos)
    assert pos.member_id is None, "member_id must be cleared when member removed from siege"
    assert (
        pos.matched_condition_id is None
    ), "matched_condition_id must also be cleared when member removed (P2b)"
