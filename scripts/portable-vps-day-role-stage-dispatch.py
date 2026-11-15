#!/usr/bin/env python3
"""Stage one private day-role decision through the restricted VPS SSH command."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid


SOURCE = Path(__file__).with_name("portable-vps-access-check.py")
SPEC = importlib.util.spec_from_file_location("day_role_stage_access", SOURCE)
ACCESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCESS)
SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_DECISION = 4096


def canonical_uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def dispatch(source_sha, stage_id, decision):
    checks = {"request_shape": bool(SHA.fullmatch(source_sha)) and canonical_uuid(stage_id),
              "decision_present": isinstance(decision, str) and bool(decision)}
    if not all(checks.values()):
        return checks
    raw = decision.encode("utf-8")
    checks["decision_bounded"] = len(raw) <= MAX_DECISION
    if not checks["decision_bounded"]:
        return checks
    baseline = ACCESS.check()
    checks["read_only_vps_baseline"] = bool(baseline) and all(baseline.values())
    if not checks["read_only_vps_baseline"]:
        return checks
    _, host, user, _, key, known_hosts = ACCESS.settings()
    old_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="vps-day-role-stage-") as directory:
            private_key = Path(directory) / "key"
            known_hosts_file = Path(directory) / "known_hosts"
            private_key.write_text(key)
            known_hosts_file.write_text(known_hosts + "\n")
            expected = hashlib.sha256(raw).hexdigest()
            result = subprocess.run(
                ["ssh", "-T", "-i", str(private_key),
                 "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                 "-o", "StrictHostKeyChecking=yes",
                 "-o", f"UserKnownHostsFile={known_hosts_file}",
                 "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
                 "-o", "LogLevel=QUIET", "-o", "NumberOfPasswordPrompts=0",
                 f"{user}@{host}",
                 f"stage-day-role {source_sha} {stage_id} {expected} confirm-stage-day-role"],
                input=raw, capture_output=True, timeout=240, check=False)
            try:
                report = json.loads(result.stdout)
            except (UnicodeError, ValueError):
                report = None
            checks["remote_stage_report"] = (
                result.returncode == 0 and isinstance(report, dict)
                and set(report) == {"phase", "status", "checks"}
                and report["phase"] == "day-role-stage-dispatch"
                and report["status"] == "PASS"
                and isinstance(report["checks"], dict) and bool(report["checks"])
                and all(value is True for value in report["checks"].values()))
    except (OSError, subprocess.TimeoutExpired):
        checks["remote_stage_report"] = False
    finally:
        os.umask(old_umask)
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--stage-id", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        checks = dispatch(args.source_sha, args.stage_id,
                          os.environ.get("VPS_DAY_ROLE_DECISION_JSON", ""))
    except (OSError, UnicodeError, ValueError):
        checks = {"local_stage": False}
    result = {"phase": "day-role-stage-dispatch", "status": "PASS" if checks and all(checks.values()) else "STOP",
              "stage_id": args.stage_id if canonical_uuid(args.stage_id) else None, "checks": checks}
    try:
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(result, output, sort_keys=True)
            output.write("\n")
    except OSError:
        print("STOP report_write_failed")
        return 2
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
