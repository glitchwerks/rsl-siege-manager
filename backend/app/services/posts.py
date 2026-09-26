from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.building import Building
from app.models.post import Post
from app.models.post_active_condition import post_active_condition
from app.models.post_condition import PostCondition
from app.models.siege import Siege
from app.schemas.post import PostUpdate
from app.services.siege_lock import require_planning_siege


async def _get_siege_or_404(session: AsyncSession, siege_id: int) -> Siege:
    result = await session.execute(select(Siege).where(Siege.id == siege_id))
    siege = result.scalar_one_or_none()
    if siege is None:
        raise HTTPException(status_code=404, detail="Siege not found")
    return siege


async def _get_post_for_siege_or_404(session: AsyncSession, siege_id: int, post_id: int) -> Post:
    result = await session.execute(
        select(Post)
        .where(Post.id == post_id)
        .where(Post.siege_id == siege_id)
        .options(selectinload(Post.active_conditions), selectinload(Post.building))
    )
    post = result.scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


async def list_posts(session: AsyncSession, siege_id: int) -> list[Post]:
    """Return all Post records for a siege with active_conditions loaded.

    Raises:
        404 if siege not found.
    """
    await _get_siege_or_404(session, siege_id)

    result = await session.execute(
        select(Post)
        .join(Post.building)
        .where(Post.siege_id == siege_id)
        .order_by(Building.building_number)
        .options(selectinload(Post.active_conditions), selectinload(Post.building))
    )
    return list(result.scalars().all())


async def update_post(session: AsyncSession, siege_id: int, post_id: int, data: PostUpdate) -> Post:
    """Update a post's priority and/or description.

    Raises:
        404 if post not found or doesn't belong to siege.
        400 if siege is not in planning.
    """
    siege = await _get_siege_or_404(session, siege_id)
    require_planning_siege(siege)

    post = await _get_post_for_siege_or_404(session, siege_id, post_id)

    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(post, field, value)

    await session.commit()
    await session.refresh(post)
    return post


async def set_post_conditions(
    session: AsyncSession, siege_id: int, post_id: int, condition_ids: list[int]
) -> Post:
    """Replace all active conditions on a post.

    Raises:
        404 if post not found.
        400 if siege is not in planning.
        400 if more than 3 condition IDs provided.
        404 if any condition_id does not exist in PostCondition table.
    """
    if len(condition_ids) > 3:
        raise HTTPException(
            status_code=400,
            detail="A post can have at most 3 active conditions",
        )

    siege = await _get_siege_or_404(session, siege_id)
    require_planning_siege(siege)

    post = await _get_post_for_siege_or_404(session, siege_id, post_id)

    # Validate all condition IDs exist
    if condition_ids:
        conditions_result = await session.execute(
            select(PostCondition).where(PostCondition.id.in_(condition_ids))
        )
        found_conditions = conditions_result.scalars().all()
        found_ids = {c.id for c in found_conditions}
        missing = set(condition_ids) - found_ids
        if missing:
            raise HTTPException(
                status_code=404,
                detail=f"PostCondition IDs not found: {sorted(missing)}",
            )

    # Replace all PostActiveCondition rows for this post
    await session.execute(
        delete(post_active_condition).where(post_active_condition.c.post_id == post.id)
    )
    for cond_id in condition_ids:
        await session.execute(
            post_active_condition.insert().values(post_id=post.id, post_condition_id=cond_id)
        )

    await session.commit()

    # Reload post with fresh active_conditions
    result = await session.execute(
        select(Post)
        .where(Post.id == post_id)
        .options(selectinload(Post.active_conditions), selectinload(Post.building))
    )
    return result.scalar_one()
