"""Exercise the incident inspector on private fixtures without host access."""

import importlib.util
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-backup-incident-inspect.py"
SPEC = importlib.util.spec_from_file_location("incident_inspect", SCRIPT)
inspector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspector)


class IncidentInspectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.state = self.base / "state"
        self.state.mkdir(mode=0o700)
        (self.state / "runs").mkdir(mode=0o700)
        self.profile = self.base / "job.json"
        self.profile.write_text(json.dumps({"root": str(self.state), "passwords": "sensitive"}))
        self.profile.chmod(0o600)
        self.patches = [patch.object(inspector, "PROFILE", self.profile),
                        patch.object(inspector, "STATE", self.state),
                        patch.object(inspector.os, "geteuid", return_value=0),
                        patch.object(inspector.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=os.getuid()))]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def write_run(self, run_id, started, result, error=None):
        directory = self.state / "runs" / run_id
        directory.mkdir(mode=0o700)
        report = {"schema": 1, "phase": "run", "timestamp": started,
                  "finished_at": started, "result": result,
                  "checks": {"database_backup": result == "PASS",
                             "independent_failure_acknowledged": result == "STOP"}}
        if error is not None:
            report["error"] = error
        path = directory / "job.json"
        path.write_text(json.dumps(report))
        path.chmod(0o600)

    def test_sanitized_history_identifies_failure_without_leaking_details(self):
        self.write_run("00000000-0000-4000-8000-000000000001", "2026-11-15T18:01:00+00:00", "PASS")
        self.write_run("00000000-0000-4000-8000-000000000002", "2026-11-15T19:01:00+00:00", "STOP",
                       "https://secret.example/private-token")
        self.write_run("00000000-0000-4000-8000-000000000003", "2026-11-25T20:01:00+00:00", "STOP",
                       "backup_phase_failed")
        phase = self.state / "runs/00000000-0000-4000-8000-000000000003/seal.json"
        phase.write_text(json.dumps({"phase": "seal", "result": "STOP",
                                     "error": "remote_token=https://secret.example/private-token"}))
        phase.chmod(0o600)
        with patch.object(inspector.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout=b"LoadState=loaded\nActiveState=active\nResult=success\n")):
            result = inspector.inspect()
        self.assertEqual(result["inspection_status"], "PASS")
        self.assertEqual(result["backup_status"], "STOP")
        self.assertEqual(result["latest"]["error_code"], "backup_phase_failed")
        self.assertEqual(result["latest"]["phase_reports"]["seal"]["result"], "STOP")
        self.assertIsNone(result["latest"]["phase_reports"]["seal"]["error_code"])
        self.assertIsNone(result["first_failure_since_nov_15"]["error_code"])
        self.assertEqual(result["last_success"]["result"], "PASS")
        self.assertNotIn("secret.example", json.dumps(result))
        self.assertNotIn("00000000-0000-4000", json.dumps(result))
        self.assertNotIn(str(self.state), json.dumps(result))

    def test_symlinked_profile_stops_without_reading_reports(self):
        target = self.base / "target"
        target.write_text(self.profile.read_text())
        self.profile.unlink()
        self.profile.symlink_to(target)
        with patch.object(inspector, "reports") as reports:
            result = inspector.inspect()
        self.assertEqual(result["inspection_status"], "STOP")
        reports.assert_not_called()

    def test_incomplete_run_is_newer_than_prior_success(self):
        self.write_run("00000000-0000-4000-8000-000000000001", "2026-11-15T18:01:00+00:00", "PASS")
        success = self.state / "runs/00000000-0000-4000-8000-000000000001/job.json"
        old_time = datetime.fromisoformat("2026-11-15T18:02:00+00:00").timestamp()
        os.utime(success, (old_time, old_time))
        directory = self.state / "runs/00000000-0000-4000-8000-000000000002"
        directory.mkdir(mode=0o700)
        incomplete = directory / "job.json"
        incomplete.write_text('{"result":"INCOMPLETE"}')
        incomplete.chmod(0o600)
        phase = directory / "backup.json"
        phase.write_text(json.dumps({"phase": "backup", "result": "STOP", "error": "source_unavailable"}))
        phase.chmod(0o600)
        with patch.object(inspector.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout=b"LoadState=loaded\nActiveState=active\n")):
            result = inspector.inspect()
        self.assertEqual(result["inspection_status"], "PASS")
        self.assertEqual(result["backup_status"], "INCOMPLETE")
        self.assertIsNone(result["latest"]["started_at"])
        self.assertEqual(result["latest"]["phase_reports"]["backup"]["error_code"], "source_unavailable")
        self.assertEqual(result["last_success"]["result"], "PASS")

    def test_unreadable_selected_phase_stops_inspection(self):
        run_id = "00000000-0000-4000-8000-000000000004"
        self.write_run(run_id, "2026-11-25T20:01:00+00:00", "STOP", "backup_phase_failed")
        phase = self.state / "runs" / run_id / "backup.json"
        phase.write_text("not-json")
        phase.chmod(0o600)
        with patch.object(inspector.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout=b"LoadState=loaded\nActiveState=active\n")):
            result = inspector.inspect()
        self.assertEqual(result["inspection_status"], "STOP")
        self.assertEqual(result["backup_status"], "STOP")
        self.assertFalse(result["checks"]["phase_reports_readable"])
        self.assertEqual(result["latest"]["phase_reports"]["backup"]["result"], "UNREADABLE")


if __name__ == "__main__":
    unittest.main()
