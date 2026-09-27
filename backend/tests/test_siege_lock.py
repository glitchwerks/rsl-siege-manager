"""Regression coverage for the planning-only siege mutation invariant."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.enums import SiegeStatus
from app.schemas.board import PositionUpdate
from app.schemas.post import PostUpdate
from app.schemas.post_suggestions import PostSuggestionApplyRequest
from app.schemas.siege_member import SiegeMemberUpdate
from app.services.attack_day import apply_attack_day, preview_attack_day
from app.services.autofill import apply_autofill, preview_autofill
from app.services.board import bulk_update_positions, update_position
from app.services.post_suggestions import apply_post_suggestions, preview_post_suggestions
from app.services.posts import set_post_conditions, update_post
from app.services.siege_members import remove_siege_member, update_siege_member


def _locked_session(status: SiegeStatus) -> AsyncMock:
    siege = SimpleNamespace(status=status)
    result = MagicMock()
    result.scalar_one_or_none.return_value = siege
    session = AsyncMock()
    session.execute.return_value = result
    return session


async def _update_siege_member(session):
    await update_siege_member(session, 1, 2, SiegeMemberUpdate(attack_day=1))


async def _remove_siege_member(session):
    await remove_siege_member(session, 1, 2)


async def _update_position(session):
    await update_position(session, 1, 2, PositionUpdate(member_id=3))


async def _bulk_update_positions(session):
    await bulk_update_positions(session, 1, [])


async def _update_post(session):
    await update_post(session, 1, 2, PostUpdate(priority=3))


async def _set_post_conditions(session):
    await set_post_conditions(session, 1, 2, [])


async def _preview_autofill(session):
    await preview_autofill(session, 1)


async def _apply_autofill(session):
    await apply_autofill(session, 1)


async def _preview_attack_day(session):
    await preview_attack_day(session, 1)


async def _apply_attack_day(session):
    await apply_attack_day(session, 1)


async def _preview_post_suggestions(session):
    await preview_post_suggestions(session, 1)


async def _apply_post_suggestions(session):
    await apply_post_suggestions(
        session,
        1,
        PostSuggestionApplyRequest(apply_position_ids=[]),
    )


LOCKED_MUTATIONS = [
    pytest.param(_update_siege_member, id="update-siege-member"),
    pytest.param(_remove_siege_member, id="remove-siege-member"),
    pytest.param(_update_position, id="update-position"),
    pytest.param(_bulk_update_positions, id="bulk-update-positions"),
    pytest.param(_update_post, id="update-post"),
    pytest.param(_set_post_conditions, id="set-post-conditions"),
    pytest.param(_preview_autofill, id="preview-autofill"),
    pytest.param(_apply_autofill, id="apply-autofill"),
    pytest.param(_preview_attack_day, id="preview-attack-day"),
    pytest.param(_apply_attack_day, id="apply-attack-day"),
    pytest.param(_preview_post_suggestions, id="preview-post-suggestions"),
    pytest.param(_apply_post_suggestions, id="apply-post-suggestions"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [SiegeStatus.active, SiegeStatus.complete])
@pytest.mark.parametrize("mutation", LOCKED_MUTATIONS)
async def test_siege_mutations_reject_non_planning_status(mutation, status):
    session = _locked_session(status)

    with pytest.raises(HTTPException) as exc_info:
        await mutation(session)

    assert exc_info.value.status_code == 400
    assert "planning" in exc_info.value.detail.lower()
    lock_statement = session.execute.await_args_list[0].args[0]
    assert lock_statement._for_update_arg is not None
    session.commit.assert_not_awaited()
