"""Service-authenticated status for the portable maintenance drain gate."""

import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from app.config import settings
from app.maintenance import gate


router = APIRouter(tags=["maintenance"])


@router.get("/maintenance/status")
def maintenance_status(authorization: Annotated[str | None, Header()] = None) -> dict:
    """Return pause and in-flight count without exposing runtime inputs."""
    prefix = "Bearer "
    if (not settings.bot_service_token or authorization is None
            or not authorization.startswith(prefix)
            or not secrets.compare_digest(authorization[len(prefix):], settings.bot_service_token)):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return gate.snapshot()
