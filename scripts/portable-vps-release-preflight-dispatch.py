#!/usr/bin/env python3
"""Run one staged, read-only VPS release preflight through restricted SSH."""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


SOURCE = Path(__file__).with_name("portable-vps-access-check.py")
SPEC = importlib.util.spec_from_file_location("portable_vps_access", SOURCE)
ACCESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCESS)
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
RUN = re.compile(r"[1-9][0-9]{0,19}-[1-9][0-9]{0,2}\Z")


def preflight(expected, run_id):
    checks = {"request_shape": bool(SHA256.fullmatch(expected)) and bool(RUN.fullmatch(run_id))}
    if not all(checks.values()):
        return checks
    baseline = ACCESS.check()
    checks["read_only_vps_baseline"] = bool(baseline) and all(baseline.values())
    if not checks["read_only_vps_baseline"]:
        return checks
    _, host, user, _, key, known_hosts = ACCESS.settings()
    old_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="vps-preflight-") as directory:
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
                 f"preflight-release {expected} {run_id} confirm-readonly-preflight"],
                capture_output=True, timeout=330, check=False,
            )
            try:
                report = json.loads(result.stdout)
            except (UnicodeError, ValueError):
                report = None
            checks["remote_preflight_report"] = (
                result.returncode == 0 and isinstance(report, dict)
                and set(report) == {"phase", "status", "checks"}
                and report["phase"] == "release-preflight-dispatch" and report["status"] == "PASS"
                and isinstance(report["checks"], dict) and bool(report["checks"])
                and all(value is True for value in report["checks"].values())
            )
    except (OSError, subprocess.TimeoutExpired):
        checks["remote_preflight_report"] = False
    finally:
        os.umask(old_umask)
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--stage-run-id", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        checks = preflight(args.manifest_sha256, args.stage_run_id)
    except (OSError, UnicodeError, ValueError):
        checks = {"local_preflight": False}
    result = {"phase": "release-preflight-dispatch",
              "status": "PASS" if checks and all(checks.values()) else "STOP", "checks": checks}
    try:
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as report:
            json.dump(result, report, sort_keys=True)
            report.write("\n")
    except OSError:
        print("STOP report_write_failed")
        return 2
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
