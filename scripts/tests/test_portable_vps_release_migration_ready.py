"""Migration readiness is candidate-bound and reads revisions after writers stop."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-release-migration-ready.py"
SPEC = importlib.util.spec_from_file_location("release_migration_ready", SCRIPT)
READY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(READY)


def candidate():
    return {"schema_version": 2, "release_id": "candidate-ready",
            "siege": {"source_sha": "1" * 40, "ci_run_id": 1,
                      "api_image": "ghcr.io/glitchwerks/siege-api@sha256:" + "a" * 64,
                      "frontend_image": "ghcr.io/glitchwerks/siege-frontend@sha256:" + "b" * 64},
            "mom": {"source_sha": "2" * 40, "ci_run_id": 2,
                    "image": "ghcr.io/glitchwerks/mom-bot@sha256:" + "c" * 64,
                    "portable_auth": True, "scheduler_progress": True},
            "configuration": {"source_sha": "1" * 40, "topology": "mom"},
            "migrations": {"siege": {"mode": "upgrade", "from_revision": "0012",
                                     "to_revision": "0013"},
                           "mom": {"mode": "none", "from_revision": "b6_member",
                                   "to_revision": "b6_member"},
                           "order": ["siege"]}}


def proof_baseline():
    return {"schema": 1, "phase": "release-recovery-proof-baseline",
            "manifest_sha256": "a" * 64, "plan_sha256": "b" * 64,
            "preflight_baseline_sha256": "c" * 64,
            "backup_selector_sha256": "d" * 64, "backup_run_id": "example",
            "proofs": {name: {"path": f"/private/{name}.json", "sha256": "e" * 64}
                       for name in ("backup", "recover", "restore")}}


class MigrationReadyTests(unittest.TestCase):
    def test_stage_report_path_rejected_before_creating_any_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            stage = project / "evidence" / "release-stages" / "123-1"
            stage.mkdir(parents=True)
            report = stage / "migration-ready-report.json"
            with patch.object(READY.os, "geteuid", return_value=0), patch.object(
                READY.PAUSE, "profile", return_value=({"project_directory": project}, "b" * 64)
            ), patch.object(READY.PAUSE, "stage") as lookup:
                code = READY.main(["--profile", str(root / "profile"),
                    "--stage-run-id", "123-1", "--manifest-sha256", "a" * 64,
                    "--plan-sha256", "b" * 64, "--report", str(report)])
            self.assertEqual(code, 2)
            self.assertFalse(report.exists())
            lookup.assert_not_called()

    def test_postgres_query_uses_mounted_secret_and_exact_read_only_sql(self):
        identifier = "f" * 64
        with patch.object(READY.subprocess, "run", return_value=SimpleNamespace(
            returncode=0, stdout="0012\n", stderr=""
        )) as run:
            self.assertEqual(READY.current_revision(identifier, "siege"), "0012")
        command = run.call_args.args[0]
        self.assertEqual(command[:5], ["docker", "exec", "--user", "0", identifier])
        self.assertEqual(command[-3:], ["release-revision", "siege", READY.QUERY])
        self.assertIn("/run/secrets/postgres-admin", command[-4])
        self.assertIn("SELECT version_num", command[-1])
        self.assertEqual(run.call_args.kwargs["stdin"], READY.subprocess.DEVNULL)
        with patch.object(READY.subprocess, "run", return_value=SimpleNamespace(
            returncode=0, stdout="0012\n0013\n", stderr=""
        )):
            self.assertIsNone(READY.current_revision(identifier, "siege"))
        with patch.object(READY.subprocess, "run") as run:
            self.assertIsNone(READY.current_revision(identifier, "invalid"))
            run.assert_not_called()

    def test_accepted_proof_requires_exact_candidate_plan_and_private_paths(self):
        baseline = proof_baseline()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "recovery-proof-baseline.json").write_text(json.dumps(baseline))
            with patch.object(READY.GATE, "read_private", side_effect=lambda path, owner: path.read_bytes()), patch.object(
                READY.PROOF, "accepted_report", return_value=True
            ):
                loaded, digest = READY.accepted_proof(directory, "a" * 64, "b" * 64, "c" * 64)
                self.assertEqual(loaded, baseline)
                self.assertEqual(digest, READY.PAUSE.digest((directory / "recovery-proof-baseline.json").read_bytes()))
                with self.assertRaises(ValueError):
                    READY.accepted_proof(directory, "0" * 64, "b" * 64, "c" * 64)
                baseline["proofs"]["recover"]["path"] = "../unsafe"
                (directory / "recovery-proof-baseline.json").write_text(json.dumps(baseline))
                with self.assertRaises(ValueError):
                    READY.accepted_proof(directory, "a" * 64, "b" * 64, "c" * 64)

    def test_exact_both_revisions_and_rechecked_proof_are_required(self):
        raw = json.dumps(candidate()).encode()
        manifest_digest = READY.PAUSE.digest(raw)
        baseline = proof_baseline()
        baseline["manifest_sha256"] = manifest_digest
        value = {"preflight_baseline_sha256": "c" * 64,
                 "ids": {"postgres": "f" * 64}}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            def read(path, owner, limit=65536):
                self.assertEqual(path, directory / "candidate.json")
                return raw
            sequence = []
            def proof(*args):
                sequence.append("proof")
                return {"valid": True}, baseline
            def revision(identifier, database):
                sequence.append(database)
                return {"siege": "0012", "mom_bot": "b6_member"}[database]
            with patch.object(READY.os, "geteuid", return_value=0), patch.object(
                READY, "accepted_proof", return_value=(baseline, "e" * 64)
            ), patch.object(READY.PROOF, "inspect", side_effect=proof), patch.object(
                READY.GATE, "read_private", side_effect=read
            ), patch.object(READY, "current_revision", side_effect=revision):
                checks, result = READY.inspect({}, directory, value, "b" * 64, manifest_digest)
                self.assertTrue(all(checks.values()), checks)
                self.assertEqual(result["migration_order"], ["siege"])
                self.assertEqual(sequence, ["proof", "siege", "mom_bot", "siege", "mom_bot", "proof"])
            with patch.object(READY.os, "geteuid", return_value=0), patch.object(
                READY, "accepted_proof", return_value=(baseline, "e" * 64)
            ), patch.object(READY.PROOF, "inspect", return_value=({"valid": True}, baseline)), patch.object(
                READY.GATE, "read_private", side_effect=read
            ), patch.object(READY, "current_revision", side_effect=["0012", "different"]):
                checks, result = READY.inspect({}, directory, value, "b" * 64, manifest_digest)
                self.assertFalse(checks["mom_revision_exact"])
                self.assertIsNone(result)
            with patch.object(READY.os, "geteuid", return_value=0), patch.object(
                READY, "accepted_proof", return_value=(baseline, "e" * 64)
            ), patch.object(READY.PROOF, "inspect", side_effect=[
                ({"valid": True}, baseline), ({"valid": False}, None)
            ]), patch.object(READY.GATE, "read_private", side_effect=read), patch.object(
                READY, "current_revision", side_effect=["0012", "b6_member", "0012", "b6_member"]):
                checks, result = READY.inspect({}, directory, value, "b" * 64, manifest_digest)
                self.assertFalse(checks["selected_backup_proof_still_current"])
                self.assertIsNone(result)

    def test_query_failure_writes_sanitized_stop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "stage"
            stage.mkdir()
            report = root / "report.json"
            with patch.object(READY.os, "geteuid", return_value=0), patch.object(
                READY.GATE, "protected", return_value=True
            ), patch.object(READY.PAUSE, "profile", return_value=({"project_directory": root / "project"}, "b" * 64)), patch.object(
                READY.PAUSE, "stage", return_value=stage
            ), patch.object(READY.PAUSE, "load_plan", return_value={}), patch.object(
                READY, "inspect", side_effect=RuntimeError("sensitive database detail")):
                code = READY.main(["--profile", str(root / "profile"), "--stage-run-id", "123-1",
                    "--manifest-sha256", "a" * 64, "--plan-sha256", "b" * 64,
                    "--report", str(report)])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(report.read_text())["status"], "STOP")
            self.assertEqual(json.loads((stage / "migration-ready-report.json").read_text())["status"], "STOP")
            self.assertNotIn("sensitive", report.read_text())


if __name__ == "__main__":
    unittest.main()
