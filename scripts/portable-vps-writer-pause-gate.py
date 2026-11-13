#!/usr/bin/env python3
"""Read-only proof that both Compose writers stopped before a release backup."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import uuid


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("vps_remote", HERE / "portable-vps-remote.py")
REMOTE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REMOTE)
SPEC = importlib.util.spec_from_file_location("backup_gate", HERE / "portable-vps-backup-evidence.py")
BACKUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKUP)
PROJECT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")
CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")


def protected(path, owner, directory=False):
    try:
        if not path.is_absolute() or any(item.is_symlink() for item in (path, *path.parents)):
            return False
        info = path.lstat()
    except OSError:
        return False
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    return kind(info.st_mode) and info.st_uid == owner and not info.st_mode & 0o077


def read_private(path, owner, limit=65536):
    if not protected(path.parent, owner, directory=True) or not protected(path, owner):
        raise ValueError("unsafe file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_mode & 0o077 or info.st_size > limit:
            raise ValueError("unsafe file")
        content = source.read(limit + 1)
    if len(content) > limit:
        raise ValueError("oversized file")
    return content


def project_name(stack_env):
    content = read_private(stack_env, 0).decode("utf-8")
    lines = [line.split("=", 1)[1] for line in content.splitlines()
             if line.startswith("COMPOSE_PROJECT_NAME=")]
    if len(lines) != 1 or not PROJECT_NAME.fullmatch(lines[0]):
        return None
    return lines[0]


def docker_state(project, service):
    environment = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/root", "LANG": "C",
                   "DOCKER_HOST": "unix:///var/run/docker.sock"}
    def run(arguments):
        try:
            value = subprocess.run(arguments, env=environment, capture_output=True,
                                   text=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return value.stdout.strip() if value.returncode == 0 else None

    identifiers = run(["docker", "ps", "--all", "--no-trunc",
                       "--filter", f"label=com.docker.compose.project={project}",
                       "--filter", f"label=com.docker.compose.service={service}",
                       "--format", "{{.ID}}"])
    if identifiers is None:
        return None
    lines = identifiers.splitlines()
    if len(lines) != 1 or not CONTAINER_ID.fullmatch(lines[0]):
        return None
    raw = run(["docker", "inspect", "--format", "{{json .State}}", lines[0]])
    if raw is None:
        return None
    try:
        state = json.loads(raw, object_pairs_hook=REMOTE.no_duplicate_keys)
    except (ValueError, TypeError):
        return None
    if not isinstance(state, dict) or state.get("Running") is not False or state.get("Status") != "exited":
        return None
    finished = BACKUP.moment(state.get("FinishedAt"))
    return (lines[0], finished) if finished else None


def inspect(project, run_id, digest, backup_owner, backup_root, backup_path, now=None):
    checks = {
        "root_execution": os.geteuid() == 0,
        "request_shape": bool(REMOTE.RUN.fullmatch(run_id)) and bool(REMOTE.SHA256.fullmatch(digest)),
        "project_private": protected(project, 0, directory=True),
        "evidence_private": protected(project / "evidence", 0, directory=True),
    }
    stage = project / "evidence" / "release-stages" / run_id
    checks["stage_private"] = protected(stage, 0, directory=True)
    if not all(checks.values()):
        return checks
    try:
        raw = read_private(stage / "candidate.json", 0, 16384)
        stage_report = json.loads(read_private(stage / "report.json", 0),
                                  object_pairs_hook=REMOTE.no_duplicate_keys)
        checks["staged_manifest"] = (bool(raw) and hashlib.sha256(raw).hexdigest() == digest
                                     and REMOTE.valid_manifest(raw)
                                     and isinstance(stage_report, dict)
                                     and stage_report.get("phase") == "stage-release"
                                     and stage_report.get("status") == "PASS")
        if not checks["staged_manifest"]:
            return checks
        name = project_name(project / "stack.env")
        checks["project_identity"] = name is not None
        if not checks["project_identity"]:
            return checks
        stopped = {service: docker_state(name, service) for service in ("backend", "mom")}
        checks["both_writers_stopped"] = all(stopped.values())
        if not checks["both_writers_stopped"]:
            return checks
        checks["backup_owner_supported"] = backup_owner in {"root", "rsl-backup"}
        if not checks["backup_owner_supported"]:
            return checks
        backup_uid = pwd.getpwnam(backup_owner).pw_uid
        checks["backup_root_private"] = (protected(backup_root, backup_uid, directory=True)
                                         and protected(backup_root / "runs", backup_uid, directory=True))
        checks["managed_report_path"] = (checks["backup_root_private"]
                                         and backup_path.name == "job.json"
                                         and backup_path.parent.parent == backup_root / "runs")
        if not checks["managed_report_path"]:
            return checks
        backup = json.loads(read_private(backup_path, backup_uid),
                            object_pairs_hook=REMOTE.no_duplicate_keys)
        checks["managed_backup"] = (isinstance(backup, dict)
                                    and backup.get("schema") == 1
                                    and backup.get("phase") == "run"
                                    and backup.get("result") == "PASS"
                                    and isinstance(backup.get("run_id"), str)
                                    and str(uuid.UUID(backup["run_id"])) == backup["run_id"]
                                    and backup_path.parent.name == backup["run_id"]
                                    and isinstance(backup.get("checks"), dict)
                                    and all(backup["checks"].get(key) is True for key in BACKUP.JOB_CHECKS))
        if not checks["managed_backup"]:
            return checks
        started = BACKUP.moment(backup.get("timestamp"))
        finished = BACKUP.moment(backup.get("finished_at"))
        current = now or datetime.now(timezone.utc)
        checks["backup_after_pause"] = bool(
            started and finished and all(value[1] <= started for value in stopped.values())
            and started <= finished <= current and current - finished <= timedelta(hours=2)
        )
        # A second state read narrows the race between timestamp comparison and report creation.
        checks["writers_still_stopped"] = all(docker_state(name, service) == stopped[service]
                                                for service in ("backend", "mom"))
    except (OSError, ValueError, TypeError, UnicodeError, KeyError):
        checks["inspection"] = False
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-directory", required=True, type=Path)
    parser.add_argument("--stage-run-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--backup-owner", required=True, choices=("root", "rsl-backup"))
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--backup-report", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    checks = inspect(args.project_directory, args.stage_run_id, args.manifest_sha256,
                     args.backup_owner, args.backup_root, args.backup_report)
    result = {"phase": "writer-pause-gate", "status": "PASS" if checks and all(checks.values()) else "STOP",
              "checks": checks}
    try:
        if not protected(args.report.parent, 0, directory=True):
            raise OSError("unsafe report directory")
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(json.dumps(result, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        print("STOP report_write_failed")
        return 2
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
