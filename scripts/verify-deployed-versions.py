#!/usr/bin/env python3
"""Verify a deployed stack's health and exact component versions."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def expected_versions(version: dict[str, object], expected_sha: str) -> dict[str, str]:
    short_sha = expected_sha[:7]
    backend = str(version.get("backend_version") or "")
    metadata_match = re.fullmatch(r"[^+]+\+(\d+)\.([0-9a-f]{7})", backend)
    if not metadata_match or metadata_match.group(2) != short_sha:
        raise ValueError("backend_version does not contain expected build metadata")
    metadata = f"{metadata_match.group(1)}.{short_sha}"
    frontend_package = json.loads(
        Path("frontend/package.json").read_text(encoding="utf-8")
    )
    return {
        "backend_version": (
            f"{Path('backend/VERSION').read_text(encoding='utf-8').strip()}+{metadata}"
        ),
        "bot_version": f"{Path('bot/VERSION').read_text(encoding='utf-8').strip()}+{metadata}",
        "frontend_version": f"{frontend_package['version']}+{metadata}",
        "git_sha": expected_sha,
    }


def validate(
    health: dict[str, object],
    version: dict[str, object],
    frontend_marker: dict[str, object],
    expected_sha: str,
) -> list[str]:
    errors: list[str] = []
    if health.get("status") != "healthy":
        errors.append("health status is not healthy")
    try:
        expected = expected_versions(version, expected_sha)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        return [str(exc)]
    for key, value in expected.items():
        if version.get(key) != value:
            errors.append(f"{key} mismatch: expected {value!r}, got {version.get(key)!r}")
    for key in ("frontend_version", "git_sha"):
        if frontend_marker.get(key) != expected[key]:
            errors.append(
                f"frontend marker {key} mismatch: expected {expected[key]!r}, "
                f"got {frontend_marker.get(key)!r}"
            )
    return errors


def _get_json(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument(
        "--attempts", type=int, default=int(os.getenv("VERSION_VERIFY_ATTEMPTS", "30"))
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.getenv("VERSION_VERIFY_INTERVAL_SECONDS", "10")),
    )
    args = parser.parse_args()
    if not args.url or args.attempts < 1 or args.interval < 0:
        parser.error("url, positive attempts, and a non-negative interval are required")

    base_url = args.url.rstrip("/")
    last_errors = ["deployment has not been queried"]
    for attempt in range(1, args.attempts + 1):
        try:
            health = _get_json(f"{base_url}/api/health")
            version = _get_json(f"{base_url}/api/version")
            frontend_marker = _get_json(f"{base_url}/version.json")
            last_errors = validate(health, version, frontend_marker, args.expected_sha)
            if not last_errors:
                print(json.dumps(version, sort_keys=True))
                print(f"Deployment is healthy with exact versions for {args.expected_sha}.")
                return 0
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_errors = [f"request failed: {exc}"]
        print(f"Attempt {attempt}/{args.attempts}: {'; '.join(last_errors)}")
        if attempt < args.attempts:
            time.sleep(args.interval)

    print(
        "Deployment version verification failed: " + "; ".join(last_errors),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
