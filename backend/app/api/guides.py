"""Private guide delivery. Content is never included in the public frontend bundle."""

from fastapi import APIRouter, Depends, HTTPException, Response

from app.dependencies.auth import AuthenticatedUser, get_current_user
from app.services.guide_access import can_view_mom_bot_guide
from app.services.mom_bot_guide import MOM_BOT_GUIDE

router = APIRouter(prefix="/guides", tags=["guides"])


@router.get("/mom-bot")
async def mom_bot_guide(
    response: Response,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Serve instructions to verified guild officers or opted-in developer previews."""
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie, Authorization"
    if not await can_view_mom_bot_guide(user):
        raise HTTPException(
            status_code=403,
            detail="This guide is not available for your account.",
            headers={"Cache-Control": "private, no-store"},
        )
    return MOM_BOT_GUIDE
