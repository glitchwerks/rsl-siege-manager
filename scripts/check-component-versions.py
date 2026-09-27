#!/usr/bin/env python3
"""Enforce per-component version bumps for external-surface changes."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


@dataclass(frozen=True)
class Component:
    name: str
    version_path: str


COMPONENTS = {
    "siege-api": Component("siege-api", "backend/VERSION"),
    "siege-frontend": Component("siege-frontend", "frontend/package.json"),
    "siege-bot": Component("siege-bot", "bot/VERSION"),
}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def _version_from_text(component: Component, text: str) -> str:
    if component.version_path.endswith("package.json"):
        return str(json.loads(text)["version"])
    return text.strip()


def _version_at(component: Component, revision: str | None = None) -> str:
    if revision:
        text = _git("show", f"{revision}:{component.version_path}")
    else:
        text = Path(component.version_path).read_text(encoding="utf-8")
    return _version_from_text(component, text)


def _semver(version: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(version)
    if not match:
        raise ValueError(f"{version!r} is not a stable MAJOR.MINOR.PATCH version")
    return tuple(int(part) for part in match.groups())


def _vite_names_in_base(base: str) -> set[str]:
    try:
        output = _git(
            "grep", "-h", "-o", "-E", r"VITE_[A-Z0-9_]+", base, "--", "frontend"
        )
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 1:
            return set()
        raise
    return set(output.splitlines())


def _vite_names_in_worktree() -> set[str]:
    names: set[str] = set()
    excluded = {"node_modules", "dist", ".git"}
    for path in Path("frontend").rglob("*"):
        if not path.is_file() or excluded.intersection(path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        names.update(re.findall(r"VITE_[A-Z0-9_]+", text))
    return names


def affected_components(base: str) -> dict[str, list[str]]:
    paths = [p for p in _git("diff", "--name-only", f"{base}...HEAD").splitlines() if p]
    affected: dict[str, list[str]] = {name: [] for name in COMPONENTS}

    for path in paths:
        if path.startswith(
            ("backend/app/api/", "backend/app/schemas/", "backend/app/auth/")
        ) or path in {"backend/app/main.py", "backend/app/rate_limit.py"}:
            affected["siege-api"].append(path)
        if path in {"bot/app/http_api.py", "bot/app/discord_client.py", "bot/app/__init__.py"}:
            affected["siege-bot"].append(path)
        if path == "frontend/src/App.tsx" or path.startswith("frontend/src/pages/"):
            affected["siege-frontend"].append(path)

    if _vite_names_in_base(base) != _vite_names_in_worktree():
        affected["siege-frontend"].append("VITE_* environment contract")

    return {name: sorted(set(items)) for name, items in affected.items() if items}


def bypass_reason(body: str) -> str | None:
    match = re.search(
        r"^## Version bump bypass[ \t]*\r?\n([\s\S]*?)(?=^## |\Z)",
        body,
        re.MULTILINE,
    )
    if not match:
        return None
    reason = re.sub(r"<!--.*?-->", "", match.group(1), flags=re.DOTALL).strip()
    plain_reason = re.sub(r"[^A-Za-z0-9]+", " ", reason).strip()
    if (
        not re.search(r"[A-Za-z0-9]{3}", plain_reason)
        or reason.lower() in {"", "n/a", "na", "none", "not applicable"}
    ):
        return None
    return reason


def check(base: str, allow_bypass: bool = False, pr_body: str = "") -> list[str]:
    affected = affected_components(base)
    if allow_bypass:
        if not bypass_reason(pr_body):
            return [
                "skip-version-bump requires a concrete reason under the "
                "'Version bump bypass' PR heading"
            ]
        print("Version bump bypass accepted; the audit workflow will record its use.")

    errors: list[str] = []
    versions: dict[str, tuple[tuple[int, int, int], tuple[int, int, int], str, str]] = {}
    for name, component in COMPONENTS.items():
        old = _version_at(component, base)
        new = _version_at(component)
        try:
            old_semver, new_semver = _semver(old), _semver(new)
        except ValueError as exc:
            errors.append(f"{name}: {exc}")
            continue
        versions[name] = (old_semver, new_semver, old, new)
        if new_semver < old_semver:
            errors.append(
                f"{name}: {component.version_path} must not move backward "
                f"({old} -> {new})"
            )

    for name, paths in affected.items():
        if allow_bypass:
            continue
        if name not in versions:
            continue
        component = COMPONENTS[name]
        old_semver, new_semver, old, new = versions[name]
        if new_semver <= old_semver:
            errors.append(
                f"{name}: external surface changed but {component.version_path} "
                f"did not advance ({old} -> {new}); flagged: {', '.join(paths)}"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="base commit SHA")
    parser.add_argument("--allow-bypass", action="store_true")
    args = parser.parse_args()

    errors = check(
        args.base,
        allow_bypass=args.allow_bypass,
        pr_body=os.environ.get("PR_BODY", ""),
    )
    if errors:
        print("Component version discipline failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Component version discipline passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
