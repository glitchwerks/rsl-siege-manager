"""Signal exporter safety; every observation and HTTPS request is mocked."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

SPEC = importlib.util.spec_from_file_location("monitor", Path(__file__).parents[1] / "portable-monitor.py")
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.secret = self.root / "endpoint"
        self.secret.write_text("https://monitor.fixture-domain.net/private-token")
        self.secret.chmod(0o600)
        self.args = ["--config", str(self.root / "pilot.json"), "--endpoint-file", str(self.secret),
                     "--observation-report", str(self.root / "observation.json"),
                     "--report", str(self.root / "delivery.json")]
        self.gates = ["--confirm-readonly-pilot", "--confirm-notification"]

    def run_main(self, args):
        with redirect_stdout(io.StringIO()) as output:
            code = monitor.main(args)
        raw = output.getvalue()
        self.assertNotIn("private-token", raw)
        self.assertNotIn("fixture-domain", raw)
        return code, json.loads(raw)

    def test_both_gates_required_before_observation_or_delivery(self):
        for gates in ([], [self.gates[0]], [self.gates[1]]):
            with self.subTest(gates=gates), patch.object(monitor.pilot, "main") as observe, patch.object(monitor, "deliver") as send:
                (self.root / "delivery.json").unlink(missing_ok=True)
                code, result = self.run_main(self.args + gates)
                self.assertEqual(code, 1)
                self.assertFalse(result["delivery_acknowledged"])
                observe.assert_not_called()
                send.assert_not_called()

    def test_fresh_success_and_failure_are_separate_signals(self):
        for observation_code in (0, 1):
            with self.subTest(code=observation_code), patch.object(monitor.pilot, "main", return_value=observation_code) as observe, patch.object(monitor, "deliver") as send:
                (self.root / "delivery.json").unlink(missing_ok=True)
                code, result = self.run_main(self.args + self.gates)
                self.assertEqual(code, observation_code)
                self.assertEqual(result["observation_passed"], observation_code == 0)
                self.assertTrue(result["delivery_acknowledged"])
                self.assertFalse(result["pilot_accepted"])
                observe.assert_called_once()
                send.assert_called_once_with("https://monitor.fixture-domain.net/private-token", observation_code == 0)

    def test_old_pass_cannot_be_replayed(self):
        observation = self.root / "observation.json"
        observation.write_text('{"result":"PASS"}')
        with patch.object(monitor.pilot, "main") as observe, patch.object(monitor, "deliver") as send:
            code, result = self.run_main(self.args + self.gates)
        self.assertEqual(code, 1)
        self.assertEqual(result["error"], "fresh_observation_report_required")
        self.assertEqual(observation.read_text(), '{"result":"PASS"}')
        observe.assert_not_called()
        send.assert_not_called()

    def test_no_overwrite_or_input_alias(self):
        previous = self.root / "delivery.json"
        previous.write_text("preserve")
        with patch.object(monitor, "deliver") as send:
            self.assertEqual(self.run_main(self.args + self.gates)[0], 1)
            self.assertEqual(previous.read_text(), "preserve")
            send.assert_not_called()
        previous.unlink()
        self.args[5] = self.args[1]
        with patch.object(monitor.pilot, "main") as observe:
            self.assertEqual(self.run_main(self.args + self.gates)[0], 1)
            observe.assert_not_called()

    def test_unsafe_endpoint_never_observes_or_sends(self):
        self.secret.chmod(0o644)
        with patch.object(monitor.pilot, "main") as observe, patch.object(monitor, "deliver") as send:
            self.assertEqual(self.run_main(self.args + self.gates)[0], 1)
            observe.assert_not_called()
            send.assert_not_called()

    def test_endpoint_symlink_and_fifo_refused(self):
        import os
        target = self.root / "target"
        self.secret.rename(target)
        self.secret.symlink_to(target)
        with self.assertRaises(monitor.Stop):
            monitor.endpoint(self.secret)
        self.secret.unlink()
        os.mkfifo(self.secret, 0o600)
        with self.assertRaises(monitor.Stop):
            monitor.endpoint(self.secret)

    def test_invalid_urls_and_oversized_endpoint(self):
        for value in ("http://host.net/token", "https://user:pass@host.net/token", "https://host.net",
                      "https://host.net/token?secret=yes", "https://host.net/token#part",
                      "https://host.net/token/fail", "https://host.net:443/token",
                      "https://host.net/to\nken", "x" * 4097):
            with self.subTest(value=value[:50]):
                self.secret.write_text(value)
                with self.assertRaises((monitor.Stop, ValueError)):
                    monitor.endpoint(self.secret)

    def test_delivery_failure_is_sanitized_and_not_pass(self):
        with patch.object(monitor.pilot, "main", return_value=0), patch.object(monitor, "deliver", side_effect=monitor.Stop("signal_delivery_failed")):
            code, result = self.run_main(self.args + self.gates)
        self.assertEqual(code, 1)
        self.assertTrue(result["observation_passed"])
        self.assertFalse(result["delivery_acknowledged"])

    def test_empty_post_success_and_failure_no_proxy_or_redirect(self):
        for success in (True, False):
            response = MagicMock(status=200)
            response.read.return_value = b"OK"
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value = response
            with patch.object(monitor.urllib.request, "build_opener", return_value=opener) as build:
                monitor.deliver("https://host.net/token", success)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://host.net/token" + ("" if success else "/fail"))
            self.assertEqual(request.data, b"")
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.headers, {})
            self.assertEqual(opener.open.call_args.kwargs, {"timeout": 10})
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertIsInstance(build.call_args.args[1], monitor.NoRedirect)
            with self.assertRaises(monitor.Stop):
                build.call_args.args[1].redirect_request(None, None, None, None, None, None)

    def test_200_unknown_rate_limited_and_large_bodies_refused(self):
        for body in (b"OK (not found)", b"OK (rate limited)", b"OK" + b" " * 31, b"secret"):
            response = MagicMock(status=200)
            response.read.return_value = body
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value = response
            with patch.object(monitor.urllib.request, "build_opener", return_value=opener), self.assertRaises(monitor.Stop):
                monitor.deliver("https://host.net/token", True)

    def test_delivery_deadline_and_existing_timer_protected(self):
        previous = monitor.signal.getsignal(monitor.signal.SIGALRM)
        with self.assertRaisesRegex(monitor.Stop, "signal_delivery_timeout"):
            with monitor.delivery_deadline():
                monitor.signal.getsignal(monitor.signal.SIGALRM)()
        self.assertEqual(monitor.signal.getsignal(monitor.signal.SIGALRM), previous)
        self.assertEqual(monitor.signal.getitimer(monitor.signal.ITIMER_REAL), (0.0, 0.0))
        with patch.object(monitor.signal, "getitimer", return_value=(5.0, 0.0)), patch.object(monitor.signal, "setitimer") as timer:
            with self.assertRaisesRegex(monitor.Stop, "existing_process_timer"), monitor.delivery_deadline():
                pass
            timer.assert_not_called()

    def test_raw_transport_error_never_leaks(self):
        opener = MagicMock()
        opener.open.side_effect = OSError("https://host.net/secret-token")
        with patch.object(monitor.urllib.request, "build_opener", return_value=opener), self.assertRaisesRegex(monitor.Stop, "^signal_delivery_failed$"):
            monitor.deliver("https://host.net/token", True)

    def test_overlap_never_observes_or_sends(self):
        with monitor.locked(self.root), patch.object(monitor.pilot, "main") as observe, patch.object(monitor, "deliver") as send:
            code, result = self.run_main(self.args + self.gates)
        self.assertEqual(code, 1)
        self.assertEqual(result["error"], "monitor_already_running")
        observe.assert_not_called()
        send.assert_not_called()

    def test_actual_observer_invalid_configuration_sends_failure_only(self):
        # Exercises real observation error handling without Docker/HTTPS access.
        with patch.object(monitor, "deliver") as send:
            code, result = self.run_main(self.args + self.gates)
        self.assertEqual(code, 1)
        self.assertFalse(result["observation_passed"])
        send.assert_called_once_with("https://monitor.fixture-domain.net/private-token", False)
        self.assertTrue((self.root / "observation.json").exists())


if __name__ == "__main__":
    unittest.main()
