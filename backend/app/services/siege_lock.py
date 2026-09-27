from typing import Protocol

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import SiegeStatus
from app.models.siege import Siege


class SiegeWithStatus(Protocol):
    status: SiegeStatus


def require_planning_siege(
    siege: SiegeWithStatus,
    *,
    detail: str = "Siege is locked — changes are only allowed during planning",
) -> None:
    """Reject a siege-scoped mutation after the planning phase."""
    if siege.status != SiegeStatus.planning:
        raise HTTPException(status_code=400, detail=detail)


async def lock_planning_siege(
    session: AsyncSession,
    siege_id: int,
    *options,
    detail: str = "Siege is locked — changes are only allowed during planning",
) -> Siege:
    """Lock a siege row and verify that it is still editable.

    Holding this row lock until commit serializes planning mutations against
    activation. Without it, a mutation can read ``planning``, activation can
    validate and commit, and the stale mutation can then commit into the active
    siege.
    """
    statement = select(Siege).where(Siege.id == siege_id)
    if options:
        statement = statement.options(*options)
    result = await session.execute(statement.with_for_update())
    siege = result.scalar_one_or_none()
    if siege is None:
        raise HTTPException(status_code=404, detail="Siege not found")
    require_planning_siege(siege, detail=detail)
    return siege


async def lock_all_planning_sieges(session: AsyncSession) -> list[Siege]:
    """Lock every planning siege in a stable order for global roster writes."""
    result = await session.execute(
        select(Siege)
        .where(Siege.status == SiegeStatus.planning)
        .order_by(Siege.id)
        .with_for_update()
    )
    return list(result.scalars().all())
