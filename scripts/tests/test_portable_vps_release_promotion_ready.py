"""Promotion readiness requires the entire accepted migration chain."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-release-promotion-ready.py"
SPEC = importlib.util.spec_from_file_location("promotion_ready", SCRIPT)
PROMOTE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROMOTE)


def manifest(order):
    return {"migrations": {"siege": {"mode": "upgrade", "from_revision": "s1", "to_revision": "s2"},
                           "mom": {"mode": "none", "from_revision": "m1", "to_revision": "m1"},
                           "order": order},
            "siege": {"api_image": "api", "frontend_image": "frontend"},
            "mom": {"image": "mom", "source_sha": "d" * 40}}


class PromotionReadyTests(unittest.TestCase):
    def test_completed_step_binds_marker_to_selected_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            marker = {"phase": "migrate-siege-start", "manifest_sha256": "a" * 64,
                      "plan_sha256": "b" * 64, "readiness_sha256": "c" * 64,
                      "service": "migrate-siege", "image": "api",
                      "from_revision": "s1", "to_revision": "s2"}
            (directory / "migrate-siege-start.json").write_text(json.dumps(marker))
            with patch.object(PROMOTE.READY.PROOF, "accepted_report", return_value=True), patch.object(
                PROMOTE.GATE, "read_private", side_effect=lambda *args: json.dumps(marker).encode()
            ):
                self.assertTrue(PROMOTE.completed_step(directory, manifest(["siege"]),
                    "a" * 64, "b" * 64, "c" * 64, "siege"))
                self.assertFalse(PROMOTE.completed_step(directory, manifest(["siege"]),
                    "a" * 64, "b" * 64, "d" * 64, "siege"))

    def test_no_promotion_before_accepted_migration_and_final_revisions(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            data = {"project_directory": directory}
            plan = {"ids": {"postgres": "f" * 64}, "project": "example",
                    "preflight_baseline_sha256": "c" * 64}
            ready = {"migration_order": ["siege"], "postgres_id": "f" * 64,
                     "revisions": {"siege": "s1", "mom": "m1"},
                     "recovery_proof_sha256": "d" * 64}
            patches = [patch.object(PROMOTE.os, "geteuid", return_value=0),
                       patch.object(PROMOTE.MIGRATE, "accepted_readiness", return_value=ready),
                       patch.object(PROMOTE.MIGRATE, "candidate", return_value=manifest(["siege"])),
                       patch.object(PROMOTE.READY, "accepted_proof", return_value=({}, "d" * 64)),
                       patch.object(PROMOTE.READY, "proof_current", return_value=True),
                       patch.object(PROMOTE.PAUSE, "paused", return_value=True),
                       patch.object(PROMOTE.PAUSE, "bound_preflight", return_value=(True, {}))]
            for item in patches:
                item.start()
                self.addCleanup(item.stop)
            with patch.object(PROMOTE, "completed_step", return_value=False), patch.object(
                PROMOTE.MIGRATE, "revisions_match"
            ) as revisions:
                checks, baseline = PROMOTE.inspect(data, directory, plan, "a" * 64,
                                                    "b" * 64, "e" * 64)
            self.assertFalse(checks["siege_migration_accepted"])
            self.assertIsNone(baseline)
            revisions.assert_not_called()
            with patch.object(PROMOTE, "completed_step", return_value=True), patch.object(
                PROMOTE.MIGRATE, "revisions_match", return_value=False
            ) as revisions, patch.object(PROMOTE.MIGRATE, "candidate_config") as config:
                checks, baseline = PROMOTE.inspect(data, directory, plan, "a" * 64,
                                                    "b" * 64, "e" * 64)
            self.assertFalse(checks["final_database_revisions_exact"])
            self.assertIsNone(baseline)
            config.assert_not_called()
            revisions.assert_called_once_with("f" * 64, {"siege": "s2", "mom": "m1"})

    def test_none_mode_rejects_unexpected_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "migrate-mom-start.json").write_text("{}")
            plan = {"ids": {"postgres": "f" * 64}, "project": "example",
                    "preflight_baseline_sha256": "c" * 64}
            ready = {"migration_order": [], "postgres_id": "f" * 64,
                     "revisions": {"siege": "s1", "mom": "m1"},
                     "recovery_proof_sha256": "d" * 64}
            with patch.object(PROMOTE.os, "geteuid", return_value=0), patch.object(
                PROMOTE.MIGRATE, "accepted_readiness", return_value=ready
            ), patch.object(PROMOTE.MIGRATE, "candidate", return_value=manifest([])), patch.object(
                PROMOTE.READY, "accepted_proof", return_value=({}, "d" * 64)
            ), patch.object(PROMOTE.READY, "proof_current", return_value=True), patch.object(
                PROMOTE.PAUSE, "paused", return_value=True
            ), patch.object(PROMOTE.PAUSE, "bound_preflight", return_value=(True, {})):
                checks, baseline = PROMOTE.inspect({"project_directory": directory}, directory,
                                                    plan, "a" * 64, "b" * 64, "e" * 64)
            self.assertFalse(checks["mom_migration_not_attempted"])
            self.assertIsNone(baseline)

    def test_passing_gate_records_exact_final_revisions(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = {"ids": {"postgres": "f" * 64}, "project": "example",
                    "preflight_baseline_sha256": "c" * 64}
            ready = {"migration_order": ["siege"], "postgres_id": "f" * 64,
                     "revisions": {"siege": "s1", "mom": "m1"},
                     "recovery_proof_sha256": "d" * 64}
            with patch.object(PROMOTE.os, "geteuid", return_value=0), patch.object(
                PROMOTE.MIGRATE, "accepted_readiness", return_value=ready
            ), patch.object(PROMOTE.MIGRATE, "candidate", return_value=manifest(["siege"])), patch.object(
                PROMOTE.READY, "accepted_proof", return_value=({}, "d" * 64)
            ), patch.object(PROMOTE.READY, "proof_current", return_value=True), patch.object(
                PROMOTE.PAUSE, "paused", return_value=True
            ), patch.object(PROMOTE.PAUSE, "bound_preflight", return_value=(True, {})), patch.object(
                PROMOTE, "completed_step", return_value=True
            ), patch.object(PROMOTE.MIGRATE, "revisions_match", return_value=True), patch.object(
                PROMOTE.MIGRATE, "candidate_config", return_value=(True, {})
            ), patch.object(PROMOTE.MIGRATE, "image_cached", return_value=True), patch.object(
                PROMOTE.PAUSE, "no_extra_writers", return_value=True):
                checks, baseline = PROMOTE.inspect({"project_directory": directory}, directory,
                    plan, "a" * 64, "b" * 64, "e" * 64)
            self.assertTrue(all(checks.values()), checks)
            self.assertEqual(baseline["revisions"], {"siege": "s2", "mom": "m1"})
            self.assertEqual(baseline["migration_order"], ["siege"])


if __name__ == "__main__":
    unittest.main()
