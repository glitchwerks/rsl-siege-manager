"""Off-host public observation delivery; no real HTTPS requests or notifications."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-public-monitor.py"
SPEC = importlib.util.spec_from_file_location("public_monitor", SCRIPT)
MONITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MONITOR)


class PublicMonitorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "public.json"
        self.config.write_text(json.dumps({"origin": "https://siege.example.io",
            "versions": {"backend_version": "1.6.0", "frontend_version": "1.6.0",
                         "bot_version": "1.6.0", "git_sha": "a" * 40}, "minimum_tls_days": 14}))
        self.config.chmod(0o600)
        self.endpoint = self.root / "endpoint"
        self.endpoint.write_text("https://monitor.fixture-domain.net/private-token")
        self.endpoint.chmod(0o600)
        self.observation = self.root / "observation.json"
        self.report = self.root / "delivery.json"
        self.args = ["--config", str(self.config), "--endpoint-file", str(self.endpoint),
                     "--observation-report", str(self.observation), "--report", str(self.report)]
        self.gates = ["--confirm-external-readonly", "--confirm-notification"]

    def run_monitor(self, gates=None):
        with redirect_stdout(io.StringIO()) as output:
            code = MONITOR.main(self.args + (self.gates if gates is None else gates))
        self.assertEqual(output.getvalue().strip(), "PASS" if code == 0 else "STOP")
        self.assertNotIn("private-token", self.report.read_text())
        self.assertNotIn("siege.example.io", self.report.read_text())
        return code, json.loads(self.report.read_text())

    def test_both_confirmations_required_before_observation_or_notification(self):
        for gates in ([], [self.gates[0]], [self.gates[1]]):
            with self.subTest(gates=gates), patch.object(MONITOR.public, "main") as observe, patch.object(
                    MONITOR.sender, "deliver") as deliver:
                self.report.unlink(missing_ok=True)
                code, result = self.run_monitor(gates)
                self.assertEqual(code, 1)
                self.assertFalse(result["delivery_acknowledged"])
                observe.assert_not_called()
                deliver.assert_not_called()

    def test_fresh_pass_and_failure_send_distinct_empty_signals(self):
        for passed in (True, False):
            with self.subTest(passed=passed), patch.object(MONITOR.public, "inspect", return_value={
                    name: passed for name in MONITOR.public.CHECKS}), patch.object(
                    MONITOR.sender, "deliver") as deliver:
                self.report.unlink(missing_ok=True)
                self.observation.unlink(missing_ok=True)
                code, result = self.run_monitor()
                self.assertEqual(code, 0 if passed else 1)
                self.assertEqual(result["observation_passed"], passed)
                self.assertTrue(result["delivery_acknowledged"])
                self.assertEqual(json.loads(self.observation.read_text())["status"],
                                 "PASS" if passed else "STOP")
                deliver.assert_called_once_with(
                    "https://monitor.fixture-domain.net/private-token", passed)

    def test_preflight_and_incomplete_observation_never_send(self):
        self.config.chmod(0o644)
        with patch.object(MONITOR.public, "main") as observe, patch.object(
                MONITOR.sender, "deliver") as deliver:
            self.assertEqual(self.run_monitor()[0], 1)
            observe.assert_not_called()
            deliver.assert_not_called()
        self.config.chmod(0o600)
        self.report.unlink()
        with patch.object(MONITOR.public, "main", return_value=2), patch.object(
                MONITOR.sender, "deliver") as deliver:
            self.assertEqual(self.run_monitor()[0], 1)
            deliver.assert_not_called()

    def test_old_observation_and_overlapping_run_are_refused(self):
        self.observation.write_text('{"status":"PASS"}')
        with patch.object(MONITOR.public, "main") as observe, patch.object(
                MONITOR.sender, "deliver") as deliver:
            self.assertEqual(self.run_monitor()[0], 1)
            observe.assert_not_called()
            deliver.assert_not_called()
        self.observation.unlink()
        self.report.unlink()
        with MONITOR.sender.locked(self.root), patch.object(MONITOR.public, "main") as observe:
            self.assertEqual(self.run_monitor()[0], 1)
            observe.assert_not_called()

    def test_delivery_failure_never_reports_pass(self):
        with patch.object(MONITOR.public, "inspect", return_value={
                name: True for name in MONITOR.public.CHECKS}), patch.object(
                MONITOR.sender, "deliver", side_effect=MONITOR.sender.Stop("signal_delivery_failed")):
            code, result = self.run_monitor()
        self.assertEqual(code, 1)
        self.assertTrue(result["observation_passed"])
        self.assertFalse(result["delivery_acknowledged"])

    def test_failed_final_publication_preserves_incomplete_marker(self):
        with patch.object(MONITOR.public, "inspect", return_value={
                name: True for name in MONITOR.public.CHECKS}), patch.object(
                MONITOR.sender, "deliver"), patch.object(
                MONITOR.public.os, "replace", side_effect=OSError("publication failed")):
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(MONITOR.main(self.args + self.gates), 2)
        self.assertEqual(output.getvalue().strip(), "STOP")
        self.assertEqual(json.loads(self.report.read_text()), {"status": "INCOMPLETE"})


if __name__ == "__main__":
    unittest.main()
