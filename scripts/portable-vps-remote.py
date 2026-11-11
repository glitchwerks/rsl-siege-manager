#!/usr/bin/env python3
"""Restricted SSH forced command for reviewed VPS release operations."""

import os
import stat
import subprocess
from pathlib import Path


def private_directory(path):
    try:
        metadata = Path(path).lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == 0
        and metadata.st_mode & 0o077 == 0
    )


def succeeds(arguments):
    try:
        result = subprocess.run(arguments, capture_output=True, timeout=10, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def access_check():
    return all(
        private_directory(path)
        for path in ("/srv/rsl-siege", "/srv/rsl-siege/runtime", "/srv/rsl-siege/evidence")
    ) and all(
        succeeds(command)
        for command in (
            ["docker", "compose", "version"],
            ["docker", "info"],
            ["systemctl", "is-active", "--quiet", "rsl-production-backup.timer"],
        )
    )


def main():
    # The SSH key must be restricted to this absolute command in authorized_keys.
    # Never dispatch arbitrary SSH_ORIGINAL_COMMAND text to a shell.
    if os.environ.get("SSH_ORIGINAL_COMMAND") != "access-check":
        print("STOP")
        return 1
    if access_check():
        print("PASS")
        return 0
    print("STOP")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
