"""Tests for the member_is_active field on SiegeMemberResponse (issue #487).

Verifies that the denormalized ``member_is_active`` boolean is correctly
populated from the related Member's ``is_active`` attribute, both through
the ORM from-attributes path (the primary read path) and directly via
schema instantiation with a mock object.
"""

from types import SimpleNamespace

from app.schemas.siege_member import SiegeMemberResponse

# ---------------------------------------------------------------------------
# Helper: build a minimal SimpleNamespace that quacks like a SiegeMember ORM
# row with an eagerly loaded .member relation.
# ---------------------------------------------------------------------------


def _make_orm_row(*, is_active: bool) -> SimpleNamespace:
    """Return a fake ORM SiegeMember row with a nested .member relation.

    Args:
        is_active: The ``is_active`` flag to assign to the fake member.

    Returns:
        A ``SimpleNamespace`` that satisfies the ``model_validator`` in
        ``SiegeMemberResponse`` — specifically the ``hasattr(data, "member")``
        branch that reads ``data.member.name``, ``data.member.role``,
        ``data.member.power_level``, and ``data.member.is_active``.
    """
    member = SimpleNamespace(
        id=1,
        name="Alice",
        role="advanced",
        power_level=None,
        is_active=is_active,
    )
    return SimpleNamespace(
        siege_id=10,
        member_id=1,
        member_name="",
        member_role="",
        member_power_level=None,
        attack_day=1,
        has_reserve_set=False,
        attack_day_override=False,
        member=member,
    )


# ---------------------------------------------------------------------------
# 1. Active member → member_is_active is True
# ---------------------------------------------------------------------------


def test_siege_member_response_member_is_active_true_for_active_member():
    """SiegeMemberResponse.member_is_active must be True when member.is_active
    is True (active clan member appearing in the siege roster).
    """
    row = _make_orm_row(is_active=True)
    response = SiegeMemberResponse.model_validate(row)

    assert (
        response.member_is_active is True
    ), "member_is_active must be True when the related member is active"


# ---------------------------------------------------------------------------
# 2. Inactive member → member_is_active is False
# ---------------------------------------------------------------------------


def test_siege_member_response_member_is_active_false_for_inactive_member():
    """SiegeMemberResponse.member_is_active must be False when member.is_active
    is False (a deactivated member retained in an active/complete siege for
    historical purposes — the primary scenario for issue #487).
    """
    row = _make_orm_row(is_active=False)
    response = SiegeMemberResponse.model_validate(row)

    assert (
        response.member_is_active is False
    ), "member_is_active must be False when the related member is inactive"


# ---------------------------------------------------------------------------
# 3. Field is present in the serialized JSON output
# ---------------------------------------------------------------------------


def test_siege_member_response_member_is_active_present_in_json():
    """member_is_active must appear in the serialized response payload so the
    frontend type can rely on it always being present.
    """
    row = _make_orm_row(is_active=True)
    response = SiegeMemberResponse.model_validate(row)
    data = response.model_dump()

    assert (
        "member_is_active" in data
    ), "member_is_active must be present in the serialized response dict"
