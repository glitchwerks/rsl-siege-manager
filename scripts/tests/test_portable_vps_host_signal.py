"""Host pressure producer uses synthetic counters and private disposable paths."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid


SPEC = importlib.util.spec_from_file_location("host_signal", Path(__file__).parents[1] / "portable-vps-host-signal.py")
host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(host)


def config(root):
    database, backup = root / "database", root / "backups"
    database.mkdir()
    backup.mkdir()
    return {"database_storage_path": str(database), "backup_state_root": str(backup),
            "minimum_available_memory_bytes": 512 * host.MIB,
            "maximum_swap_used_bytes": 256 * host.MIB,
            "minimum_database_disk_bytes": 10 * host.GIB,
            "minimum_backup_disk_bytes": 5 * host.GIB,
            "baseline_boot_id": str(uuid.uuid4()), "baseline_oom_kills": 2}


MEMORY = "MemTotal: 4194304 kB\nMemAvailable: 1048576 kB\nSwapTotal: 1048576 kB\nSwapFree: 1048576 kB\n"


class HostSignalTests(unittest.TestCase):
    def test_exact_private_configuration_and_policy_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "host.json"
            value = config(root)
            path.write_text(json.dumps(value)); path.chmod(0o600)
            self.assertEqual(host.configuration(path), value)
            for change in ({"maximum_swap_used_bytes": True}, {"baseline_boot_id": "wrong"},
                           {"minimum_backup_disk_bytes": 0}, {"backup_state_root": "/missing"}):
                path.write_text(json.dumps({**value, **change}))
                with self.assertRaises(ValueError):
                    host.configuration(path)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                host.configuration(path)

    def test_host_pressure_and_oom_baseline_are_independent_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            value = config(Path(tmp))
            disk = lambda path: 20 * host.GIB
            checks, measurements = host.inspect(value, meminfo=MEMORY, vmstat="oom_kill 2\n",
                                                  boot_id=value["baseline_boot_id"], disk=disk)
            self.assertTrue(all(checks.values()))
            self.assertEqual(measurements["available_memory_bytes"], host.GIB)
            self.assertEqual(measurements["swap_used_bytes"], 0)
            changed = MEMORY.replace("SwapFree: 1048576", "SwapFree: 0")
            checks, _ = host.inspect(value, meminfo=changed, vmstat="oom_kill 3\n",
                                     boot_id=value["baseline_boot_id"], disk=lambda path: 0)
            self.assertFalse(checks["no_new_oom"])
            self.assertFalse(checks["swap_headroom"])
            self.assertFalse(checks["database_disk_headroom"])
            self.assertFalse(checks["backup_disk_headroom"])
            checks, _ = host.inspect(value, meminfo=MEMORY, vmstat="oom_kill 0\n",
                                     boot_id=str(uuid.uuid4()), disk=disk)
            self.assertFalse(checks["same_boot"])
            self.assertFalse(checks["no_new_oom"])

    def test_missing_duplicate_and_impossible_counters_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            value = config(Path(tmp))
            for memory in (MEMORY.replace("MemAvailable: 1048576 kB\n", ""),
                           MEMORY + "MemTotal: 4194304 kB\n",
                           MEMORY.replace("MemAvailable: 1048576", "MemAvailable: 9999999")):
                with self.assertRaises(ValueError):
                    host.inspect(value, meminfo=memory, vmstat="oom_kill 2", boot_id=value["baseline_boot_id"],
                                 disk=lambda path: 20 * host.GIB)

    def test_report_is_private_sanitized_and_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value = config(root)
            configuration = root / "host.json"
            configuration.write_text(json.dumps(value))
            configuration.chmod(0o600)
            report = root / "report.json"
            arguments = ["--config", str(configuration), "--report", str(report)]
            with patch.object(host, "inspect", return_value=(
                     {name: True for name in host.CHECKS}, {"available_memory_bytes": host.GIB})) as inspect:
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(host.main(arguments), 2)
                inspect.assert_not_called()
                self.assertFalse(report.exists())
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(host.main(arguments + ["--confirm-readonly-host"]), 0)
            evidence = json.loads(report.read_text())
            self.assertEqual(evidence["status"], "PASS")
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            for sensitive in (value["database_storage_path"], value["backup_state_root"], value["baseline_boot_id"]):
                self.assertNotIn(sensitive, report.read_text())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(host.main(arguments + ["--confirm-readonly-host"]), 2)

    def test_failed_publication_keeps_incomplete_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value = config(root)
            configuration = root / "host.json"
            configuration.write_text(json.dumps(value))
            configuration.chmod(0o600)
            report = root / "report.json"
            with patch.object(host, "inspect", return_value=(
                     {name: True for name in host.CHECKS}, {})), patch.object(host.os, "replace", side_effect=OSError):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(host.main(["--config", str(configuration), "--report", str(report),
                                                "--confirm-readonly-host"]), 2)
            self.assertEqual(json.loads(report.read_text()), {"status": "INCOMPLETE"})


if __name__ == "__main__":
    unittest.main()
