#!/usr/bin/env python3
"""Restricted SSH forced command for reviewed VPS release operations."""

import os
import hashlib
import json
import re
import stat
import subprocess
import sys
from pathlib import Path


EVIDENCE = Path("/srv/rsl-siege/evidence")
PROJECT = Path("/srv/rsl-siege")
REVIEWED_SOURCE = Path("/opt/rsl-siege-manager")
PREFLIGHT_SCRIPT = REVIEWED_SOURCE / "scripts/portable-vps-release-preflight.py"
PLAN_SCRIPT = REVIEWED_SOURCE / "scripts/portable-vps-release-pause-backup.py"
BACKUP_PROFILE = PROJECT / "runtime/release-backup-profile.json"
EXTERNAL_REPORTS = Path("/root/vps-release-reports")
STAGE_SCRIPT_BACKUP = REVIEWED_SOURCE / "scripts/portable-vps-remote.py.before-release-stage"
INITIAL_REMOTE_SHA256 = "f965a4edc244c7ece696bbeb30b650f67c3c21e82584c44185ca57a94db31da7"
MAX_MANIFEST = 16 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
RUN = re.compile(r"[1-9][0-9]{0,19}-[1-9][0-9]{0,2}\Z")
SOURCE_SHA = re.compile(r"[0-9a-f]{40}\Z")
RELEASE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}\Z")
REVISION = re.compile(r"[a-zA-Z0-9_]{1,64}\Z")


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


def reviewed_script(path):
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022


def private_file(path):
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077


def reviewed_checkout(source_sha):
    head = subprocess.run(
        ["git", "-C", str(REVIEWED_SOURCE), "rev-parse", "HEAD"],
        capture_output=True, timeout=10, check=False,
    )
    changed = subprocess.run(
        ["git", "-C", str(REVIEWED_SOURCE), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, timeout=10, check=False,
    )
    if head.returncode != 0 or head.stdout.strip() != source_sha.encode() or changed.returncode != 0:
        return False
    entries = changed.stdout.splitlines()
    if not entries:
        return True
    return (entries == [b"?? scripts/portable-vps-remote.py.before-release-stage"]
            and private_file(STAGE_SCRIPT_BACKUP)
            and hashlib.sha256(STAGE_SCRIPT_BACKUP.read_bytes()).hexdigest() == INITIAL_REMOTE_SHA256)


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


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def exact_keys(value, required, optional=()):
    return isinstance(value, dict) and set(required) <= set(value) <= set(required) | set(optional)


def image(value, name):
    prefix = f"ghcr.io/glitchwerks/{name}@sha256:"
    return isinstance(value, str) and value.startswith(prefix) and bool(
        SHA256.fullmatch(value[len(prefix):])
    )


def valid_migrations(value):
    if not exact_keys(value, ("siege", "mom", "order")):
        return False
    changed = []
    for name in ("siege", "mom"):
        item = value[name]
        if not exact_keys(item, ("mode", "from_revision", "to_revision")):
            return False
        before, after = item["from_revision"], item["to_revision"]
        if not all(isinstance(revision, str) and REVISION.fullmatch(revision)
                   and revision.lower() not in {"head", "heads", "base", "current"}
                   for revision in (before, after)):
            return False
        if item["mode"] == "none" and before == after:
            continue
        if item["mode"] == "upgrade" and before != after:
            changed.append(name)
            continue
        return False
    order = value["order"]
    return (isinstance(order, list)
            and all(type(name) is str and name in ("siege", "mom") for name in order)
            and len(order) == len(set(order)) and set(order) == set(changed))


def valid_manifest(raw):
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicate_keys)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return False
    if not exact_keys(data, ("schema_version", "release_id", "siege", "mom", "configuration", "migrations")):
        return False
    if type(data["schema_version"]) is not int or data["schema_version"] != 2:
        return False
    if not isinstance(data["release_id"], str) or not RELEASE_ID.fullmatch(data["release_id"]):
        return False
    siege, mom, config = data["siege"], data["mom"], data["configuration"]
    if not exact_keys(siege, ("source_sha", "ci_run_id", "api_image", "frontend_image"),
                      ("reference_bot_image",)):
        return False
    if not exact_keys(mom, ("source_sha", "ci_run_id", "image", "portable_auth", "scheduler_progress")):
        return False
    if not exact_keys(config, ("source_sha", "topology")):
        return False
    if not all(isinstance(item["source_sha"], str) and SOURCE_SHA.fullmatch(item["source_sha"])
               for item in (siege, mom, config)):
        return False
    if config["source_sha"] != siege["source_sha"] or config["topology"] != "mom":
        return False
    if any(type(item["ci_run_id"]) is not int or item["ci_run_id"] <= 0
           for item in (siege, mom)):
        return False
    if mom["portable_auth"] is not True or mom["scheduler_progress"] is not True:
        return False
    if not valid_migrations(data["migrations"]):
        return False
    return (image(siege["api_image"], "siege-api")
            and image(siege["frontend_image"], "siege-frontend")
            and image(mom["image"], "mom-bot")
            and ("reference_bot_image" not in siege
                 or image(siege["reference_bot_image"], "siege-bot")))


def write_private(path, data):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def stage_release(expected, run_id, stream):
    checks = {
        "root_execution": os.geteuid() == 0,
        "request_shape": bool(SHA256.fullmatch(expected)) and bool(RUN.fullmatch(run_id)),
        "read_only_baseline": access_check(),
    }
    report_dir = None
    if all(checks.values()):
        try:
            base = EVIDENCE / "release-stages"
            try:
                base.mkdir(mode=0o700)
            except FileExistsError:
                pass
            checks["stage_parent_private"] = private_directory(base)
            if checks["stage_parent_private"]:
                report_dir = base / run_id
                report_dir.mkdir(mode=0o700)
                checks["stage_report_private"] = private_directory(report_dir)
            if all(checks.values()):
                raw = stream.read(MAX_MANIFEST + 1)
                checks["input_bounded"] = 0 < len(raw) <= MAX_MANIFEST
                checks["manifest_digest"] = checks["input_bounded"] and (
                    hashlib.sha256(raw).hexdigest() == expected
                )
                checks["manifest_shape"] = checks["manifest_digest"] and valid_manifest(raw)
                if checks["manifest_shape"]:
                    write_private(report_dir / "candidate.json", raw)
                    checks["manifest_stored"] = True
        except (OSError, ValueError, TypeError):
            checks["stage_io"] = False
    status = "PASS" if all(checks.values()) else "STOP"
    report = {"phase": "stage-release", "status": status, "checks": checks}
    if report_dir is not None and checks.get("stage_report_private"):
        try:
            write_private(report_dir / "report.json", (json.dumps(report, sort_keys=True) + "\n").encode())
        except OSError:
            report = {"phase": "stage-release", "status": "STOP", "checks": {"report_write": False}}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


def preflight_release(expected, run_id):
    """Invoke only the reviewed, staged candidate's read-only preflight."""
    stage = EVIDENCE / "release-stages" / run_id
    checks = {
        "root_execution": os.geteuid() == 0,
        "request_shape": bool(SHA256.fullmatch(expected)) and bool(RUN.fullmatch(run_id)),
        "read_only_baseline": access_check(),
    }
    if all(checks.values()):
        try:
            checks["stage_private"] = private_directory(stage)
            candidate = stage / "candidate.json"
            staged_report = stage / "report.json"
            checks["stage_files_private"] = all(
                private_file(path) for path in (candidate, staged_report)
            ) if checks["stage_private"] else False
            checks["stage_files_bounded"] = (
                0 < candidate.stat().st_size <= MAX_MANIFEST
                and 0 < staged_report.stat().st_size <= 4096
            ) if checks["stage_files_private"] else False
            if checks["stage_files_bounded"]:
                raw = candidate.read_bytes()
                staged = json.loads(staged_report.read_bytes(), object_pairs_hook=no_duplicate_keys)
                checks["staged_candidate_bound"] = (
                    0 < len(raw) <= MAX_MANIFEST and hashlib.sha256(raw).hexdigest() == expected
                    and valid_manifest(raw) and isinstance(staged, dict)
                    and staged.get("phase") == "stage-release" and staged.get("status") == "PASS"
                    and isinstance(staged.get("checks"), dict) and bool(staged["checks"])
                    and all(value is True for value in staged["checks"].values())
                )
            if checks.get("staged_candidate_bound"):
                source_sha = json.loads(raw, object_pairs_hook=no_duplicate_keys)["configuration"]["source_sha"]
                checks["reviewed_checkout"] = reviewed_checkout(source_sha)
                checks["reviewed_script"] = reviewed_script(PREFLIGHT_SCRIPT)
            if checks.get("reviewed_checkout") and checks.get("reviewed_script"):
                EXTERNAL_REPORTS.mkdir(mode=0o700, exist_ok=True)
                checks["external_report_directory_private"] = private_directory(EXTERNAL_REPORTS)
            if checks.get("external_report_directory_private"):
                private_report = EXTERNAL_REPORTS / f"{run_id}-preflight.json"
                result = subprocess.run(
                    ["/usr/bin/python3", "-I", "-B", str(PREFLIGHT_SCRIPT),
                     "--project-directory", str(PROJECT), "--stage-run-id", run_id,
                     "--manifest-sha256", expected,
                     "--report", str(private_report)],
                    env={"PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1",
                         "PYTHONDONTWRITEBYTECODE": "1", "PYTHONSAFEPATH": "1"},
                    capture_output=True, timeout=300, check=False,
                )
                evidence = json.loads(private_report.read_bytes(), object_pairs_hook=no_duplicate_keys)
                checks["preflight_passed"] = (
                    result.returncode == 0 and result.stdout.strip() == b"PASS"
                    and isinstance(evidence, dict) and evidence.get("phase") == "release-preflight"
                    and evidence.get("status") == "PASS" and isinstance(evidence.get("checks"), dict)
                    and bool(evidence["checks"]) and all(value is True for value in evidence["checks"].values())
                )
        except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
            checks["preflight_execution"] = False
    status = "PASS" if all(checks.values()) else "STOP"
    report = {"phase": "release-preflight-dispatch", "status": status, "checks": checks}
    if checks.get("stage_private"):
        try:
            write_private(stage / "preflight-dispatch-report.json", (json.dumps(report, sort_keys=True) + "\n").encode())
        except OSError:
            report = {"phase": "release-preflight-dispatch", "status": "STOP", "checks": {"report_write": False}}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


def plan_release(expected, run_id):
    """Record a candidate-bound pause plan without stopping a writer."""
    stage = EVIDENCE / "release-stages" / run_id
    checks = {
        "root_execution": os.geteuid() == 0,
        "request_shape": bool(SHA256.fullmatch(expected)) and bool(RUN.fullmatch(run_id)),
        "read_only_baseline": access_check(),
    }
    plan_sha256 = None
    if all(checks.values()):
        try:
            candidate = stage / "candidate.json"
            checks["stage_private"] = private_directory(stage)
            checks["candidate_private_bounded"] = (
                private_file(candidate) and 0 < candidate.stat().st_size <= MAX_MANIFEST
            ) if checks["stage_private"] else False
            if checks["candidate_private_bounded"]:
                raw = candidate.read_bytes()
                checks["candidate_digest"] = (
                    hashlib.sha256(raw).hexdigest() == expected and valid_manifest(raw)
                )
            if checks.get("candidate_digest"):
                source_sha = json.loads(raw, object_pairs_hook=no_duplicate_keys)["configuration"]["source_sha"]
                checks["reviewed_checkout"] = reviewed_checkout(source_sha)
                checks["reviewed_script"] = reviewed_script(PLAN_SCRIPT)
                checks["profile_private"] = private_file(BACKUP_PROFILE)
            if (checks.get("reviewed_checkout") and checks.get("reviewed_script")
                    and checks.get("profile_private")):
                EXTERNAL_REPORTS.mkdir(mode=0o700, exist_ok=True)
                checks["external_report_directory_private"] = private_directory(EXTERNAL_REPORTS)
            if checks.get("external_report_directory_private"):
                external = EXTERNAL_REPORTS / f"{run_id}-plan.json"
                result = subprocess.run(
                    ["/usr/bin/python3", "-I", "-B", str(PLAN_SCRIPT), "plan",
                     "--profile", str(BACKUP_PROFILE), "--stage-run-id", run_id,
                     "--manifest-sha256", expected, "--report", str(external)],
                    env={"PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1",
                         "PYTHONDONTWRITEBYTECODE": "1", "PYTHONSAFEPATH": "1"},
                    capture_output=True, timeout=300, check=False,
                )
                evidence = json.loads(external.read_bytes(), object_pairs_hook=no_duplicate_keys)
                checks["plan_passed"] = (
                    result.returncode == 0 and result.stdout.strip() == b"PASS"
                    and isinstance(evidence, dict) and evidence.get("phase") == "plan"
                    and evidence.get("status") == "PASS" and isinstance(evidence.get("checks"), dict)
                    and bool(evidence["checks"]) and all(value is True for value in evidence["checks"].values())
                )
            if checks.get("plan_passed"):
                plan_file = stage / "pause-plan.json"
                checks["plan_private_bounded"] = (
                    private_file(plan_file) and 0 < plan_file.stat().st_size <= 4096
                )
                if checks["plan_private_bounded"]:
                    plan_sha256 = hashlib.sha256(plan_file.read_bytes()).hexdigest()
        except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
            checks["plan_execution"] = False
    status = "PASS" if all(checks.values()) and plan_sha256 is not None else "STOP"
    report = {"phase": "release-pause-plan-dispatch", "status": status, "checks": checks}
    if status == "PASS":
        report["plan_sha256"] = plan_sha256
    if checks.get("stage_private"):
        try:
            write_private(stage / "plan-dispatch-report.json", (json.dumps(report, sort_keys=True) + "\n").encode())
        except OSError:
            report = {"phase": "release-pause-plan-dispatch", "status": "STOP", "checks": {"report_write": False}}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


def main():
    # The SSH key must be restricted to this absolute command in authorized_keys.
    # Never dispatch arbitrary SSH_ORIGINAL_COMMAND text to a shell.
    command = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if command == "access-check":
        if access_check():
            print("PASS")
            return 0
        print("STOP")
        return 1
    parts = command.split(" ")
    if len(parts) == 4 and parts[0] == "stage-release" and parts[3] == "confirm-stage":
        return stage_release(parts[1], parts[2], sys.stdin.buffer)
    if len(parts) == 4 and parts[0] == "preflight-release" and parts[3] == "confirm-readonly-preflight":
        return preflight_release(parts[1], parts[2])
    if len(parts) == 4 and parts[0] == "plan-release" and parts[3] == "confirm-readonly-plan":
        return plan_release(parts[1], parts[2])
    print("STOP")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
