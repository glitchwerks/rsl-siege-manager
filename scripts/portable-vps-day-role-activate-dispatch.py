#!/usr/bin/env python3
"""Confirm one window-bound day-role backend activation through restricted SSH."""

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
SPEC = importlib.util.spec_from_file_location("day_role_activate_access", SOURCE)
ACCESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCESS)
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
PATH = re.compile(r"/[a-zA-Z0-9._/-]{1,254}\Z")
WINDOW = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{3,63}\Z")


def canonical_uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def dispatch(source_sha, stage_id, proof_sha, preflight_sha, public_config,
             window_id, approved_window):
    checks = {"request_shape": bool(SHA.fullmatch(source_sha)) and canonical_uuid(stage_id)
                               and bool(DIGEST.fullmatch(proof_sha))
                               and bool(DIGEST.fullmatch(preflight_sha))
                               and bool(WINDOW.fullmatch(window_id)),
              "public_selector": bool(PATH.fullmatch(public_config)) and ".." not in Path(public_config).parts,
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
        with tempfile.TemporaryDirectory(prefix="vps-day-role-activate-") as directory:
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
                 f"activate-day-role {source_sha} {stage_id} {proof_sha} {preflight_sha} "
                 f"{public_config} {window_id} confirm-activate-day-role"],
                capture_output=True, timeout=540, check=False)
            try:
                report = json.loads(result.stdout)
            except (UnicodeError, ValueError):
                report = None
            checks["remote_activation_report"] = (
                result.returncode == 0 and isinstance(report, dict)
                and set(report) == {"phase", "status", "checks"}
                and report["phase"] == "day-role-activate-dispatch"
                and report["status"] == "PASS"
                and isinstance(report["checks"], dict) and bool(report["checks"])
                and all(value is True for value in report["checks"].values()))
    except (OSError, subprocess.TimeoutExpired):
        checks["remote_activation_report"] = False
    finally:
        os.umask(old_umask)
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--stage-id", required=True)
    parser.add_argument("--proof-sha256", required=True)
    parser.add_argument("--preflight-sha256", required=True)
    parser.add_argument("--window-id", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        checks = dispatch(args.source_sha, args.stage_id, args.proof_sha256,
                          args.preflight_sha256,
                          os.environ.get("VPS_PUBLIC_SIGNAL_CONFIG_PATH", ""),
                          args.window_id,
                          os.environ.get("VPS_APPROVED_DAY_ROLE_WINDOW_ID", ""))
    except (OSError, UnicodeError, ValueError):
        checks = {"local_activation": False}
    status = "PASS" if checks and all(checks.values()) else "STOP"
    result = {"phase": "day-role-activate-dispatch", "status": status,
              "stage_id": args.stage_id if canonical_uuid(args.stage_id) else None,
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
