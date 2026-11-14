"""A release proof must bind the paused candidate to one fresh restored backup."""

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-release-recovery-proof.py"
SPEC = importlib.util.spec_from_file_location("release_recovery_proof", SCRIPT)
PROOF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROOF)


def private(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


class ReleaseRecoveryProofTests(unittest.TestCase):
    def test_accepted_proof_is_bound_to_selected_run_and_rejects_other_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            backup_root = root / "backups"
            backup_root.mkdir(mode=0o700)
            runs = backup_root / "runs"
            runs.mkdir(mode=0o700)
            run_id, copy_id, bundle_id = (str(uuid.uuid4()) for _ in range(3))
            run = runs / run_id
            run.mkdir(mode=0o700)
            now = datetime.now(timezone.utc)
            job = {"schema": 1, "phase": "run", "result": "PASS", "run_id": run_id,
                   "copy_id": copy_id, "bundle_id": bundle_id,
                   "timestamp": (now - timedelta(minutes=30)).isoformat(),
                   "finished_at": (now - timedelta(minutes=25)).isoformat(),
                   "checks": {key: True for key in PROOF.BACKUP.JOB_CHECKS}}
            recover = {"schema": 1, "phase": "recover", "result": "PASS",
                       "copy_id": copy_id, "bundle_id": bundle_id,
                       "timestamp": (now - timedelta(minutes=20)).isoformat(),
                       "checks": {"remote_readback_matches": True, "recovery_key_decrypts": True,
                                  "recovered_bundle_verified": True}}
            restore = {"schema": 1, "phase": "restore", "result": "PASS",
                       "bundle_id": bundle_id, "timestamp": (now - timedelta(minutes=15)).isoformat(),
                       "checks": {"siege_restored_controls_match": True,
                                  "mom_restored_controls_match": True}}
            private(run / "job.json", job)
            recover_path = private(backup_root / "recover.json", recover)
            restore_path = private(backup_root / "restore.json", restore)
            private(stage / "backup-selector.json", {"schema": 1, "run_id": run_id})
            original_read = PROOF.GATE.read_private
            def read(path, owner, limit=65536):
                if path == stage / "backup-selector.json":
                    return path.read_bytes()
                return original_read(path, owner, limit)
            data = {"backup_root": backup_root, "backup_owner": "rsl-backup",
                    "project_directory": root / "project"}
            value = {"preflight_baseline_sha256": "c" * 64}
            with patch.object(PROOF.os, "geteuid", return_value=0), patch.object(
                PROOF.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=os.stat(backup_root).st_uid)
            ), patch.object(PROOF, "accepted_report", return_value=True), patch.object(
                PROOF.PAUSE, "bound_preflight", return_value=(True, {})
            ), patch.object(PROOF.PAUSE, "paused", return_value=True), patch.object(
                PROOF.PAUSE, "backup_idle", return_value=True
            ), patch.object(PROOF.GATE, "inspect", return_value={"backup_after_pause": True}), patch.object(
                PROOF.GATE, "read_private", side_effect=read
            ):
                checks, baseline = PROOF.inspect(data, stage, value, "b" * 64, "a" * 64,
                                                 recover_path, restore_path)
                self.assertTrue(all(checks.values()), checks)
                self.assertEqual(baseline["backup_run_id"], run_id)
                self.assertEqual(baseline["proofs"]["backup"]["sha256"],
                                 PROOF.PAUSE.digest((run / "job.json").read_bytes()))
                original_inspect = PROOF.BACKUP.inspect
                def change_after_verification(*args, **kwargs):
                    result = original_inspect(*args, **kwargs)
                    private(recover_path, {**recover, "copy_id": str(uuid.uuid4())})
                    return result
                with patch.object(PROOF.BACKUP, "inspect", side_effect=change_after_verification):
                    checks, baseline = PROOF.inspect(data, stage, value, "b" * 64, "a" * 64,
                                                     recover_path, restore_path)
                self.assertFalse(checks["proofs_unchanged"])
                self.assertIsNone(baseline)
                recover["copy_id"] = str(uuid.uuid4())
                private(recover_path, recover)
                checks, baseline = PROOF.inspect(data, stage, value, "b" * 64, "a" * 64,
                                                 recover_path, restore_path)
                self.assertFalse(checks["same_copy_recovery_restored"])
                self.assertIsNone(baseline)

    def test_bad_selector_and_render_failure_stop_without_exposing_detail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "stage"
            stage.mkdir()
            private(stage / "backup-selector.json", {"schema": 1, "run_id": "not-a-uuid"})
            with patch.object(PROOF.GATE, "read_private", side_effect=lambda path, owner: path.read_bytes()):
                with self.assertRaises(ValueError):
                    PROOF.backup_run(stage)
            report = root / "report.json"
            with patch.object(PROOF.os, "geteuid", return_value=0), patch.object(
                PROOF.GATE, "protected", return_value=True
            ), patch.object(PROOF.PAUSE, "profile", return_value=({"project_directory": root / "project"}, "b" * 64)), patch.object(
                PROOF.PAUSE, "stage", return_value=stage
            ), patch.object(PROOF.PAUSE, "load_plan", return_value={}), patch.object(
                PROOF, "inspect", side_effect=RuntimeError("sensitive details")):
                result = PROOF.main(["--profile", str(root / "profile"), "--stage-run-id", "123-1",
                    "--manifest-sha256", "a" * 64, "--plan-sha256", "b" * 64,
                    "--recover-report", str(root / "recover"), "--restore-report", str(root / "restore"),
                    "--report", str(report)])
            self.assertEqual(result, 1)
            self.assertEqual(json.loads(report.read_text())["status"], "STOP")
            self.assertEqual(json.loads((stage / "recovery-proof-report.json").read_text())["status"], "STOP")
            self.assertNotIn("sensitive", report.read_text())


if __name__ == "__main__":
    unittest.main()
