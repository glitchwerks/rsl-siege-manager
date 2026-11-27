"""The rehearsal checkout installer stops before replacing existing host state."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-rehearsal-checkout.py"
SPEC = importlib.util.spec_from_file_location("rehearsal_checkout", SCRIPT)
checkout = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checkout)
SHA = "a" * 40


class RehearsalCheckoutTests(unittest.TestCase):
    def test_occupied_target_stops_before_clone(self):
        with (patch.object(checkout, "host_checks", return_value={"identity": True}),
              patch.object(checkout, "unused", side_effect=[False, True]),
              patch.object(checkout, "success") as run):
            checks = checkout.install("100.93.16.52", "SHA256:" + "A" * 43, SHA)
        self.assertFalse(checks["target_unused"])
        run.assert_not_called()

    def test_existing_stage_stops_before_clone(self):
        with (patch.object(checkout, "host_checks", return_value={"identity": True}),
              patch.object(checkout, "unused", side_effect=[True, False]),
              patch.object(checkout, "success") as run):
            checks = checkout.install("100.93.16.52", "SHA256:" + "A" * 43, SHA)
        self.assertFalse(checks["stage_unused"])
        run.assert_not_called()

    def test_main_refuses_install_without_confirmation_and_snapshot(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            args = ["checkout", "install", "--expected-ip", "100.93.16.52",
                    "--expected-fingerprint", "SHA256:" + "A" * 43,
                    "--source-sha", SHA, "--report", str(report)]
            descriptor = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with (patch("sys.argv", args),
                  patch.object(checkout, "reserve_report", return_value=descriptor),
                  patch("builtins.print"),
                  patch.object(checkout, "install") as install):
                self.assertEqual(checkout.main(), 1)
            install.assert_not_called()
            value = json.loads(report.read_text())
            self.assertFalse(value["checks"]["phase_confirmation"])
            self.assertFalse(value["checks"]["snapshot_confirmed"])

    def test_checkout_checks_require_exact_head_and_clean_status(self):
        head = subprocess.CompletedProcess([], 0, SHA.encode() + b"\n", b"")
        remote = subprocess.CompletedProcess([], 0, checkout.SOURCE_URL.encode() + b"\n", b"")
        dirty = subprocess.CompletedProcess([], 0, b" M README.md\n", b"")
        with (patch.object(checkout, "protected_directory", return_value=True),
              patch.object(checkout, "command", side_effect=[head, remote, dirty]),
              patch.object(Path, "is_file", return_value=True),
              patch.object(Path, "is_symlink", return_value=False)):
            checks = checkout.checkout_checks(SHA)
        self.assertTrue(checks["exact_head"])
        self.assertFalse(checks["clean_checkout"])

    def test_exit_node_policy_route_blocks_host_check(self):
        def routes(args, timeout=30):
            data = ([{"dst": "default", "dev": "ens33"}] if args[-1] == "default"
                    else [{"dst": "1.1.1.1", "dev": "tailscale0"}])
            return subprocess.CompletedProcess(args, 0, json.dumps(data).encode(), b"")
        with patch.object(checkout, "command", side_effect=routes):
            self.assertFalse(checkout.route_safe())

    def test_report_path_must_be_root_private(self):
        self.assertIsNone(checkout.reserve_report(Path("/tmp/report.json")))


if __name__ == "__main__":
    unittest.main()
