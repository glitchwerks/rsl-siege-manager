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
            "profile_sha256": "b" * 64, "preflight_baseline_sha256": "e" * 64,
            "project": "siege-ci",
            "ids": {name: character * 64 for name, character in
                    (("backend", "a"), ("mom", "b"), ("postgres", "c"), ("proxy", "d"))}}


class ReleasePauseBackupTests(unittest.TestCase):
    def test_plan_requires_matching_accepted_preflight(self):
        value = plan_fixture()
        baseline = {"project": value["project"], "installed": {
            name: {"id": identifier} for name, identifier in value["ids"].items()}}
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            PHASE.os, "geteuid", return_value=0
        ), patch.object(PHASE, "planned", return_value=value), patch.object(
            PHASE, "bound_preflight", return_value=(False, baseline)
        ):
            checks = PHASE.plan({}, "b" * 64, Path(temporary), "a" * 64)
            self.assertFalse(checks["preflight_current"])
            self.assertFalse((Path(temporary) / "pause-plan.json").exists())
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            PHASE.os, "geteuid", return_value=0
        ), patch.object(PHASE, "planned", return_value=value), patch.object(
            PHASE, "bound_preflight", return_value=("e" * 64, baseline)
        ):
            checks = PHASE.plan({}, "b" * 64, Path(temporary), "a" * 64)
            self.assertTrue(all(checks.values()))
            self.assertEqual(json.loads((Path(temporary) / "pause-plan.json").read_text())[
                "preflight_baseline_sha256"], "e" * 64)

    def test_preflight_drift_denies_writer_stop(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            PHASE, "bound_preflight", return_value=(False, {})
        ), patch.object(PHASE, "command") as run:
            checks = PHASE.pause({}, plan_fixture(), Path(temporary))
            self.assertFalse(checks["preflight_current"])
            run.assert_not_called()
            self.assertFalse((Path(temporary) / "pause-start.json").exists())

    def test_effective_unit_requires_reviewed_drop_ins_and_loaded_state(self):
        unit = "# /etc/systemd/system/rsl-pilot-backup.service\n[Service]\nExecStart=/bin/true"
        data = {"backup_unit": "rsl-pilot-backup.service",
                "backup_unit_effective_sha256": PHASE.digest(unit.encode())}
        with patch.object(PHASE, "command", side_effect=["no", unit]) as run:
            self.assertTrue(PHASE.effective_unit(data))
            self.assertEqual(run.call_count, 2)
        with patch.object(PHASE, "command", side_effect=["no", unit + "# drop-in override\n"]):
            self.assertFalse(PHASE.effective_unit(data))
        with patch.object(PHASE, "command", return_value="yes") as run:
            self.assertFalse(PHASE.effective_unit(data))
            self.assertEqual(run.call_count, 1)

    def test_only_two_recorded_writers_stop_in_order(self):
        value = plan_fixture()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            PHASE, "unchanged_running", return_value=True
        ), patch.object(PHASE, "command", side_effect=["", ""]) as run, patch.object(
            PHASE, "paused", return_value=True
        ), patch.object(PHASE, "bound_preflight", return_value=(True, {})), patch.object(
            PHASE.GATE, "read_private", return_value=b"plan"):
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
        ), patch.object(PHASE, "bound_preflight", return_value=(True, {})), patch.object(
            PHASE, "command") as run:
            self.assertFalse(PHASE.pause({}, value, Path(temporary))["baseline_unchanged"])
            run.assert_not_called()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            PHASE, "unchanged_running", return_value=True
        ), patch.object(PHASE, "command", return_value=None) as run, patch.object(
            PHASE, "paused", return_value=False
        ), patch.object(PHASE, "bound_preflight", return_value=(True, {})), patch.object(
            PHASE.GATE, "read_private", return_value=b"plan"):
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
            ), patch.object(PHASE, "backup_idle", return_value=True), patch.object(
                PHASE, "effective_unit", return_value=True
            ), patch.object(PHASE, "bound_preflight", return_value=(True, {})), patch.object(
                PHASE, "command") as run:
                checks = PHASE.backup({"backup_root": directory}, value, directory, "a" * 64)
            self.assertFalse(checks["pause_pass"])
            run.assert_not_called()

    def test_backup_rejects_effective_unit_drift_before_start(self):
        value = plan_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "pause-report.json").write_text('{"phase":"pause","status":"PASS"}')
            with patch.object(PHASE.GATE, "read_private", side_effect=lambda path, owner: path.read_bytes()), patch.object(
                PHASE, "paused", return_value=True
            ), patch.object(PHASE, "backup_idle", return_value=True), patch.object(
                PHASE, "effective_unit", return_value=False
            ), patch.object(PHASE, "bound_preflight", return_value=(True, {})), patch.object(
                PHASE, "command") as run:
                checks = PHASE.backup({"backup_root": directory}, value, directory, "a" * 64)
            self.assertFalse(checks["effective_unit_unchanged"])
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
                PHASE, "effective_unit", return_value=True
            ), patch.object(PHASE, "bound_preflight", return_value=(True, {})
            ), patch.object(
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
                PHASE, "effective_unit", return_value=True
            ), patch.object(PHASE, "bound_preflight", return_value=(True, {})
            ), patch.object(
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

    def test_render_failure_writes_sanitized_stop_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "stage"
            stage.mkdir()
            report = root / "report.json"
            with patch.object(PHASE.os, "geteuid", return_value=0), patch.object(
                PHASE.GATE, "protected", return_value=True
            ), patch.object(PHASE, "profile", return_value=({"project_directory": root / "project"}, "b" * 64)), patch.object(
                PHASE, "stage", return_value=stage
            ), patch.object(PHASE, "plan", side_effect=RuntimeError("sensitive render detail")):
                result = PHASE.main(["plan", "--profile", str(root / "profile.json"),
                    "--stage-run-id", "123-1", "--manifest-sha256", "a" * 64,
                    "--report", str(report)])
            self.assertEqual(result, 1)
            self.assertEqual(json.loads(report.read_text())["status"], "STOP")
            self.assertEqual(json.loads((stage / "plan-report.json").read_text())["status"], "STOP")
            self.assertNotIn("sensitive", report.read_text())


if __name__ == "__main__":
    unittest.main()
