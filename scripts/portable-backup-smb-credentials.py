#!/usr/bin/env python3
"""Rotate the production SMB backup login after a read-only candidate check.

Run interactively as root on the VPS. No password, account, domain, endpoint,
remote listing, or raw tool error is printed. This does not start a backup.
"""

import argparse
import configparser
import fcntl
import getpass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


PROFILE = Path("/srv/rsl-siege/backup-candidate/job.json")
SERVICE = "rsl-production-backup.service"


def private_file(path, limit):
    if not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("unsafe_path")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > limit:
        raise ValueError("unsafe_private_file")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("file_changed")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("oversize_private_file")
    return info, data


def service_idle():
    result = subprocess.run(
        ["systemctl", "show", SERVICE, "-p", "LoadState", "-p", "ActiveState", "--no-pager"],
        capture_output=True, text=True, timeout=8, check=False,
    )
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return result.returncode == 0 and fields.get("LoadState") == "loaded" and fields.get("ActiveState") in {"inactive", "failed"}


def read_state():
    _, raw_profile = private_file(PROFILE, 4096)
    profile = json.loads(raw_profile)
    root = Path(profile["root"])
    if not root.is_absolute() or any(part.is_symlink() for part in (root, *root.parents)):
        raise ValueError("unsafe_root")
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != 0 or root_info.st_mode & 0o077:
        raise ValueError("unsafe_root")
    path = Path(profile["remote_config"])
    info, original = private_file(path, 16384)
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(original.decode("utf-8"))
    if parser.sections() != ["backup"] or parser.defaults() or parser["backup"].get("type") != "smb":
        raise ValueError("invalid_smb_config")
    config = parser["backup"]
    if config.get("host") != "server-nas" or not config.get("user") or not config.get("pass"):
        raise ValueError("unexpected_smb_config")
    prefix = profile["prefix"]
    if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*", prefix):
        raise ValueError("invalid_prefix")
    return root, path, info, original, config, prefix.split("/", 1)[0]


def replace_field(original, field, value):
    pattern = re.compile(rb"(?im)^([ \t]*" + field.encode() + rb"[ \t]*=[ \t]*)([^\r\n]*)(\r?)$")
    updated, count = pattern.subn(lambda match: match.group(1) + value.encode() + match.group(3), original)
    if count != 1:
        raise ValueError("config_field_not_unique")
    return updated


def obscure(password):
    result = subprocess.run(["rclone", "obscure", "-"], input=password + "\n", capture_output=True,
                            text=True, timeout=10, check=False)
    value = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"[A-Za-z0-9_=-]{20,}", value):
        raise ValueError("obscure_failed")
    return value


def write_private(path, data, mode=0o600):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode), "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def probe_failure(stderr):
    """Return only a fixed diagnosis; raw rclone errors can contain private data."""
    value = stderr[:8192].lower()
    if any(marker in value for marker in (b"password must change", b"password change required",
                                           b"status_password_must_change", b"0xc0000224")):
        return "candidate_password_change_required"
    if any(marker in value for marker in (b"logon failure", b"invalid credentials", b"bad password",
                                           b"authentication failed", b"bad username")):
        return "candidate_authentication_failed"
    if any(marker in value for marker in (b"network name cannot be found", b"share not found")):
        return "candidate_share_not_found"
    if any(marker in value for marker in (b"access denied", b"permission denied", b"access is denied")):
        return "candidate_permission_denied"
    if any(marker in value for marker in (b"connection refused", b"network is unreachable", b"no route to host",
                                           b"i/o timeout", b"connection timed out", b"context deadline exceeded")):
        return "candidate_connection_failed"
    return "candidate_remote_read_unknown"


def probe(config_path, share):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("RCLONE", "AGE", "AWS", "AZURE", "PG", "SSH_AUTH_SOCK"))}
    result = subprocess.run(
        ["rclone", "lsd", f"backup:{share}", "--config", str(config_path), "--log-level", "ERROR",
         "--retries", "1", "--low-level-retries", "1", "--contimeout", "5s", "--timeout", "10s"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env=env, timeout=30, check=False,
    )
    if result.returncode:
        raise ValueError(probe_failure(result.stderr))


def run(apply):
    if os.geteuid() != 0:
        raise ValueError("root_required")
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError("interactive_tty_required")
    root, path, info, original, config, share = read_state()
    lock = root / "job.lock"
    lock_info, _ = private_file(lock, 1024)
    if lock_info.st_uid != 0:
        raise ValueError("unsafe_job_lock")
    with os.fdopen(os.open(lock, os.O_RDWR | os.O_NOFOLLOW), "rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not service_idle():
            raise ValueError("backup_not_idle")
        if not apply:
            if path.with_name(path.name + ".before-smb-credentials").exists():
                raise ValueError("private_destination_exists")
            return {"status": "PASS", "phase": "inspect", "checks": {
                "private_config": True, "job_idle": True, "smb_config_expected": True,
                "rollback_path_available": True,
            }}
        backup = path.with_name(path.name + ".before-smb-credentials")
        candidate = path.with_name(path.name + f".smb-candidate-{os.getpid()}")
        if backup.exists() or candidate.exists():
            raise ValueError("private_destination_exists")
        user = input("New SMB account name: ").strip()
        domain = input("NAS computer or AD domain (Enter retains current): ").strip() or config.get("domain", "WORKGROUP")
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", user) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", domain):
            raise ValueError("invalid_account_or_domain")
        password = getpass.getpass("New SMB password: ")
        confirm = getpass.getpass("Repeat SMB password: ")
        if not password or "\n" in password or "\r" in password or password != confirm:
            raise ValueError("password_not_confirmed")
        obscured = obscure(password)
        del password, confirm
        updated = replace_field(original, "user", user)
        updated = replace_field(updated, "pass", obscured)
        if "domain" in config:
            updated = replace_field(updated, "domain", domain)
        else:
            raise ValueError("domain_field_missing")
        try:
            write_private(candidate, updated, stat.S_IMODE(info.st_mode))
            probe(candidate, share)
            current_info, current = private_file(path, 16384)
            if (current_info.st_dev, current_info.st_ino) != (info.st_dev, info.st_ino) or current != original:
                raise ValueError("config_changed")
            write_private(backup, original, stat.S_IMODE(info.st_mode))
            os.replace(candidate, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            candidate.unlink(missing_ok=True)
    return {"status": "PASS", "phase": "update", "checks": {
        "candidate_remote_read": True, "rollback_preserved": True, "active_config_updated": True,
    }}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--inspect", action="store_true")
    group.add_argument("--confirm-update", action="store_true")
    args = parser.parse_args()
    try:
        report = run(args.confirm_update)
    except (OSError, ValueError, KeyError, TypeError, configparser.Error, subprocess.TimeoutExpired, BlockingIOError) as error:
        allowed = {"root_required", "interactive_tty_required", "unsafe_path", "unsafe_private_file",
                   "file_changed", "oversize_private_file", "unsafe_root", "invalid_smb_config",
                   "unexpected_smb_config", "invalid_prefix", "config_field_not_unique", "obscure_failed",
                   "unsafe_job_lock", "backup_not_idle", "private_destination_exists", "invalid_account_or_domain",
                   "password_not_confirmed", "candidate_password_change_required", "candidate_authentication_failed", "candidate_share_not_found",
                   "candidate_permission_denied", "candidate_connection_failed", "candidate_remote_read_unknown",
                   "config_changed", "domain_field_missing"}
        code = str(error) if isinstance(error, ValueError) and str(error) in allowed else "operation_failed"
        report = {"status": "STOP", "phase": "update" if args.confirm_update else "inspect", "error_code": code}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
