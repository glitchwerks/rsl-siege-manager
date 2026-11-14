#!/usr/bin/env python3
"""Phase-gated installation of the restricted GitHub VPS access key."""

import argparse
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


AUTHORIZED_KEYS = Path("/root/.ssh/authorized_keys")
REMOTE_SCRIPT = Path("/opt/rsl-siege-manager/scripts/portable-vps-remote.py")
SOURCE_SCRIPT = Path(__file__).with_name("portable-vps-remote.py")
KEY_COMMENT = "github-vps-deploy"
FORCED_COMMAND = f'restrict,command="/usr/bin/python3 {REMOTE_SCRIPT}"'
OLD_REMOTE_SHA256S = (
    "f965a4edc244c7ece696bbeb30b650f67c3c21e82584c44185ca57a94db31da7",
    "70030e03fa566033397d876bf408883b86e0de8d626edb177feac839907b28b4",
    "379f6fa1709fd0490abc4820fddf1826492426349d239148a30d5dafa92dbf30",
    "4664f652bcc15e7ba2f0c25fc3214d314e2b64fe5edacee960c8fa3ea9983fcb",
    "c3ffb5e3351e3089bf75d4f77d2e905d1d61b244d0ce43eed0ebb7025dba0c2b",
)
NEW_REMOTE_SHA256 = "7cbc65af08a6c01bacc3557532c2885cdeb6d66952d21d4d8aa45e9d04452c05"


def script_backup_path():
    return Path("/root/portable-vps-remote.py.before-release-backup")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked_file(path, uid=0):
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_uid == uid and not (info.st_mode & 0o022)


def private_file(path):
    try:
        info = path.lstat()
    except OSError:
        return False
    return checked_file(path) and not (info.st_mode & 0o077)


def protected_directory(path):
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not (info.st_mode & 0o022)


def destination_directories():
    return (Path("/opt"), REMOTE_SCRIPT.parent.parent, REMOTE_SCRIPT.parent)


def destination_path_safe():
    for path in destination_directories():
        if path.is_symlink():
            return False
        if path.exists() and not protected_directory(path):
            return False
        if path == Path("/opt") and not protected_directory(path):
            return False
    return True


def prepare_destination():
    for path in destination_directories():
        if not path.exists():
            path.mkdir(mode=0o755)
        if not protected_directory(path):
            return False
    return True


def public_key(path):
    fields = path.read_text().strip().split()
    if len(fields) != 3 or fields[0] != "ssh-ed25519" or fields[2] != KEY_COMMENT:
        raise ValueError("dedicated public key format")
    result = subprocess.run(
        ["ssh-keygen", "-lf", str(path)], capture_output=True, timeout=10, check=False
    )
    if result.returncode != 0:
        raise ValueError("dedicated public key validation")
    return " ".join(fields)


def key_entry(key):
    return f"{FORCED_COMMAND} {key}"


def preflight():
    checks = {
        "root_execution": os.geteuid() == 0,
        "authorized_keys_regular_private": private_file(AUTHORIZED_KEYS),
        "ssh_directory_private": False,
        "script_source_present": SOURCE_SCRIPT.is_file(),
        "script_destination_path_safe": destination_path_safe(),
    }
    try:
        info = AUTHORIZED_KEYS.parent.lstat()
        checks["ssh_directory_private"] = (
            stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not (info.st_mode & 0o077)
        )
    except OSError:
        pass
    return checks


def atomic_write(path, data, mode):
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(key):
    checks = preflight()
    checks["public_key_valid"] = bool(key)
    if not all(checks.values()):
        return checks
    current = AUTHORIZED_KEYS.read_bytes()
    entry = key_entry(key).encode()
    lines = current.splitlines()
    checks["no_existing_deploy_key"] = not any(KEY_COMMENT.encode() in line for line in lines)
    script_data = SOURCE_SCRIPT.read_bytes()
    checks["script_destination_available"] = (
        (not REMOTE_SCRIPT.exists() and not REMOTE_SCRIPT.is_symlink())
        or (checked_file(REMOTE_SCRIPT) and REMOTE_SCRIPT.read_bytes() == script_data)
    )
    if not all(checks.values()):
        return checks
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = AUTHORIZED_KEYS.with_name(f"authorized_keys.before-github-deploy.{stamp}")
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(current)
        stream.flush()
        os.fsync(stream.fileno())
    checks["authorized_keys_backup"] = private_file(backup) and backup.read_bytes() == current
    if not checks["authorized_keys_backup"]:
        return checks
    try:
        checks["script_destination_directory_ready"] = prepare_destination()
        if not checks["script_destination_directory_ready"]:
            return checks
        if not REMOTE_SCRIPT.exists():
            atomic_write(REMOTE_SCRIPT, script_data, 0o644)
        checks["script_installed"] = checked_file(REMOTE_SCRIPT) and REMOTE_SCRIPT.read_bytes() == script_data
        if not checks["script_installed"]:
            return checks
        updated = current + (b"" if not current or current.endswith(b"\n") else b"\n") + entry + b"\n"
        atomic_write(AUTHORIZED_KEYS, updated, 0o600)
        checks["restricted_key_installed"] = AUTHORIZED_KEYS.read_bytes() == updated
    except OSError:
        checks["installation"] = False
    if not checks.get("restricted_key_installed"):
        # Restore only if the key file was changed by this operation.
        if AUTHORIZED_KEYS.read_bytes() != current:
            atomic_write(AUTHORIZED_KEYS, current, 0o600)
        checks["authorized_keys_restored"] = AUTHORIZED_KEYS.read_bytes() == current
    return checks


def verify(key):
    checks = preflight()
    checks["public_key_valid"] = bool(key)
    if not all(checks.values()):
        return checks
    entry = key_entry(key).encode()
    lines = AUTHORIZED_KEYS.read_bytes().splitlines()
    checks["restricted_key_exactly_once"] = lines.count(entry) == 1
    checks["no_other_deploy_key"] = sum(KEY_COMMENT.encode() in line for line in lines) == 1
    checks["reviewed_script_installed"] = (
        checked_file(REMOTE_SCRIPT) and REMOTE_SCRIPT.read_bytes() == SOURCE_SCRIPT.read_bytes()
    )
    if checks["reviewed_script_installed"]:
        environment = dict(os.environ, SSH_ORIGINAL_COMMAND="access-check")
        result = subprocess.run(
            ["/usr/bin/python3", str(REMOTE_SCRIPT)], env=environment,
            capture_output=True, timeout=30, check=False,
        )
        checks["local_read_only_baseline"] = result.returncode == 0 and result.stdout.strip() == b"PASS"
        environment["SSH_ORIGINAL_COMMAND"] = "id"
        result = subprocess.run(
            ["/usr/bin/python3", str(REMOTE_SCRIPT)], env=environment,
            capture_output=True, timeout=10, check=False,
        )
        checks["arbitrary_command_denied"] = result.returncode != 0 and result.stdout.strip() == b"STOP"
    return checks


def update_script(key):
    checks = preflight()
    checks["public_key_valid"] = bool(key)
    if not all(checks.values()):
        return checks
    entry = key_entry(key).encode()
    checks["restricted_key_exactly_once"] = AUTHORIZED_KEYS.read_bytes().splitlines().count(entry) == 1
    checks["old_script_reviewed"] = checked_file(REMOTE_SCRIPT) and digest(REMOTE_SCRIPT.read_bytes()) in OLD_REMOTE_SHA256S
    checks["new_script_reviewed"] = digest(SOURCE_SCRIPT.read_bytes()) == NEW_REMOTE_SHA256
    backup = script_backup_path()
    checks["backup_path_unused"] = not backup.exists() and not backup.is_symlink()
    if not all(checks.values()):
        return checks
    old_data = REMOTE_SCRIPT.read_bytes()
    new_data = SOURCE_SCRIPT.read_bytes()
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(old_data)
        stream.flush()
        os.fsync(stream.fileno())
    checks["old_script_backup"] = private_file(backup) and backup.read_bytes() == old_data
    if not checks["old_script_backup"]:
        return checks
    try:
        atomic_write(REMOTE_SCRIPT, new_data, 0o644)
        checks["updated_script_installed"] = checked_file(REMOTE_SCRIPT) and REMOTE_SCRIPT.read_bytes() == new_data
    except OSError:
        checks["script_update"] = False
    if not checks.get("updated_script_installed"):
        atomic_write(REMOTE_SCRIPT, old_data, 0o644)
        checks["old_script_restored"] = REMOTE_SCRIPT.read_bytes() == old_data
    return checks


def restore_script(key):
    checks = preflight()
    checks["public_key_valid"] = bool(key)
    if not all(checks.values()):
        return checks
    entry = key_entry(key).encode()
    checks["restricted_key_exactly_once"] = AUTHORIZED_KEYS.read_bytes().splitlines().count(entry) == 1
    checks["updated_script_present"] = checked_file(REMOTE_SCRIPT) and digest(REMOTE_SCRIPT.read_bytes()) == NEW_REMOTE_SHA256
    checks["new_script_reviewed"] = digest(SOURCE_SCRIPT.read_bytes()) == NEW_REMOTE_SHA256
    backup = script_backup_path()
    checks["reviewed_old_script_backup"] = private_file(backup) and digest(backup.read_bytes()) in OLD_REMOTE_SHA256S
    if not all(checks.values()):
        return checks
    old_data = backup.read_bytes()
    atomic_write(REMOTE_SCRIPT, old_data, 0o644)
    checks["old_script_restored"] = REMOTE_SCRIPT.read_bytes() == old_data
    return checks


def rollback(key):
    checks = preflight()
    checks["public_key_valid"] = bool(key)
    if not all(checks.values()):
        return checks
    entry = key_entry(key).encode()
    current = AUTHORIZED_KEYS.read_bytes()
    lines = current.splitlines(keepends=True)
    matches = [line for line in lines if line.rstrip(b"\r\n") == entry]
    checks["one_restricted_key_found"] = len(matches) == 1
    if not checks["one_restricted_key_found"]:
        return checks
    updated = b"".join(line for line in lines if line.rstrip(b"\r\n") != entry)
    atomic_write(AUTHORIZED_KEYS, updated, 0o600)
    checks["restricted_key_removed"] = AUTHORIZED_KEYS.read_bytes() == updated
    # Keep the script and backup as evidence; they cannot grant access without the key.
    return checks


def replace_restricted_key(old_key, new_key, phase):
    checks = preflight()
    checks["old_public_key_valid"] = bool(old_key)
    checks["new_public_key_valid"] = bool(new_key) and new_key != old_key
    if not all(checks.values()):
        return checks
    current = AUTHORIZED_KEYS.read_bytes()
    lines = current.splitlines(keepends=True)
    old_entry = key_entry(old_key).encode()
    new_entry = key_entry(new_key).encode()
    checks["old_restricted_key_exactly_once"] = sum(
        line.rstrip(b"\r\n") == old_entry for line in lines
    ) == 1
    checks["new_restricted_key_absent"] = not any(
        line.rstrip(b"\r\n") == new_entry for line in lines
    )
    checks["no_other_deploy_key"] = sum(KEY_COMMENT.encode() in line for line in lines) == 1
    if not all(checks.values()):
        return checks
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = AUTHORIZED_KEYS.with_name(f"authorized_keys.before-github-deploy-{phase}.{stamp}")
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(current)
        stream.flush()
        os.fsync(stream.fileno())
    checks["authorized_keys_backup"] = private_file(backup) and backup.read_bytes() == current
    if not checks["authorized_keys_backup"]:
        return checks
    updated = b"".join(
        new_entry + line[len(old_entry):] if line.rstrip(b"\r\n") == old_entry else line
        for line in lines
    )
    atomic_write(AUTHORIZED_KEYS, updated, 0o600)
    checks["restricted_key_replaced"] = AUTHORIZED_KEYS.read_bytes() == updated
    if not checks["restricted_key_replaced"]:
        atomic_write(AUTHORIZED_KEYS, current, 0o600)
        checks["authorized_keys_restored"] = AUTHORIZED_KEYS.read_bytes() == current
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("inspect", "install", "verify", "rollback", "update-script", "restore-script", "rotate-key", "restore-key"))
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--old-public-key", type=Path)
    parser.add_argument("--confirm", choices=("install", "rollback", "update-script", "restore-script", "rotate-key", "restore-key"))
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    checks = preflight()
    if args.phase != "inspect":
        try:
            key = public_key(args.public_key) if args.public_key else ""
        except (OSError, ValueError, subprocess.TimeoutExpired):
            key = ""
        try:
            old_key = public_key(args.old_public_key) if args.old_public_key else ""
        except (OSError, ValueError, subprocess.TimeoutExpired):
            old_key = ""
        if args.phase in ("install", "rollback", "update-script", "restore-script", "rotate-key", "restore-key") and args.confirm != args.phase:
            checks["explicit_phase_confirmation"] = False
        elif args.phase == "install":
            checks = install(key)
        elif args.phase == "verify":
            checks = verify(key)
        elif args.phase == "update-script":
            checks = update_script(key)
        elif args.phase == "restore-script":
            checks = restore_script(key)
        elif args.phase == "rotate-key":
            checks = replace_restricted_key(old_key, key, "rotation")
        elif args.phase == "restore-key":
            checks = replace_restricted_key(key, old_key, "restore")
        else:
            checks = rollback(key)
    status = "PASS" if all(checks.values()) else "STOP"
    report = {"phase": args.phase, "status": status, "checks": checks}
    try:
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(report, stream, sort_keys=True)
            stream.write("\n")
    except OSError:
        print("STOP report_write_failed")
        return 2
    print(status)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
