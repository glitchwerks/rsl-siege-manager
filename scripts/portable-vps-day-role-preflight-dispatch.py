#!/usr/bin/env python3
"""Request a protected, read-only day-role activation preflight."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile


SOURCE = Path(__file__).with_name("portable-vps-day-role-proof-dispatch.py")
SPEC = importlib.util.spec_from_file_location("day_role_proof_transport", SOURCE)
PROOF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROOF)
ACCESS = PROOF.ACCESS


def dispatch(source_sha, stage_id, proof_sha, public_config):
    checks = {"request_shape": bool(PROOF.SHA.fullmatch(source_sha))
                               and PROOF.canonical_uuid(stage_id)
                               and bool(PROOF.DIGEST.fullmatch(proof_sha)),
              "public_selector": bool(PROOF.PATH.fullmatch(public_config))
                                 and ".." not in Path(public_config).parts}
    if not all(checks.values()):
        return checks, None
    baseline = ACCESS.check()
    checks["read_only_vps_baseline"] = bool(baseline) and all(baseline.values())
    if not checks["read_only_vps_baseline"]:
        return checks, None
    _, host, user, _, key, known_hosts = ACCESS.settings()
    preflight_sha = None
    old_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="vps-day-role-preflight-") as directory:
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
                 f"preflight-day-role {source_sha} {stage_id} {proof_sha} "
                 f"{public_config} confirm-readonly-preflight-day-role"],
                capture_output=True, timeout=420, check=False)
            try:
                report = json.loads(result.stdout)
            except (UnicodeError, ValueError):
                report = None
            checks["remote_preflight_report"] = (
                result.returncode == 0 and isinstance(report, dict)
                and set(report) == {"phase", "status", "checks", "preflight_sha256"}
                and report["phase"] == "day-role-preflight-dispatch"
                and report["status"] == "PASS"
                and isinstance(report["checks"], dict) and bool(report["checks"])
                and all(value is True for value in report["checks"].values())
                and isinstance(report["preflight_sha256"], str)
                and bool(PROOF.DIGEST.fullmatch(report["preflight_sha256"])))
            if checks["remote_preflight_report"]:
                preflight_sha = report["preflight_sha256"]
    except (OSError, subprocess.TimeoutExpired):
        checks["remote_preflight_report"] = False
    finally:
        os.umask(old_umask)
    return checks, preflight_sha


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--stage-id", required=True)
    parser.add_argument("--proof-sha256", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        checks, preflight_sha = dispatch(args.source_sha, args.stage_id, args.proof_sha256,
                                          os.environ.get("VPS_PUBLIC_SIGNAL_CONFIG_PATH", ""))
    except (OSError, UnicodeError, ValueError):
        checks, preflight_sha = {"local_preflight": False}, None
    status = "PASS" if checks and all(checks.values()) and preflight_sha else "STOP"
    result = {"phase": "day-role-preflight-dispatch", "status": status,
              "stage_id": args.stage_id if PROOF.canonical_uuid(args.stage_id) else None,
              "checks": checks}
    if status == "PASS":
        result["preflight_sha256"] = preflight_sha
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
