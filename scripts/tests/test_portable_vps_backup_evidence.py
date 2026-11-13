"""Backup release evidence must be fresh, same-copy, private, and sanitized."""

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-backup-evidence.py"
SPEC = importlib.util.spec_from_file_location("vps_backup_evidence", SCRIPT)
EVIDENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVIDENCE)


def save(path, data):
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return path


class BackupEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2020, 11, 12, 13, 0, tzinfo=timezone.utc)
        self.copy = str(uuid.uuid4())
        self.bundle = str(uuid.uuid4())
        self.job = {
            "schema": 1, "phase": "run", "result": "PASS", "run_id": str(uuid.uuid4()),
            "copy_id": self.copy, "bundle_id": self.bundle,
            "timestamp": (self.now - timedelta(minutes=48)).isoformat(),
            "finished_at": (self.now - timedelta(minutes=40)).isoformat(),
            "checks": {name: True for name in EVIDENCE.JOB_CHECKS},
        }
        self.recover = {
            "schema": 1, "phase": "recover", "result": "PASS", "copy_id": self.copy,
            "bundle_id": self.bundle,
            "timestamp": (self.now - timedelta(minutes=30)).isoformat(),
            "checks": {"remote_readback_matches": True, "recovery_key_decrypts": True,
                       "recovered_bundle_verified": True},
        }
        self.restore = {
            "schema": 1, "phase": "restore", "result": "PASS", "bundle_id": self.bundle,
            "timestamp": (self.now - timedelta(minutes=20)).isoformat(),
            "checks": {"siege_restored_controls_match": True, "mom_restored_controls_match": True},
        }

    def check(self):
        paths = [save(self.root / name, value) for name, value in
                 (("job.json", self.job), ("recover.json", self.recover), ("restore.json", self.restore))]
        return EVIDENCE.inspect(*paths, now=self.now)

    def test_complete_same_copy_proof_passes(self):
        self.assertTrue(all(self.check().values()))

    def test_stale_backup_and_old_restore_anchor_stop(self):
        self.job["finished_at"] = (self.now - timedelta(hours=3)).isoformat()
        self.assertFalse(self.check()["fresh_backup"])
        self.job["finished_at"] = (self.now - timedelta(minutes=40)).isoformat()
        self.recover["copy_id"] = str(uuid.uuid4())
        self.assertFalse(self.check()["same_verified_copy"])
        self.recover["copy_id"] = self.copy
        self.restore["bundle_id"] = str(uuid.uuid4())
        self.assertFalse(self.check()["same_verified_copy"])

    def test_missing_proof_and_wrong_order_stop(self):
        self.restore["checks"]["mom_restored_controls_match"] = False
        self.assertFalse(self.check()["isolated_restore"])
        self.restore["checks"]["mom_restored_controls_match"] = True
        self.recover["timestamp"] = (self.now - timedelta(hours=1)).isoformat()
        self.assertFalse(self.check()["proof_after_backup"])

    def test_symlink_or_open_permissions_stop(self):
        self.check()
        path = self.root / "job.json"
        path.chmod(0o644)
        self.assertFalse(EVIDENCE.inspect(path, self.root / "recover.json",
                                          self.root / "restore.json", now=self.now)["reports_private"])
        path.unlink()
        path.symlink_to(self.root / "recover.json")
        self.assertFalse(EVIDENCE.inspect(path, self.root / "recover.json",
                                          self.root / "restore.json", now=self.now)["reports_private"])

    def test_new_report_contains_only_boolean_checks(self):
        self.check()
        report = self.root / "gate.json"
        paths = [self.root / name for name in ("job.json", "recover.json", "restore.json")]
        code = EVIDENCE.main(["--backup-report", str(paths[0]), "--recover-report", str(paths[1]),
                              "--restore-report", str(paths[2]), "--report", str(report)])
        self.assertEqual(code, 1)  # Fixture timestamps are historical at execution time.
        self.assertEqual(report.stat().st_mode & 0o777, 0o600)
        content = json.loads(report.read_text())
        self.assertTrue(all(type(value) is bool for value in content["checks"].values()))
        self.assertNotIn(self.copy, report.read_text())
        self.assertEqual(EVIDENCE.main(["--backup-report", str(paths[0]), "--recover-report", str(paths[1]),
                                        "--restore-report", str(paths[2]), "--report", str(report)]), 2)


if __name__ == "__main__":
    unittest.main()
