import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LOCAL = load("vps_access", "scripts/portable-vps-access-check.py")
REMOTE = load("vps_remote", "scripts/portable-vps-remote.py")


class AccessTests(unittest.TestCase):
    def test_pinned_ed25519_and_ecdsa_host_keys(self):
        for algorithm in ("ssh-ed25519", "ecdsa-sha2-nistp256"):
            with self.subTest(algorithm=algorithm):
                environment = {
                    "VPS_TAILSCALE_HOST": "100.117.110.57",
                    "VPS_SSH_USER": "root",
                    "VPS_SSH_HOST_FINGERPRINT": "SHA256:" + "A" * 43,
                    "VPS_DEPLOY_SSH_KEY": "-----BEGIN OPENSSH PRIVATE KEY-----\n" + "x" * 100,
                    "VPS_SSH_KNOWN_HOSTS": "100.117.110.57 " + algorithm + " " + "a" * 80,
                }
                results = [(True, ""), (True, "256 SHA256:" + "A" * 43 + " host"), (True, "PASS\n")]
                with patch.dict(os.environ, environment, clear=True), patch.object(
                    LOCAL, "run", side_effect=results
                ):
                    self.assertTrue(all(LOCAL.check().values()))

    def test_missing_settings_fail_without_ssh(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(LOCAL, "run") as runner:
            checks = LOCAL.check()
        self.assertFalse(all(checks.values()))
        runner.assert_not_called()

    def test_remote_rejects_any_other_command(self):
        with patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "docker compose up -d"}):
            self.assertEqual(REMOTE.main(), 1)

    def test_remote_access_check_is_read_only(self):
        with patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "access-check"}), patch.object(
            REMOTE, "private_directory", return_value=True
        ), patch.object(REMOTE, "succeeds", return_value=True) as runner:
            self.assertEqual(REMOTE.main(), 0)
        self.assertEqual(runner.call_count, 3)

    def test_report_exclusive_and_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            with patch.object(LOCAL, "check", return_value={"ssh_key": False}), patch(
                "sys.argv", ["access", "--report", str(report)]
            ):
                self.assertEqual(LOCAL.main(), 1)
                self.assertEqual(LOCAL.main(), 2)
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("PRIVATE KEY", report.read_text())


if __name__ == "__main__":
    unittest.main()
