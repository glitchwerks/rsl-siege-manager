from typing import Protocol

from fastapi import HTTPException

from app.models.enums import SiegeStatus


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
