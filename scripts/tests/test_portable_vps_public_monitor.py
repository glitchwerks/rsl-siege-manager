"""Off-host public observation delivery; no real HTTPS requests or notifications."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, patch


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
        self.endpoints = self.root / "endpoints"
        self.endpoints.mkdir(mode=0o700)
        for name in MONITOR.SIGNALS:
            endpoint = self.endpoints / f"{name}-url"
            endpoint.write_text(f"https://monitor.fixture-domain.net/{name}-private-token")
            endpoint.chmod(0o600)
        self.observation = self.root / "observation.json"
        self.report = self.root / "delivery.json"
        self.args = ["--config", str(self.config), "--endpoint-directory", str(self.endpoints),
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
                self.assertEqual(deliver.call_args_list, [
                    call(f"https://monitor.fixture-domain.net/{name}-private-token", passed)
                    for name in MONITOR.SIGNALS])

    def test_failed_surface_signals_only_its_receiver(self):
        for failed_check, failed_signal in (("apex_home", "website"),
                                            ("api_health", "api"),
                                            ("release_identity", "identity")):
            with self.subTest(failed_check=failed_check):
                checks = {name: True for name in MONITOR.public.CHECKS}
                checks[failed_check] = False
                with patch.object(MONITOR.public, "inspect", return_value=checks), patch.object(
                        MONITOR.sender, "deliver") as deliver:
                    code, result = self.run_monitor()
                self.assertEqual(code, 1)
                for name in MONITOR.SIGNALS:
                    self.assertEqual(result["signals"][name]["healthy"], name != failed_signal)
                self.assertEqual(deliver.call_args_list, [
                    call(f"https://monitor.fixture-domain.net/{name}-private-token",
                         name != failed_signal) for name in MONITOR.SIGNALS])
                self.observation.unlink()
                self.report.unlink()

    def test_duplicate_endpoint_refuses_observation_and_delivery(self):
        (self.endpoints / "api-url").write_text(
            "https://monitor.fixture-domain.net/website-private-token")
        with patch.object(MONITOR.public, "main") as observe, patch.object(
                MONITOR.sender, "deliver") as deliver:
            self.assertEqual(self.run_monitor()[0], 1)
        observe.assert_not_called()
        deliver.assert_not_called()

    def test_invalid_observation_report_never_sends(self):
        def invalid_observation(_arguments):
            self.observation.write_text('{"schema":1,"phase":"public-signal","status":"PASS","checks":{}}')
            self.observation.chmod(0o600)
            return 0

        with patch.object(MONITOR.public, "main", side_effect=invalid_observation), patch.object(
                MONITOR.sender, "deliver") as deliver:
            code, result = self.run_monitor()
        self.assertEqual(code, 1)
        self.assertFalse(result["delivery_acknowledged"])
        deliver.assert_not_called()

    def test_one_delivery_failure_does_not_suppress_other_checks(self):
        def deliver_one_fails(url, _healthy):
            if "/api-private-token" in url:
                raise MONITOR.sender.Stop("signal_delivery_failed")

        with patch.object(MONITOR.public, "inspect", return_value={
                name: True for name in MONITOR.public.CHECKS}), patch.object(
                MONITOR.sender, "deliver", side_effect=deliver_one_fails) as deliver:
            code, result = self.run_monitor()
        self.assertEqual(code, 1)
        self.assertEqual(deliver.call_count, 3)
        self.assertTrue(result["signals"]["website"]["delivery_acknowledged"])
        self.assertFalse(result["signals"]["api"]["delivery_acknowledged"])
        self.assertTrue(result["signals"]["identity"]["delivery_acknowledged"])

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

    def test_deadline_expiry_never_sends_failure_signal(self):
        with patch.object(MONITOR.public, "request", side_effect=MONITOR.public.DeadlineExpired("expired")), patch.object(
                MONITOR.sender, "deliver") as deliver:
            code, result = self.run_monitor()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(self.observation.read_text())["status"], "INCOMPLETE")
        self.assertFalse(result["delivery_acknowledged"])
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
        with MONITOR.sender.locked(self.endpoints), patch.object(MONITOR.public, "main") as observe:
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
