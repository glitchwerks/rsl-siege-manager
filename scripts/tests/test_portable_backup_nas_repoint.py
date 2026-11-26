"""The NAS repair changes exactly one reviewed SMB host line."""

from contextlib import nullcontext, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-backup-nas-repoint.py"
SPEC = importlib.util.spec_from_file_location("backup_nas_repoint", SCRIPT)
repoint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repoint)


class NasRepointTests(unittest.TestCase):
    def test_only_host_changes_and_secret_bytes_are_preserved(self):
        original = (b"[backup]\r\ntype = smb\r\nhost = 100.126.80.92\r\n"
                    b"user = backup\r\npass = opaque-private-value\r\n")
        updated = repoint.replacement(original)
        self.assertEqual(updated, original.replace(b"host = 100.126.80.92", b"host = server-nas"))

    def test_unexpected_host_stops(self):
        with self.assertRaises(ValueError):
            repoint.replacement(b"[backup]\ntype=smb\nhost=another-host\npass=private\n")

    def test_existing_magicdns_host_is_reported_without_rewrite(self):
        with self.assertRaisesRegex(ValueError, "host_already_magicdns"):
            repoint.replacement(b"[backup]\ntype=smb\nhost=server-nas\npass=private\n")

    def test_other_backend_stops(self):
        with self.assertRaises(ValueError):
            repoint.replacement(b"[backup]\ntype=sftp\nhost=100.126.80.92\npass=private\n")

    def test_ambiguous_host_line_stops(self):
        with self.assertRaises(ValueError):
            repoint.replacement(b"[backup]\ntype=smb\nhost=100.126.80.92\nhost=100.126.80.92\n")

    def test_replacement_preserves_private_rollback_copy(self):
        original = b"[backup]\ntype=smb\nhost=100.126.80.92\npass=private\n"
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "remote.conf"
            config.write_bytes(original)
            config.chmod(0o600)
            repoint.preserve_and_replace(config, config.stat(), original, repoint.replacement(original))
            self.assertEqual(config.read_bytes(), repoint.replacement(original))
            self.assertEqual(config.with_name("remote.conf.before-nas-repoint").read_bytes(), original)
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)

    def test_existing_rollback_copy_prevents_second_change(self):
        original = b"[backup]\ntype=smb\nhost=100.126.80.92\npass=private\n"
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "remote.conf"
            config.write_bytes(original)
            config.chmod(0o600)
            config.with_name("remote.conf.before-nas-repoint").write_bytes(b"preserve")
            with self.assertRaises(FileExistsError):
                repoint.preserve_and_replace(config, config.stat(), original, repoint.replacement(original))
            self.assertEqual(config.read_bytes(), original)

    def test_service_must_be_loaded_and_idle(self):
        with patch.object(repoint.subprocess, "run", return_value=subprocess.CompletedProcess([], 0,
                "LoadState=loaded\nActiveState=failed\n", "")):
            self.assertTrue(repoint.backup_service_idle())
        with patch.object(repoint.subprocess, "run", return_value=subprocess.CompletedProcess([], 0,
                "LoadState=not-found\nActiveState=inactive\n", "")):
            self.assertFalse(repoint.backup_service_idle())
        with patch.object(repoint.subprocess, "run", return_value=subprocess.CompletedProcess([], 0,
                "LoadState=loaded\nActiveState=activating\n", "")):
            self.assertFalse(repoint.backup_service_idle())

    def test_magicdns_name_must_resolve_only_to_rebuilt_nas_and_accept_smb(self):
        with (patch.object(repoint.subprocess, "run", return_value=subprocess.CompletedProcess([], 0,
                "100.117.158.121 STREAM server-nas\n", "")),
              patch.object(repoint.socket, "create_connection") as connect):
            self.assertEqual(repoint.new_nas_name_status(), "ready")
            connect.assert_called_once_with(("server-nas", 445), timeout=5)
        with (patch.object(repoint.subprocess, "run", return_value=subprocess.CompletedProcess([], 0,
                "100.126.80.92 STREAM server-nas\n", "")),
              patch.object(repoint.socket, "create_connection") as connect):
            self.assertEqual(repoint.new_nas_name_status(), "magicdns_address_mismatch")
            connect.assert_not_called()

    def test_repair_holds_same_exclusive_job_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            lock = root / "job.lock"
            lock.write_bytes(b"")
            lock.chmod(0o600)
            with repoint.backup_job_lock(root, owner=os.getuid()):
                with self.assertRaises(BlockingIOError):
                    with repoint.backup_job_lock(root, owner=os.getuid()):
                        pass
            with repoint.backup_job_lock(root, owner=os.getuid()):
                pass

    def test_inspect_mode_runs_all_guards_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "job.json"
            config = Path(directory) / "remote.conf"
            profile.write_text(json.dumps({"root": "/private/backup", "remote_config": str(config)}))
            config.write_bytes(b"[backup]\ntype=smb\nhost=100.126.80.92\npass=private\n")
            info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0)
            output = io.StringIO()
            with (patch.object(repoint, "PROFILE", profile),
                  patch.object(repoint.os, "geteuid", return_value=0),
                  patch.object(repoint.Path, "lstat", return_value=info),
                  patch.object(repoint, "backup_job_lock", return_value=nullcontext()),
                  patch.object(repoint, "backup_service_idle", return_value=True),
                  patch.object(repoint, "new_nas_name_status", return_value="ready"),
                  patch.object(repoint, "preserve_and_replace") as write,
                  patch.object(repoint.sys, "argv", ["script", "--inspect"]),
                  redirect_stdout(output)):
                self.assertEqual(repoint.main(), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "PASS")
            write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
