"""Confirmed migration must follow manifest order and fail closed around writes."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-release-migrate.py"
SPEC = importlib.util.spec_from_file_location("release_migrate", SCRIPT)
MIGRATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATE)


def manifest():
    return {"migrations": {"siege": {"mode": "upgrade", "from_revision": "0012",
                                     "to_revision": "0013"},
                           "mom": {"mode": "upgrade", "from_revision": "b6_member",
                                   "to_revision": "b7_member"},
                           "order": ["siege", "mom"]},
            "siege": {"api_image": "ghcr.io/glitchwerks/siege-api@sha256:" + "a" * 64,
                      "frontend_image": "ghcr.io/glitchwerks/siege-frontend@sha256:" + "b" * 64},
            "mom": {"image": "ghcr.io/glitchwerks/mom-bot@sha256:" + "c" * 64,
                    "source_sha": "d" * 40}}


def context():
    return {"readiness": {"postgres_id": "f" * 64}, "manifest": manifest(),
            "proof": {}, "expected": {"siege": "0012", "mom": "b6_member"},
            "overrides": {"BACKEND_IMAGE": "api", "FRONTEND_IMAGE": "frontend", "MOM_IMAGE": "mom"},
            "image": manifest()["siege"]["api_image"]}


class ReleaseMigrateTests(unittest.TestCase):
    def test_compose_run_uses_one_service_exact_target_and_no_implicit_pull(self):
        with patch.object(MIGRATE.subprocess, "run", return_value=SimpleNamespace(
            returncode=0, stdout="0013 (head)\n"
        )) as run:
            self.assertEqual(MIGRATE.compose_run(Path("/private/project"), "migrate-siege", "heads",
                                                 None, context()["overrides"]), "0013 (head)\n")
        command = run.call_args.args[0]
        self.assertEqual(command[-4:], ["--entrypoint", "/app/.venv/bin/alembic", "migrate-siege", "heads"])
        self.assertIn("--no-deps", command)
        self.assertEqual(command[command.index("--pull") + 1], "never")
        self.assertEqual(run.call_args.kwargs["env"]["BACKEND_IMAGE"], "api")
        self.assertEqual(run.call_args.kwargs["stdin"], MIGRATE.subprocess.DEVNULL)
        with patch.object(MIGRATE.subprocess, "run", return_value=SimpleNamespace(
            returncode=0, stdout=None
        )) as run:
            self.assertEqual(MIGRATE.compose_run(Path("/private/project"), "migrate-mom", "upgrade",
                                                 "b7_member", context()["overrides"]), "")
        self.assertEqual(run.call_args.args[0][-3:], ["migrate-mom", "upgrade", "b7_member"])
        self.assertEqual(run.call_args.kwargs["stdout"], MIGRATE.subprocess.DEVNULL)
        with patch.object(MIGRATE.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                MIGRATE.compose_run(Path("/private/project"), "postgres", "upgrade", "0013", {})
            run.assert_not_called()

    def test_previous_step_must_pass_before_second_database(self):
        ready = {"migration_order": ["siege", "mom"], "postgres_id": "f" * 64,
                 "revisions": {"siege": "0012", "mom": "b6_member"},
                 "recovery_proof_sha256": "e" * 64}
        plan = {"ids": {"postgres": "f" * 64}, "preflight_baseline_sha256": "c" * 64}
        with patch.object(MIGRATE.os, "geteuid", return_value=0), patch.object(
            MIGRATE, "accepted_readiness", return_value=ready
        ), patch.object(MIGRATE, "candidate", return_value=manifest()), patch.object(
            MIGRATE.READY, "accepted_proof", return_value=({}, "e" * 64)
        ), patch.object(MIGRATE.READY, "proof_current", return_value=True), patch.object(
            MIGRATE.READY.PROOF, "accepted_report", return_value=False
        ), patch.object(MIGRATE, "candidate_config") as config:
            checks, result = MIGRATE.inspect({}, Path("/private/stage"), plan,
                                             "a" * 64, "b" * 64, "d" * 64, "mom")
        self.assertFalse(checks["previous_step_accepted"])
        self.assertIsNone(result)
        config.assert_not_called()

    def test_wrong_head_cannot_write_and_success_records_one_explicit_step(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            data = {"project_directory": directory}
            plan = {"project": "synthetic"}
            with patch.object(MIGRATE, "inspect", return_value=({"ready": True}, context())), patch.object(
                MIGRATE, "compose_run", return_value="different (head)"
            ) as run, patch.object(MIGRATE.PAUSE, "no_extra_writers", return_value=True), patch.object(
                MIGRATE.READY, "proof_current", return_value=True), patch.object(
                MIGRATE, "revisions_match", return_value=True):
                checks = MIGRATE.migrate(data, directory, plan, "a" * 64, "b" * 64, "c" * 64, "siege")
                self.assertFalse(checks["candidate_migration_head_exact"])
                self.assertEqual(run.call_count, 1)
                self.assertFalse((directory / "migrate-siege-start.json").exists())
            events = []
            with patch.object(MIGRATE, "inspect", return_value=({"ready": True}, context())), patch.object(
                MIGRATE, "compose_run", side_effect=lambda project, service, operation, revision, overrides:
                    events.append(operation) or ("0013 (head)\n" if operation == "heads" else "")
            ) as run, patch.object(MIGRATE.PAUSE, "no_extra_writers", return_value=True), patch.object(
                MIGRATE.READY, "proof_current", side_effect=lambda *args:
                    events.append("proof") or True), patch.object(
                MIGRATE, "revisions_match", side_effect=lambda *args:
                    events.append("revisions") or True):
                checks = MIGRATE.migrate(data, directory, plan, "a" * 64, "b" * 64, "c" * 64, "siege")
            self.assertTrue(all(checks.values()), checks)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.args[2:4], ("upgrade", "0013"))
            self.assertEqual(events, ["heads", "revisions", "proof", "upgrade", "revisions", "proof"])
            marker = json.loads((directory / "migrate-siege-start.json").read_text())
            self.assertEqual(marker["to_revision"], "0013")
            self.assertNotIn("password", json.dumps(marker).lower())

    def test_failed_upgrade_stops_after_one_attempt_with_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch.object(MIGRATE, "inspect", return_value=({"ready": True}, context())), patch.object(
                MIGRATE, "compose_run", side_effect=["0013 (head)", None]
            ) as run, patch.object(MIGRATE.PAUSE, "no_extra_writers", return_value=True), patch.object(
                MIGRATE.READY, "proof_current", return_value=True), patch.object(
                MIGRATE, "revisions_match", return_value=True):
                checks = MIGRATE.migrate({"project_directory": directory}, directory,
                                         {"project": "synthetic"}, "a" * 64, "b" * 64, "c" * 64, "siege")
            self.assertFalse(checks["migration_command_success"])
            self.assertEqual(run.call_count, 2)
            self.assertTrue((directory / "migrate-siege-start.json").exists())
            self.assertNotIn("target_revision_exact", checks)

    def test_missing_confirmation_never_creates_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "report.json"
            with patch.object(MIGRATE.PAUSE, "profile") as profile:
                code = MIGRATE.main(["--profile", str(root / "profile"), "--stage-run-id", "123-1",
                    "--manifest-sha256", "a" * 64, "--plan-sha256", "b" * 64,
                    "--readiness-sha256", "c" * 64, "--database", "siege",
                    "--confirm-migrate", "mom", "--report", str(report)])
            self.assertEqual(code, 2)
            self.assertFalse(report.exists())
            profile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
