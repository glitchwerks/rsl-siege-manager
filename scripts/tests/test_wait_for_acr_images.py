"""Tests for the release image-availability gate."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "wait-for-acr-images.sh"


def _fake_az(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    az = bin_dir / "az"
    az.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
image=""
while [[ $# -gt 0 ]]; do
  if [[ $1 == "--image" ]]; then
    image=$2
    break
  fi
  shift
done

repo=${image%%:*}
if [[ ",${FAKE_MISSING_REPOS:-}," == *",${repo},"* ]]; then
  exit 1
fi

if [[ -n ${FAKE_COUNTER_FILE:-} ]]; then
  count=0
  [[ -f $FAKE_COUNTER_FILE ]] && count=$(<"$FAKE_COUNTER_FILE")
  count=$((count + 1))
  printf '%s' "$count" >"$FAKE_COUNTER_FILE"
  if (( count <= ${FAKE_FAIL_CALLS:-0} )); then
    exit 1
  fi
fi

printf 'sha256:test\n'
""",
        encoding="utf-8",
    )
    az.chmod(0o755)
    return bin_dir


def _run(tmp_path: Path, *repositories: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
    bin_dir = _fake_az(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "ACR_WAIT_ATTEMPTS": "3",
            "ACR_WAIT_INTERVAL_SECONDS": "0",
            **extra_env,
        }
    )
    return subprocess.run(
        [str(SCRIPT), "example-acr", "abc123", *repositories],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_succeeds_when_all_images_exist(tmp_path: Path) -> None:
    result = _run(tmp_path, "siege-api", "siege-frontend", "siege-bot")

    assert result.returncode == 0
    assert "All release images are available" in result.stdout


def test_retries_until_delayed_image_exists(tmp_path: Path) -> None:
    counter = tmp_path / "counter"
    result = _run(
        tmp_path,
        "siege-api",
        FAKE_COUNTER_FILE=str(counter),
        FAKE_FAIL_CALLS="1",
    )

    assert result.returncode == 0
    assert "Attempt 1/3" in result.stdout
    assert counter.read_text(encoding="utf-8") == "2"


def test_fails_closed_when_an_image_never_appears(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "siege-api",
        "siege-frontend",
        FAKE_MISSING_REPOS="siege-frontend",
    )

    assert result.returncode == 1
    assert "Timed out waiting for release images tagged abc123: siege-frontend" in result.stderr


def test_rejects_invalid_retry_configuration(tmp_path: Path) -> None:
    result = _run(tmp_path, "siege-api", ACR_WAIT_ATTEMPTS="0")

    assert result.returncode == 2
    assert "ACR_WAIT_ATTEMPTS must be a positive integer" in result.stderr
