"""The live revision baseline is read-only, exact, and sanitized."""

import importlib.util
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-migration-baseline.py"
SPEC = importlib.util.spec_from_file_location("vps_migration_baseline", SCRIPT)
BASELINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASELINE)


def candidate():
    return {
        "schema_version": 2,
        "release_id": "test-candidate",
        "siege": {
            "source_sha": "1" * 40, "ci_run_id": 123,
            "api_image": "ghcr.io/glitchwerks/siege-api@sha256:" + "a" * 64,
            "frontend_image": "ghcr.io/glitchwerks/siege-frontend@sha256:" + "b" * 64,
        },
        "mom": {
            "source_sha": "2" * 40, "ci_run_id": 456,
            "image": "ghcr.io/glitchwerks/mom-bot@sha256:" + "c" * 64,
            "portable_auth": True, "scheduler_progress": True,
        },
        "configuration": {"source_sha": "1" * 40, "topology": "mom"},
        "migrations": {
            "siege": {"mode": "none", "from_revision": "0012", "to_revision": "0012"},
            "mom": {"mode": "none", "from_revision": "b6_new_member_alert_subscription",
                    "to_revision": "b6_new_member_alert_subscription"},
            "order": [],
        },
    }


class MigrationBaselineTests(unittest.TestCase):
    def test_current_revision_uses_local_docker_and_only_executes_alembic_current(self):
        project = Path("/protected/project")
        with patch.object(BASELINE.subprocess, "run", return_value=SimpleNamespace(
            returncode=0, stdout="0012 (head)\n", stderr=""
        )) as run:
            self.assertEqual(BASELINE.current_revision(project, "backend"), "0012")
        args = run.call_args.args[0]
        self.assertEqual(args[-5:], ["exec", "-T", "backend", "/app/.venv/bin/alembic", "current"])
        self.assertEqual(run.call_args.kwargs["env"]["DOCKER_HOST"], "unix:///var/run/docker.sock")
        self.assertNotIn("COMPOSE_FILE", run.call_args.kwargs["env"])
        with patch.object(BASELINE.subprocess, "run", return_value=SimpleNamespace(
            returncode=0, stdout="0012\nother\n", stderr=""
        )):
            self.assertIsNone(BASELINE.current_revision(project, "backend"))

    def test_matching_both_live_revisions_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "candidate.json"
            manifest.write_text(json.dumps(candidate()))
            digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
            with patch.object(BASELINE.os, "geteuid", return_value=0), patch.object(
                BASELINE, "private_path", return_value=True
            ), patch.object(BASELINE, "readable_config", return_value=True), patch.object(
                BASELINE, "current_revision", side_effect=["0012", "b6_new_member_alert_subscription"]
            ):
                self.assertTrue(all(BASELINE.inspect(manifest, root, digest).values()))
            with patch.object(BASELINE.os, "geteuid", return_value=0), patch.object(
                BASELINE, "private_path", return_value=True
            ), patch.object(BASELINE, "readable_config", return_value=True), patch.object(
                BASELINE, "current_revision", side_effect=["0012", "different"]
            ):
                checks = BASELINE.inspect(manifest, root, digest)
            self.assertTrue(checks["siege_live_revision"])
            self.assertFalse(checks["mom_live_revision"])
            self.assertNotIn("b6_new_member_alert_subscription", str(checks))
            with patch.object(BASELINE.os, "geteuid", return_value=0), patch.object(
                BASELINE, "private_path", return_value=True
            ), patch.object(BASELINE, "readable_config", return_value=True), patch.object(
                BASELINE, "current_revision"
            ) as run:
                checks = BASELINE.inspect(manifest, root, "0" * 64)
                self.assertFalse(checks["manifest_digest"])
                run.assert_not_called()

    def test_report_is_private_exclusive_and_contains_only_booleans(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "baseline.json"
            with patch.object(BASELINE, "inspect", return_value={"siege_live_revision": False}), patch(
                "sys.argv", ["baseline", "--manifest", str(root / "candidate.json"),
                             "--manifest-sha256", "a" * 64,
                             "--project-directory", str(root), "--report", str(report)]
            ):
                self.assertEqual(BASELINE.main(), 1)
                self.assertEqual(BASELINE.main(), 2)
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(report.read_text())["checks"], {"siege_live_revision": False})


if __name__ == "__main__":
    unittest.main()
