#!/usr/bin/env python3
"""Repoint only the incident's SMB host, preserving a private rollback copy."""
import argparse
import configparser
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys

OLD = "100.126.80.92"
NEW_HOST = "server-nas"
EXPECTED_ADDRESS = "100.117.158.121"
PROFILE = Path("/srv/rsl-siege/backup-candidate/job.json")


def replacement(original):
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(original.decode())
    except (configparser.Error, UnicodeError):
        raise ValueError("invalid_config") from None
    if parser.sections() != ["backup"] or parser["backup"].get("type") != "smb":
        raise ValueError("invalid_smb_config")
    if parser["backup"].get("host") == NEW_HOST:
        raise ValueError("host_already_magicdns")
    if parser["backup"].get("host") != OLD:
        raise ValueError("unexpected_host")
    pattern = re.compile(rb"(?im)^([ \t]*host[ \t]*=[ \t]*)100\.126\.80\.92([ \t]*\r?$)")
    updated, count = pattern.subn(lambda match: match.group(1) + NEW_HOST.encode() + match.group(2), original)
    if count != 1:
        raise ValueError("host_line_not_unique")
    return updated


def preserve_and_replace(path, info, original, updated):
    backup = path.with_name(path.name + ".before-nas-repoint")
    temporary = path.with_name(path.name + f".repoint-{os.getpid()}")
    with os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
        os.fchown(stream.fileno(), info.st_uid, info.st_gid)
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
            os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != original or path.stat().st_ino != info.st_ino:
            raise ValueError("config_changed_during_update")
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def backup_service_idle():
    result = subprocess.run(
        ["systemctl", "show", "rsl-production-backup.service", "-p", "LoadState", "-p", "ActiveState", "--no-pager"],
        capture_output=True, text=True, timeout=8, check=False,
    )
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return result.returncode == 0 and fields.get("LoadState") == "loaded" and fields.get("ActiveState") in {"inactive", "failed"}


def new_nas_name_status():
    try:
        result = subprocess.run(
            ["getent", "ahostsv4", NEW_HOST], capture_output=True, text=True, timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "magicdns_query_failed"
    addresses = {line.split()[0] for line in result.stdout.splitlines() if line.split()}
    if result.returncode or addresses != {EXPECTED_ADDRESS}:
        return "magicdns_address_mismatch"
    try:
        with socket.create_connection((NEW_HOST, 445), timeout=5):
            return "ready"
    except OSError:
        return "magicdns_smb_unreachable"


@contextmanager
def backup_job_lock(root, owner=0):
    if not root.is_absolute() or any(part.is_symlink() for part in (root, *root.parents)):
        raise ValueError("unsafe_backup_root")
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o077:
        raise ValueError("unsafe_backup_root")
    path = root / "job.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        locked = os.fstat(descriptor)
        if not stat.S_ISREG(locked.st_mode) or locked.st_uid != owner or locked.st_mode & 0o077:
            raise ValueError("unsafe_job_lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def main():
    phase = "preflight"
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        operation = parser.add_mutually_exclusive_group(required=True)
        operation.add_argument("--inspect", action="store_true", help="Read-only, sanitized preflight diagnosis")
        operation.add_argument("--confirm-repoint", action="store_true", help="Apply the guarded host repair")
        args = parser.parse_args()
        if os.geteuid() != 0:
            raise ValueError("root_required")
        profile_info = PROFILE.lstat()
        if not stat.S_ISREG(profile_info.st_mode) or profile_info.st_uid != 0 or profile_info.st_mode & 0o077:
            raise ValueError("unsafe_profile")
        profile = json.loads(PROFILE.read_text())
        phase = "config"
        path = Path(profile["remote_config"])
        if not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("unsafe_path")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError("unsafe_config")
        phase = "job_lock"
        with backup_job_lock(Path(profile["root"])):
            phase = "service"
            if not backup_service_idle():
                raise ValueError("backup_not_idle")
            phase = "configured_host"
            original = path.read_bytes()
            if len(original) > 16384:
                raise ValueError("oversize_config")
            updated = replacement(original)
            phase = "magicdns"
            name_status = new_nas_name_status()
            if name_status != "ready":
                raise ValueError(name_status)
            if args.inspect:
                print(json.dumps({"status": "PASS", "phase": "inspect", "checks": {
                    "configured_host_expected": True, "backup_service_idle": True,
                    "magicdns_matches_new_nas": True, "smb_reachable_by_name": True,
                }}))
                return 0
            phase = "preserve_original"
            preserve_and_replace(path, info, original, updated)
        phase = "repoint_smb_host"
        print(json.dumps({"status": "PASS", "phase": "repoint_smb_host", "backup_preserved": True, "host_updated": True}))
        return 0
    except Exception as error:
        safe = {"root_required", "unsafe_profile", "unsafe_path", "unsafe_config", "unsafe_backup_root",
                "unsafe_job_lock", "backup_not_idle", "oversize_config", "invalid_config", "invalid_smb_config",
                "host_already_magicdns", "unexpected_host", "host_line_not_unique", "magicdns_query_failed",
                "magicdns_address_mismatch", "magicdns_smb_unreachable", "config_changed_during_update"}
        code = str(error) if isinstance(error, ValueError) and str(error) in safe else "operation_failed"
        print(json.dumps({"status": "STOP", "phase": phase, "error_code": code,
                          "config_may_need_inspection": phase in {"preserve_original", "repoint_smb_host"}}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
