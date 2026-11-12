#!/usr/bin/env python3
"""Read-only live Alembic revision gate for a staged VPS release manifest."""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "vps_remote", Path(__file__).with_name("portable-vps-remote.py")
)
REMOTE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REMOTE)
REVISION_LINE = re.compile(r"([a-zA-Z0-9_]{1,64})(?: \(head\))?\Z")
MAX_MANIFEST = 16 * 1024


def private_path(path, directory=False):
    try:
        info = path.lstat()
    except OSError:
        return False
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    return (expected(info.st_mode) and info.st_uid == os.geteuid()
            and not info.st_mode & 0o077)


def readable_config(path):
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o022


def current_revision(project, service):
    command = [
        "docker", "compose", "--project-directory", str(project),
        "--env-file", str(project / "stack.env"),
        "-f", str(project / "compose.yml"),
        "-f", str(project / "compose.mom.yml"),
        "exec", "-T", service, "/app/.venv/bin/alembic", "current",
    ]
    environment = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": "/root",
        "LANG": "C",
        "DOCKER_HOST": "unix:///var/run/docker.sock",
    }
    try:
        result = subprocess.run(
            command, env=environment, capture_output=True, text=True,
            timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    match = REVISION_LINE.fullmatch(lines[0])
    return match.group(1) if match else None


def inspect(manifest, project, expected_digest):
    checks = {
        "root_execution": os.geteuid() == 0,
        "manifest_private": private_path(manifest),
        "project_private": private_path(project, directory=True),
        "stack_env_private": private_path(project / "stack.env"),
        "compose_files_reviewed": all(readable_config(project / name)
                                      for name in ("compose.yml", "compose.mom.yml")),
    }
    if not all(checks.values()):
        return checks
    try:
        if manifest.stat().st_size > MAX_MANIFEST:
            checks["manifest_bounded"] = False
            return checks
        raw = manifest.read_bytes()
        checks["manifest_bounded"] = 0 < len(raw) <= MAX_MANIFEST
        checks["manifest_digest"] = (checks["manifest_bounded"]
                                     and bool(REMOTE.SHA256.fullmatch(expected_digest))
                                     and hashlib.sha256(raw).hexdigest() == expected_digest)
        checks["manifest_shape"] = checks["manifest_digest"] and REMOTE.valid_manifest(raw)
        if not checks["manifest_shape"]:
            return checks
        data = json.loads(raw, object_pairs_hook=REMOTE.no_duplicate_keys)
    except (OSError, ValueError, UnicodeError, TypeError, RecursionError):
        checks["manifest_read"] = False
        return checks
    for database, service in (("siege", "backend"), ("mom", "mom")):
        expected = data["migrations"][database]["from_revision"]
        actual = current_revision(project, service)
        checks[f"{database}_live_revision"] = actual is not None and actual == expected
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--project-directory", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        checks = inspect(args.manifest, args.project_directory, args.manifest_sha256)
    except (OSError, ValueError, UnicodeError, TypeError):
        checks = {"baseline_inspection": False}
    result = {"phase": "migration-baseline", "status": "PASS" if checks and all(checks.values()) else "STOP",
              "checks": checks}
    try:
        if not private_path(args.report.parent, directory=True):
            raise OSError("unsafe report directory")
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as report:
            json.dump(result, report, sort_keys=True)
            report.write("\n")
            report.flush()
            os.fsync(report.fileno())
    except OSError:
        print("STOP report_write_failed")
        return 2
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
