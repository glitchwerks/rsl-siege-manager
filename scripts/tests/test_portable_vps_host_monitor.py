"""Host receiver sender uses only disposable reports and mocked delivery."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid


SPEC = importlib.util.spec_from_file_location("host_monitor", Path(__file__).parents[1] / "portable-vps-host-monitor.py")
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


class HostMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        database, backup = self.root / "database", self.root / "backup"
        database.mkdir(); backup.mkdir()
        self.config = self.root / "host.json"
        self.config.write_text(json.dumps({
            "database_storage_path": str(database), "backup_state_root": str(backup),
            "minimum_available_memory_bytes": 512 * monitor.host.MIB,
            "maximum_swap_used_bytes": 256 * monitor.host.MIB,
            "minimum_database_disk_bytes": 10 * monitor.host.GIB,
            "minimum_backup_disk_bytes": 5 * monitor.host.GIB,
            "baseline_boot_id": str(uuid.uuid4()), "baseline_oom_kills": 0}))
        self.config.chmod(0o600)
        self.endpoint = self.root / "url"
        self.endpoint.write_text("https://monitor.fixture-domain.net/private-token")
        self.endpoint.chmod(0o600)
        self.observation = self.root / "observation.json"
        self.report = self.root / "delivery.json"
        self.args = ["--config", str(self.config), "--endpoint-file", str(self.endpoint),
                     "--observation-report", str(self.observation), "--report", str(self.report),
                     "--confirm-readonly-host", "--confirm-notification"]

    def run_main(self, args=None):
        with redirect_stdout(io.StringIO()) as output:
            code = monitor.main(self.args if args is None else args)
        self.assertNotIn("private-token", output.getvalue())
        return code, json.loads(self.report.read_text()) if self.report.exists() else None

    def observer(self, code, *, complete=True):
        def run(argv):
            path = Path(argv[argv.index("--report") + 1])
            value = {"schema": 1, "phase": "host-signal", "status": "PASS" if code == 0 else "STOP",
                     "checks": {name: code == 0 for name in monitor.host.CHECKS},
                     "measurements": {name: 1 for name in ("available_memory_bytes", "swap_used_bytes",
                         "oom_kills_since_boot", "database_disk_available_bytes", "backup_disk_available_bytes")}}
            if not complete:
                value = {"status": "INCOMPLETE"}
            path.write_text(json.dumps(value)); path.chmod(0o600)
            return code
        return run

    def test_success_and_completed_failure_send_distinct_signals(self):
        for code in (0, 1):
            with self.subTest(code=code), patch.object(monitor.host, "main", side_effect=self.observer(code)), \
                 patch.object(monitor.sender, "deliver") as deliver:
                self.observation.unlink(missing_ok=True); self.report.unlink(missing_ok=True)
                actual, report = self.run_main()
            self.assertEqual(actual, code)
            self.assertEqual(report["observation_passed"], code == 0)
            self.assertTrue(report["delivery_acknowledged"])
            deliver.assert_called_once_with("https://monitor.fixture-domain.net/private-token", code == 0)
            raw = self.report.read_text()
            self.assertNotIn("private-token", raw)
            self.assertNotIn(str(self.root), raw)

    def test_incomplete_and_missing_reports_send_nothing(self):
        for code, complete in ((2, False), (1, False), (0, False)):
            with self.subTest(code=code), patch.object(monitor.host, "main", side_effect=self.observer(code, complete=complete)), \
                 patch.object(monitor.sender, "deliver") as deliver:
                self.observation.unlink(missing_ok=True); self.report.unlink(missing_ok=True)
                actual, report = self.run_main()
            self.assertEqual(actual, 1)
            self.assertFalse(report["delivery_acknowledged"])
            deliver.assert_not_called()

    def test_confirmation_and_fresh_paths_precede_signal(self):
        with patch.object(monitor.host, "main") as observe, patch.object(monitor.sender, "deliver") as deliver:
            code, report = self.run_main(self.args[:-1])
        self.assertEqual(code, 1)
        observe.assert_not_called(); deliver.assert_not_called()
        self.assertFalse(report["delivery_acknowledged"])
        self.report.unlink()
        self.observation.write_text("old pass"); self.observation.chmod(0o600)
        with patch.object(monitor.host, "main") as observe, patch.object(monitor.sender, "deliver") as deliver:
            code, report = self.run_main()
        self.assertEqual(code, 1)
        observe.assert_not_called(); deliver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
