#!/usr/bin/env python3
"""Send one reviewed release manifest through the restricted VPS stage command."""

import argparse
import hashlib
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
MAX_MANIFEST = 16 * 1024


def stage(manifest, expected, run_id):
    checks = {
        "request_shape": bool(SHA256.fullmatch(expected)) and bool(RUN.fullmatch(run_id)),
        "manifest_regular": manifest.is_file() and not manifest.is_symlink(),
    }
    if not all(checks.values()):
        return checks
    try:
        if manifest.stat().st_size > MAX_MANIFEST:
            checks["input_bounded"] = False
            return checks
        raw = manifest.read_bytes()
    except OSError:
        checks["manifest_read"] = False
        return checks
    checks["input_bounded"] = 0 < len(raw) <= MAX_MANIFEST
    checks["manifest_digest"] = checks["input_bounded"] and hashlib.sha256(raw).hexdigest() == expected
    if not all(checks.values()):
        return checks
    baseline = ACCESS.check()
    checks["read_only_vps_baseline"] = bool(baseline) and all(baseline.values())
    if not checks["read_only_vps_baseline"]:
        return checks
    _, host, user, _, key, known_hosts = ACCESS.settings()
    old_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="vps-stage-") as directory:
            private_key = Path(directory) / "key"
            known_hosts_file = Path(directory) / "known_hosts"
            private_key.write_text(key)
            known_hosts_file.write_text(known_hosts + "\n")
            result = subprocess.run(
                [
                    "ssh", "-T", "-i", str(private_key),
                    "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                    "-o", "StrictHostKeyChecking=yes",
                    "-o", f"UserKnownHostsFile={known_hosts_file}",
                    "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
                    "-o", "LogLevel=QUIET", "-o", "NumberOfPasswordPrompts=0",
                    f"{user}@{host}", f"stage-release {expected} {run_id} confirm-stage",
                ],
                input=raw, capture_output=True, timeout=60, check=False,
            )
            try:
                report = json.loads(result.stdout)
            except (UnicodeError, ValueError):
                report = None
            checks["remote_stage_report"] = (
                result.returncode == 0 and isinstance(report, dict)
                and set(report) == {"phase", "status", "checks"}
                and report["phase"] == "stage-release" and report["status"] == "PASS"
                and isinstance(report["checks"], dict) and bool(report["checks"])
                and all(value is True for value in report["checks"].values())
            )
    except (OSError, subprocess.TimeoutExpired):
        checks["remote_stage_report"] = False
    finally:
        os.umask(old_umask)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        checks = stage(args.manifest, args.digest, args.run_id)
    except (OSError, UnicodeError, ValueError):
        checks = {"local_stage": False}
    result = {"status": "PASS" if checks and all(checks.values()) else "STOP", "checks": checks}
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
