"""The SMB credential repair tests the candidate before replacing private state."""

import importlib.util
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-backup-smb-credentials.py"
SPEC = importlib.util.spec_from_file_location("backup_smb_credentials", SCRIPT)
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


class SmbCredentialRepairTests(unittest.TestCase):
    def test_field_replacement_changes_only_selected_line(self):
        original = b"[backup]\r\ntype=smb\r\nhost=server-nas\r\nuser=old\r\npass=old-secret\r\ndomain=OLD\r\n"
        updated = repair.replace_field(original, "user", "new")
        self.assertEqual(updated, original.replace(b"user=old", b"user=new"))
        self.assertNotIn(b"old-secret\n", updated)

    def test_duplicate_field_stops(self):
        with self.assertRaisesRegex(ValueError, "config_field_not_unique"):
            repair.replace_field(b"user=one\nuser=two\n", "user", "new")

    def test_probe_reports_only_fixed_failure_class(self):
        samples = {
            b"Logon failure: STATUS_PASSWORD_MUST_CHANGE on secret-endpoint": "candidate_password_change_required",
            b"mount failed: Logon failure: invalid credentials for secret-endpoint": "candidate_authentication_failed",
            b"share not found on secret-endpoint": "candidate_share_not_found",
            b"access denied on secret-endpoint": "candidate_permission_denied",
            b"connection refused by secret-endpoint": "candidate_connection_failed",
            b"unrecognized private error secret-endpoint": "candidate_remote_read_unknown",
        }
        for raw_error, expected in samples.items():
            with self.subTest(expected=expected):
                self.assertEqual(repair.probe_failure(raw_error), expected)
        with patch.object(repair.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"share not found on secret-endpoint")):
            with self.assertRaisesRegex(ValueError, "^candidate_share_not_found$"):
                repair.probe(Path("/private/candidate"), "share")

    def run_repair(self, probe_result, fail_write=False):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        lock = root / "job.lock"
        lock.write_bytes(b"")
        lock.chmod(0o600)
        config = root / "upload.conf"
        original = b"[backup]\ntype=smb\nhost=server-nas\nuser=old\npass=old-secret\ndomain=OLD\n"
        config.write_bytes(original)
        config.chmod(0o600)
        info = config.stat()
        values = {"domain": "OLD"}

        def read_private(path, _limit):
            current = path.stat()
            return SimpleNamespace(st_uid=0, st_dev=current.st_dev, st_ino=current.st_ino), path.read_bytes()

        original_write = repair.write_private
        def write_candidate(path, data, mode=0o600):
            if fail_write:
                original_write(path, data[:16], mode)
                raise OSError("simulated partial write")
            return original_write(path, data, mode)

        with (patch.object(repair.os, "geteuid", return_value=0),
              patch.object(repair, "read_state", return_value=(root, config, info, original, values, "share")),
              patch.object(repair, "private_file", side_effect=read_private),
              patch.object(repair, "service_idle", return_value=True),
              patch.object(repair, "obscure", return_value="obscured-new-password"),
              patch.object(repair, "probe", side_effect=None if probe_result else ValueError("candidate_authentication_failed")),
              patch.object(repair, "write_private", side_effect=write_candidate),
              patch.object(repair.sys.stdin, "isatty", return_value=True),
              patch.object(repair.sys.stderr, "isatty", return_value=True),
              patch("builtins.input", side_effect=["new-account", "NEW-NAS"]),
              patch.object(repair.getpass, "getpass", side_effect=["password", "password"])):
            if fail_write:
                with self.assertRaises(OSError):
                    repair.run(True)
                report = None
            elif probe_result:
                report = repair.run(True)
            else:
                with self.assertRaisesRegex(ValueError, "candidate_authentication_failed"):
                    repair.run(True)
                report = None
        return config, original, report

    def test_failed_candidate_read_keeps_active_config(self):
        config, original, _ = self.run_repair(False)
        self.assertEqual(config.read_bytes(), original)
        self.assertFalse(config.with_name(config.name + ".before-smb-credentials").exists())
        self.assertEqual(list(config.parent.glob("*.smb-candidate-*")), [])

    def test_partial_candidate_write_is_removed(self):
        config, original, _ = self.run_repair(True, fail_write=True)
        self.assertEqual(config.read_bytes(), original)
        self.assertFalse(config.with_name(config.name + ".before-smb-credentials").exists())
        self.assertEqual(list(config.parent.glob("*.smb-candidate-*")), [])

    def test_success_preserves_private_rollback_and_updates_config(self):
        config, original, report = self.run_repair(True)
        self.assertEqual(report["status"], "PASS")
        backup = config.with_name(config.name + ".before-smb-credentials")
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertIn(b"user=new-account\n", config.read_bytes())
        self.assertIn(b"domain=NEW-NAS\n", config.read_bytes())
        self.assertIn(b"pass=obscured-new-password\n", config.read_bytes())
        self.assertNotIn(b"old-secret", config.read_bytes())


if __name__ == "__main__":
    unittest.main()
