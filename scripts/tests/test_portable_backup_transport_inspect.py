"""Read-only transport inspection emits fixed outcomes without private data."""

import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-backup-transport-inspect.py"
SPEC = importlib.util.spec_from_file_location("backup_transport_inspect", SCRIPT)
inspector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspector)


PROFILE = json.dumps({"remote_config": "/private/upload.conf", "prefix": "share/backups"}).encode()
CONFIG = b"[backup]\ntype=smb\nhost=private-nas\nport=445\nuser=backup\npass=not-shared\n"


class TransportInspectorTests(unittest.TestCase):
    def inputs(self, path, limit):
        return PROFILE if path == inspector.PROFILE else CONFIG

    def test_reachable_remote_read_is_sanitized(self):
        with (patch.object(inspector.os, "geteuid", return_value=0),
              patch.object(inspector, "private_bytes", side_effect=self.inputs),
              patch.object(inspector, "tailscale_state", return_value="PASS"),
              patch.object(inspector, "resolved_addresses", return_value=["100.1.2.3"]),
              patch.object(inspector.socket, "create_connection") as connect,
              patch.object(inspector.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run):
            report = inspector.inspect()
        self.assertEqual(report["inspection_status"], "PASS")
        self.assertEqual(report["checks"]["remote_read"], True)
        self.assertIsNone(report["failure_class"])
        self.assertNotIn("private-nas", json.dumps(report))
        self.assertNotIn("not-shared", json.dumps(report))
        self.assertEqual(run.call_args.args[0][:2], ["rclone", "lsd"])
        connect.assert_called_once_with(("100.1.2.3", 445), timeout=5)

    def test_dns_failure_skips_remote_read(self):
        with (patch.object(inspector.os, "geteuid", return_value=0),
              patch.object(inspector, "private_bytes", side_effect=self.inputs),
              patch.object(inspector, "tailscale_state", return_value="STOP"),
              patch.object(inspector, "resolved_addresses", return_value=[]),
              patch.object(inspector.subprocess, "run") as run):
            report = inspector.inspect()
        self.assertEqual(report["failure_class"], "name_resolution")
        self.assertFalse(report["checks"]["dns_resolved"])
        run.assert_not_called()

    def test_rehearsal_profile_must_match_expected_share_and_host_before_network(self):
        rehearsal = Path("/etc/rsl-backup/job.json")
        with (patch.object(inspector.os, "geteuid", return_value=0),
              patch.object(inspector, "private_bytes", side_effect=lambda path, limit: PROFILE if path == rehearsal else CONFIG),
              patch.object(inspector, "tailscale_state") as tailscale,
              patch.object(inspector, "resolved_addresses") as resolve,
              patch.object(inspector.subprocess, "run") as run):
            report = inspector.inspect(rehearsal, "rsl_rehearsal/siege-backups", "server-nas")
        self.assertEqual(report["inspection_status"], "STOP")
        self.assertEqual(report["failed_stage"], "expected_remote")
        self.assertFalse(report["checks"]["expected_prefix"])
        tailscale.assert_not_called()
        resolve.assert_not_called()
        run.assert_not_called()

    def test_remote_failure_class_discards_raw_stderr(self):
        error = b"private-nas: username=backup password=not-shared: logon failure"
        with (patch.object(inspector.os, "geteuid", return_value=0),
              patch.object(inspector, "private_bytes", side_effect=self.inputs),
              patch.object(inspector, "tailscale_state", return_value="PASS"),
              patch.object(inspector, "resolved_addresses", return_value=["100.1.2.3"]),
              patch.object(inspector.socket, "create_connection"),
              patch.object(inspector.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", error))):
            report = inspector.inspect()
        self.assertEqual(report["failure_class"], "authentication")
        self.assertFalse(report["checks"]["remote_read"])
        self.assertNotIn("private-nas", json.dumps(report))
        self.assertNotIn("not-shared", json.dumps(report))

    def test_unapproved_sftp_command_never_reaches_rclone(self):
        config = (b"[backup]\ntype=sftp\nhost=private-nas\nuser=backup\npass=hidden\n"
                  b"known_hosts_file=/private/known_hosts\nshell_type=none\ndisable_hashcheck=true\n"
                  b"server_command=touch /tmp/probe-marker\n")
        with (patch.object(inspector.os, "geteuid", return_value=0),
              patch.object(inspector, "private_bytes", side_effect=lambda path, limit: PROFILE if path == inspector.PROFILE else config),
              patch.object(inspector.subprocess, "run") as run):
            report = inspector.inspect()
        self.assertEqual(report["inspection_status"], "STOP")
        self.assertEqual(report["failed_stage"], "private_transport_config")
        run.assert_not_called()

    def test_sftp_without_private_host_trust_never_reaches_rclone(self):
        config = (b"[backup]\ntype=sftp\nhost=private-nas\nuser=backup\npass=hidden\n"
                  b"shell_type=none\ndisable_hashcheck=true\n")
        with (patch.object(inspector.os, "geteuid", return_value=0),
              patch.object(inspector, "private_bytes", side_effect=lambda path, limit: PROFILE if path == inspector.PROFILE else config),
              patch.object(inspector.subprocess, "run") as run):
            report = inspector.inspect()
        self.assertEqual(report["inspection_status"], "STOP")
        self.assertEqual(report["failed_stage"], "private_transport_config")
        run.assert_not_called()

    def test_approved_sftp_requires_private_trust_before_probe(self):
        config = (b"[backup]\ntype=sftp\nhost=private-nas\nuser=backup\npass=hidden\n"
                  b"known_hosts_file=/private/known_hosts\nshell_type=none\ndisable_hashcheck=true\n")
        read_paths = []
        def read(path, limit):
            read_paths.append(path)
            if path == inspector.PROFILE:
                return PROFILE
            if path == Path("/private/upload.conf"):
                return config
            return b"private-host-trust"
        with (patch.object(inspector.os, "geteuid", return_value=0),
              patch.object(inspector, "private_bytes", side_effect=read),
              patch.object(inspector, "tailscale_state", return_value="PASS"),
              patch.object(inspector, "resolved_addresses", return_value=["100.1.2.3"]),
              patch.object(inspector.socket, "create_connection"),
              patch.object(inspector.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b""))):
            report = inspector.inspect()
        self.assertEqual(report["inspection_status"], "PASS")
        self.assertEqual(report["checks"]["remote_read"], True)
        self.assertIn(Path("/private/known_hosts"), read_paths)


if __name__ == "__main__":
    unittest.main()
