"""Tests for scripts/extract_highlights.py.

Covers the Highlights-extraction logic used by the Discord release-notification
workflow. The script is invoked from the workflow with the GitHub Release body
piped to stdin; it prints the extracted (or fallback) description to stdout.

Fixture cases exercised:
  1. Highlights present with plain heading (## Highlights)
  2. Highlights present with emoji heading (## 📣 Highlights)
  3. Highlights section is multi-paragraph
  4. Body oversized — truncated to 1499 chars + ellipsis + URL line
  5. Body exactly at the 1500-char boundary — NOT truncated
  6. No Highlights section — fallback with release name + URL
  7. No Highlights section, no release name — fallback uses tag
  8. Highlights text is empty (heading with nothing before next ##) — fallback
  9. Case-insensitive match (## HIGHLIGHTS)

Exit codes:
  0 — always (the script must not fail even on missing sections)

Usage (matches how the workflow calls it):
    python scripts/extract_highlights.py \
        --tag "v1.2.0" \
        --name "v1.2.0 - Headline" \
        --url "https://github.com/.../releases/tag/v1.2.0" \
        < release_body.txt
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "extract_highlights.py"

RELEASE_URL = "https://github.com/glitchwerks/rsl-siege-manager/releases/tag/v1.2.0"
RELEASE_TAG = "v1.2.0"
RELEASE_NAME = "v1.2.0 - The Siege Begins"


def run_script(body: str, tag: str = RELEASE_TAG, name: str = RELEASE_NAME, url: str = RELEASE_URL) -> subprocess.CompletedProcess:
    """Run extract_highlights.py with the given release body on stdin."""
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--tag", tag,
            "--name", name,
            "--url", url,
        ],
        input=body,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

PLAIN_BODY = """\
## What's Changed

Minor dependency updates.

## Highlights

This release ships the new siege-planner feature.
Officers can now assign raid windows directly from the calendar view.

## Full Changelog

https://github.com/...
"""

EMOJI_BODY = """\
## What's Changed

Some fixes.

## 📣 Highlights

Emoji heading variant — same extraction should apply.

## Notes
"""

MULTI_PARA_BODY = """\
## Highlights

First paragraph describing the big feature.

Second paragraph with more detail.

- Bullet one
- Bullet two

## Full Changelog
"""

OVERSIZED_BASE = "x" * 2000
OVERSIZED_BODY = f"## Highlights\n\n{OVERSIZED_BASE}\n\n## Other\n"

# Exactly 1500 chars in the highlights section — must NOT truncate.
AT_LIMIT_BODY = f"## Highlights\n\n{'y' * 1500}\n\n## Other\n"

NO_HIGHLIGHTS_BODY = """\
## What's Changed

- Fix login bug (#123)
- Update dependencies

## Full Changelog
"""

EMPTY_HIGHLIGHTS_BODY = """\
## Highlights

## Full Changelog

Nothing here.
"""

UPPERCASE_BODY = """\
## HIGHLIGHTS

All-caps heading should still match.

## Notes
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHighlightsPresent:
    def test_plain_heading_extracts_content(self):
        result = run_script(PLAIN_BODY)
        assert result.returncode == 0
        assert "siege-planner" in result.stdout
        assert "calendar view" in result.stdout

    def test_plain_heading_strips_heading_line(self):
        result = run_script(PLAIN_BODY)
        assert "## Highlights" not in result.stdout

    def test_emoji_heading_extracts_content(self):
        result = run_script(EMOJI_BODY)
        assert result.returncode == 0
        assert "Emoji heading variant" in result.stdout

    def test_emoji_heading_strips_heading_line(self):
        result = run_script(EMOJI_BODY)
        assert "## 📣 Highlights" not in result.stdout

    def test_multi_paragraph_preserved(self):
        result = run_script(MULTI_PARA_BODY)
        assert result.returncode == 0
        assert "First paragraph" in result.stdout
        assert "Second paragraph" in result.stdout
        assert "Bullet one" in result.stdout

    def test_stops_at_next_heading(self):
        result = run_script(PLAIN_BODY)
        # Content after ## Full Changelog should NOT appear
        assert "Full Changelog" not in result.stdout

    def test_case_insensitive_match(self):
        result = run_script(UPPERCASE_BODY)
        assert result.returncode == 0
        assert "All-caps" in result.stdout


class TestLengthCap:
    def test_oversized_is_truncated(self):
        # Expected: 1499 body chars + "…" (1 char) + "\n" (1 char)
        # + "View full release notes: " (25 chars) + URL.
        # Total = 1499 + 1 + 1 + 25 + len(RELEASE_URL) = 1526 + len(RELEASE_URL).
        expected_len = 1499 + 1 + 1 + len("View full release notes: ") + len(RELEASE_URL)
        result = run_script(OVERSIZED_BODY)
        assert result.returncode == 0
        assert len(result.stdout) == expected_len

    def test_oversized_ends_with_ellipsis(self):
        result = run_script(OVERSIZED_BODY)
        assert "…" in result.stdout

    def test_oversized_appends_url_line(self):
        result = run_script(OVERSIZED_BODY)
        assert f"View full release notes: {RELEASE_URL}" in result.stdout

    def test_at_limit_not_truncated(self):
        result = run_script(AT_LIMIT_BODY)
        assert result.returncode == 0
        assert "…" not in result.stdout
        assert "View full release notes" not in result.stdout
        # All 1500 'y' chars should be present
        assert "y" * 100 in result.stdout


class TestMissingSummaryFallback:
    def test_no_highlights_uses_fallback(self):
        result = run_script(NO_HIGHLIGHTS_BODY)
        assert result.returncode == 0
        assert RELEASE_URL in result.stdout

    def test_no_highlights_includes_release_name(self):
        result = run_script(NO_HIGHLIGHTS_BODY)
        assert RELEASE_NAME in result.stdout

    def test_no_highlights_no_name_uses_tag(self):
        result = run_script(NO_HIGHLIGHTS_BODY, name="")
        assert result.returncode == 0
        assert RELEASE_TAG in result.stdout
        assert RELEASE_URL in result.stdout

    def test_empty_highlights_uses_fallback(self):
        """A ## Highlights heading with no content before next ## counts as missing."""
        result = run_script(EMPTY_HIGHLIGHTS_BODY)
        assert result.returncode == 0
        assert RELEASE_URL in result.stdout

    def test_fallback_does_not_include_changelog_content(self):
        """The fallback must not accidentally include unrelated body text."""
        result = run_script(NO_HIGHLIGHTS_BODY)
        assert "Fix login bug" not in result.stdout


class TestAlwaysSucceeds:
    def test_empty_body(self):
        result = run_script("")
        assert result.returncode == 0

    def test_body_is_only_whitespace(self):
        result = run_script("   \n\n  ")
        assert result.returncode == 0

    def test_no_stderr_on_normal_input(self):
        result = run_script(PLAIN_BODY)
        assert result.stderr == ""
