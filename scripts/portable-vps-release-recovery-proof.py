#!/usr/bin/env python3
"""Bind one paused release candidate to its fresh managed backup and restore proof."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import pwd
import subprocess
import uuid


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("release_pause", HERE / "portable-vps-release-pause-backup.py")
PAUSE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PAUSE)
GATE = PAUSE.GATE
BACKUP = GATE.BACKUP
REMOTE = PAUSE.REMOTE


def accepted_report(directory, name, phase):
    value = json.loads(GATE.read_private(directory / name, 0),
                       object_pairs_hook=REMOTE.no_duplicate_keys)
    return isinstance(value, dict) and value.get("phase") == phase and value.get("status") == "PASS"


def backup_run(directory):
    raw = GATE.read_private(directory / "backup-selector.json", 0)
    value = json.loads(raw, object_pairs_hook=REMOTE.no_duplicate_keys)
    run_id = value.get("run_id") if isinstance(value, dict) and value.get("schema") == 1 else None
    if not isinstance(run_id, str) or str(uuid.UUID(run_id)) != run_id:
        raise ValueError("invalid backup selector")
    return run_id, PAUSE.digest(raw)


def owned_json(path, owner):
    return json.loads(GATE.read_private(path, owner), object_pairs_hook=REMOTE.no_duplicate_keys)


def inspect(data, directory, value, plan_digest, manifest_digest, recover_path, restore_path):
    checks = {"root_execution": os.geteuid() == 0,
              "pause_accepted": accepted_report(directory, "pause-report.json", "pause"),
              "backup_accepted": accepted_report(directory, "backup-report.json", "backup")}
    if not all(checks.values()):
        return checks, None
    checks["preflight_current"] = PAUSE.bound_preflight(data, directory, manifest_digest,
        value["preflight_baseline_sha256"], writers_paused=True)[0]
    checks["writers_paused"] = PAUSE.paused(data, value)
    checks["backup_idle"] = PAUSE.backup_idle(data)
    if not all(checks.values()):
        return checks, None
    run_id, selector_digest = backup_run(directory)
    backup_path = data["backup_root"] / "runs" / run_id / "job.json"
    owner = pwd.getpwnam(data["backup_owner"]).pw_uid
    checks["proof_paths_distinct"] = (all(path.is_absolute() and ".." not in path.parts
        for path in (recover_path, restore_path)) and len({backup_path, recover_path, restore_path}) == 3)
    if not checks["proof_paths_distinct"]:
        return checks, None
    paths = {"backup": backup_path, "recover": recover_path, "restore": restore_path}
    hashes = {name: PAUSE.digest(GATE.read_private(path, owner)) for name, path in paths.items()}
    gate = GATE.inspect(data["project_directory"], directory.name, manifest_digest,
                        data["backup_owner"], data["backup_root"], backup_path)
    checks["pause_backup_order_verified"] = bool(gate) and all(gate.values())
    if not checks["pause_backup_order_verified"]:
        return checks, None
    proof = BACKUP.inspect(backup_path, recover_path, restore_path, owner_uid=owner,
                           reader=lambda path: owned_json(path, owner))
    checks["same_copy_recovery_restored"] = bool(proof) and all(proof.values())
    if not checks["same_copy_recovery_restored"]:
        return checks, None
    checks["preflight_still_current"] = PAUSE.bound_preflight(data, directory, manifest_digest,
        value["preflight_baseline_sha256"], writers_paused=True)[0]
    checks["writers_still_paused"] = PAUSE.paused(data, value)
    checks["proofs_unchanged"] = all(PAUSE.digest(GATE.read_private(path, owner)) == hashes[name]
                                     for name, path in paths.items())
    if not all(checks.values()):
        return checks, None
    baseline = {"schema": 1, "phase": "release-recovery-proof-baseline",
                "manifest_sha256": manifest_digest, "plan_sha256": plan_digest,
                "preflight_baseline_sha256": value["preflight_baseline_sha256"],
                "backup_selector_sha256": selector_digest, "backup_run_id": run_id,
                "proofs": {name: {"path": str(path), "sha256": hashes[name]}
                           for name, path in paths.items()}}
    return checks, baseline


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--stage-run-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--recover-report", required=True, type=Path)
    parser.add_argument("--restore-report", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.report.is_absolute() or ".." in args.report.parts:
        print("STOP invalid_report_path")
        return 2
    try:
        if not GATE.protected(args.report.parent, 0, directory=True):
            raise OSError("unsafe report directory")
        PAUSE.write_private(args.report, {"phase": "release-recovery-proof", "status": "INCOMPLETE"})
    except OSError:
        print("STOP report_write_failed")
        return 2
    directory = None
    checks = {"root_execution": os.geteuid() == 0}
    try:
        if not checks["root_execution"]:
            raise ValueError("root required")
        data, profile_digest = PAUSE.profile(args.profile)
        directory = PAUSE.stage(data["project_directory"], args.stage_run_id, args.manifest_sha256)
        if args.report.is_relative_to(data["project_directory"]):
            raise ValueError("report outside project required")
        if any((directory / name).exists() for name in
               ("recovery-proof-report.json", "recovery-proof-baseline.json")):
            raise ValueError("proof already attempted")
        value = PAUSE.load_plan(directory, args.plan_sha256, profile_digest, args.manifest_sha256)
        checks, baseline = inspect(data, directory, value, args.plan_sha256, args.manifest_sha256,
                                   args.recover_report, args.restore_report)
        if baseline is not None and all(checks.values()):
            PAUSE.write_private(directory / "recovery-proof-baseline.json", baseline)
            checks["baseline_recorded"] = True
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError,
            subprocess.TimeoutExpired):
        checks["proof_inspection"] = False
    result = {"phase": "release-recovery-proof",
              "status": "PASS" if checks and all(checks.values()) else "STOP", "checks": checks}
    try:
        if directory is not None:
            PAUSE.write_private(directory / "recovery-proof-report.json", result)
    except OSError:
        result["status"] = "STOP"
        result["checks"]["stage_report_write"] = False
    try:
        PAUSE.replace_private(args.report, result)
    except OSError:
        print("STOP report_write_failed")
        return 2
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
