"""Release pause and backup phases fail closed and target only reviewed services."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-release-pause-backup.py"
SPEC = importlib.util.spec_from_file_location("release_pause_backup", SCRIPT)
PHASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PHASE)


def plan_fixture():
    return {"schema": 1, "phase": "pause-plan", "manifest_sha256": "a" * 64,
            "profile_sha256": "b" * 64, "project": "siege-ci",
            "ids": {name: character * 64 for name, character in
                    (("backend", "a"), ("mom", "b"), ("postgres", "c"), ("proxy", "d"))}}


class ReleasePauseBackupTests(unittest.TestCase):
    def test_only_two_recorded_writers_stop_in_order(self):
        value = plan_fixture()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            PHASE, "unchanged_running", return_value=True
        ), patch.object(PHASE, "command", side_effect=["", ""]) as run, patch.object(
            PHASE, "paused", return_value=True
        ), patch.object(PHASE.GATE, "read_private", return_value=b"plan"):
            checks = PHASE.pause({}, value, Path(temporary))
            self.assertTrue(all(checks.values()))
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                ["docker", "stop", "--time", "30", value["ids"]["mom"]],
                ["docker", "stop", "--time", "30", value["ids"]["backend"]],
            ])
            self.assertEqual(json.loads((Path(temporary) / "pause-start.json").read_text())["phase"], "pause-start")

    def test_drift_and_failed_first_stop_do_not_start_backend(self):
        value = plan_fixture()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            PHASE, "unchanged_running", return_value=False
        ), patch.object(PHASE, "command") as run:
            self.assertFalse(PHASE.pause({}, value, Path(temporary))["baseline_unchanged"])
            run.assert_not_called()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            PHASE, "unchanged_running", return_value=True
        ), patch.object(PHASE, "command", return_value=None) as run, patch.object(
            PHASE, "paused", return_value=False
        ), patch.object(PHASE.GATE, "read_private", return_value=b"plan"):
            checks = PHASE.pause({}, value, Path(temporary))
            self.assertFalse(checks["mom_stop"])
            self.assertEqual(run.call_count, 1)
            self.assertTrue((Path(temporary) / "pause-start.json").exists())

    def test_backup_requires_accepted_pause_and_keeps_writers_stopped(self):
        value = plan_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "pause-report.json").write_text('{"phase":"pause","status":"STOP"}')
            with patch.object(PHASE.GATE, "read_private", side_effect=lambda path, owner: path.read_bytes()), patch.object(
                PHASE, "paused", return_value=True
            ), patch.object(PHASE, "backup_idle", return_value=True), patch.object(PHASE, "command") as run:
                checks = PHASE.backup({"backup_root": directory}, value, directory, "a" * 64)
            self.assertFalse(checks["pause_pass"])
            run.assert_not_called()

    def test_backup_uses_one_new_managed_run_and_rechecks_ordering(self):
        value = plan_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "pause-report.json").write_text('{"phase":"pause","status":"PASS"}')
            (directory / "pause-plan.json").write_text("plan")
            runs = directory / "runs"
            runs.mkdir()
            run_id = str(uuid.uuid4())
            def start(arguments, timeout=20):
                self.assertEqual(arguments, ["systemctl", "start", "rsl-pilot-backup.service"])
                (runs / run_id).mkdir()
                return ""
            data = {"backup_root": directory, "backup_unit": "rsl-pilot-backup.service",
                    "project_directory": directory, "backup_owner": "root"}
            with patch.object(PHASE.GATE, "read_private", side_effect=lambda path, owner: path.read_bytes()), patch.object(
                PHASE, "paused", return_value=True
            ), patch.object(PHASE, "backup_idle", return_value=True), patch.object(
                PHASE, "command", side_effect=start
            ), patch.object(PHASE.GATE, "inspect", return_value={"backup_after_pause": True}):
                checks = PHASE.backup(data, value, directory, "a" * 64)
            self.assertTrue(all(checks.values()))
            self.assertEqual(json.loads((directory / "backup-selector.json").read_text())["run_id"], run_id)

    def test_backup_rejects_writer_started_during_run(self):
        value = plan_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "pause-report.json").write_text('{"phase":"pause","status":"PASS"}')
            (directory / "pause-plan.json").write_text("plan")
            runs = directory / "runs"
            runs.mkdir()
            run_id = str(uuid.uuid4())
            def start(arguments, timeout=20):
                (runs / run_id).mkdir()
                return ""
            data = {"backup_root": directory, "backup_unit": "rsl-pilot-backup.service",
                    "project_directory": directory, "backup_owner": "root"}
            with patch.object(PHASE.GATE, "read_private", side_effect=lambda path, owner: path.read_bytes()), patch.object(
                PHASE, "paused", side_effect=[True, False]
            ), patch.object(PHASE, "backup_idle", return_value=True), patch.object(
                PHASE, "command", side_effect=start
            ), patch.object(PHASE.GATE, "inspect", return_value={"backup_after_pause": True}):
                checks = PHASE.backup(data, value, directory, "a" * 64)
            self.assertFalse(checks["post_backup_baseline"])
            self.assertFalse((directory / "backup-selector.json").exists())

    def test_stage_and_plan_are_digest_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "evidence" / "release-stages" / "123-1"
            stage.mkdir(parents=True)
            candidate = b'{"synthetic":true}'
            (stage / "candidate.json").write_bytes(candidate)
            (stage / "report.json").write_text('{"phase":"stage-release","status":"PASS"}')
            with patch.object(PHASE.GATE, "protected", return_value=True), patch.object(
                PHASE.GATE, "read_private", side_effect=lambda path, owner, limit=65536: path.read_bytes()
            ), patch.object(PHASE.REMOTE, "valid_manifest", return_value=True):
                self.assertEqual(PHASE.stage(root, "123-1", PHASE.digest(candidate)), stage)
                with self.assertRaises(ValueError):
                    PHASE.stage(root, "123-1", "0" * 64)

    def test_unconfirmed_pause_never_calls_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "report.json"
            with patch.object(PHASE.os, "geteuid", return_value=0), patch.object(
                PHASE.GATE, "protected", return_value=True
            ), patch.object(PHASE, "profile", return_value=({"project_directory": root / "project"}, "b" * 64)), patch.object(
                PHASE, "stage", return_value=root
            ), patch.object(PHASE, "pause") as mutation:
                result = PHASE.main(["pause", "--profile", str(root / "profile.json"),
                                     "--stage-run-id", "123-1", "--manifest-sha256", "a" * 64,
                                     "--plan-sha256", "c" * 64, "--report", str(report)])
            self.assertEqual(result, 1)
            mutation.assert_not_called()
            self.assertEqual(json.loads(report.read_text())["status"], "STOP")


if __name__ == "__main__":
    unittest.main()
