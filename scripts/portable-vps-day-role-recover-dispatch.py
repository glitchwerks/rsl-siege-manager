#!/usr/bin/env python3
"""Confirm failed day-role activation recovery through restricted SSH."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile


SOURCE = Path(__file__).with_name("portable-vps-day-role-activate-dispatch.py")
SPEC = importlib.util.spec_from_file_location("day_role_activate_transport", SOURCE)
ACTIVATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACTIVATE)
ACCESS = ACTIVATE.ACCESS


def dispatch(source_sha, stage_id, public_config, window_id, approved_window):
    checks = {"request_shape": bool(ACTIVATE.SHA.fullmatch(source_sha))
                               and ACTIVATE.canonical_uuid(stage_id)
                               and bool(ACTIVATE.WINDOW.fullmatch(window_id)),
              "public_selector": bool(ACTIVATE.PATH.fullmatch(public_config))
                                 and ".." not in Path(public_config).parts,
              "window_approved": bool(approved_window) and window_id == approved_window}
    if not all(checks.values()):
        return checks
    baseline = ACCESS.check()
    checks["read_only_vps_baseline"] = bool(baseline) and all(baseline.values())
    if not checks["read_only_vps_baseline"]:
        return checks
    _, host, user, _, key, known_hosts = ACCESS.settings()
    old_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="vps-day-role-recover-") as directory:
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
                 f"recover-day-role {source_sha} {stage_id} {public_config} "
                 f"{window_id} confirm-recover-day-role"],
                capture_output=True, timeout=540, check=False)
            try:
                report = json.loads(result.stdout)
            except (UnicodeError, ValueError):
                report = None
            checks["remote_recovery_report"] = (
                result.returncode == 0 and isinstance(report, dict)
                and set(report) == {"phase", "status", "checks"}
                and report["phase"] == "day-role-recover-dispatch"
                and report["status"] == "PASS"
                and isinstance(report["checks"], dict) and bool(report["checks"])
                and all(value is True for value in report["checks"].values()))
    except (OSError, subprocess.TimeoutExpired):
        checks["remote_recovery_report"] = False
    finally:
        os.umask(old_umask)
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--stage-id", required=True)
    parser.add_argument("--window-id", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        checks = dispatch(args.source_sha, args.stage_id,
                          os.environ.get("VPS_PUBLIC_SIGNAL_CONFIG_PATH", ""),
                          args.window_id,
                          os.environ.get("VPS_APPROVED_DAY_ROLE_WINDOW_ID", ""))
    except (OSError, UnicodeError, ValueError):
        checks = {"local_recovery": False}
    status = "PASS" if checks and all(checks.values()) else "STOP"
    result = {"phase": "day-role-recover-dispatch", "status": status,
              "stage_id": args.stage_id if ACTIVATE.canonical_uuid(args.stage_id) else None,
              "checks": checks}
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
