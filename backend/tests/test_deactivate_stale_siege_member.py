"""Regression tests for issue #485 — deactivating a member must remove their
SiegeMember rows from planning sieges and must not affect active/complete ones.

Uses an in-memory SQLite database (same pattern as
tests/test_post_suggestions_integration.py) so no live DB is required.
"""

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — populate Base.metadata
from app.db.base import Base
from app.models.building import Building
from app.models.building_group import BuildingGroup
from app.models.enums import BuildingType, MemberRole, SiegeStatus
from app.models.member import Member
from app.models.position import Position
from app.models.siege import Siege
from app.models.siege_member import SiegeMember
from app.services.members import deactivate_member
from app.services.siege_members import list_siege_members

# ---------------------------------------------------------------------------
# Engine / session fixtures
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
    from sqlalchemy import select
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
# Issue #485 — regression: deactivating a member removes their SiegeMember
# rows from planning sieges.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deactivate_removes_siege_member_from_planning_siege(session):
    """Deactivating a member must remove their SiegeMember row from every
    planning siege so they no longer appear in the roster.

    This is the primary regression for issue #485.
    """
    # Seed: one member + one planning siege
    member = Member(name="Alice", role=MemberRole.advanced, is_active=True)
    session.add(member)
    await session.flush()

    siege = await _make_siege(session, SiegeStatus.planning)

    # Assign the member to the siege roster
    siege_member = SiegeMember(siege_id=siege.id, member_id=member.id)
    session.add(siege_member)
    await session.commit()

    # Pre-condition: member IS in the roster
    roster_before = await list_siege_members(session, siege.id)
    ids_before = [sm.member_id for sm in roster_before]
    assert member.id in ids_before, "Precondition: member should be in planning roster"

    # Act: deactivate the member
    await deactivate_member(session, member.id)

    # Assert (a): member is NOT in the roster after deactivation
    roster_after = await list_siege_members(session, siege.id)
    ids_after = [sm.member_id for sm in roster_after]
    assert (
        member.id not in ids_after
    ), "Deactivated member must not appear in planning siege roster (issue #485)"


@pytest.mark.asyncio
async def test_deactivate_clears_position_assignments_in_planning_siege(session):
    """Deactivating a member must clear their position assignments in planning
    sieges (existing behaviour, confirmed unbroken by this fix).
    """
    member = Member(name="Bob", role=MemberRole.advanced, is_active=True)
    session.add(member)
    await session.flush()

    siege = await _make_siege(session, SiegeStatus.planning)
    siege_member = SiegeMember(siege_id=siege.id, member_id=member.id)
    session.add(siege_member)
    await session.flush()

    pos = await _assign_member_to_position(session, siege, member)
    await session.commit()

    # Confirm position is assigned
    await session.refresh(pos)
    assert pos.member_id == member.id

    # Deactivate
    await deactivate_member(session, member.id)

    # Assert (b): position is cleared
    await session.refresh(pos)
    assert pos.member_id is None, "Position must be cleared after member deactivation"


@pytest.mark.asyncio
async def test_deactivate_does_not_remove_siege_member_from_active_siege(session):
    """Deactivating a member must NOT delete their SiegeMember row from an
    active siege — the DB row must be preserved for history.

    Note: list_siege_members (Fix 2) intentionally filters out inactive
    members, so we check the raw SiegeMember row directly to verify Fix 1
    does not touch non-planning sieges.
    """
    from sqlalchemy import select

    member = Member(name="Carol", role=MemberRole.advanced, is_active=True)
    session.add(member)
    await session.flush()

    siege = await _make_siege(session, SiegeStatus.active)
    siege_member = SiegeMember(siege_id=siege.id, member_id=member.id)
    session.add(siege_member)
    await session.commit()

    # Deactivate
    await deactivate_member(session, member.id)

    # Assert: SiegeMember row still exists in the database
    result = await session.execute(
        select(SiegeMember).where(
            SiegeMember.siege_id == siege.id,
            SiegeMember.member_id == member.id,
        )
    )
    assert (
        result.scalar_one_or_none() is not None
    ), "SiegeMember row must be preserved in active sieges after deactivation"


@pytest.mark.asyncio
async def test_deactivate_does_not_remove_siege_member_from_complete_siege(session):
    """Deactivating a member must NOT delete their SiegeMember row from a
    complete siege — historical record must be intact.

    Note: list_siege_members (Fix 2) intentionally filters out inactive
    members, so we check the raw SiegeMember row directly to verify Fix 1
    does not touch non-planning sieges.
    """
    from sqlalchemy import select

    member = Member(name="Dave", role=MemberRole.advanced, is_active=True)
    session.add(member)
    await session.flush()

    siege = await _make_siege(session, SiegeStatus.complete)
    siege_member = SiegeMember(siege_id=siege.id, member_id=member.id)
    session.add(siege_member)
    await session.commit()

    # Deactivate
    await deactivate_member(session, member.id)

    # Assert: SiegeMember row still exists in the database
    result = await session.execute(
        select(SiegeMember).where(
            SiegeMember.siege_id == siege.id,
            SiegeMember.member_id == member.id,
        )
    )
    assert (
        result.scalar_one_or_none() is not None
    ), "SiegeMember row must be preserved in complete sieges after deactivation"


@pytest.mark.asyncio
async def test_list_siege_members_excludes_inactive_members(session):
    """list_siege_members must filter out inactive members even when a stale
    SiegeMember row exists (defense-in-depth for Fix 2).
    """
    active = Member(name="Eve", role=MemberRole.advanced, is_active=True)
    inactive = Member(name="Frank", role=MemberRole.advanced, is_active=False)
    session.add_all([active, inactive])
    await session.flush()

    siege = await _make_siege(session, SiegeStatus.planning)
    session.add(SiegeMember(siege_id=siege.id, member_id=active.id))
    # Deliberately insert a stale row for the inactive member (simulates bug state)
    session.add(SiegeMember(siege_id=siege.id, member_id=inactive.id))
    await session.commit()

    roster = await list_siege_members(session, siege.id)
    ids = [sm.member_id for sm in roster]

    assert active.id in ids, "Active member must appear in roster"
    assert (
        inactive.id not in ids
    ), "Inactive member must be excluded by list_siege_members (defense-in-depth)"
