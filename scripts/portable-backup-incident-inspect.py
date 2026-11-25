#!/usr/bin/env python3
"""Read-only, sanitized diagnosis of the installed hourly backup job.

Run on the VPS as root. No backup, network request, service operation, or file write.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import uuid


PROFILE = Path("/etc/rsl-backup/job.json")
STATE = Path("/var/lib/rsl-backup")
UNITS = ("rsl-production-backup.timer", "rsl-production-backup.service")
CHECKS = ("database_backup", "encrypted_bundle", "encrypted_remote_readback",
          "remote_receipt_readback", "independent_monitor_acknowledged",
          "independent_failure_acknowledged")
CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
MAX_RUNS = 2000


def private_regular(path, limit):
    meta = path.lstat()
    job_uid = pwd.getpwnam("rsl-backup").pw_uid
    if not stat.S_ISREG(meta.st_mode) or meta.st_uid not in (0, job_uid):
        raise ValueError("unsafe_file")
    if meta.st_mode & 0o077 or meta.st_size > limit:
        raise ValueError("unsafe_file")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        opened = os.fstat(source.fileno())
        if (opened.st_ino != meta.st_ino or opened.st_dev != meta.st_dev
                or opened.st_uid != meta.st_uid or opened.st_mode & 0o077
                or not stat.S_ISREG(opened.st_mode)):
            raise ValueError("changed_file")
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("oversize_file")
    return raw


def job_root():
    value = json.loads(private_regular(PROFILE, 4096))
    if not isinstance(value, dict) or value.get("root") != str(STATE):
        raise ValueError("unexpected_job_root")
    meta = STATE.lstat()
    runs = STATE / "runs"
    runs_meta = runs.lstat()
    if not all(stat.S_ISDIR(item.st_mode) and not item.st_mode & 0o077
               for item in (meta, runs_meta)):
        raise ValueError("unsafe_job_root")
    if meta.st_uid != pwd.getpwnam("rsl-backup").pw_uid or runs_meta.st_uid != meta.st_uid:
        raise ValueError("unsafe_job_root")
    return runs


def safe_time(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.astimezone(timezone.utc).isoformat() if stamp.tzinfo else None
    except ValueError:
        return None


def summarize(path):
    value = json.loads(private_regular(path, 8192))
    if not isinstance(value, dict):
        raise ValueError("invalid_report")
    checks = value.get("checks")
    if not isinstance(checks, dict):
        checks = {}
    error = value.get("error")
    return {
        "started_at": safe_time(value.get("timestamp")),
        "finished_at": safe_time(value.get("finished_at")),
        "result": value.get("result") if value.get("result") in ("PASS", "STOP", "INCOMPLETE") else "UNKNOWN",
        "error_code": error if isinstance(error, str) and CODE.fullmatch(error) else None,
        "checks": {name: checks[name] if type(checks.get(name)) is bool else None for name in CHECKS},
    }


def reports(runs):
    entries = list(runs.iterdir())
    if len(entries) > MAX_RUNS:
        raise ValueError("too_many_runs")
    result = []
    unreadable = 0
    for entry in entries:
        try:
            if str(uuid.UUID(entry.name)) != entry.name or not stat.S_ISDIR(entry.lstat().st_mode):
                continue
            report = summarize(entry / "job.json")
            if report["started_at"]:
                result.append((report, entry))
            else:
                unreadable += 1
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            unreadable += 1
    return sorted(result, key=lambda item: item[0]["started_at"], reverse=True), unreadable


def phase_reports(directory):
    outcome = {}
    for phase in ("backup", "seal", "publish", "catalog"):
        path = directory / f"{phase}.json"
        try:
            value = json.loads(private_regular(path, 8192))
            if not isinstance(value, dict) or value.get("phase") != phase:
                raise ValueError("invalid_phase_report")
            error = value.get("error")
            outcome[phase] = {"result": value["result"] if value.get("result") in
                              ("PASS", "STOP", "INCOMPLETE") else "UNKNOWN",
                              "error_code": error if isinstance(error, str) and CODE.fullmatch(error) else None}
        except FileNotFoundError:
            outcome[phase] = {"result": "MISSING", "error_code": None}
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            outcome[phase] = {"result": "UNREADABLE", "error_code": None}
    return outcome


def detailed(item):
    if item is None:
        return None
    report, directory = item
    return {**report, "phase_reports": phase_reports(directory)}


def unit_state(unit):
    completed = subprocess.run(
        ["systemctl", "show", unit, "--property=LoadState,ActiveState,SubState,Result,ExecMainStatus,LastTriggerUSec"],
        capture_output=True, timeout=10, check=False,
    )
    if completed.returncode or len(completed.stdout) > 4096:
        return {"query_ok": False}
    allowed = {"LoadState", "ActiveState", "SubState", "Result", "ExecMainStatus", "LastTriggerUSec"}
    fields = {}
    for line in completed.stdout.decode("utf-8", "replace").splitlines():
        key, _, value = line.partition("=")
        if key in allowed:
            fields[key] = value if len(value) <= 64 and re.fullmatch(r"[A-Za-z0-9 :+._-]*", value) else None
    return {"query_ok": True, "fields": fields}


def inspect():
    result = {"schema": 1, "phase": "backup-incident-readonly", "timestamp": datetime.now(timezone.utc).isoformat(),
              "inspection_status": "STOP", "backup_status": "UNKNOWN", "checks": {}}
    try:
        if os.geteuid() != 0:
            raise ValueError("root_required")
        runs = job_root()
        entries, unreadable = reports(runs)
        usage = shutil.disk_usage(STATE)
        units = {unit: unit_state(unit) for unit in UNITS}
        last_success = next((item for item in entries if item[0]["result"] == "PASS"), None)
        latest = entries[0] if entries else None
        first_failure = next((item for item in reversed(entries)
                              if item[0]["result"] != "PASS"
                              and item[0]["started_at"] >= "2026-11-15T18:00:00+00:00"), None)
        result.update(inspection_status="PASS", backup_status=latest[0]["result"] if latest else "UNKNOWN",
                      total_reports=len(entries), unreadable_reports=unreadable,
                      latest=detailed(latest), first_failure_since_nov_15=detailed(first_failure),
                      last_success=detailed(last_success),
                      disk={"total_bytes": usage.total, "free_bytes": usage.free}, units=units)
        result["checks"] = {"reports_complete": bool(entries) and unreadable == 0,
                            "timer_query": units[UNITS[0]]["query_ok"],
                            "service_query": units[UNITS[1]]["query_ok"]}
        if not all(result["checks"].values()):
            result["inspection_status"] = "STOP"
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired):
        result["checks"] = {"inspection": False}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-readonly", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_readonly:
        print('{"phase":"backup-incident-readonly","inspection_status":"STOP","checks":{"confirmation":false}}')
        return 2
    result = inspect()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["inspection_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
