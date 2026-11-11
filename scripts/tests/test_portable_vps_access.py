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


class AccessTests(unittest.TestCase):
    def candidate(self):
        return {
            "schema_version": 1,
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
        duplicate = raw.replace(b'"schema_version": 1,', b'"schema_version": 1, "schema_version": 1,')
        self.assertFalse(REMOTE.valid_manifest(duplicate))
        self.assertFalse(REMOTE.valid_manifest(b"[" * 1100 + b"0" + b"]" * 1100))
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
