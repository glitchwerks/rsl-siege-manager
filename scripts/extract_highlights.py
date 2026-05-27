#!/usr/bin/env python3
"""Extract the Highlights section from a GitHub Release body for Discord notifications.

Reads the release body from stdin, extracts the text under a ``## Highlights``
heading (with or without the ``📣`` emoji), applies a 1500-character soft cap,
and prints the result to stdout.

If no Highlights section is found (or it is empty after stripping), prints a
fallback line of the form::

    <release name or tag> published. View notes: <url>

**Always exits 0** — the workflow must post even when the section is absent.

Usage (matches how the workflow invokes it)::

    python scripts/extract_highlights.py \\
        --tag  "v1.2.0" \\
        --name "v1.2.0 - Headline" \\
        --url  "https://github.com/glitchwerks/.../releases/tag/v1.2.0" \\
        < release_body.txt
"""

import argparse
import re
import sys

# Maximum description length before truncation (coord §R5).
_SOFT_CAP = 1500
# Characters reserved for the ellipsis when truncating.
_ELLIPSIS = "…"  # Unicode HORIZONTAL ELLIPSIS (…)
# Pattern matching a Highlights heading: ## (optional emoji) Highlights, case-insensitive.
_HIGHLIGHTS_RE = re.compile(
    r"^##\s+(?:📣\s+)?highlights\b",
    re.IGNORECASE | re.MULTILINE,
)
# Pattern matching any ## heading (used as the stop marker).
_NEXT_HEADING_RE = re.compile(r"^##\s+", re.MULTILINE)


def extract_highlights(body: str) -> str | None:
    """Return the text under the Highlights heading, or None if absent/empty.

    Args:
        body: The full GitHub Release body text.

    Returns:
        Stripped text from the Highlights section, or ``None`` if the section
        does not exist or contains only whitespace.
    """
    match = _HIGHLIGHTS_RE.search(body)
    if not match:
        return None

    # Text starts immediately after the heading line.
    after_heading = body[match.end():]

    # Find the next ## heading within that remainder.
    stop = _NEXT_HEADING_RE.search(after_heading)
    section = after_heading[: stop.start()] if stop else after_heading

    stripped = section.strip()
    return stripped if stripped else None


def apply_length_cap(text: str, url: str) -> str:
    """Truncate *text* to 1500 chars if needed, appending an ellipsis and URL line.

    Args:
        text: The description text to potentially truncate.
        url: The release HTML URL, appended when truncation occurs.

    Returns:
        The original text (unchanged) if ``len(text) <= 1500``, otherwise the
        text truncated to 1497 chars + ``…`` followed by a newline and the URL.
    """
    if len(text) <= _SOFT_CAP:
        return text
    truncated = text[:_SOFT_CAP - 1] + _ELLIPSIS
    return f"{truncated}\nView full release notes: {url}"


def build_fallback(tag: str, name: str, url: str) -> str:
    """Build the fallback description when no Highlights section exists.

    Args:
        tag: The release tag (e.g. ``v1.2.0``).
        name: The release name (may be empty).
        url: The release HTML URL.

    Returns:
        A one-line fallback string per coord §R6.
    """
    label = name.strip() if name.strip() else tag
    return f"{label} published. View notes: {url}"


def main() -> None:
    """Entry point — parse args, read stdin, print result, exit 0."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Release tag (e.g. v1.2.0)")
    parser.add_argument("--name", default="", help="Release name (may be empty)")
    parser.add_argument("--url", required=True, help="Release HTML URL")
    args = parser.parse_args()

    body = sys.stdin.read()

    highlights = extract_highlights(body)
    if highlights:
        description = apply_length_cap(highlights, args.url)
    else:
        description = build_fallback(args.tag, args.name, args.url)

    print(description, end="")


if __name__ == "__main__":
    main()
