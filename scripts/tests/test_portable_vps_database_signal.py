"""Database signal uses app authentication and reports pressure without data content."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-database-signal.py"
SPEC = importlib.util.spec_from_file_location("database_signal", SCRIPT)
SIGNAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SIGNAL)


def config(path):
    return {"project": "siege-ci", "postgres_image": "postgres@sha256:" + "a" * 64,
            "postgres_id": "b" * 64, "postgres_started_at": "2026-11-14T10:00:00Z",
            "postgres_restarts": 0, "storage_path": str(path),
            "minimum_disk_bytes": 1024**3,
            "max_database_bytes": {"siege": 4 * 1024**3, "mom_bot": 3 * 1024**3},
            "max_connection_percent": 80,
            "revisions": {"siege": "0012", "mom_bot": "b6_member"}}


def container(cfg):
    return {"project": cfg["project"], "service": "postgres", "id": cfg["postgres_id"],
            "image": cfg["postgres_image"], "running": True, "health": "healthy",
            "oom": False, "restarts": 0, "started_at": cfg["postgres_started_at"]}


class DatabaseSignalTests(unittest.TestCase):
    def test_both_app_roles_and_schema_are_required_without_content_queries(self):
        with patch.object(SIGNAL, "command", return_value=(
            "siege_app|siege|0012|t|1000000|7|100|on\n")) as command:
            result = SIGNAL.database("b" * 64, "siege", time.monotonic() + 10)
        self.assertEqual(result["revision"], "0012")
        arguments = command.call_args.args[0]
        self.assertEqual(arguments[0:4], ["docker", "exec", "--user", "postgres"])
        self.assertNotIn("--env", arguments)
        self.assertIn('PGPASSWORD="$SIEGE_DB_PASSWORD"', arguments[8])
        self.assertIn("BEGIN READ ONLY", SIGNAL.QUERY["siege"])
        self.assertIn("backend_type = 'client backend'", SIGNAL.QUERY["siege"])
        self.assertNotIn("SELECT *", SIGNAL.QUERY["siege"])
        with patch.object(SIGNAL, "command", return_value="postgres|siege|0012|t|1|1|100|on\n"):
            self.assertIsNone(SIGNAL.database("b" * 64, "siege", time.monotonic() + 10))
        with patch.object(SIGNAL, "command", return_value="mom_app|mom_bot|b6_member|f|1|1|100|on\n"):
            self.assertIsNone(SIGNAL.database("b" * 64, "mom_bot", time.monotonic() + 10))

    def test_database_failure_and_pressure_are_distinct(self):
        with tempfile.TemporaryDirectory() as temporary:
            cfg = config(Path(temporary))
            info = container(cfg)
            rows = {"siege": {"revision": "0012", "bytes": 2 * 1024**3,
                              "connections": 12, "max_connections": 100},
                    "mom_bot": {"revision": "b6_member", "bytes": 2 * 1024**3,
                                "connections": 12, "max_connections": 100}}
            filesystem = SimpleNamespace(f_bavail=2 * 1024**3, f_frsize=1)
            with patch.object(SIGNAL, "postgres", return_value=info), patch.object(
                SIGNAL, "storage_source", return_value=Path(temporary)
            ), patch.object(SIGNAL.os, "statvfs", return_value=filesystem), patch.object(
                SIGNAL, "database", side_effect=lambda _id, name, _deadline: rows[name]):
                checks, metrics = SIGNAL.inspect(cfg)
                self.assertTrue(all(checks.values()), checks)
                self.assertEqual(metrics["connections"], 12)
                rows["mom_bot"] = None
                checks, _metrics = SIGNAL.inspect(cfg)
                self.assertFalse(checks["mom_authenticated_schema"])
                self.assertTrue(checks["siege_authenticated_schema"])
                self.assertFalse(checks["connections_within_budget"])
                rows["mom_bot"] = {"revision": "b6_member", "bytes": 2 * 1024**3,
                                   "connections": 85, "max_connections": 100}
                checks, _metrics = SIGNAL.inspect(cfg)
                self.assertFalse(checks["connections_within_budget"])
                self.assertTrue(checks["mom_authenticated_schema"])

    def test_missing_confirmation_never_queries_docker(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(SIGNAL, "postgres") as postgres:
            root = Path(temporary)
            self.assertEqual(SIGNAL.main(["--config", str(root / "config"),
                "--report", str(root / "report")]), 2)
            postgres.assert_not_called()
            self.assertFalse((root / "report").exists())

    def test_final_report_is_sanitized_and_atomic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configuration = root / "config.json"
            report = root / "report.json"
            configuration.write_text("{}")
            checks = {name: True for name in SIGNAL.CHECK_NAMES}
            measurements = {"siege_bytes": 1024, "mom_bytes": 2048,
                            "disk_available_bytes": 4096, "connections": 3, "max_connections": 100}
            with patch.object(SIGNAL.os, "geteuid", return_value=0), patch.object(
                SIGNAL, "read_config", return_value={"project": "private-name"}
            ), patch.object(SIGNAL, "private"), patch.object(
                SIGNAL, "inspect", return_value=(checks, measurements)):
                self.assertEqual(SIGNAL.main(["--config", str(configuration),
                    "--report", str(report), "--confirm-readonly-databases"]), 0)
            raw = report.read_text()
            self.assertNotIn("private-name", raw)
            self.assertEqual(json.loads(raw)["status"], "PASS")
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
