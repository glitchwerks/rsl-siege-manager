"""Synthetic running-container checks; no Docker or production access."""

import importlib.util
import io
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-day-role-effective.py"
SPEC = importlib.util.spec_from_file_location("day_role_effective", SCRIPT)
effective = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(effective)
IDENTIFIER = "a" * 64
IMAGE = "ghcr.io/glitchwerks/siege-api@sha256:" + "b" * 64
PROJECT = "rsl-test"
SECRET = "private-value-must-not-enter-report"


def model(enabled="true"):
    return {"name": PROJECT, "services": {"backend": {"image": IMAGE, "environment": {
        "ENVIRONMENT": "production", "DAY_ROLE_SYNC_ENABLED": enabled,
        "DAY_ROLE_SYNC_URL": "https://example.invalid/api/internal/role-sync",
        "DISCORD_DAY_1_ROLE_ID": "123456789012345678",
        "DISCORD_DAY_2_ROLE_ID": "123456789012345679",
        "SESSION_SECRET": SECRET}}}}


def container(data, **changes):
    result = {"id": IDENTIFIER, "project": PROJECT, "service": "backend",
              "image": IMAGE, "running": True, "health": "healthy", "oom": False,
              "started_at": "2026-11-15T16:00:00Z", "restarts": 0,
              "env": [f"{key}={value}" for key, value in
                      data["services"]["backend"]["environment"].items()]}
    result.update(changes)
    return result


class EffectiveTests(unittest.TestCase):
    def observe(self, data, info=None, ids=IDENTIFIER + "\n"):
        current = json.dumps(info or container(data))
        with patch.object(effective, "docker", side_effect=[ids, current, current, ids]):
            return effective.inspect(data, data["services"]["backend"]["environment"]["DAY_ROLE_SYNC_ENABLED"])

    def test_matching_running_backend_passes_without_exposing_values(self):
        data = model()
        checks = self.observe(data)
        self.assertTrue(all(checks.values()), checks)
        self.assertNotIn(SECRET, json.dumps(checks))
        self.assertNotIn("123456789012345678", json.dumps(checks))

    def test_rejects_duplicate_or_changed_backend(self):
        data = model()
        self.assertFalse(self.observe(data, ids=IDENTIFIER + "\n" + "c" * 64 + "\n")["single_backend"])
        self.assertFalse(self.observe(data, info=container(data, image="unreviewed"))["container_identity"])
        self.assertFalse(self.observe(data, info=container(data, health="unhealthy"))["running_healthy"])

    def test_rejects_running_environment_drift(self):
        data = model()
        info = container(data)
        info["env"] = [entry.replace(SECRET, "changed-secret") for entry in info["env"]]
        self.assertFalse(self.observe(data, info=info)["effective_environment"])

    def test_rejects_container_change_during_observation(self):
        data = model()
        current = json.dumps(container(data))
        changed = json.dumps(container(data, started_at="2026-11-15T16:30:00Z", restarts=1))
        with patch.object(effective, "docker", side_effect=[IDENTIFIER + "\n", current,
                                                          changed, IDENTIFIER + "\n"]):
            checks = effective.inspect(data, "true")
        self.assertFalse(checks["container_stable"])

    def test_rejects_equal_numeric_role_ids_and_missing_role(self):
        data = model()
        info = container(data)
        info["env"] = [entry.replace("123456789012345679", "0123456789012345678")
                       for entry in info["env"]]
        self.assertFalse(self.observe(data, info=info)["role_ids_distinct"])
        info["env"] = [entry for entry in info["env"] if not entry.startswith("DISCORD_DAY_2_ROLE_ID=")]
        self.assertFalse(self.observe(data, info=info)["role_id_presence"])

    def test_disabled_baseline_needs_no_roles(self):
        data = model("false")
        for key in effective.ROLE_KEYS:
            data["services"]["backend"]["environment"].pop(key)
        checks = self.observe(data)
        self.assertTrue(all(checks.values()), checks)

    def test_report_is_sanitized_and_existing_report_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir(mode=0o700)
            report = Path(directory) / "report.json"
            original_lstat = Path.lstat

            def owner_lstat(path):
                if path == project:
                    return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)
                return original_lstat(path)

            model_value = model()
            with (patch.object(effective.os, "geteuid", return_value=0),
                  patch.object(Path, "lstat", owner_lstat),
                  patch.object(effective.PREFLIGHT, "private_report_directory", return_value=True),
                  patch.object(effective.PREFLIGHT, "private_runtime_files", return_value=True),
                  patch.object(effective.PREFLIGHT, "deployment_bind_sources_current", return_value=True),
                  patch.object(effective.PREFLIGHT, "render", return_value=model_value),
                  patch.object(effective.PREFLIGHT, "read_mom_approval", return_value={}),
                  patch.object(effective.PREFLIGHT, "validate", return_value={"valid": True}),
                  patch.object(effective, "inspect", return_value=dict.fromkeys(effective.CHECKS, True))):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(effective.main(["--project-directory", str(project),
                        "--expected-enabled", "true", "--report", str(report),
                        "--confirm-readonly"]), 0)
                result = report.read_text()
                self.assertEqual(json.loads(result)["status"], "PASS")
                self.assertNotIn(SECRET, result)
                self.assertNotIn("123456789012345678", result)
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(effective.main(["--project-directory", str(project),
                        "--expected-enabled", "true", "--report", str(report),
                        "--confirm-readonly"]), 1)
                self.assertEqual(report.read_text(), result)

    def test_report_publish_failure_cannot_return_success(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir(mode=0o700)
            report = Path(directory) / "report.json"
            original_lstat = Path.lstat

            def owner_lstat(path):
                if path == project:
                    return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)
                return original_lstat(path)

            with (patch.object(effective.os, "geteuid", return_value=0),
                  patch.object(Path, "lstat", owner_lstat),
                  patch.object(effective.PREFLIGHT, "private_report_directory", return_value=True),
                  patch.object(effective.PREFLIGHT, "private_runtime_files", return_value=True),
                  patch.object(effective.PREFLIGHT, "deployment_bind_sources_current", return_value=True),
                  patch.object(effective.PREFLIGHT, "render", return_value=model()),
                  patch.object(effective.PREFLIGHT, "read_mom_approval", return_value={}),
                  patch.object(effective.PREFLIGHT, "validate", return_value={"valid": True}),
                  patch.object(effective, "inspect", return_value=dict.fromkeys(effective.CHECKS, True)),
                  patch.object(effective, "publish", side_effect=OSError("disk full"))):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(effective.main(["--project-directory", str(project),
                        "--expected-enabled", "true", "--report", str(report),
                        "--confirm-readonly"]), 1)
                self.assertEqual(output.getvalue().strip(), "STOP")
                self.assertEqual(json.loads(report.read_text())["status"], "INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
