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
    frontend_marker: dict[str, object] | None,
    expected_sha: str,
    verify_frontend: bool = True,
) -> list[str]:
    errors: list[str] = []
    if health.get("status") != "healthy":
        errors.append("health status is not healthy")
    try:
        expected = expected_versions(version, expected_sha)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        return [str(exc)]
    for key, value in expected.items():
        if not verify_frontend and key == "frontend_version":
            continue
        if version.get(key) != value:
            errors.append(f"{key} mismatch: expected {value!r}, got {version.get(key)!r}")
    if verify_frontend:
        if frontend_marker is None:
            errors.append("frontend marker is missing")
        else:
            for key in ("frontend_version", "git_sha"):
                if frontend_marker.get(key) != expected[key]:
                    errors.append(
                        f"frontend marker {key} mismatch: expected {expected[key]!r}, "
                        f"got {frontend_marker.get(key)!r}"
                    )
    return errors


def _get_json(url: str, *, github_auth: bool = False) -> dict[str, object]:
    headers = {"Accept": "application/json", "User-Agent": "siege-version-verifier"}
    if github_auth:
        if token := os.getenv("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.load(response)


def superseding_sha(latest_sha_url: str | None, expected_sha: str) -> str | None:
    """Return the newer selected SHA, or None when this run is still current."""
    if not latest_sha_url:
        return None
    selected_sha = _get_json(latest_sha_url, github_auth=True).get("sha")
    if selected_sha == expected_sha:
        return None
    return str(selected_sha)


def deployment_matches_sha(
    health: dict[str, object],
    version: dict[str, object],
    frontend_marker: dict[str, object],
    expected_sha: str,
) -> bool:
    """Return true when both public artifacts have converged on a healthy SHA."""
    short_sha = expected_sha[:7]
    component_keys = ("backend_version", "bot_version", "frontend_version")
    return (
        health.get("status") == "healthy"
        and version.get("git_sha") == expected_sha
        and frontend_marker.get("git_sha") == expected_sha
        and all(
            re.fullmatch(rf"[^+]+\+\d+\.{re.escape(short_sha)}", str(version.get(key) or ""))
            for key in component_keys
        )
        and re.fullmatch(
            rf"[^+]+\+\d+\.{re.escape(short_sha)}",
            str(frontend_marker.get("frontend_version") or ""),
        )
        is not None
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument(
        "--latest-sha-url",
        help="skip successfully if this endpoint reports a newer selected SHA",
    )
    parser.add_argument(
        "--legacy-without-frontend-marker",
        action="store_true",
        help="for rollback only: verify health, API, bot, and SHA without frontend metadata",
    )
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
            selected_sha = superseding_sha(args.latest_sha_url, args.expected_sha)
            health = _get_json(f"{base_url}/api/health")
            version = _get_json(f"{base_url}/api/version")
            frontend_marker = (
                None
                if args.legacy_without_frontend_marker
                else _get_json(f"{base_url}/version.json")
            )
            if (
                selected_sha
                and frontend_marker is not None
                and deployment_matches_sha(health, version, frontend_marker, selected_sha)
            ):
                print(
                    f"Deployment {args.expected_sha} was superseded by healthy "
                    f"revision {selected_sha}; skipping stale verification."
                )
                return 0
            last_errors = validate(
                health,
                version,
                frontend_marker,
                args.expected_sha,
                verify_frontend=not args.legacy_without_frontend_marker,
            )
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
