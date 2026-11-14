"""Local database sender safety; Docker, PostgreSQL and HTTPS are mocked."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-database-monitor.py"
SPEC = importlib.util.spec_from_file_location("database_monitor", SCRIPT)
MONITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MONITOR)


class DatabaseMonitorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "database.json"
        self.config.write_text(json.dumps({
            "project": "siege-ci", "postgres_image": "postgres@sha256:" + "a" * 64,
            "postgres_id": "b" * 64, "postgres_started_at": "2026-11-14T10:00:00Z",
            "postgres_restarts": 0, "storage_path": str(self.root),
            "minimum_disk_bytes": 1024**3,
            "max_database_bytes": {"siege": 4 * 1024**3, "mom_bot": 3 * 1024**3},
            "max_connection_percent": 80,
            "revisions": {"siege": "0012", "mom_bot": "b6_member"}}))
        self.config.chmod(0o600)
        self.endpoint = self.root / "endpoint"
        self.endpoint.write_text("https://monitor.fixture-domain.net/private-token")
        self.endpoint.chmod(0o600)
        self.observation = self.root / "observation.json"
        self.report = self.root / "delivery.json"
        self.args = ["--config", str(self.config), "--endpoint-file", str(self.endpoint),
                     "--observation-report", str(self.observation), "--report", str(self.report)]
        self.gates = ["--confirm-readonly-databases", "--confirm-notification"]

    def run_monitor(self, gates=None, root=True):
        with patch.object(MONITOR, "root_required", return_value=root), redirect_stdout(io.StringIO()) as output:
            code = MONITOR.main(self.args + (self.gates if gates is None else gates))
        self.assertEqual(output.getvalue().strip(), "PASS" if code == 0 else "STOP")
        raw = self.report.read_text()
        self.assertNotIn("private-token", raw)
        self.assertNotIn("siege-ci", raw)
        return code, json.loads(raw)

    def observe(self, passed):
        def fake(args):
            path = Path(args[args.index("--report") + 1])
            checks = {name: passed for name in MONITOR.database.CHECK_NAMES}
            MONITOR.database.publish(path, {"schema": 1, "phase": "database-signal",
                "status": "PASS" if passed else "STOP", "checks": checks})
            return 0 if passed else 1
        return fake

    def test_root_and_both_confirmations_precede_probe_and_notification(self):
        for gates, root in (([], True), ([self.gates[0]], True), ([self.gates[1]], True),
                            (self.gates, False)):
            with self.subTest(gates=gates, root=root), patch.object(MONITOR.database, "main") as observe, patch.object(
                    MONITOR.sender, "deliver") as deliver:
                self.report.unlink(missing_ok=True)
                code, result = self.run_monitor(gates, root)
                self.assertEqual(code, 1)
                self.assertFalse(result["delivery_acknowledged"])
                observe.assert_not_called()
                deliver.assert_not_called()

    def test_fresh_pass_and_failure_send_distinct_signals(self):
        for passed in (True, False):
            with self.subTest(passed=passed), patch.object(
                    MONITOR.database, "main", side_effect=self.observe(passed)), patch.object(
                    MONITOR.sender, "deliver") as deliver:
                self.report.unlink(missing_ok=True)
                self.observation.unlink(missing_ok=True)
                code, result = self.run_monitor()
                self.assertEqual(code, 0 if passed else 1)
                self.assertEqual(result["observation_passed"], passed)
                self.assertTrue(result["delivery_acknowledged"])
                deliver.assert_called_once_with(
                    "https://monitor.fixture-domain.net/private-token", passed)

    def test_missing_or_mismatched_observation_never_sends(self):
        def mismatched(args):
            self.observe(False)(args)
            return 0

        for fake in (lambda _args: 0, mismatched):
            with self.subTest(fake=fake), patch.object(
                    MONITOR.database, "main", side_effect=fake), patch.object(
                    MONITOR.sender, "deliver") as deliver:
                self.report.unlink(missing_ok=True)
                self.observation.unlink(missing_ok=True)
                code, result = self.run_monitor()
                self.assertEqual(code, 1)
                self.assertFalse(result["delivery_acknowledged"])
                deliver.assert_not_called()

    def test_incomplete_probe_stale_evidence_and_unsafe_endpoint_never_send(self):
        with patch.object(MONITOR.database, "main", return_value=2), patch.object(
                MONITOR.sender, "deliver") as deliver:
            self.assertEqual(self.run_monitor()[0], 1)
            deliver.assert_not_called()
        self.report.unlink()
        self.observation.write_text('{"status":"PASS"}')
        with patch.object(MONITOR.database, "main") as observe:
            self.assertEqual(self.run_monitor()[0], 1)
            observe.assert_not_called()
        self.report.unlink()
        self.observation.unlink()
        self.endpoint.chmod(0o644)
        with patch.object(MONITOR.database, "main") as observe:
            self.assertEqual(self.run_monitor()[0], 1)
            observe.assert_not_called()

    def test_overlap_and_failed_delivery_never_report_pass(self):
        with MONITOR.sender.locked(self.root), patch.object(MONITOR.database, "main") as observe:
            self.assertEqual(self.run_monitor()[0], 1)
            observe.assert_not_called()
        self.report.unlink()
        with patch.object(MONITOR.database, "main", side_effect=self.observe(True)), patch.object(
                MONITOR.sender, "deliver", side_effect=MONITOR.sender.Stop("signal_delivery_failed")):
            code, result = self.run_monitor()
        self.assertEqual(code, 1)
        self.assertTrue(result["observation_passed"])
        self.assertFalse(result["delivery_acknowledged"])

    def test_interrupted_publication_preserves_incomplete_marker(self):
        publish = MONITOR.database.publish

        def final_publish_fails(path, value):
            if path == self.report:
                raise OSError("publication failed")
            publish(path, value)

        with patch.object(MONITOR.database, "main", side_effect=self.observe(True)), patch.object(
                MONITOR.sender, "deliver"), patch.object(
                MONITOR.database, "publish", side_effect=final_publish_fails):
            with patch.object(MONITOR, "root_required", return_value=True), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(MONITOR.main(self.args + self.gates), 2)
        self.assertEqual(output.getvalue().strip(), "STOP")
        self.assertEqual(json.loads(self.report.read_text()), {"status": "INCOMPLETE"})


if __name__ == "__main__":
    unittest.main()
