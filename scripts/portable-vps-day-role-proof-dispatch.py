#!/usr/bin/env python3
"""Request read-only, stage-bound day-role backup proof through restricted SSH."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid


SOURCE = Path(__file__).with_name("portable-vps-access-check.py")
SPEC = importlib.util.spec_from_file_location("day_role_proof_access", SOURCE)
ACCESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCESS)
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
PATH = re.compile(r"/[a-zA-Z0-9._/-]{1,254}\Z")


def canonical_uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def dispatch(source_sha, stage_id, maintenance_kind, backup_report,
             recover_report, restore_report):
    paths = tuple(Path(item) for item in (backup_report, recover_report, restore_report))
    checks = {"request_shape": bool(SHA.fullmatch(source_sha)) and canonical_uuid(stage_id)
                               and maintenance_kind in {"pause", "drain"},
              "evidence_selectors": all(PATH.fullmatch(str(path)) and ".." not in path.parts
                                        for path in paths) and len(set(paths)) == 3}
    if not all(checks.values()):
        return checks, None
    baseline = ACCESS.check()
    checks["read_only_vps_baseline"] = bool(baseline) and all(baseline.values())
    if not checks["read_only_vps_baseline"]:
        return checks, None
    _, host, user, _, key, known_hosts = ACCESS.settings()
    proof_sha = None
    old_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="vps-day-role-proof-") as directory:
            private_key = Path(directory) / "key"
            known_hosts_file = Path(directory) / "known_hosts"
            private_key.write_text(key)
            known_hosts_file.write_text(known_hosts + "\n")
            result = subprocess.run(
                ["ssh", "-T", "-i", str(private_key),
                 "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                 "-o", "StrictHostKeyChecking=yes",
                 "-o", f"UserKnownHostsFile={known_hosts_file}",
                 "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
                 "-o", "LogLevel=QUIET", "-o", "NumberOfPasswordPrompts=0",
                 f"{user}@{host}",
                 f"proof-day-role {source_sha} {stage_id} {maintenance_kind} "
                 f"{backup_report} {recover_report} {restore_report} confirm-readonly-proof-day-role"],
                capture_output=True, timeout=420, check=False)
            try:
                report = json.loads(result.stdout)
            except (UnicodeError, ValueError):
                report = None
            checks["remote_proof_report"] = (
                result.returncode == 0 and isinstance(report, dict)
                and set(report) == {"phase", "status", "checks", "proof_sha256"}
                and report["phase"] == "day-role-proof-dispatch" and report["status"] == "PASS"
                and isinstance(report["checks"], dict) and bool(report["checks"])
                and all(value is True for value in report["checks"].values())
                and isinstance(report["proof_sha256"], str)
                and bool(DIGEST.fullmatch(report["proof_sha256"])))
            if checks["remote_proof_report"]:
                proof_sha = report["proof_sha256"]
    except (OSError, subprocess.TimeoutExpired):
        checks["remote_proof_report"] = False
    finally:
        os.umask(old_umask)
    return checks, proof_sha


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--stage-id", required=True)
    parser.add_argument("--maintenance-kind", choices=("pause", "drain"), required=True)
    parser.add_argument("--backup-report", required=True)
    parser.add_argument("--recover-report", required=True)
    parser.add_argument("--restore-report", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        checks, proof_sha = dispatch(args.source_sha, args.stage_id, args.maintenance_kind,
                                     args.backup_report, args.recover_report, args.restore_report)
    except (OSError, UnicodeError, ValueError):
        checks, proof_sha = {"local_proof": False}, None
    status = "PASS" if checks and all(checks.values()) and proof_sha else "STOP"
    result = {"phase": "day-role-proof-dispatch", "status": status,
              "stage_id": args.stage_id if canonical_uuid(args.stage_id) else None, "checks": checks}
    if status == "PASS":
        result["proof_sha256"] = proof_sha
    try:
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(result, output, sort_keys=True)
            output.write("\n")
    except OSError:
        print("STOP report_write_failed")
        return 2
    print(status)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
