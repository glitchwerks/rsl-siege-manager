"""New-instance database proof rejects partial and unsafe bootstrap state."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-db-bootstrap-readiness.py"
SPEC = importlib.util.spec_from_file_location("portable_db_bootstrap", SCRIPT)
BOOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOT)


class BootstrapReadinessTests(unittest.TestCase):
    def test_only_complete_empty_least_privilege_instance_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "stack.env").write_text("COMPOSE_PROJECT_NAME=synthetic\n")
            (project / "stack.env").chmod(0o600)
            with patch.object(BOOT, "postgres_container", return_value="a" * 64), patch.object(
                BOOT, "command", return_value="a" * 64
            ), patch.object(BOOT, "query", return_value="t") as query:
                def result(container, database, sql):
                    return "0" if "count(*) FROM pg_class" in sql else "t"
                query.side_effect = result
                checks = BOOT.inspect(project)
            self.assertTrue(all(checks.values()), checks)
            self.assertEqual(query.call_count, 6)

    def test_partial_roles_or_nonempty_database_stop(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "stack.env").write_text("COMPOSE_PROJECT_NAME=synthetic\n")
            (project / "stack.env").chmod(0o600)
            with patch.object(BOOT, "postgres_container", return_value="a" * 64), patch.object(
                BOOT, "command", return_value="a" * 64
            ), patch.object(BOOT, "query") as query:
                query.side_effect = lambda container, database, sql: (
                    "1" if database == "mom_bot" and "count(*) FROM pg_class" in sql else
                    None if "rolname='mom_app'" in sql else
                    "0" if "count(*) FROM pg_class" in sql else "t")
                checks = BOOT.inspect(project)
            self.assertFalse(checks["mom_app_least_privilege"])
            self.assertFalse(checks["mom_bot_empty"])

    def test_project_identity_and_report_are_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            stack = project / "stack.env"
            stack.write_text("COMPOSE_PROJECT_NAME=synthetic\n")
            stack.chmod(0o600)
            self.assertEqual(BOOT.project_name(project), "synthetic")
            stack.chmod(0o644)
            self.assertIsNone(BOOT.project_name(project))
            stack.chmod(0o600)
            report = project / "result.json"
            with patch.object(BOOT, "inspect", return_value={"roles": False}):
                self.assertEqual(BOOT.main(["--project-directory", str(project),
                                            "--report", str(report)]), 1)
            self.assertEqual(json.loads(report.read_text())["status"], "STOP")
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            self.assertEqual(BOOT.main(["--project-directory", str(project),
                                        "--report", str(report)]), 2)


if __name__ == "__main__":
    unittest.main()
