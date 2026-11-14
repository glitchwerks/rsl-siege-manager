"""Recovery can restore protected configuration only before writes or migration."""

from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-release-recover.py"
SPEC = importlib.util.spec_from_file_location("release_recover", SCRIPT)
RECOVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVER)


def manifest():
    return {"siege": {"api_image": "api", "frontend_image": "frontend"},
            "mom": {"image": "mom"},
            "migrations": {"siege": {"from_revision": "s1"},
                           "mom": {"from_revision": "m1"}, "order": []}}


class ReleaseRecoveryTests(unittest.TestCase):
    def test_activation_or_any_migration_attempt_blocks_config_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "stage"
            directory.mkdir()
            for name in ("promote-activation-start.json", "migrate-siege-start.json",
                         "migrate-mom-report.json"):
                marker = directory / name
                marker.write_text("{}")
                self.assertTrue(RECOVER.migration_or_activation_started(directory))
                marker.unlink()
            self.assertFalse(RECOVER.migration_or_activation_started(directory))

    def test_inspection_stops_before_mutation_after_activation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            directory = root / "stage"
            project.mkdir()
            directory.mkdir()
            plan = {"project": "prod", "preflight_baseline_sha256": "c" * 64}
            preflight = {"stack_env_sha256": "old", "runtime_sha256": {"mom-image.json": "old"}}
            with patch.object(RECOVER.os, "geteuid", return_value=0), patch.object(
                RECOVER.MIGRATE, "candidate", return_value=manifest()
            ), patch.object(RECOVER.PROMOTE, "accepted_gate", return_value=({}, "d" * 64)), patch.object(
                RECOVER.PREFLIGHT, "accepted_baseline", return_value=(preflight, "c" * 64)
            ), patch.object(RECOVER, "promotion_evidence", return_value=(b"old", b"old")), patch.object(
                RECOVER, "migration_or_activation_started", return_value=True
            ), patch.object(RECOVER.GATE, "read_private") as read, patch.object(
                RECOVER.PROMOTE, "replace_bytes"
            ) as replace:
                checks, context = RECOVER.inspect({"project_directory": project}, directory, plan,
                    "a" * 64, "b" * 64, "e" * 64, "d" * 64)
            self.assertFalse(checks["no_migration_or_activation"])
            self.assertIsNone(context)
            read.assert_not_called()
            replace.assert_not_called()

    def test_confirmed_restore_writes_only_two_files_and_leaves_writers_paused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "runtime").mkdir(parents=True)
            directory = root / "stage"
            directory.mkdir()
            stack = project / "stack.env"
            approval = project / "runtime" / "mom-image.json"
            files = {stack: b"candidate stack", approval: b"candidate mom"}
            context = {"old_stack": b"old stack", "old_mom": b"old mom",
                       "current_stack": files[stack], "current_mom": files[approval],
                       "manifest": manifest(), "preflight": {}}
            plan = {"ids": {"postgres": "f" * 64}, "project": "prod"}
            events = []
            def replace(path, raw):
                events.append((path, raw))
                files[path] = raw
            patches = [
                patch.object(RECOVER, "inspect", return_value=({"eligible": True}, context)),
                patch.object(RECOVER, "migration_or_activation_started", return_value=False),
                patch.object(RECOVER.PAUSE, "paused", return_value=True),
                patch.object(RECOVER, "writer_stop_times_match", return_value=True),
                patch.object(RECOVER.MIGRATE, "revisions_match", return_value=True),
                patch.object(RECOVER.GATE, "read_private", side_effect=lambda path, *_args: files[path]),
                patch.object(RECOVER.PAUSE, "write_private",
                             side_effect=lambda path, value: path.write_text(json.dumps(value))),
                patch.object(RECOVER.PROMOTE, "replace_bytes", side_effect=replace),
                patch.object(RECOVER.PREFLIGHT, "baseline_current", return_value=True),
                patch.object(RECOVER, "old_frontend_healthy", return_value=True),
                patch.object(RECOVER.PROMOTE, "unchanged_foundation", return_value=True),
            ]
            with ExitStack() as stack_context:
                for item in patches:
                    stack_context.enter_context(item)
                checks = RECOVER.restore({"project_directory": project}, directory, plan,
                    "a" * 64, "b" * 64, "e" * 64, "d" * 64)
            self.assertTrue(all(checks.values()), checks)
            self.assertEqual(events, [(stack, b"old stack"), (approval, b"old mom")])
            self.assertEqual(files[stack], b"old stack")
            self.assertEqual(files[approval], b"old mom")
            self.assertTrue((directory / "recover-preactivation-start.json").exists())

    def test_missing_restore_confirmation_cannot_inspect_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            with patch.object(RECOVER.PAUSE, "profile") as profile:
                code = RECOVER.main(["restore-preactivation", "--profile", "/private/profile",
                    "--stage-run-id", "123-1", "--manifest-sha256", "a" * 64,
                    "--plan-sha256", "b" * 64, "--readiness-sha256", "c" * 64,
                    "--promotion-ready-sha256", "d" * 64, "--report", str(report)])
            self.assertEqual(code, 2)
            self.assertFalse(report.exists())
            profile.assert_not_called()

    def test_activation_marker_prevents_first_restore_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "runtime").mkdir(parents=True)
            stack = project / "stack.env"
            approval = project / "runtime" / "mom-image.json"
            stack.write_bytes(b"candidate stack")
            approval.write_bytes(b"candidate mom")
            context = {"old_stack": b"old stack", "old_mom": b"old mom",
                       "current_stack": b"candidate stack", "current_mom": b"candidate mom",
                       "manifest": manifest(), "preflight": {}}
            plan = {"ids": {"postgres": "f" * 64}, "project": "prod"}
            with patch.object(RECOVER, "inspect", return_value=({"eligible": True}, context)), patch.object(
                RECOVER, "migration_or_activation_started", return_value=True
            ), patch.object(RECOVER.PROMOTE, "replace_bytes") as replace:
                checks = RECOVER.restore({"project_directory": project}, root, plan,
                    "a" * 64, "b" * 64, "e" * 64, "d" * 64)
            self.assertFalse(checks["no_activation_or_migration_before_write"])
            replace.assert_not_called()
            self.assertEqual(stack.read_bytes(), b"candidate stack")
            self.assertEqual(approval.read_bytes(), b"candidate mom")

    def test_atomic_replace_restores_file_bytes_without_touching_neighbor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "stack.env"
            neighbor = root / "secrets.env"
            target.write_bytes(b"candidate")
            neighbor.write_bytes(b"private neighbor")
            with patch.object(RECOVER.GATE, "protected", return_value=True):
                RECOVER.PROMOTE.replace_bytes(target, b"original")
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(neighbor.read_bytes(), b"private neighbor")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_writer_restart_then_stop_denies_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = {"project": "prod", "ids": {"backend": "a" * 64, "mom": "b" * 64}}
            (directory / "pause-plan.json").write_bytes(b"plan")
            (directory / "pause-report.json").write_text('{"phase":"pause","status":"PASS"}')
            (directory / "pause-writer-stops.json").write_text(json.dumps({
                "phase": "pause-writer-stops", "plan_sha256": RECOVER.PAUSE.digest(b"plan"),
                "ids": plan["ids"], "finished_at": {
                    "backend": "2026-11-14T10:00:00Z", "mom": "2026-11-14T10:00:01Z"}}))
            snapshot = {"project": "prod", "services": {
                "backend": {"id": "a" * 64, "finished_at": "2026-11-14T10:00:00Z"},
                "mom": {"id": "b" * 64, "finished_at": "2026-11-14T10:00:01Z"}}}
            with patch.object(RECOVER.GATE, "read_private", side_effect=lambda path, *_: path.read_bytes()), patch.object(
                RECOVER.PAUSE, "service_baseline", return_value=snapshot):
                self.assertTrue(RECOVER.writer_stop_times_match({}, directory, plan))
                snapshot["services"]["backend"]["finished_at"] = "2026-11-14T10:10:00Z"
                self.assertFalse(RECOVER.writer_stop_times_match({}, directory, plan))

    def test_other_stage_migration_marker_denies_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selected = root / "selected"
            other = root / "other"
            selected.mkdir()
            other.mkdir()
            self.assertFalse(RECOVER.migration_or_activation_started(selected))
            (other / "migrate-siege-start.json").write_text("{}")
            self.assertTrue(RECOVER.migration_or_activation_started(selected))

    def test_migration_and_recovery_contend_for_same_project_lock(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            RECOVER.GATE, "protected", return_value=True
        ), patch.object(RECOVER.MIGRATE.GATE, "protected", return_value=True):
            project = Path(temporary)
            with RECOVER.MIGRATE.PAUSE.project_lock(project):
                with self.assertRaises(BlockingIOError):
                    with RECOVER.PROMOTE.project_lock(project):
                        self.fail("recovery entered during migration")


if __name__ == "__main__":
    unittest.main()
