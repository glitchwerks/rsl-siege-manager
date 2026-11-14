"""Confirmed promotion preserves protected inputs and activates only target services."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-release-promote.py"
SPEC = importlib.util.spec_from_file_location("release_promote", SCRIPT)
PROMOTE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROMOTE)


def manifest():
    return {"siege": {"api_image": "ghcr.io/example/api@sha256:" + "a" * 64,
                      "frontend_image": "ghcr.io/example/frontend@sha256:" + "b" * 64},
            "mom": {"image": "ghcr.io/example/mom@sha256:" + "c" * 64,
                    "source_sha": "d" * 40}}


def old_approval():
    return (json.dumps({"schema": 1, "image": "ghcr.io/example/mom@sha256:" + "f" * 64,
                        "source_commit": "e" * 40,
                        "capabilities": ["portable-auth-v1", "scheduler-progress-probe-v1"]}) + "\n").encode()


class ReleasePromoteTests(unittest.TestCase):
    def test_project_lock_rejects_parallel_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            with patch.object(PROMOTE.GATE, "protected", return_value=True):
                with PROMOTE.project_lock(project):
                    with self.assertRaises(BlockingIOError):
                        with PROMOTE.project_lock(project):
                            self.fail("parallel promotion acquired the lock")

    def test_image_config_changes_only_unique_assignments(self):
        raw = (b"# keep this comment\nCOMPOSE_PROJECT_NAME=prod\nBACKEND_IMAGE=old-api\r\n"
               b"FRONTEND_IMAGE=old-frontend\nMOM_IMAGE=old-mom\n"
               b"POSTGRES_IMAGE=do-not-touch\n")
        changed = PROMOTE.image_config(raw, manifest())
        self.assertIn(manifest()["siege"]["api_image"].encode() + b"\r\n", changed)
        self.assertIn(b"POSTGRES_IMAGE=do-not-touch\n", changed)
        self.assertIn(b"# keep this comment\n", changed)
        with self.assertRaises(ValueError):
            PROMOTE.image_config(raw + b"MOM_IMAGE=duplicate\n", manifest())
        with self.assertRaises(ValueError):
            PROMOTE.image_config(raw.replace(b"MOM_IMAGE=old-mom\n", b""), manifest())
        with self.assertRaises(ValueError):
            PROMOTE.image_config(raw.replace(b"MOM_IMAGE=old-mom", b"export MOM_IMAGE=old-mom"), manifest())

    def test_compose_activation_is_targeted_and_cannot_pull_or_build(self):
        with patch.object(PROMOTE.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(PROMOTE.compose_up(Path("/private/project")))
        command = run.call_args.args[0]
        self.assertEqual(command[-3:], ["backend", "mom", "frontend"])
        self.assertEqual(command[command.index("--pull") + 1], "never")
        self.assertIn("--no-deps", command)
        self.assertIn("--no-build", command)
        self.assertIn("--wait", command)
        self.assertNotIn("postgres", command[-3:])
        self.assertNotIn("proxy", command[-3:])
        self.assertEqual(run.call_args.kwargs["env"], PROMOTE.ENVIRONMENT)
        self.assertEqual(run.call_args.kwargs["stdin"], PROMOTE.subprocess.DEVNULL)

    def test_missing_confirmation_does_not_touch_profile_or_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            with patch.object(PROMOTE.PAUSE, "profile") as profile:
                code = PROMOTE.main(["--profile", "/private/profile", "--stage-run-id", "123-1",
                    "--manifest-sha256", "a" * 64, "--plan-sha256", "b" * 64,
                    "--readiness-sha256", "c" * 64, "--promotion-ready-sha256", "d" * 64,
                    "--confirm-promote", "wrong", "--report", str(report)])
            self.assertEqual(code, 2)
            self.assertFalse(report.exists())
            profile.assert_not_called()

    def test_pre_activation_failure_restores_files_and_never_runs_compose(self):
        self._exercise_operation(fail_before_activation=True)

    def test_activation_failure_does_not_automatically_restore_files(self):
        self._exercise_operation(fail_before_activation=False)

    def test_successful_activation_keeps_candidate_and_preserves_originals(self):
        checks = self._exercise_operation(fail_before_activation=False, compose_result=True)
        self.assertTrue(all(checks.values()), checks)

    def _exercise_operation(self, fail_before_activation, compose_result=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            directory = root / "stage"
            (project / "runtime").mkdir(parents=True)
            directory.mkdir()
            stack = project / "stack.env"
            approval = project / "runtime" / "mom-image.json"
            old_stack = (b"COMPOSE_PROJECT_NAME=prod\nBACKEND_IMAGE=old-api\n"
                         b"FRONTEND_IMAGE=old-frontend\nMOM_IMAGE=old-mom\n")
            files = {stack: old_stack, approval: old_approval()}
            plan = {"project": "prod", "ids": {"postgres": "f" * 64, "proxy": "e" * 64},
                    "preflight_baseline_sha256": "b" * 64}
            gate = {"revisions": {"siege": "s2", "mom": "m1"}, "postgres_id": "f" * 64}
            recorded = []
            backups = []
            def read(path, *_args):
                return files[path]
            def replace(path, raw):
                files[path] = raw
                recorded.append(("replace", path, raw))
            def save(path, raw):
                backups.append((path, raw))
                recorded.append(("save", path, raw))
            def write(path, value):
                path.write_text(json.dumps(value))
            def render(_topology, _stack, _project, overrides):
                return {"services": "candidate"}
            with patch.object(PROMOTE.os, "geteuid", return_value=0), patch.object(
                PROMOTE, "accepted_gate", return_value=(gate, "d" * 64)
            ), patch.object(PROMOTE.READY, "inspect", return_value=({"ready": True}, gate)), patch.object(
                PROMOTE.MIGRATE, "candidate", return_value=manifest()
            ), patch.object(PROMOTE.GATE, "read_private", side_effect=read), patch.object(
                PROMOTE.PAUSE, "bound_preflight", return_value=(True, {"installed": {}})
            ), patch.object(PROMOTE.PREFLIGHT, "render", side_effect=render), patch.object(
                PROMOTE.MIGRATE, "candidate_config", return_value=(True, {})
            ), patch.object(PROMOTE, "unchanged_foundation", return_value=True), patch.object(
                PROMOTE, "write_exclusive", side_effect=save
            ), patch.object(PROMOTE.PAUSE, "write_private", side_effect=write), patch.object(
                PROMOTE, "replace_bytes", side_effect=replace
            ), patch.object(PROMOTE.PAUSE, "paused", return_value=not fail_before_activation), patch.object(
                PROMOTE.MIGRATE, "revisions_match", return_value=True), patch.object(
                PROMOTE, "compose_up", return_value=compose_result
            ) as compose, patch.object(PROMOTE, "live_service", return_value="id"), patch.object(
                PROMOTE.PAUSE, "no_extra_writers", return_value=True):
                checks = PROMOTE.promote({"project_directory": project}, directory, plan,
                                         "a" * 64, "c" * 64, "e" * 64, "d" * 64)
            if fail_before_activation:
                self.assertFalse(checks["writers_paused_before_activation"])
                self.assertTrue(checks["pre_activation_config_restored"])
                self.assertEqual(files[stack], old_stack)
                self.assertEqual(files[approval], old_approval())
                compose.assert_not_called()
                self.assertFalse((directory / "promote-activation-start.json").exists())
            else:
                self.assertEqual(checks["targeted_compose_activation"], compose_result)
                self.assertNotEqual(files[stack], old_stack)
                self.assertNotEqual(files[approval], old_approval())
                compose.assert_called_once_with(project)
                self.assertTrue((directory / "promote-activation-start.json").exists())
            self.assertEqual(backups, [(directory / "installed-stack.env", old_stack),
                                       (directory / "installed-mom-image.json", old_approval())])
            self.assertEqual([event[0] for event in recorded[:3]], ["save", "save", "replace"])
            return checks


if __name__ == "__main__":
    unittest.main()
