#!/usr/bin/env python3
"""Read-only, same-copy backup and isolated restore proof gate for a VPS release."""

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import uuid


SPEC = importlib.util.spec_from_file_location(
    "backup_copy", Path(__file__).with_name("portable-backup-copy.py")
)
COPIES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COPIES)
JOB_CHECKS = (
    "database_backup", "encrypted_bundle", "encrypted_remote_readback",
    "remote_receipt_readback", "independent_monitor_acknowledged",
)


def unique_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def private_json(path):
    return json.loads(COPIES.read(path), object_pairs_hook=unique_keys)


def moment(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result.astimezone(timezone.utc) if result.tzinfo else None


def valid_uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def inspect(backup_path, recover_path, restore_path, now=None):
    checks = {"same_owner": False, "reports_private": False}
    if len({backup_path.absolute(), recover_path.absolute(), restore_path.absolute()}) != 3:
        return checks
    try:
        owners = {path.lstat().st_uid for path in (backup_path, recover_path, restore_path)}
        checks["same_owner"] = owners == {os.geteuid()}
        if not checks["same_owner"]:
            return checks
        backup, recovered, restored = (
            private_json(path) for path in (backup_path, recover_path, restore_path)
        )
        checks["reports_private"] = True
        if not all(isinstance(report, dict) for report in (backup, recovered, restored)):
            return {**checks, "report_shape": False}
        current = now or datetime.now(timezone.utc)
        started, finished = moment(backup.get("timestamp")), moment(backup.get("finished_at"))
        recovered_at, restored_at = moment(recovered.get("timestamp")), moment(restored.get("timestamp"))
        checks["successful_backup"] = (
            backup.get("schema") == 1 and backup.get("phase") == "run"
            and backup.get("result") == "PASS" and isinstance(backup.get("checks"), dict)
            and all(backup["checks"].get(name) is True for name in JOB_CHECKS)
        )
        checks["fresh_backup"] = bool(
            started and finished and started <= finished <= current
            and current - finished <= timedelta(hours=2)
        )
        checks["same_verified_copy"] = (
            valid_uuid(backup.get("copy_id")) and valid_uuid(backup.get("bundle_id"))
            and valid_uuid(backup.get("run_id"))
            and recovered.get("copy_id") == backup.get("copy_id")
            and recovered.get("bundle_id") == restored.get("bundle_id") == backup.get("bundle_id")
        )
        checks["off_host_recovery"] = (
            recovered.get("schema") == 1 and recovered.get("phase") == "recover"
            and recovered.get("result") == "PASS" and isinstance(recovered.get("checks"), dict)
            and recovered["checks"].get("remote_readback_matches") is True
            and recovered["checks"].get("recovery_key_decrypts") is True
            and recovered["checks"].get("recovered_bundle_verified") is True
        )
        checks["isolated_restore"] = (
            restored.get("schema") == 1 and restored.get("phase") == "restore"
            and restored.get("result") == "PASS" and isinstance(restored.get("checks"), dict)
            and all(restored["checks"].get(f"{app}_restored_controls_match") is True
                    for app in ("siege", "mom"))
        )
        checks["proof_after_backup"] = bool(
            finished and recovered_at and restored_at
            and finished <= recovered_at <= restored_at <= current
        )
    except (OSError, ValueError, TypeError, UnicodeError, COPIES.Stop):
        checks["evidence_read"] = False
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-report", required=True, type=Path)
    parser.add_argument("--recover-report", required=True, type=Path)
    parser.add_argument("--restore-report", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    checks = inspect(args.backup_report, args.recover_report, args.restore_report)
    result = {"phase": "backup-evidence", "status": "PASS" if checks and all(checks.values()) else "STOP",
              "checks": checks}
    try:
        COPIES.protected(args.report.parent, True)
        with COPIES.recovery.create_private(args.report) as output:
            output.write((json.dumps(result, sort_keys=True) + "\n").encode())
            output.flush()
            os.fsync(output.fileno())
    except (OSError, COPIES.Stop):
        print("STOP report_write_failed")
        return 2
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
