"""Build rich per-member Discord DM messages for siege assignment notifications."""

from dataclasses import dataclass

from app.models.enums import BuildingType

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BUILDING_TYPE_EMOJI: dict[BuildingType, str] = {
    BuildingType.stronghold: ":red_circle:",
    BuildingType.defense_tower: ":green_circle:",
    BuildingType.mana_shrine: ":yellow_circle:",
    BuildingType.magic_tower: ":blue_circle:",
    BuildingType.post: ":white_circle:",
}

_BUILDING_TYPE_LABEL: dict[BuildingType, str] = {
    BuildingType.stronghold: "Stronghold",
    BuildingType.defense_tower: "Defense Tower",
    BuildingType.mana_shrine: "Mana Shrine",
    BuildingType.magic_tower: "Magic Tower",
    BuildingType.post: "Post",
}

# Per-change-type Discord shortcode icons used in section headers.
# Discord renders these shortcodes into emoji in DMs; shortcodes are the
# canonical format matching the design spec's intended output.
_CHANGE_TYPE_ICON: dict[str, str] = {
    "no_change": ":shield:",
    "remove_from": ":x:",
    "set_at": ":crossed_swords:",
}

# Human-readable label for each change type, used alongside the icon in the
# section header line (e.g. ":shield:  No Change  :shield:").
_CHANGE_TYPE_LABEL: dict[str, str] = {
    "no_change": "No Change",
    "remove_from": "Remove From",
    "set_at": "Set At",
}

# Canonical ordering for BuildingType enum values (used to sort positions).
# StrEnum inherits from str, so we build an explicit order list.
_BUILDING_TYPE_ORDER: list[BuildingType] = [
    BuildingType.stronghold,
    BuildingType.defense_tower,
    BuildingType.mana_shrine,
    BuildingType.magic_tower,
    BuildingType.post,
]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionInfo:
    """Minimal description of a single assigned position."""

    building_type: BuildingType
    building_number: int
    group_number: int
    position_number: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _position_sort_key(p: PositionInfo) -> tuple[int, int, int, int]:
    """Return a sortable tuple for consistent ordering within a section.

    Sorts by: building type (canonical order), building number,
    group number, position number.
    """
    type_order = _BUILDING_TYPE_ORDER.index(p.building_type)
    return (type_order, p.building_number, p.group_number, p.position_number)


def _position_label(p: PositionInfo, building_type_counts: dict[BuildingType, int]) -> str:
    """Format a human-readable label for a position.

    Posts always use the short ``Post {N}`` format.  For all other building
    types the building number is included only when there is more than one
    building of that type in the current siege.
    """
    emoji = _BUILDING_TYPE_EMOJI[p.building_type]
    label = _BUILDING_TYPE_LABEL[p.building_type]

    if p.building_type == BuildingType.post:
        # Short format regardless of building count.
        return f"{emoji} {label} {p.building_number}"

    count = building_type_counts.get(p.building_type, 1)
    if count > 1:
        return (
            f"{emoji} {label} {p.building_number}"
            f" / Group {p.group_number}"
            f" / Pos {p.position_number}"
        )
    else:
        # Only one building of this type — omit the building number.
        return f"{emoji} {label} / Group {p.group_number} / Pos {p.position_number}"


def _positions_to_key_set(positions: list[PositionInfo]) -> set[tuple]:
    """Convert a list of PositionInfo objects to a set of comparable tuples."""
    return {
        (p.building_type, p.building_number, p.group_number, p.position_number) for p in positions
    }


def _positions_from_keys(positions: list[PositionInfo], keys: set[tuple]) -> list[PositionInfo]:
    """Filter ``positions`` to those whose key is in ``keys``, preserving PositionInfo objects."""
    return [
        p
        for p in positions
        if (p.building_type, p.building_number, p.group_number, p.position_number) in keys
    ]


def _build_section(
    change_type: str,
    positions: list[PositionInfo],
    building_type_counts: dict[BuildingType, int],
) -> str:
    """Render one diff section as a header line followed by plain position lines.

    The header takes the form ``{icon}  {Label}  {icon}`` (e.g.
    ``:shield:  No Change  :shield:``).  Each position line starts directly
    with the building-type circle emoji provided by ``_position_label()``.
    Sections are separated by blank lines by the caller.
    """
    icon = _CHANGE_TYPE_ICON[change_type]
    label = _CHANGE_TYPE_LABEL[change_type]
    header = f"{icon} ** {label} ** {icon}"
    position_lines = [
        "- " + _position_label(p, building_type_counts)
        for p in sorted(positions, key=_position_sort_key)
    ]
    return header + "\n" + "\n".join(position_lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_member_notification_message(
    siege_date: str,
    has_reserve_set: bool | None,
    attack_day: int | None,
    current_positions: list[PositionInfo],
    previous_positions: list[PositionInfo],
    building_type_counts: dict[BuildingType, int],
) -> str:
    """Build a rich Discord DM message for a single siege member.

    Args:
        siege_date: ISO date string, e.g. ``"2026-03-17"``.
        has_reserve_set: Whether the member has a reserve set configured,
            or ``None`` when unknown.
        attack_day: The member's attack day (1 or 2), or ``None`` when unknown.
        current_positions: All non-reserve, non-disabled positions assigned
            to this member in the current siege.
        previous_positions: Same for the most recent completed siege, or an
            empty list when there is no previous siege.
        building_type_counts: Mapping of building type → number of buildings
            of that type in the current siege.  Used to decide whether to
            include the building number in position labels.

    Returns:
        A formatted multi-line string ready to send as a Discord DM.
    """
    # --- Header ---
    reserve_str = "Unknown" if has_reserve_set is None else ("Yes" if has_reserve_set else "No")
    attack_day_str = "Unknown" if attack_day is None else str(attack_day)

    lines: list[str] = [
        f"**[1MOM] Masters of Magicka Siege Assignment ({siege_date})**",
        "",
        f"**Have Reserve Set:** {reserve_str}",
        f"**Attack Day:** {attack_day_str}",
    ]

    # --- Diff computation ---
    current_keys = _positions_to_key_set(current_positions)
    previous_keys = _positions_to_key_set(previous_positions)

    no_change_keys = current_keys & previous_keys
    remove_from_keys = previous_keys - current_keys
    set_at_keys = current_keys - previous_keys

    # Resolve back to PositionInfo objects (use current list for no_change/set_at,
    # previous list for remove_from so we preserve the original objects).
    no_change_positions = _positions_from_keys(current_positions, no_change_keys)
    remove_from_positions = _positions_from_keys(previous_positions, remove_from_keys)
    set_at_positions = _positions_from_keys(current_positions, set_at_keys)

    # --- Sections (omit empty ones; separate non-empty ones with a blank line) ---
    sections: list[str] = []

    if no_change_positions:
        sections.append(_build_section("no_change", no_change_positions, building_type_counts))

    if remove_from_positions:
        sections.append(_build_section("remove_from", remove_from_positions, building_type_counts))

    if set_at_positions:
        sections.append(_build_section("set_at", set_at_positions, building_type_counts))

    if sections:
        lines.append("")
        # Join sections with a blank line so each change-type group is visually
        # separated in the Discord DM.
        lines.append("\n\n".join(sections))

    return "\n".join(lines)
