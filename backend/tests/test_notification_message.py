"""Unit tests for build_member_notification_message in notification_message.py."""

from app.models.enums import BuildingType
from app.services.notification_message import PositionInfo, build_member_notification_message

# ---------------------------------------------------------------------------
# Icon constants (mirrors _CHANGE_TYPE_ICON in notification_message.py)
# ---------------------------------------------------------------------------

ICON_NO_CHANGE = ":shield:"
ICON_REMOVE_FROM = ":x:"
ICON_SET_AT = ":crossed_swords:"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SINGLE_STRONGHOLD_COUNTS: dict[BuildingType, int] = {
    BuildingType.stronghold: 1,
    BuildingType.defense_tower: 3,
    BuildingType.post: 18,
}

MULTI_DEFENSE_TOWER_COUNTS: dict[BuildingType, int] = {
    BuildingType.stronghold: 1,
    BuildingType.defense_tower: 5,
    BuildingType.post: 18,
}


def _stronghold_pos(group: int = 6, pos: int = 1) -> PositionInfo:
    return PositionInfo(
        building_type=BuildingType.stronghold,
        building_number=1,
        group_number=group,
        position_number=pos,
    )


def _defense_tower_pos(number: int = 5, group: int = 1, pos: int = 3) -> PositionInfo:
    return PositionInfo(
        building_type=BuildingType.defense_tower,
        building_number=number,
        group_number=group,
        position_number=pos,
    )


def _post_pos(number: int = 2) -> PositionInfo:
    return PositionInfo(
        building_type=BuildingType.post,
        building_number=number,
        group_number=1,
        position_number=1,
    )


# ---------------------------------------------------------------------------
# 1. No previous siege — all current positions appear with Set At icon (⚔️)
# ---------------------------------------------------------------------------


def test_no_previous_siege_all_current_in_set_at():
    """When previous_positions is empty every current position gets the ⚔️ icon."""
    current = [_stronghold_pos(), _post_pos(2)]
    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=2,
        current_positions=current,
        previous_positions=[],
        building_type_counts=SINGLE_STRONGHOLD_COUNTS,
    )
    assert ICON_SET_AT in msg
    assert ICON_NO_CHANGE not in msg
    assert ICON_REMOVE_FROM not in msg


# ---------------------------------------------------------------------------
# 2. Empty sections are omitted — no diff → only No Change icon (🛡️)
# ---------------------------------------------------------------------------


def test_empty_sections_omitted_all_no_change():
    """When current and previous are identical only the 🛡️ icon appears."""
    pos = [_stronghold_pos(), _post_pos(8)]
    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=False,
        attack_day=1,
        current_positions=pos,
        previous_positions=pos,
        building_type_counts=SINGLE_STRONGHOLD_COUNTS,
    )
    assert ICON_NO_CHANGE in msg
    assert ICON_REMOVE_FROM not in msg
    assert ICON_SET_AT not in msg


# ---------------------------------------------------------------------------
# 3. Full diff — positions spread across all three sections
# ---------------------------------------------------------------------------


def test_full_diff_three_sections():
    """Positions only in current → ⚔️, only in previous → ❌, both → 🛡️."""
    shared = _stronghold_pos(group=6, pos=1)
    only_current = _post_pos(2)
    only_previous = _post_pos(18)

    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=2,
        current_positions=[shared, only_current],
        previous_positions=[shared, only_previous],
        building_type_counts=SINGLE_STRONGHOLD_COUNTS,
    )
    assert ICON_NO_CHANGE in msg
    assert ICON_REMOVE_FROM in msg
    assert ICON_SET_AT in msg

    # Correct positions end up with the right icon prefix
    no_change_idx = msg.index(ICON_NO_CHANGE)
    remove_from_idx = msg.index(ICON_REMOVE_FROM)
    set_at_idx = msg.index(ICON_SET_AT)

    # Stronghold (shared) has the 🛡️ icon before it
    stronghold_label = ":red_circle: Stronghold / Group 6 / Pos 1"
    assert stronghold_label in msg
    assert msg.index(stronghold_label) > no_change_idx

    # Post 18 (only previous) has the ❌ icon before it
    post18_label = ":white_circle: Post 18"
    assert post18_label in msg
    assert msg.index(post18_label) > remove_from_idx

    # Post 2 (only current) has the ⚔️ icon before it
    post2_label = ":white_circle: Post 2"
    assert post2_label in msg
    assert msg.index(post2_label) > set_at_idx


# ---------------------------------------------------------------------------
# 4. Header content — siege date, reserve set, attack day
# ---------------------------------------------------------------------------


def test_header_contains_siege_date_and_member_settings():
    """The message header must include the date, reserve status and attack day."""
    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=2,
        current_positions=[],
        previous_positions=[],
        building_type_counts={},
    )
    assert "**[1MOM] Masters of Magicka Siege Assignment (2026-03-17)**" in msg
    assert "**Have Reserve Set:** Yes" in msg
    assert "**Attack Day:** 2" in msg


# ---------------------------------------------------------------------------
# 5. None values for has_reserve_set and attack_day → "Unknown"
# ---------------------------------------------------------------------------


def test_none_fields_display_as_unknown():
    """None for has_reserve_set or attack_day should render as 'Unknown'."""
    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=None,
        attack_day=None,
        current_positions=[],
        previous_positions=[],
        building_type_counts={},
    )
    assert "**Have Reserve Set:** Unknown" in msg
    assert "**Attack Day:** Unknown" in msg


def test_false_reserve_set_displays_no():
    """has_reserve_set=False should render as 'No', not 'Unknown'."""
    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=False,
        attack_day=1,
        current_positions=[],
        previous_positions=[],
        building_type_counts={},
    )
    assert "**Have Reserve Set:** No" in msg


# ---------------------------------------------------------------------------
# 6. Single building type — Stronghold label omits building number
# ---------------------------------------------------------------------------


def test_single_building_type_omits_building_number():
    """When count == 1 the building number is omitted from the label."""
    current = [_stronghold_pos(group=6, pos=1)]
    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=2,
        current_positions=current,
        previous_positions=[],
        building_type_counts={BuildingType.stronghold: 1},
    )
    # No building number in label
    assert ":red_circle: Stronghold / Group 6 / Pos 1" in msg
    assert ":red_circle: Stronghold 1 / Group" not in msg


# ---------------------------------------------------------------------------
# 7. Multiple of same type — Defense Tower label includes building number
# ---------------------------------------------------------------------------


def test_multiple_building_type_includes_building_number():
    """When count > 1 the building number is included in the label."""
    current = [_defense_tower_pos(number=5, group=1, pos=3)]
    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=2,
        current_positions=current,
        previous_positions=[],
        building_type_counts=MULTI_DEFENSE_TOWER_COUNTS,
    )
    assert ":green_circle: Defense Tower 5 / Group 1 / Pos 3" in msg


# ---------------------------------------------------------------------------
# 8. Post format — always uses short "Post {N}" format
# ---------------------------------------------------------------------------


def test_post_always_uses_short_format():
    """Posts always render as ':white_circle: Post {N}' regardless of count."""
    current = [_post_pos(8)]
    # Even with many posts, short format must be used
    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=1,
        current_positions=current,
        previous_positions=[],
        building_type_counts={BuildingType.post: 18},
    )
    assert ":white_circle: Post 8" in msg
    assert "Group" not in msg.split(":white_circle:")[1]


def test_post_with_single_count_still_uses_short_format():
    """Posts with count == 1 still use short format (not the number-omitting path)."""
    current = [_post_pos(1)]
    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=1,
        current_positions=current,
        previous_positions=[],
        building_type_counts={BuildingType.post: 1},
    )
    assert ":white_circle: Post 1" in msg


# ---------------------------------------------------------------------------
# 9. Section order — No Change → Remove From → Set At (icon order in message)
# ---------------------------------------------------------------------------


def test_section_order_no_change_then_remove_then_set_at():
    """Icons must appear in the order: 🛡️ (No Change), ❌ (Remove From), ⚔️ (Set At)."""
    shared = _stronghold_pos(group=1, pos=1)
    only_prev = _defense_tower_pos(number=1, group=1, pos=1)
    only_curr = _post_pos(5)

    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=2,
        current_positions=[shared, only_curr],
        previous_positions=[shared, only_prev],
        building_type_counts=MULTI_DEFENSE_TOWER_COUNTS,
    )

    no_change_idx = msg.index(ICON_NO_CHANGE)
    remove_from_idx = msg.index(ICON_REMOVE_FROM)
    set_at_idx = msg.index(ICON_SET_AT)

    assert no_change_idx < remove_from_idx < set_at_idx


# ---------------------------------------------------------------------------
# 10. Positions within a section are sorted by building type then number etc.
# ---------------------------------------------------------------------------


def test_positions_sorted_within_section():
    """Within Set At, positions should be sorted by type order then building/group/pos."""
    # Post comes after Stronghold in canonical order
    sh = _stronghold_pos(group=2, pos=1)
    post = _post_pos(3)

    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=1,
        current_positions=[post, sh],  # deliberately reversed
        previous_positions=[],
        building_type_counts=SINGLE_STRONGHOLD_COUNTS,
    )

    stronghold_idx = msg.index(":red_circle: Stronghold")
    post_idx = msg.index(":white_circle: Post")
    assert stronghold_idx < post_idx


# ---------------------------------------------------------------------------
# 11. Section headers — change-type icon flanks the label; position lines are plain
# ---------------------------------------------------------------------------

_BUILDING_CIRCLE_EMOJIS = (
    ":red_circle:",
    ":green_circle:",
    ":yellow_circle:",
    ":blue_circle:",
    ":white_circle:",
)


def test_no_change_section_has_header_and_plain_position_lines():
    """No Change section must have a ':shield:  No Change  :shield:' header.

    Position lines must start with a building-type circle emoji, NOT the
    change-type icon — the icon belongs only on the header.
    """
    pos = [_stronghold_pos(group=3, pos=2), _post_pos(5)]
    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=1,
        current_positions=pos,
        previous_positions=pos,
        building_type_counts=SINGLE_STRONGHOLD_COUNTS,
    )
    assert ":shield: ** No Change ** :shield:" in msg
    position_lines = [
        ln
        for ln in msg.splitlines()
        if ln.startswith("- ") and any(c in ln for c in _BUILDING_CIRCLE_EMOJIS)
    ]
    assert len(position_lines) > 0
    assert not any(ln.startswith(ICON_NO_CHANGE) for ln in position_lines)


def test_remove_from_section_has_header_and_plain_position_lines():
    """Remove From section must have a ':x:  Remove From  :x:' header.

    Position lines must start with a building-type circle emoji, NOT the
    change-type icon.
    """
    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=1,
        current_positions=[],
        previous_positions=[_post_pos(7)],
        building_type_counts=SINGLE_STRONGHOLD_COUNTS,
    )
    assert ":x: ** Remove From ** :x:" in msg
    position_lines = [
        ln
        for ln in msg.splitlines()
        if ln.startswith("- ") and any(c in ln for c in _BUILDING_CIRCLE_EMOJIS)
    ]
    assert len(position_lines) > 0
    assert not any(ln.startswith(ICON_REMOVE_FROM) for ln in position_lines)


def test_set_at_section_has_header_and_plain_position_lines():
    """Set At section must have a ':crossed_swords:  Set At  :crossed_swords:' header.

    Position lines must start with a building-type circle emoji, NOT the
    change-type icon.
    """
    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=1,
        current_positions=[_post_pos(3)],
        previous_positions=[],
        building_type_counts=SINGLE_STRONGHOLD_COUNTS,
    )
    assert ":crossed_swords: ** Set At ** :crossed_swords:" in msg
    position_lines = [
        ln
        for ln in msg.splitlines()
        if ln.startswith("- ") and any(c in ln for c in _BUILDING_CIRCLE_EMOJIS)
    ]
    assert len(position_lines) > 0
    assert not any(ln.startswith(ICON_SET_AT) for ln in position_lines)


# ---------------------------------------------------------------------------
# 12. Blank line between sections when multiple sections are present
# ---------------------------------------------------------------------------


def test_blank_line_between_no_change_and_remove_from():
    """A blank line must appear between the No Change and Remove From sections."""
    shared = _stronghold_pos(group=1, pos=1)
    only_prev = _post_pos(18)

    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=1,
        current_positions=[shared],
        previous_positions=[shared, only_prev],
        building_type_counts=SINGLE_STRONGHOLD_COUNTS,
    )
    # A blank line (\n\n) exists somewhere after the header block
    assert "\n\n" in msg


def test_blank_line_between_remove_from_and_set_at():
    """A blank line must appear between the Remove From and Set At sections."""
    only_prev = _post_pos(18)
    only_curr = _post_pos(2)

    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=1,
        current_positions=[only_curr],
        previous_positions=[only_prev],
        building_type_counts=SINGLE_STRONGHOLD_COUNTS,
    )
    assert "\n\n" in msg


def test_no_blank_line_when_only_one_section():
    """When only one section is present there should be no blank line within that section."""
    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=1,
        current_positions=[_post_pos(1)],
        previous_positions=[],
        building_type_counts=SINGLE_STRONGHOLD_COUNTS,
    )
    # The header contributes 2 blank lines (\n\n): one between the title and
    # the metadata block, and one between the metadata and the section block
    # (issue #510 removed the work-in-progress disclaimer and its trailing
    # blank line, dropping this from 3 to 2).  With only one section there
    # are no inter-section separators, so the total count should be exactly 2.
    assert msg.count("\n\n") == 2


def test_blank_line_count_with_all_three_sections():
    """With all three sections there should be exactly two blank lines between them
    (plus one after header)."""
    shared = _stronghold_pos(group=1, pos=1)
    only_prev = _post_pos(18)
    only_curr = _post_pos(2)

    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=1,
        current_positions=[shared, only_curr],
        previous_positions=[shared, only_prev],
        building_type_counts=SINGLE_STRONGHOLD_COUNTS,
    )
    # Header contributes 2 blank lines (issue #510 removed the disclaimer's
    # trailing blank line) + 2 inter-section separators = 4 total
    assert msg.count("\n\n") == 4


# ---------------------------------------------------------------------------
# 13. Section header exact format
# ---------------------------------------------------------------------------


def test_all_three_section_headers_exact_format():
    """All three section headers must appear as exact lines in the message."""
    shared = _stronghold_pos(group=1, pos=1)
    only_prev = _post_pos(18)
    only_curr = _post_pos(2)

    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=2,
        current_positions=[shared, only_curr],
        previous_positions=[shared, only_prev],
        building_type_counts=SINGLE_STRONGHOLD_COUNTS,
    )
    lines = msg.splitlines()
    assert ":shield: ** No Change ** :shield:" in lines
    assert ":x: ** Remove From ** :x:" in lines
    assert ":crossed_swords: ** Set At ** :crossed_swords:" in lines


# ---------------------------------------------------------------------------
# 14. Work-in-progress disclaimer must not appear (issue #510)
# ---------------------------------------------------------------------------


def test_message_omits_work_in_progress_disclaimer():
    """The message must not include the 'bot is a work in progress' disclaimer.

    Regression coverage for issue #510: the disclaimer line and its
    ``:warning:`` wrapper were removed entirely, and the message must now
    start directly with the siege-assignment title line (no leading blank
    line left behind from the removed disclaimer block).
    """
    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=2,
        current_positions=[_post_pos(1)],
        previous_positions=[],
        building_type_counts=SINGLE_STRONGHOLD_COUNTS,
    )
    assert "work in progress" not in msg
    assert "Please verify assignments manually" not in msg
    assert msg.splitlines()[0].startswith("**[1MOM] Masters of Magicka Siege Assignment (")


def test_header_line_not_a_position_line():
    """The section header line itself must not contain a building-type circle emoji."""
    msg = build_member_notification_message(
        siege_date="2026-03-17",
        has_reserve_set=True,
        attack_day=1,
        current_positions=[_post_pos(1)],
        previous_positions=[],
        building_type_counts=SINGLE_STRONGHOLD_COUNTS,
    )
    lines = msg.splitlines()
    header_line = ":crossed_swords: ** Set At ** :crossed_swords:"
    assert header_line in lines
    # The header line must not contain any building-type circle emoji
    assert not any(circle in header_line for circle in _BUILDING_CIRCLE_EMOJIS)
