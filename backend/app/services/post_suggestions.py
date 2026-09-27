"""Service implementing the Suggest Post Assignments feature.

Public API:
    preview_post_suggestions(session, siege_id) -> PostSuggestionPreviewResult
    apply_post_suggestions(session, siege_id, data) -> PostSuggestionApplyResult

Algorithm overview
------------------
preview_post_suggestions runs a priority-ordered greedy assignment:

1. Load siege with posts, buildings, groups, positions, and siege members
   with their post_preferences via eager-load (selectinload).
2. Guard against completed siege (400).
3. For each post, identify the target position (the one position in the
   post's building's single group).
4. Skip posts whose target position is is_reserve or is_disabled.
5. Build candidate set: active siege members whose post_preferences
   intersect the post's active_conditions AND who still have at least
   one fresh (not yet used this run) matching condition.  This prevents
   the same (member, condition) pair from appearing on multiple posts
   (issue #381).
6. Sort posts by priority desc, building_number asc.
7. Greedy loop with a lexicographic scoring tuple:
       (duplicate_penalty, assignment_count, member_name)
   where duplicate_penalty is retained for structural symmetry but is
   always 0 (the candidate filter guarantees freshness).
8. Persist preview JSON + 30-minute TTL in siege.post_suggest_preview.
9. Return the full assignment list.

apply_post_suggestions validates the stored preview and writes inline using
SELECT ... FOR UPDATE to fence the TOCTOU window between revalidation and
the commit.  Any stale state is surfaced as a structured 409 with reasons
from the StaleEntry schema.

Divergences from bulk_update_positions
---------------------------------------
See plan docs/superpowers/plans/2026-05-09-post-suggestions.md §
"Divergences from bulk_update_positions" for the full rationale.  Short
version: the apply path writes inline (no delegation) to keep the FOR
UPDATE fence and the commit inside the same transaction.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.building import Building
from app.models.building_group import BuildingGroup
from app.models.member import Member
from app.models.position import Position
from app.models.post import Post
from app.models.siege import Siege
from app.models.siege_member import SiegeMember
from app.schemas.post_suggestions import (
    PostSuggestionApplyRequest,
    PostSuggestionApplyResult,
    PostSuggestionEntry,
    PostSuggestionPreviewResult,
    StaleEntry,
)
from app.services.siege_lock import lock_planning_siege

PREVIEW_TTL_MINUTES = 30


def _now_utc() -> datetime:
    """Return the current UTC datetime (naive, matching DB TIMESTAMP columns)."""
    return datetime.now(UTC).replace(tzinfo=None)


async def preview_post_suggestions(
    session: AsyncSession,
    siege_id: int,
) -> PostSuggestionPreviewResult:
    """Generate a greedy post assignment suggestion and persist it as a preview.

    The algorithm processes posts in priority-descending, building_number-
    ascending order.  Each post gets the member whose lexicographic score
    tuple (duplicate_penalty, assignment_count, member_name) is lowest.

    Args:
        session: SQLAlchemy async session.
        siege_id: Primary key of the Siege row to process.

    Returns:
        A PostSuggestionPreviewResult with one entry per post (including
        posts that were skipped) and an ISO 8601 expiry string.

    Raises:
        HTTPException(404): Siege not found.
        HTTPException(400): Siege is complete — cannot generate suggestions.
    """
    siege = await lock_planning_siege(
        session,
        siege_id,
        selectinload(Siege.posts)
        .selectinload(Post.building)
        .selectinload(Building.groups)
        .selectinload(BuildingGroup.positions)
        .selectinload(Position.matched_condition),
        selectinload(Siege.posts).selectinload(Post.active_conditions),
        selectinload(Siege.siege_members)
        .selectinload(SiegeMember.member)
        .selectinload(Member.post_preferences),
    )

    # ------------------------------------------------------------------
    # Query existing assignment counts per member (excludes disabled,
    # reserve, and broken-building positions — only "live" assignments).
    # ------------------------------------------------------------------
    counts_result = await session.execute(
        select(Position.member_id, func.count())
        .join(BuildingGroup, Position.building_group_id == BuildingGroup.id)
        .join(Building, BuildingGroup.building_id == Building.id)
        .where(Building.siege_id == siege_id)
        .where(Position.member_id.is_not(None))
        .where(Position.is_disabled == False)  # noqa: E712
        .where(Position.is_reserve == False)  # noqa: E712
        .where(Building.is_broken == False)  # noqa: E712
        .group_by(Position.member_id)
    )
    member_assignment_counts: dict[int, int] = {row[0]: row[1] for row in counts_result.all()}

    # ------------------------------------------------------------------
    # Build active-member list and their preference sets.
    # ------------------------------------------------------------------
    active_members: list[Member] = [
        sm.member for sm in siege.siege_members if sm.member is not None and sm.member.is_active
    ]
    member_preference_ids: dict[int, set[int]] = {
        m.id: {c.id for c in m.post_preferences} for m in active_members
    }
    # ------------------------------------------------------------------
    # Sort posts: priority desc, building_number asc.
    # ------------------------------------------------------------------
    sorted_posts = sorted(
        siege.posts,
        key=lambda p: (-p.priority, p.building.building_number),
    )

    # ------------------------------------------------------------------
    # Greedy assignment loop.
    # ------------------------------------------------------------------
    used_member_conditions: dict[int, set[int]] = defaultdict(set)
    assignments: list[PostSuggestionEntry] = []

    for post in sorted_posts:
        building = post.building
        if building.is_broken:
            # Broken buildings are skipped entirely — no row emitted.
            continue

        # The post has exactly 1 group with 1 position (plan §2 algorithm step 4).
        position = _get_target_position(building)
        if position is None:
            # No positions exist at all — treat as disabled.
            entry = _null_entry(post, position_id=0, skip_reason="disabled")
            assignments.append(entry)
            continue

        current_member_id = position.member_id
        current_member_name = position.member.name if position.member is not None else None
        current_condition_id = position.matched_condition_id
        # `getattr` shim so SimpleNamespace test fixtures (which don't
        # include the relationship) still work alongside real ORM objects
        # where `matched_condition` is loaded via selectinload above.
        matched_condition = getattr(position, "matched_condition", None)
        current_condition_description = (
            matched_condition.description if matched_condition is not None else None
        )

        if position.is_reserve:
            assignments.append(
                _null_entry(
                    post,
                    position_id=position.id,
                    skip_reason="reserve",
                    current_member_id=current_member_id,
                    current_member_name=current_member_name,
                    current_condition_id=current_condition_id,
                    current_condition_description=current_condition_description,
                )
            )
            continue

        if position.is_disabled:
            assignments.append(
                _null_entry(
                    post,
                    position_id=position.id,
                    skip_reason="disabled",
                    current_member_id=current_member_id,
                    current_member_name=current_member_name,
                    current_condition_id=current_condition_id,
                    current_condition_description=current_condition_description,
                )
            )
            continue

        if not post.active_conditions:
            assignments.append(
                _null_entry(
                    post,
                    position_id=position.id,
                    skip_reason="no_conditions",
                    current_member_id=current_member_id,
                    current_member_name=current_member_name,
                    current_condition_id=current_condition_id,
                    current_condition_description=current_condition_description,
                )
            )
            continue

        post_condition_ids: set[int] = {c.id for c in post.active_conditions}

        # Build candidate list: members whose preferences intersect post conditions
        # AND have at least one FRESH (not yet used by them this run) matching
        # condition.  Members who have already exhausted their matching conditions
        # on prior posts in this run are excluded — preventing the same
        # (member, condition) pair from being suggested twice.  See issue #381.
        candidates = [
            m
            for m in active_members
            if (member_preference_ids[m.id] & post_condition_ids) - used_member_conditions[m.id]
        ]

        if not candidates:
            assignments.append(
                _null_entry(
                    post,
                    position_id=position.id,
                    skip_reason="no_match",
                    current_member_id=current_member_id,
                    current_member_name=current_member_name,
                    current_condition_id=current_condition_id,
                    current_condition_description=current_condition_description,
                )
            )
            continue

        # Score each candidate and pick the best.
        # Tuple: (dup_penalty, not_current, assignment_count, member_name)
        # not_current=0 if the candidate is the current occupant (prefer them),
        # 1 otherwise.  This makes the algorithm idempotent: re-running after
        # apply always retains the incumbent when they are equally qualified,
        # preventing the bistable flip-flop described in issue #360.
        # Among non-incumbents, assignment_count then member_name (ASC)
        # break ties deterministically.
        #
        # dup_penalty is defensively retained but structurally unreachable: the
        # candidate filter above guarantees every candidate has at least one
        # fresh matching condition, so `fresh` is always non-empty and
        # dup_penalty is always 0.  See issue #381.
        def _score(m) -> tuple:
            matching = member_preference_ids[m.id] & post_condition_ids
            already_used = used_member_conditions[m.id]
            fresh = matching - already_used
            dup_penalty = 0 if fresh else 1
            not_current = 0 if m.id == current_member_id else 1
            return (dup_penalty, not_current, member_assignment_counts.get(m.id, 0), m.name)

        best = min(candidates, key=_score)

        # Pick the matched condition: always fresh (members reaching this point are
        # guaranteed by the candidate filter above to have at least one fresh
        # matching condition).  The fallback to `min(matching_ids)` that previously
        # existed here is removed because it produced duplicate (member, condition)
        # pairs across posts (issue #381).
        matching_ids = member_preference_ids[best.id] & post_condition_ids
        already_used = used_member_conditions[best.id]
        fresh_ids = matching_ids - already_used
        chosen_condition_id = min(fresh_ids)

        # Look up the condition description from the post's active_conditions list.
        chosen_condition_description: str | None = None
        for cond in post.active_conditions:
            if cond.id == chosen_condition_id:
                chosen_condition_description = cond.description
                break

        # Update tracking state.
        used_member_conditions[best.id].add(chosen_condition_id)
        member_assignment_counts[best.id] = member_assignment_counts.get(best.id, 0) + 1

        matches_current = (
            current_member_id == best.id and current_condition_id == chosen_condition_id
        )

        assignments.append(
            PostSuggestionEntry(
                post_id=post.id,
                building_number=building.building_number,
                priority=post.priority,
                position_id=position.id,
                suggested_member_id=best.id,
                suggested_member_name=best.name,
                suggested_condition_id=chosen_condition_id,
                suggested_condition_description=chosen_condition_description,
                current_member_id=current_member_id,
                current_member_name=current_member_name,
                current_condition_id=current_condition_id,
                current_condition_description=current_condition_description,
                matches_current=matches_current,
                skip_reason=None,
            )
        )

    # ------------------------------------------------------------------
    # Persist preview + TTL.
    # ------------------------------------------------------------------
    expires_at = _now_utc() + timedelta(minutes=PREVIEW_TTL_MINUTES)
    siege.post_suggest_preview = {"assignments": [a.model_dump() for a in assignments]}
    siege.post_suggest_preview_expires_at = expires_at
    await session.commit()

    return PostSuggestionPreviewResult(
        assignments=assignments,
        expires_at=expires_at.isoformat(),
    )


async def apply_post_suggestions(
    session: AsyncSession,
    siege_id: int,
    data: PostSuggestionApplyRequest,
) -> PostSuggestionApplyResult:
    """Apply a subset of a stored post-suggestion preview atomically.

    The whole sequence runs in a single SQLAlchemy transaction.  Position
    rows are locked with SELECT ... FOR UPDATE to fence the TOCTOU window
    between revalidation and the inline writes.

    See plan docs/superpowers/plans/2026-05-09-post-suggestions.md §
    "apply_post_suggestions — with apply-time revalidation (race-fenced)"
    for the full contract including divergences from bulk_update_positions.

    Args:
        session: SQLAlchemy async session (transaction not yet begun).
        siege_id: Primary key of the Siege row.
        data: Apply request carrying the caller-filtered position id set.

    Returns:
        PostSuggestionApplyResult with the count of positions updated.

    Raises:
        HTTPException(404): Siege not found.
        HTTPException(400): Siege is complete.
        HTTPException(409): Preview missing/expired OR stale state detected.
            When stale, the detail is {"stale_entries": [...]}.
    """
    siege = await lock_planning_siege(session, siege_id)

    if siege.post_suggest_preview is None or siege.post_suggest_preview_expires_at is None:
        raise HTTPException(
            status_code=409,
            detail="No valid preview to apply, generate a new one",
        )

    # Normalise expires_at to timezone-naive for comparison (DB stores naive UTC).
    expires_at = siege.post_suggest_preview_expires_at
    if hasattr(expires_at, "tzinfo") and expires_at.tzinfo is not None:
        expires_at = expires_at.replace(tzinfo=None)

    if _now_utc() > expires_at:
        raise HTTPException(
            status_code=409,
            detail="No valid preview to apply, generate a new one",
        )

    raw_assignments: list[dict] = siege.post_suggest_preview.get("assignments", [])

    # ------------------------------------------------------------------
    # Filter to caller-requested positions with a non-null member.
    # ------------------------------------------------------------------
    apply_ids = set(data.apply_position_ids)
    target_entries = [
        entry
        for entry in raw_assignments
        if entry.get("position_id") in apply_ids and entry.get("suggested_member_id") is not None
    ]

    if not target_entries:
        # Nothing to write — clear preview and return noop success.
        siege.post_suggest_preview = None
        siege.post_suggest_preview_expires_at = None
        await session.commit()
        return PostSuggestionApplyResult(applied_count=0)

    target_position_ids = [e["position_id"] for e in target_entries]
    target_member_ids = list({e["suggested_member_id"] for e in target_entries})

    # ------------------------------------------------------------------
    # Re-fetch and lock target positions (SELECT ... FOR UPDATE).
    # We also pull Building.is_broken in the same query to detect
    # broken-building state without a second round-trip.
    #
    # NOTE: _validate_position_state() from bulk_update_positions is NOT
    # called here.  The inline writes always set is_reserve=False,
    # is_disabled=False so the validator's invariants are vacuously
    # satisfied.  If future work expands the writeset (e.g. "suggest
    # reserve"), this comment is the mandatory reminder to re-introduce
    # the validator call.  See plan § "Divergences" item 1.
    # ------------------------------------------------------------------
    positions_result = await session.execute(
        select(Position, Building.is_broken.label("building_is_broken"))
        .join(BuildingGroup, Position.building_group_id == BuildingGroup.id)
        .join(Building, BuildingGroup.building_id == Building.id)
        .where(Position.id.in_(target_position_ids))
        .with_for_update()
    )
    rows = positions_result.all()
    locked_positions: dict[int, Position] = {row[0].id: row[0] for row in rows}
    position_building_broken: dict[int, bool] = {row[0].id: bool(row[1]) for row in rows}

    # ------------------------------------------------------------------
    # Re-validate member activity.
    # ------------------------------------------------------------------
    members_result = await session.execute(select(Member).where(Member.id.in_(target_member_ids)))
    members_by_id: dict[int, Member] = {m.id: m for m in members_result.scalars().all()}

    # ------------------------------------------------------------------
    # Detect stale state — collect all violations before raising.
    # ------------------------------------------------------------------
    stale_entries: list[StaleEntry] = []

    for entry in target_entries:
        pos_id = entry["position_id"]
        member_id = entry["suggested_member_id"]
        preview_member_id = entry.get("current_member_id")

        pos = locked_positions.get(pos_id)
        if pos is None:
            stale_entries.append(StaleEntry(position_id=pos_id, reason="position_missing"))
            continue

        # Check building broken (functionally disabled).
        if position_building_broken.get(pos_id, False):
            stale_entries.append(StaleEntry(position_id=pos_id, reason="position_disabled"))
            continue

        if pos.is_disabled:
            stale_entries.append(StaleEntry(position_id=pos_id, reason="position_disabled"))
            continue

        if pos.is_reserve:
            stale_entries.append(StaleEntry(position_id=pos_id, reason="position_reserve"))
            continue

        # Charge #15: another planner wrote a different member since preview.
        if pos.member_id != preview_member_id:
            stale_entries.append(StaleEntry(position_id=pos_id, reason="member_changed"))
            continue

        # Check member still active.
        member = members_by_id.get(member_id)
        if member is None or not member.is_active:
            stale_entries.append(StaleEntry(position_id=pos_id, reason="member_inactive"))

    if stale_entries:
        # Transaction rolls back implicitly when we raise; no commit called.
        raise HTTPException(
            status_code=409,
            detail={"stale_entries": [e.model_dump() for e in stale_entries]},
        )

    # ------------------------------------------------------------------
    # Write inline — still inside the locked transaction.
    # NOTE: session.refresh(pos) is not called post-write because the apply
    # response only carries applied_count.  Callers that need fresh ORM
    # state must re-query.  See plan § "Divergences" item 2.
    # ------------------------------------------------------------------
    applied_count = 0
    for entry in target_entries:
        pos = locked_positions[entry["position_id"]]
        pos.member_id = entry["suggested_member_id"]
        pos.is_reserve = False
        pos.is_disabled = False
        pos.matched_condition_id = entry.get("suggested_condition_id")
        applied_count += 1

    # Clear preview.
    siege.post_suggest_preview = None
    siege.post_suggest_preview_expires_at = None

    # Single commit — releases FOR UPDATE locks atomically with the writes.
    await session.commit()

    return PostSuggestionApplyResult(applied_count=applied_count)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _get_target_position(building) -> object | None:
    """Return the single target position from a post building, or None.

    Posts have exactly 1 building → 1 group → 1 position in this codebase.
    If the structure is absent, return None.

    Args:
        building: A Building ORM object (or SimpleNamespace in tests).

    Returns:
        The first Position in the first group, or None if unavailable.
    """
    if not building.groups:
        return None
    group = building.groups[0]
    if not group.positions:
        return None
    return group.positions[0]


def _null_entry(
    post,
    position_id: int,
    skip_reason: str,
    current_member_id: int | None = None,
    current_member_name: str | None = None,
    current_condition_id: int | None = None,
    current_condition_description: str | None = None,
) -> PostSuggestionEntry:
    """Build a PostSuggestionEntry with no suggestion (skipped post).

    Args:
        post: Post ORM object (or SimpleNamespace in tests).
        position_id: The target position id (0 if no position exists).
        skip_reason: One of "no_conditions", "no_match", "reserve", "disabled".
        current_member_id: Existing member assignment, if any.
        current_member_name: Existing member name, if any.
        current_condition_id: Existing matched condition, if any.

    Returns:
        A PostSuggestionEntry with suggested fields all None and
        matches_current=False.
    """
    return PostSuggestionEntry(
        post_id=post.id,
        building_number=post.building.building_number,
        priority=post.priority,
        position_id=position_id,
        suggested_member_id=None,
        suggested_member_name=None,
        suggested_condition_id=None,
        suggested_condition_description=None,
        current_member_id=current_member_id,
        current_member_name=current_member_name,
        current_condition_id=current_condition_id,
        current_condition_description=current_condition_description,
        matches_current=False,
        skip_reason=skip_reason,
    )
