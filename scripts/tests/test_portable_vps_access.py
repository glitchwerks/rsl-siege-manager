import importlib.util
import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LOCAL = load("vps_access", "scripts/portable-vps-access-check.py")
REMOTE = load("vps_remote", "scripts/portable-vps-remote.py")
STAGE = load("vps_stage", "scripts/portable-vps-stage.py")
PREFLIGHT_DISPATCH = load("vps_preflight_dispatch", "scripts/portable-vps-release-preflight-dispatch.py")
PLAN_DISPATCH = load("vps_plan_dispatch", "scripts/portable-vps-release-plan-dispatch.py")
PAUSE_DISPATCH = load("vps_pause_dispatch", "scripts/portable-vps-release-pause-dispatch.py")
BACKUP_DISPATCH = load("vps_backup_dispatch", "scripts/portable-vps-release-backup-dispatch.py")
PROOF_DISPATCH = load("vps_proof_dispatch", "scripts/portable-vps-release-proof-dispatch.py")
READY_DISPATCH = load("vps_ready_dispatch", "scripts/portable-vps-release-ready-dispatch.py")
MIGRATE_DISPATCH = load("vps_migrate_dispatch", "scripts/portable-vps-release-migrate-dispatch.py")


class AccessTests(unittest.TestCase):
    def candidate(self):
        return {
            "schema_version": 2,
            "release_id": "test-candidate",
            "siege": {
                "source_sha": "1" * 40,
                "ci_run_id": 123,
                "api_image": "ghcr.io/glitchwerks/siege-api@sha256:" + "a" * 64,
                "frontend_image": "ghcr.io/glitchwerks/siege-frontend@sha256:" + "b" * 64,
            },
            "mom": {
                "source_sha": "2" * 40,
                "ci_run_id": 456,
                "image": "ghcr.io/glitchwerks/mom-bot@sha256:" + "c" * 64,
                "portable_auth": True,
                "scheduler_progress": True,
            },
            "configuration": {"source_sha": "1" * 40, "topology": "mom"},
            "migrations": {
                "siege": {"mode": "none", "from_revision": "0012", "to_revision": "0012"},
                "mom": {"mode": "none", "from_revision": "b6_new_member_alert_subscription", "to_revision": "b6_new_member_alert_subscription"},
                "order": [],
            },
        }

    def test_private_host_identity_comes_from_environment_secrets(self):
        workflow = (ROOT / ".github/workflows/vps-access-check.yml").read_text()
        for name in ("VPS_TAILSCALE_HOST", "VPS_SSH_HOST_FINGERPRINT"):
            self.assertIn("secrets." + name, workflow)
            self.assertNotIn("vars." + name, workflow)

    def test_pinned_ed25519_and_ecdsa_host_keys(self):
        for algorithm in ("ssh-ed25519", "ecdsa-sha2-nistp256"):
            with self.subTest(algorithm=algorithm):
                environment = {
                    "VPS_TAILSCALE_HOST": "100.117.110.57",
                    "VPS_SSH_USER": "root",
                    "VPS_SSH_HOST_FINGERPRINT": "SHA256:" + "A" * 43,
                    "VPS_DEPLOY_SSH_KEY": "-----BEGIN OPENSSH PRIVATE KEY-----\n" + "x" * 100 + "\n-----END OPENSSH PRIVATE KEY-----\n",
                    "VPS_SSH_KNOWN_HOSTS": "100.117.110.57 " + algorithm + " " + "a" * 80,
                }
                results = [(True, ""), (True, "256 SHA256:" + "A" * 43 + " host"), (True, "PASS\n")]
                with patch.dict(os.environ, environment, clear=True), patch.object(
                    LOCAL, "run", side_effect=results
                ):
                    self.assertTrue(all(LOCAL.check().values()))

    def test_missing_settings_fail_without_ssh(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(LOCAL, "run") as runner:
            checks = LOCAL.check()
        self.assertFalse(all(checks.values()))
        runner.assert_not_called()

    def test_key_shape_reports_literal_newline_escapes_without_contents(self):
        escaped = "-----BEGIN OPENSSH PRIVATE KEY-----\\n" + "x" * 100 + "\\n-----END OPENSSH PRIVATE KEY-----"
        with patch.dict(os.environ, {"VPS_DEPLOY_SSH_KEY": escaped}, clear=True):
            checks, *_ = LOCAL.settings()
        self.assertFalse(checks["key_starts_with_header"])
        self.assertFalse(checks["key_has_multiple_lines"])
        self.assertFalse(checks["key_has_no_literal_newline_escapes"])
        self.assertNotIn("PRIVATE KEY", str(checks))

    def test_remote_rejects_any_other_command(self):
        with patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "docker compose up -d"}):
            self.assertEqual(REMOTE.main(), 1)

    def test_stage_requires_confirmation_and_rejects_shell_commands(self):
        for command in (
            "stage-release " + "a" * 64 + " 123-1",
            "stage-release " + "a" * 64 + " 123-1 confirm-stage; id",
            "stage-release " + "a" * 64 + " 123-1 confirm-stage extra",
        ):
            with self.subTest(command=command), patch.dict(
                os.environ, {"SSH_ORIGINAL_COMMAND": command}
            ), patch.object(REMOTE, "stage_release") as stage:
                self.assertEqual(REMOTE.main(), 1)
                stage.assert_not_called()

    def test_preflight_requires_exact_readonly_command(self):
        for command in (
            "preflight-release " + "a" * 64 + " 123-1",
            "preflight-release " + "a" * 64 + " 123-1 confirm-readonly-preflight; id",
            "preflight-release " + "a" * 64 + " 123-1 confirm-readonly-preflight extra",
        ):
            with self.subTest(command=command), patch.dict(
                os.environ, {"SSH_ORIGINAL_COMMAND": command}
            ), patch.object(REMOTE, "preflight_release") as preflight:
                self.assertEqual(REMOTE.main(), 1)
                preflight.assert_not_called()

    def test_pause_plan_requires_exact_readonly_command(self):
        for command in (
            "plan-release " + "a" * 64 + " 123-1",
            "plan-release " + "a" * 64 + " 123-1 confirm-readonly-plan; id",
            "plan-release " + "a" * 64 + " 123-1 confirm-readonly-plan extra",
        ):
            with self.subTest(command=command), patch.dict(
                os.environ, {"SSH_ORIGINAL_COMMAND": command}
            ), patch.object(REMOTE, "plan_release") as plan:
                self.assertEqual(REMOTE.main(), 1)
                plan.assert_not_called()

    def test_writer_pause_requires_exact_confirmed_command(self):
        base = "pause-release " + "a" * 64 + " 123-1 " + "b" * 64
        for command in (base, base + " confirm-pause; id", base + " confirm-pause extra"):
            with self.subTest(command=command), patch.dict(
                os.environ, {"SSH_ORIGINAL_COMMAND": command}
            ), patch.object(REMOTE, "pause_release") as pause:
                self.assertEqual(REMOTE.main(), 1)
                pause.assert_not_called()

    def test_writer_pause_client_reports_rejected_input_without_ssh(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            PAUSE_DISPATCH.ACCESS, "check"
        ) as baseline:
            report = Path(directory) / "report.json"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(PAUSE_DISPATCH.main([
                    "--manifest-sha256", "bad", "--stage-run-id", "123-1",
                    "--plan-sha256", "b" * 64, "--report", str(report)]), 1)
            baseline.assert_not_called()
            self.assertEqual(json.loads(report.read_text())["status"], "STOP")

    def test_managed_backup_requires_exact_confirmed_command(self):
        base = "backup-release " + "a" * 64 + " 123-1 " + "b" * 64
        for command in (base, base + " confirm-backup; id", base + " confirm-backup extra"):
            with self.subTest(command=command), patch.dict(
                os.environ, {"SSH_ORIGINAL_COMMAND": command}
            ), patch.object(REMOTE, "backup_release") as backup:
                self.assertEqual(REMOTE.main(), 1)
                backup.assert_not_called()

    def test_managed_backup_rejects_missing_accepted_pause_before_subprocess(self):
        raw = (json.dumps(self.candidate(), sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "evidence" / "release-stages" / "123-1"
            stage.mkdir(parents=True)
            (stage / "candidate.json").write_bytes(raw)
            (stage / "pause-plan.json").write_text("reviewed plan")
            (stage / "plan-report.json").write_text('{"phase":"plan","status":"PASS","checks":{"safe":true}}')
            (stage / "pause-report.json").write_text('{"phase":"pause","status":"STOP","checks":{"safe":false}}')
            plan_digest = hashlib.sha256((stage / "pause-plan.json").read_bytes()).hexdigest()
            with patch.object(REMOTE, "EVIDENCE", root / "evidence"), patch.object(
                REMOTE, "access_check", return_value=True
            ), patch.object(REMOTE, "private_directory", return_value=True), patch.object(
                REMOTE, "private_file", return_value=True
            ), patch.object(REMOTE.os, "geteuid", return_value=0), patch.object(
                REMOTE.subprocess, "run"
            ) as runner, redirect_stdout(io.StringIO()):
                self.assertEqual(REMOTE.backup_release(digest, "123-1", plan_digest), 1)
                runner.assert_not_called()
            self.assertFalse((stage / "backup-dispatch-report.json").exists())

    def test_managed_backup_invokes_only_reviewed_backup_phase(self):
        raw = (json.dumps(self.candidate(), sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "evidence" / "release-stages" / "123-1"
            stage.mkdir(parents=True)
            (stage / "candidate.json").write_bytes(raw)
            (stage / "pause-plan.json").write_text("reviewed plan")
            (stage / "plan-report.json").write_text('{"phase":"plan","status":"PASS","checks":{"safe":true}}')
            (stage / "pause-report.json").write_text('{"phase":"pause","status":"PASS","checks":{"safe":true}}')
            plan_digest = hashlib.sha256((stage / "pause-plan.json").read_bytes()).hexdigest()
            script = root / "reviewed" / "scripts" / "portable-vps-release-pause-backup.py"
            script.parent.mkdir(parents=True)
            script.write_text("# reviewed\n")

            def run(arguments, **kwargs):
                if arguments[0] == "git" and "rev-parse" in arguments:
                    return SimpleNamespace(returncode=0, stdout=("1" * 40 + "\n").encode())
                if arguments[0] == "git":
                    return SimpleNamespace(returncode=0, stdout=b"")
                self.assertEqual(arguments[:5], ["/usr/bin/python3", "-I", "-B", str(script), "backup"])
                self.assertIn("--confirm-backup", arguments)
                self.assertNotIn("--confirm-pause", arguments)
                (root / "reports" / "123-1-backup.json").write_text(
                    '{"phase":"backup","status":"PASS","checks":{"safe":true}}'
                )
                return SimpleNamespace(returncode=0, stdout=b"PASS\n")

            with patch.object(REMOTE, "EVIDENCE", root / "evidence"), patch.object(
                REMOTE, "PLAN_SCRIPT", script
            ), patch.object(REMOTE, "BACKUP_PROFILE", root / "profile.json"), patch.object(
                REMOTE, "EXTERNAL_REPORTS", root / "reports"
            ), patch.object(REMOTE, "access_check", return_value=True), patch.object(
                REMOTE, "private_directory", side_effect=lambda path: path.is_dir()
            ), patch.object(REMOTE, "private_file", return_value=True), patch.object(
                REMOTE, "reviewed_script", return_value=True
            ), patch.object(REMOTE.os, "geteuid", return_value=0), patch.object(
                REMOTE.subprocess, "run", side_effect=run
            ) as runner, redirect_stdout(io.StringIO()):
                self.assertEqual(REMOTE.backup_release(digest, "123-1", plan_digest), 0)
                self.assertEqual(runner.call_count, 3)
            self.assertEqual(json.loads((stage / "backup-dispatch-report.json").read_text())["status"], "PASS")
            self.assertTrue((stage / "backup-dispatch-start.json").exists())

    def test_existing_attempt_reservation_blocks_second_mutation(self):
        raw = (json.dumps(self.candidate(), sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        for phase, operation in (("pause", REMOTE.pause_release), ("backup", REMOTE.backup_release)):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                stage = root / "evidence" / "release-stages" / "123-1"
                stage.mkdir(parents=True)
                (stage / "candidate.json").write_bytes(raw)
                (stage / "pause-plan.json").write_text("reviewed plan")
                (stage / "plan-report.json").write_text('{"phase":"plan","status":"PASS","checks":{"safe":true}}')
                (stage / "pause-report.json").write_text('{"phase":"pause","status":"PASS","checks":{"safe":true}}')
                (stage / f"{phase}-dispatch-start.json").write_text("first attempt")
                plan_digest = hashlib.sha256((stage / "pause-plan.json").read_bytes()).hexdigest()
                with patch.object(REMOTE, "EVIDENCE", root / "evidence"), patch.object(
                    REMOTE, "EXTERNAL_REPORTS", root / "reports"
                ), patch.object(REMOTE, "access_check", return_value=True), patch.object(
                    REMOTE, "private_directory", side_effect=lambda path: path.is_dir()
                ), patch.object(REMOTE, "private_file", return_value=True), patch.object(
                    REMOTE, "reviewed_checkout", return_value=True
                ), patch.object(REMOTE, "reviewed_script", return_value=True), patch.object(
                    REMOTE.os, "geteuid", return_value=0
                ), patch.object(REMOTE.subprocess, "run") as runner, redirect_stdout(io.StringIO()):
                    self.assertEqual(operation(digest, "123-1", plan_digest), 1)
                    runner.assert_not_called()
                self.assertFalse((stage / f"{phase}-dispatch-report.json").exists())

    def test_managed_backup_client_reports_rejected_input_without_ssh(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            BACKUP_DISPATCH.ACCESS, "check"
        ) as baseline:
            report = Path(directory) / "report.json"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(BACKUP_DISPATCH.main([
                    "--manifest-sha256", "bad", "--stage-run-id", "123-1",
                    "--plan-sha256", "b" * 64, "--report", str(report)]), 1)
            baseline.assert_not_called()
            self.assertEqual(json.loads(report.read_text())["status"], "STOP")

    def test_recovery_proof_requires_exact_readonly_command(self):
        base = "proof-release " + "a" * 64 + " 123-1 " + "b" * 64 + " /private/recover.json /private/restore.json"
        for command in (base, base + " confirm-readonly-proof; id", base + " confirm-readonly-proof extra"):
            with self.subTest(command=command), patch.dict(
                os.environ, {"SSH_ORIGINAL_COMMAND": command}
            ), patch.object(REMOTE, "proof_release") as proof:
                self.assertEqual(REMOTE.main(), 1)
                proof.assert_not_called()

    def test_migration_ready_requires_exact_readonly_command(self):
        base = "ready-release " + "a" * 64 + " 123-1 " + "b" * 64
        for command in (base, base + " confirm-readonly-ready; id", base + " confirm-readonly-ready extra"):
            with self.subTest(command=command), patch.dict(
                os.environ, {"SSH_ORIGINAL_COMMAND": command}
            ), patch.object(REMOTE, "ready_release") as ready:
                self.assertEqual(REMOTE.main(), 1)
                ready.assert_not_called()

    def test_confirmed_migration_requires_exact_database_command(self):
        base = "migrate-release " + "a" * 64 + " 123-1 " + "b" * 64 + " " + "c" * 64 + " siege"
        for command in (base, base + " confirm-migrate-mom", base + " confirm-migrate-siege; id",
                        base + " confirm-migrate-siege extra"):
            with self.subTest(command=command), patch.dict(
                os.environ, {"SSH_ORIGINAL_COMMAND": command}
            ), patch.object(REMOTE, "migrate_release") as migrate:
                self.assertEqual(REMOTE.main(), 1)
                migrate.assert_not_called()

    def test_confirmed_migration_client_rejects_bad_readiness_without_ssh(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            MIGRATE_DISPATCH.ACCESS, "check"
        ) as baseline:
            report = Path(directory) / "report.json"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(MIGRATE_DISPATCH.main([
                    "--manifest-sha256", "a" * 64, "--stage-run-id", "123-1",
                    "--plan-sha256", "b" * 64, "--readiness-sha256", "bad",
                    "--database", "siege", "--report", str(report)]), 1)
            baseline.assert_not_called()
            self.assertEqual(json.loads(report.read_text())["status"], "STOP")

    def test_confirmed_migration_rejects_wrong_readiness_before_subprocess(self):
        raw = (json.dumps(self.candidate(), sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "evidence" / "release-stages" / "123-1"
            stage.mkdir(parents=True)
            (stage / "candidate.json").write_bytes(raw)
            (stage / "pause-plan.json").write_text("reviewed plan")
            (stage / "migration-ready-report.json").write_text(
                '{"phase":"release-migration-ready","status":"PASS","checks":{"safe":true}}')
            (stage / "migration-ready-baseline.json").write_text("reviewed readiness")
            plan_digest = hashlib.sha256((stage / "pause-plan.json").read_bytes()).hexdigest()
            with patch.object(REMOTE, "EVIDENCE", root / "evidence"), patch.object(
                REMOTE, "access_check", return_value=True
            ), patch.object(REMOTE, "private_directory", return_value=True), patch.object(
                REMOTE, "private_file", return_value=True
            ), patch.object(REMOTE.os, "geteuid", return_value=0), patch.object(
                REMOTE.subprocess, "run"
            ) as runner, redirect_stdout(io.StringIO()):
                self.assertEqual(REMOTE.migrate_release(digest, "123-1", plan_digest, "c" * 64, "siege"), 1)
                runner.assert_not_called()
            self.assertFalse((stage / "migrate-siege-dispatch-report.json").exists())

    def test_confirmed_migration_invokes_only_selected_reviewed_database(self):
        raw = (json.dumps(self.candidate(), sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        for database in ("siege", "mom"):
            with self.subTest(database=database), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                stage = root / "evidence" / "release-stages" / "123-1"
                stage.mkdir(parents=True)
                (stage / "candidate.json").write_bytes(raw)
                (stage / "pause-plan.json").write_text("reviewed plan")
                (stage / "migration-ready-report.json").write_text(
                    '{"phase":"release-migration-ready","status":"PASS","checks":{"safe":true}}')
                (stage / "migration-ready-baseline.json").write_text("reviewed readiness")
                plan_digest = hashlib.sha256((stage / "pause-plan.json").read_bytes()).hexdigest()
                readiness = hashlib.sha256((stage / "migration-ready-baseline.json").read_bytes()).hexdigest()
                script = root / "reviewed" / "scripts" / "portable-vps-release-migrate.py"
                script.parent.mkdir(parents=True)
                script.write_text("# reviewed\n")

                def run(arguments, **kwargs):
                    if arguments[0] == "git" and "rev-parse" in arguments:
                        return SimpleNamespace(returncode=0, stdout=("1" * 40 + "\n").encode())
                    if arguments[0] == "git":
                        return SimpleNamespace(returncode=0, stdout=b"")
                    self.assertEqual(arguments[:4], ["/usr/bin/python3", "-I", "-B", str(script)])
                    self.assertEqual(arguments[arguments.index("--database") + 1], database)
                    self.assertEqual(arguments[arguments.index("--confirm-migrate") + 1], database)
                    (root / "reports" / f"123-1-migrate-{database}.json").write_text(
                        json.dumps({"phase": f"migrate-{database}", "status": "PASS", "checks": {"safe": True}}))
                    return SimpleNamespace(returncode=0, stdout=b"PASS\n")

                with patch.object(REMOTE, "EVIDENCE", root / "evidence"), patch.object(
                    REMOTE, "MIGRATE_SCRIPT", script
                ), patch.object(REMOTE, "BACKUP_PROFILE", root / "profile.json"), patch.object(
                    REMOTE, "EXTERNAL_REPORTS", root / "reports"
                ), patch.object(REMOTE, "access_check", return_value=True), patch.object(
                    REMOTE, "private_directory", side_effect=lambda path: path.is_dir()
                ), patch.object(REMOTE, "private_file", return_value=True), patch.object(
                    REMOTE, "reviewed_script", return_value=True
                ), patch.object(REMOTE.os, "geteuid", return_value=0), patch.object(
                    REMOTE.subprocess, "run", side_effect=run
                ) as runner, redirect_stdout(io.StringIO()):
                    self.assertEqual(REMOTE.migrate_release(digest, "123-1", plan_digest, readiness, database), 0)
                    self.assertEqual(runner.call_count, 3)
                self.assertEqual(json.loads((stage / f"migrate-{database}-dispatch-report.json").read_text())["status"], "PASS")

    def test_migration_ready_client_reports_rejected_input_without_ssh(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            READY_DISPATCH.ACCESS, "check"
        ) as baseline:
            report = Path(directory) / "report.json"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(READY_DISPATCH.main([
                    "--manifest-sha256", "bad", "--stage-run-id", "123-1",
                    "--plan-sha256", "b" * 64, "--report", str(report)]), 1)
            baseline.assert_not_called()
            self.assertEqual(json.loads(report.read_text())["status"], "STOP")

    def test_migration_ready_rejects_missing_proof_before_subprocess(self):
        raw = (json.dumps(self.candidate(), sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "evidence" / "release-stages" / "123-1"
            stage.mkdir(parents=True)
            (stage / "candidate.json").write_bytes(raw)
            (stage / "pause-plan.json").write_text("reviewed plan")
            (stage / "recovery-proof-report.json").write_text(
                '{"phase":"release-recovery-proof","status":"STOP","checks":{"safe":false}}')
            (stage / "recovery-proof-baseline.json").write_text("private proof")
            plan_digest = hashlib.sha256((stage / "pause-plan.json").read_bytes()).hexdigest()
            with patch.object(REMOTE, "EVIDENCE", root / "evidence"), patch.object(
                REMOTE, "access_check", return_value=True
            ), patch.object(REMOTE, "private_directory", return_value=True), patch.object(
                REMOTE, "private_file", return_value=True
            ), patch.object(REMOTE.os, "geteuid", return_value=0), patch.object(
                REMOTE.subprocess, "run"
            ) as runner, redirect_stdout(io.StringIO()):
                self.assertEqual(REMOTE.ready_release(digest, "123-1", plan_digest), 1)
                runner.assert_not_called()
            self.assertFalse((stage / "ready-dispatch-report.json").exists())

    def test_migration_ready_invokes_reviewed_readonly_script(self):
        raw = (json.dumps(self.candidate(), sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "evidence" / "release-stages" / "123-1"
            stage.mkdir(parents=True)
            (stage / "candidate.json").write_bytes(raw)
            (stage / "pause-plan.json").write_text("reviewed plan")
            (stage / "recovery-proof-report.json").write_text(
                '{"phase":"release-recovery-proof","status":"PASS","checks":{"safe":true}}')
            (stage / "recovery-proof-baseline.json").write_text("private proof")
            plan_digest = hashlib.sha256((stage / "pause-plan.json").read_bytes()).hexdigest()
            script = root / "reviewed" / "scripts" / "portable-vps-release-migration-ready.py"
            script.parent.mkdir(parents=True)
            script.write_text("# reviewed\n")

            def run(arguments, **kwargs):
                if arguments[0] == "git" and "rev-parse" in arguments:
                    return SimpleNamespace(returncode=0, stdout=("1" * 40 + "\n").encode())
                if arguments[0] == "git":
                    return SimpleNamespace(returncode=0, stdout=b"")
                self.assertEqual(arguments[:4], ["/usr/bin/python3", "-I", "-B", str(script)])
                self.assertNotIn("--confirm-pause", arguments)
                self.assertNotIn("--confirm-backup", arguments)
                (root / "reports" / "123-1-migration-ready.json").write_text(
                    '{"phase":"release-migration-ready","status":"PASS","checks":{"safe":true}}')
                (stage / "migration-ready-baseline.json").write_text("reviewed readiness")
                return SimpleNamespace(returncode=0, stdout=b"PASS\n")

            output = io.StringIO()
            with patch.object(REMOTE, "EVIDENCE", root / "evidence"), patch.object(
                REMOTE, "MIGRATION_READY_SCRIPT", script
            ), patch.object(REMOTE, "BACKUP_PROFILE", root / "profile.json"), patch.object(
                REMOTE, "EXTERNAL_REPORTS", root / "reports"
            ), patch.object(REMOTE, "access_check", return_value=True), patch.object(
                REMOTE, "private_directory", side_effect=lambda path: path.is_dir()
            ), patch.object(REMOTE, "private_file", return_value=True), patch.object(
                REMOTE, "reviewed_script", return_value=True
            ), patch.object(REMOTE.os, "geteuid", return_value=0), patch.object(
                REMOTE.subprocess, "run", side_effect=run
            ) as runner, redirect_stdout(output):
                self.assertEqual(REMOTE.ready_release(digest, "123-1", plan_digest), 0)
                self.assertEqual(runner.call_count, 3)
            self.assertEqual(json.loads((stage / "ready-dispatch-report.json").read_text())["status"], "PASS")
            self.assertEqual(json.loads(output.getvalue())["readiness_sha256"],
                             hashlib.sha256((stage / "migration-ready-baseline.json").read_bytes()).hexdigest())

    def test_recovery_proof_client_rejects_unbounded_paths_without_ssh(self):
        for path in ("relative/recover.json", "/private/../secret.json", "/private/report;id"):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory, patch.object(
                PROOF_DISPATCH.ACCESS, "check"
            ) as baseline:
                report = Path(directory) / "report.json"
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(PROOF_DISPATCH.main([
                        "--manifest-sha256", "a" * 64, "--stage-run-id", "123-1",
                        "--plan-sha256", "b" * 64, "--recover-report", path,
                        "--restore-report", "/private/restore.json", "--report", str(report)]), 1)
                baseline.assert_not_called()
                self.assertEqual(json.loads(report.read_text())["status"], "STOP")

    def test_recovery_proof_rejects_missing_backup_before_subprocess(self):
        raw = (json.dumps(self.candidate(), sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "evidence" / "release-stages" / "123-1"
            stage.mkdir(parents=True)
            (stage / "candidate.json").write_bytes(raw)
            (stage / "pause-plan.json").write_text("reviewed plan")
            (stage / "plan-report.json").write_text('{"phase":"plan","status":"PASS","checks":{"safe":true}}')
            (stage / "pause-report.json").write_text('{"phase":"pause","status":"PASS","checks":{"safe":true}}')
            (stage / "backup-report.json").write_text('{"phase":"backup","status":"STOP","checks":{"safe":false}}')
            plan_digest = hashlib.sha256((stage / "pause-plan.json").read_bytes()).hexdigest()
            with patch.object(REMOTE, "EVIDENCE", root / "evidence"), patch.object(
                REMOTE, "access_check", return_value=True
            ), patch.object(REMOTE, "private_directory", return_value=True), patch.object(
                REMOTE, "private_file", return_value=True
            ), patch.object(REMOTE.os, "geteuid", return_value=0), patch.object(
                REMOTE.subprocess, "run"
            ) as runner, redirect_stdout(io.StringIO()):
                self.assertEqual(REMOTE.proof_release(digest, "123-1", plan_digest,
                    "/private/recover.json", "/private/restore.json"), 1)
                runner.assert_not_called()
            self.assertFalse((stage / "proof-dispatch-report.json").exists())

    def test_recovery_proof_invokes_only_reviewed_readonly_script(self):
        raw = (json.dumps(self.candidate(), sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "evidence" / "release-stages" / "123-1"
            stage.mkdir(parents=True)
            (stage / "candidate.json").write_bytes(raw)
            (stage / "pause-plan.json").write_text("reviewed plan")
            for phase in ("plan", "pause", "backup"):
                (stage / f"{phase}-report.json").write_text(
                    json.dumps({"phase": phase, "status": "PASS", "checks": {"safe": True}}))
            plan_digest = hashlib.sha256((stage / "pause-plan.json").read_bytes()).hexdigest()
            script = root / "reviewed" / "scripts" / "portable-vps-release-recovery-proof.py"
            script.parent.mkdir(parents=True)
            script.write_text("# reviewed\n")

            def run(arguments, **kwargs):
                if arguments[0] == "git" and "rev-parse" in arguments:
                    return SimpleNamespace(returncode=0, stdout=("1" * 40 + "\n").encode())
                if arguments[0] == "git":
                    return SimpleNamespace(returncode=0, stdout=b"")
                self.assertEqual(arguments[:4], ["/usr/bin/python3", "-I", "-B", str(script)])
                self.assertIn("--recover-report", arguments)
                self.assertIn("--restore-report", arguments)
                self.assertNotIn("--confirm-pause", arguments)
                self.assertNotIn("--confirm-backup", arguments)
                (root / "reports" / "123-1-recovery-proof.json").write_text(
                    '{"phase":"release-recovery-proof","status":"PASS","checks":{"safe":true}}')
                return SimpleNamespace(returncode=0, stdout=b"PASS\n")

            with patch.object(REMOTE, "EVIDENCE", root / "evidence"), patch.object(
                REMOTE, "RECOVERY_PROOF_SCRIPT", script
            ), patch.object(REMOTE, "BACKUP_PROFILE", root / "profile.json"), patch.object(
                REMOTE, "EXTERNAL_REPORTS", root / "reports"
            ), patch.object(REMOTE, "access_check", return_value=True), patch.object(
                REMOTE, "private_directory", side_effect=lambda path: path.is_dir()
            ), patch.object(REMOTE, "private_file", return_value=True), patch.object(
                REMOTE, "reviewed_script", return_value=True
            ), patch.object(REMOTE.os, "geteuid", return_value=0), patch.object(
                REMOTE.subprocess, "run", side_effect=run
            ) as runner, redirect_stdout(io.StringIO()):
                self.assertEqual(REMOTE.proof_release(digest, "123-1", plan_digest,
                    "/private/recover.json", "/private/restore.json"), 0)
                self.assertEqual(runner.call_count, 3)
            self.assertEqual(json.loads((stage / "proof-dispatch-report.json").read_text())["status"], "PASS")

    def test_writer_pause_rejects_wrong_plan_before_subprocess(self):
        raw = (json.dumps(self.candidate(), sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "evidence" / "release-stages" / "123-1"
            stage.mkdir(parents=True)
            (stage / "candidate.json").write_bytes(raw)
            (stage / "pause-plan.json").write_text("reviewed plan")
            (stage / "plan-report.json").write_text('{"phase":"plan","status":"PASS","checks":{"safe":true}}')
            with patch.object(REMOTE, "EVIDENCE", root / "evidence"), patch.object(
                REMOTE, "access_check", return_value=True
            ), patch.object(REMOTE, "private_directory", return_value=True), patch.object(
                REMOTE, "private_file", return_value=True
            ), patch.object(REMOTE.os, "geteuid", return_value=0), patch.object(
                REMOTE.subprocess, "run"
            ) as runner, redirect_stdout(io.StringIO()):
                self.assertEqual(REMOTE.pause_release(digest, "123-1", "b" * 64), 1)
                runner.assert_not_called()
            self.assertFalse((stage / "pause-dispatch-report.json").exists())

    def test_writer_pause_calls_only_reviewed_phase_with_bound_plan(self):
        raw = (json.dumps(self.candidate(), sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "evidence" / "release-stages" / "123-1"
            stage.mkdir(parents=True)
            (stage / "candidate.json").write_bytes(raw)
            (stage / "pause-plan.json").write_text("reviewed plan")
            (stage / "plan-report.json").write_text('{"phase":"plan","status":"PASS","checks":{"safe":true}}')
            plan_digest = hashlib.sha256((stage / "pause-plan.json").read_bytes()).hexdigest()
            script = root / "reviewed" / "scripts" / "portable-vps-release-pause-backup.py"
            script.parent.mkdir(parents=True)
            script.write_text("# reviewed\n")

            def run(arguments, **kwargs):
                if arguments[0] == "git" and "rev-parse" in arguments:
                    return SimpleNamespace(returncode=0, stdout=("1" * 40 + "\n").encode())
                if arguments[0] == "git":
                    return SimpleNamespace(returncode=0, stdout=b"")
                self.assertEqual(arguments[:5], ["/usr/bin/python3", "-I", "-B", str(script), "pause"])
                self.assertIn("--confirm-pause", arguments)
                self.assertNotIn("backup", arguments)
                (root / "reports" / "123-1-pause.json").write_text(
                    '{"phase":"pause","status":"PASS","checks":{"safe":true}}'
                )
                return SimpleNamespace(returncode=0, stdout=b"PASS\n")

            output = io.StringIO()
            with patch.object(REMOTE, "EVIDENCE", root / "evidence"), patch.object(
                REMOTE, "PLAN_SCRIPT", script
            ), patch.object(REMOTE, "BACKUP_PROFILE", root / "profile.json"), patch.object(
                REMOTE, "EXTERNAL_REPORTS", root / "reports"
            ), patch.object(REMOTE, "access_check", return_value=True), patch.object(
                REMOTE, "private_directory", side_effect=lambda path: path.is_dir()
            ), patch.object(REMOTE, "private_file", return_value=True), patch.object(
                REMOTE, "reviewed_script", return_value=True
            ), patch.object(REMOTE.os, "geteuid", return_value=0), patch.object(
                REMOTE.subprocess, "run", side_effect=run
            ) as runner, redirect_stdout(output):
                self.assertEqual(REMOTE.pause_release(digest, "123-1", plan_digest), 0)
                self.assertEqual(runner.call_count, 3)
            self.assertEqual(json.loads(output.getvalue())["status"], "PASS")
            self.assertEqual(json.loads((stage / "pause-dispatch-report.json").read_text())["status"], "PASS")
            self.assertTrue((stage / "pause-dispatch-start.json").exists())

    def test_pause_plan_dispatch_records_only_bound_digest(self):
        raw = (json.dumps(self.candidate(), sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "evidence" / "release-stages" / "123-1"
            stage.mkdir(parents=True, mode=0o700)
            (stage / "candidate.json").write_bytes(raw)
            profile = root / "release-backup-profile.json"
            profile.write_text("private profile")
            script = root / "reviewed" / "scripts" / "portable-vps-release-pause-backup.py"
            script.parent.mkdir(parents=True)
            script.write_text("# reviewed\n")

            def run(arguments, **kwargs):
                if arguments[0] == "git" and "rev-parse" in arguments:
                    return SimpleNamespace(returncode=0, stdout=("1" * 40 + "\n").encode())
                if arguments[0] == "git":
                    return SimpleNamespace(returncode=0, stdout=b"")
                self.assertEqual(arguments[0:4], ["/usr/bin/python3", "-I", "-B", str(script)])
                self.assertEqual(arguments[4], "plan")
                self.assertNotIn("pause", arguments)
                self.assertNotIn("backup", arguments)
                (root / "reports" / "123-1-plan.json").write_text(
                    '{"phase":"plan","status":"PASS","checks":{"safe":true}}'
                )
                (stage / "pause-plan.json").write_text('{"phase":"pause-plan"}')
                return SimpleNamespace(returncode=0, stdout=b"PASS\n")

            output = io.StringIO()
            with patch.object(REMOTE, "EVIDENCE", root / "evidence"), patch.object(
                REMOTE, "REVIEWED_SOURCE", script.parents[1]
            ), patch.object(REMOTE, "PLAN_SCRIPT", script), patch.object(
                REMOTE, "BACKUP_PROFILE", profile
            ), patch.object(REMOTE, "EXTERNAL_REPORTS", root / "reports"), patch.object(
                REMOTE, "access_check", return_value=True
            ), patch.object(REMOTE, "private_directory", side_effect=lambda path: path.is_dir()), patch.object(
                REMOTE, "private_file", return_value=True
            ), patch.object(REMOTE, "reviewed_script", return_value=True), patch.object(
                REMOTE.os, "geteuid", return_value=0
            ), patch.object(REMOTE.subprocess, "run", side_effect=run) as runner, redirect_stdout(output):
                self.assertEqual(REMOTE.plan_release(digest, "123-1"), 0)
                self.assertEqual(runner.call_count, 3)
            report = json.loads(output.getvalue())
            self.assertEqual(report["plan_sha256"], hashlib.sha256((stage / "pause-plan.json").read_bytes()).hexdigest())
            self.assertEqual(report["status"], "PASS")
            self.assertNotIn("private profile", output.getvalue())

    def test_reviewed_checkout_allows_only_hash_verified_prior_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "portable-vps-remote.py.before-release-stage"
            backup.write_bytes(b"reviewed prior script")
            sha = "1" * 40
            git_results = [
                SimpleNamespace(returncode=0, stdout=(sha + "\n").encode()),
                SimpleNamespace(returncode=0, stdout=b"?? scripts/portable-vps-remote.py.before-release-stage\n"),
            ]
            with patch.object(REMOTE, "STAGE_SCRIPT_BACKUP", backup), patch.object(
                REMOTE, "INITIAL_REMOTE_SHA256", hashlib.sha256(backup.read_bytes()).hexdigest()
            ), patch.object(REMOTE, "private_file", return_value=True), patch.object(
                REMOTE.subprocess, "run", side_effect=git_results
            ):
                self.assertTrue(REMOTE.reviewed_checkout(sha))
            with patch.object(REMOTE, "STAGE_SCRIPT_BACKUP", backup), patch.object(
                REMOTE, "INITIAL_REMOTE_SHA256", "a" * 64
            ), patch.object(REMOTE, "private_file", return_value=True), patch.object(
                REMOTE.subprocess, "run", side_effect=git_results
            ):
                self.assertFalse(REMOTE.reviewed_checkout(sha))
            git_results[1] = SimpleNamespace(returncode=0, stdout=b"?? scripts/unknown.py\n")
            with patch.object(REMOTE.subprocess, "run", side_effect=git_results):
                self.assertFalse(REMOTE.reviewed_checkout(sha))

    def test_preflight_runs_only_staged_candidate_and_reviewed_source(self):
        raw = (json.dumps(self.candidate(), sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "evidence" / "release-stages" / "123-1"
            stage.mkdir(parents=True, mode=0o700)
            (stage / "candidate.json").write_bytes(raw)
            (stage / "report.json").write_text('{"phase":"stage-release","status":"PASS","checks":{"manifest_stored":true}}')
            script = root / "reviewed" / "scripts" / "portable-vps-release-preflight.py"
            script.parent.mkdir(parents=True)
            script.write_text("# reviewed\n")
            for path in (stage / "candidate.json", stage / "report.json", script):
                path.chmod(0o600)

            def run(arguments, **kwargs):
                if arguments[0] == "git" and "rev-parse" in arguments:
                    return SimpleNamespace(returncode=0, stdout=("1" * 40 + "\n").encode())
                if arguments[0] == "git":
                    return SimpleNamespace(returncode=0, stdout=b"")
                self.assertEqual(arguments[0], "/usr/bin/python3")
                self.assertIn("--manifest-sha256", arguments)
                self.assertFalse(Path(arguments[arguments.index("--report") + 1]).is_relative_to(root / "project"))
                self.assertEqual(kwargs["env"]["PYTHONSAFEPATH"], "1")
                (root / "reports" / "123-1-preflight.json").write_text(
                    '{"phase":"release-preflight","status":"PASS","checks":{"safe":true}}'
                )
                return SimpleNamespace(returncode=0, stdout=b"PASS\n")

            with patch.object(REMOTE, "EVIDENCE", root / "evidence"), patch.object(
                REMOTE, "PROJECT", root / "project"
            ), patch.object(REMOTE, "REVIEWED_SOURCE", script.parents[1]), patch.object(
                REMOTE, "PREFLIGHT_SCRIPT", script
            ), patch.object(REMOTE, "EXTERNAL_REPORTS", root / "reports"
            ), patch.object(REMOTE, "access_check", return_value=True), patch.object(
                REMOTE, "private_directory", side_effect=lambda path: path.is_dir() and not path.is_symlink()
            ), patch.object(REMOTE, "reviewed_script", return_value=True
            ), patch.object(REMOTE, "private_file", return_value=True
            ), patch.object(REMOTE.os, "geteuid", return_value=0), patch.object(
                REMOTE.subprocess, "run", side_effect=run
            ) as runner, redirect_stdout(io.StringIO()):
                self.assertEqual(REMOTE.preflight_release(digest, "123-1"), 0)
                self.assertEqual(runner.call_count, 3)
            self.assertEqual(json.loads((stage / "preflight-dispatch-report.json").read_text())["status"], "PASS")
            self.assertNotIn("test-candidate", (stage / "preflight-dispatch-report.json").read_text())

            (stage / "preflight-dispatch-report.json").unlink()
            (root / "reports" / "123-1-preflight.json").unlink()

            def wrong_response(arguments, **kwargs):
                if arguments[0] == "git" and "rev-parse" in arguments:
                    return SimpleNamespace(returncode=0, stdout=("1" * 40 + "\n").encode())
                if arguments[0] == "git":
                    return SimpleNamespace(returncode=0, stdout=b"")
                (root / "reports" / "123-1-preflight.json").write_text(
                    '{"phase":"release-preflight","status":"PASS","checks":{"safe":true}}'
                )
                return SimpleNamespace(returncode=0, stdout=b"wrong\n")

            with patch.object(REMOTE, "EVIDENCE", root / "evidence"), patch.object(
                REMOTE, "REVIEWED_SOURCE", script.parents[1]
            ), patch.object(REMOTE, "PREFLIGHT_SCRIPT", script), patch.object(
                REMOTE, "EXTERNAL_REPORTS", root / "reports"
            ), patch.object(
                REMOTE, "access_check", return_value=True
            ), patch.object(REMOTE, "private_directory", return_value=True), patch.object(
                REMOTE, "reviewed_script", return_value=True
            ), patch.object(REMOTE, "private_file", return_value=True
            ), patch.object(
                REMOTE.os, "geteuid", return_value=0
            ), patch.object(REMOTE.subprocess, "run", side_effect=wrong_response) as runner, redirect_stdout(io.StringIO()):
                self.assertEqual(REMOTE.preflight_release(digest, "123-1"), 1)
                self.assertEqual(runner.call_count, 3)

            (stage / "preflight-dispatch-report.json").unlink()
            with patch.object(REMOTE, "EVIDENCE", root / "evidence"), patch.object(
                REMOTE, "access_check", return_value=True
            ), patch.object(REMOTE, "private_directory", return_value=True), patch.object(
                REMOTE, "private_file", return_value=True
            ), patch.object(REMOTE.os, "geteuid", return_value=0), patch.object(
                REMOTE.subprocess, "run"
            ) as runner, redirect_stdout(io.StringIO()):
                self.assertEqual(REMOTE.preflight_release("a" * 64, "123-1"), 1)
                runner.assert_not_called()

    def test_stage_writes_only_digest_bound_private_manifest_and_report(self):
        raw = (json.dumps(self.candidate(), sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence"
            evidence.mkdir(mode=0o700)
            output = io.StringIO()
            with patch.object(REMOTE, "EVIDENCE", evidence), patch.object(
                REMOTE, "access_check", return_value=True
            ), patch.object(REMOTE, "private_directory", side_effect=lambda path: path.is_dir() and not path.is_symlink()), patch.object(
                REMOTE.os, "geteuid", return_value=0
            ), redirect_stdout(output):
                self.assertEqual(REMOTE.stage_release(digest, "123-1", io.BytesIO(raw)), 0)
                self.assertEqual(REMOTE.stage_release(digest, "123-1", io.BytesIO(raw)), 1)
            staged = evidence / "release-stages" / "123-1"
            self.assertEqual((staged / "candidate.json").read_bytes(), raw)
            report = json.loads((staged / "report.json").read_text())
            self.assertEqual(report["status"], "PASS")
            self.assertTrue(all(report["checks"].values()))
            self.assertEqual((staged / "candidate.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual((staged / "report.json").stat().st_mode & 0o777, 0o600)
            self.assertNotIn("test-candidate", output.getvalue())

    def test_stage_refuses_digest_mismatch_oversize_and_duplicate_keys(self):
        raw = json.dumps(self.candidate()).encode()
        self.assertTrue(REMOTE.valid_manifest(raw))
        duplicate = raw.replace(b'"schema_version": 2,', b'"schema_version": 2, "schema_version": 2,')
        self.assertFalse(REMOTE.valid_manifest(duplicate))
        self.assertFalse(REMOTE.valid_manifest(b"[" * 1100 + b"0" + b"]" * 1100))
        migration = self.candidate()
        migration["migrations"]["siege"].update(mode="upgrade", to_revision="0013")
        self.assertFalse(REMOTE.valid_manifest(json.dumps(migration).encode()))
        migration["migrations"]["order"] = ["siege"]
        self.assertTrue(REMOTE.valid_manifest(json.dumps(migration).encode()))
        migration["migrations"]["siege"]["to_revision"] = "head"
        self.assertFalse(REMOTE.valid_manifest(json.dumps(migration).encode()))
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence"
            evidence.mkdir(mode=0o700)
            with patch.object(REMOTE, "EVIDENCE", evidence), patch.object(
                REMOTE, "access_check", return_value=True
            ), patch.object(REMOTE, "private_directory", side_effect=lambda path: path.is_dir() and not path.is_symlink()), patch.object(
                REMOTE.os, "geteuid", return_value=0
            ), redirect_stdout(io.StringIO()):
                self.assertEqual(REMOTE.stage_release("a" * 64, "124-1", io.BytesIO(raw)), 1)
                self.assertEqual(REMOTE.stage_release("a" * 64, "125-1", io.BytesIO(b"x" * (REMOTE.MAX_MANIFEST + 1))), 1)
            for run_id in ("124-1", "125-1"):
                staged = evidence / "release-stages" / run_id
                self.assertFalse((staged / "candidate.json").exists())
                self.assertEqual(json.loads((staged / "report.json").read_text())["status"], "STOP")

    def test_runner_stage_sends_exact_manifest_after_read_only_baseline(self):
        raw = (json.dumps(self.candidate()) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        remote_report = {"phase": "stage-release", "status": "PASS", "checks": {"manifest_stored": True}}
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "candidate.json"
            manifest.write_bytes(raw)
            with patch.object(STAGE.ACCESS, "check", return_value={"baseline": True}), patch.object(
                STAGE.ACCESS, "settings", return_value=({}, "host.example", "root", "", "private-key", "known-host")
            ), patch.object(
                STAGE.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(remote_report).encode())
            ) as ssh:
                checks = STAGE.stage(manifest, digest, "123-1")
            self.assertTrue(all(checks.values()))
            self.assertEqual(ssh.call_args.kwargs["input"], raw)
            self.assertEqual(ssh.call_args.args[0][-1], f"stage-release {digest} 123-1 confirm-stage")

    def test_runner_stage_rejects_wrong_digest_before_ssh(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "candidate.json"
            manifest.write_text(json.dumps(self.candidate()))
            with patch.object(STAGE.ACCESS, "check") as access, patch.object(STAGE.subprocess, "run") as ssh:
                checks = STAGE.stage(manifest, "a" * 64, "123-1")
            self.assertFalse(checks["manifest_digest"])
            access.assert_not_called()
            ssh.assert_not_called()

    def test_runner_preflight_sends_only_exact_readonly_command(self):
        digest = "a" * 64
        remote_report = {"phase": "release-preflight-dispatch", "status": "PASS", "checks": {"safe": True}}
        with patch.object(PREFLIGHT_DISPATCH.ACCESS, "check", return_value={"baseline": True}), patch.object(
            PREFLIGHT_DISPATCH.ACCESS, "settings", return_value=({}, "host.example", "root", "", "private-key", "known-host")
        ), patch.object(
            PREFLIGHT_DISPATCH.subprocess, "run",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(remote_report).encode()),
        ) as ssh:
            checks = PREFLIGHT_DISPATCH.preflight(digest, "123-1")
        self.assertTrue(all(checks.values()))
        self.assertEqual(ssh.call_args.args[0][-1], f"preflight-release {digest} 123-1 confirm-readonly-preflight")
        self.assertNotIn("input", ssh.call_args.kwargs)

        with patch.object(PREFLIGHT_DISPATCH.ACCESS, "check") as access, patch.object(
            PREFLIGHT_DISPATCH.subprocess, "run"
        ) as ssh:
            checks = PREFLIGHT_DISPATCH.preflight(digest + "; id", "123-1")
        self.assertFalse(checks["request_shape"])
        access.assert_not_called()
        ssh.assert_not_called()

    def test_runner_plan_returns_only_hash_from_exact_request(self):
        digest = "a" * 64
        plan_sha256 = "b" * 64
        remote_report = {"phase": "release-pause-plan-dispatch", "status": "PASS",
                         "checks": {"safe": True}, "plan_sha256": plan_sha256}
        with patch.object(PLAN_DISPATCH.ACCESS, "check", return_value={"baseline": True}), patch.object(
            PLAN_DISPATCH.ACCESS, "settings", return_value=({}, "host.example", "root", "", "private-key", "known-host")
        ), patch.object(
            PLAN_DISPATCH.subprocess, "run",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(remote_report).encode()),
        ) as ssh:
            checks, actual = PLAN_DISPATCH.plan(digest, "123-1")
        self.assertTrue(all(checks.values()))
        self.assertEqual(actual, plan_sha256)
        self.assertEqual(ssh.call_args.args[0][-1], f"plan-release {digest} 123-1 confirm-readonly-plan")
        self.assertNotIn("input", ssh.call_args.kwargs)
        with patch.object(PLAN_DISPATCH.ACCESS, "check") as access, patch.object(
            PLAN_DISPATCH.subprocess, "run"
        ) as ssh:
            checks, actual = PLAN_DISPATCH.plan(digest + "; id", "123-1")
        self.assertFalse(checks["request_shape"])
        self.assertIsNone(actual)
        access.assert_not_called()
        ssh.assert_not_called()

    def test_remote_access_check_is_read_only(self):
        with patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "access-check"}), patch.object(
            REMOTE, "private_directory", return_value=True
        ), patch.object(REMOTE, "succeeds", return_value=True) as runner:
            self.assertEqual(REMOTE.main(), 0)
        self.assertEqual(runner.call_count, 3)
        self.assertEqual(
            runner.call_args.args[0],
            ["systemctl", "is-active", "--quiet", "rsl-production-backup.timer"],
        )

    def test_report_exclusive_and_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            with patch.object(LOCAL, "check", return_value={"ssh_key": False}), patch(
                "sys.argv", ["access", "--report", str(report)]
            ):
                self.assertEqual(LOCAL.main(), 1)
                self.assertEqual(LOCAL.main(), 2)
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("PRIVATE KEY", report.read_text())


if __name__ == "__main__":
    unittest.main()
