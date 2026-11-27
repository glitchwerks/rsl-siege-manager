"""The rehearsal inventory binds the VM to a trusted host key without mutation."""

import base64
import hashlib
import importlib.util
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-rehearsal-host-inspect.py"
SPEC = importlib.util.spec_from_file_location("rehearsal_host_inspect", SCRIPT)
inspect = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspect)


class RehearsalHostInspectTests(unittest.TestCase):
    def test_service_and_timer_inventory_uses_unit_names_only(self):
        result = subprocess.CompletedProcess([], 0, b"ssh.service loaded active running OpenSSH\n", b"")
        with patch.object(inspect, "command", return_value=result):
            self.assertEqual(inspect.unit_names("service", "running"), ["ssh.service"])
        result = subprocess.CompletedProcess([], 0, b"unexpected private data\n", b"")
        with patch.object(inspect, "command", return_value=result):
            self.assertIsNone(inspect.unit_names("timer", "active"))

    def test_public_host_key_fingerprint_uses_exact_key_blob(self):
        blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(range(32))
        encoded = base64.b64encode(blob).decode()
        expected = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ssh_host_ed25519_key.pub"
            path.write_text(f"ssh-ed25519 {encoded} test-host\n")
            with patch.object(inspect, "HOST_KEY", path):
                self.assertEqual(inspect.host_fingerprint(), expected)
            path.write_text("ssh-ed25519 invalid!\n")
            with patch.object(inspect, "HOST_KEY", path):
                with self.assertRaises(ValueError):
                    inspect.host_fingerprint()

    def test_identity_and_route_checks_must_all_pass(self):
        with (patch.object(inspect.os, "geteuid", return_value=0),
              patch.object(inspect, "os_release", return_value={"ID": "debian", "VERSION_CODENAME": "trixie"}),
              patch.object(inspect.platform, "machine", return_value="x86_64"),
              patch.object(inspect, "service_state", return_value=True),
              patch.object(inspect, "host_fingerprint", return_value="SHA256:" + "A" * 43),
              patch.object(inspect, "tailscale_state", return_value=True),
              patch.object(inspect, "default_route_safe", return_value=True),
              patch.object(inspect, "listening_ports", return_value=[22]),
              patch.object(inspect, "container_count", return_value=(0, False)),
              patch.object(inspect, "unit_names", side_effect=[["ssh.service", "tailscaled.service"], []])):
            report = inspect.inspect("100.93.16.52", "SHA256:" + "A" * 43)
            self.assertEqual(report["inspection_status"], "PASS")
            self.assertEqual(report["existing_containers"], 0)

    def test_mismatched_host_key_stops(self):
        with (patch.object(inspect.os, "geteuid", return_value=0),
              patch.object(inspect, "os_release", return_value={"ID": "debian", "VERSION_CODENAME": "trixie"}),
              patch.object(inspect.platform, "machine", return_value="x86_64"),
              patch.object(inspect, "service_state", return_value=True),
              patch.object(inspect, "host_fingerprint", return_value="SHA256:" + "B" * 43),
              patch.object(inspect, "tailscale_state", return_value=True),
              patch.object(inspect, "default_route_safe", return_value=True),
              patch.object(inspect, "listening_ports", return_value=[22]),
              patch.object(inspect, "container_count", return_value=(0, False)),
              patch.object(inspect, "unit_names", side_effect=[["ssh.service"], []])):
            report = inspect.inspect("100.93.16.52", "SHA256:" + "A" * 43)
            self.assertEqual(report["inspection_status"], "STOP")
            self.assertFalse(report["checks"]["host_fingerprint_matches"])

    def test_active_ssh_without_port_22_listener_stops(self):
        with (patch.object(inspect.os, "geteuid", return_value=0),
              patch.object(inspect, "os_release", return_value={"ID": "debian", "VERSION_CODENAME": "trixie"}),
              patch.object(inspect.platform, "machine", return_value="x86_64"),
              patch.object(inspect, "service_state", return_value=True),
              patch.object(inspect, "host_fingerprint", return_value="SHA256:" + "A" * 43),
              patch.object(inspect, "tailscale_state", return_value=True),
              patch.object(inspect, "default_route_safe", return_value=True),
              patch.object(inspect, "listening_ports", return_value=[53]),
              patch.object(inspect, "container_count", return_value=(0, False)),
              patch.object(inspect, "unit_names", side_effect=[["ssh.service"], []])):
            report = inspect.inspect("100.93.16.52", "SHA256:" + "A" * 43)
            self.assertEqual(report["inspection_status"], "STOP")
            self.assertFalse(report["checks"]["ssh_port_22_listening"])


if __name__ == "__main__":
    unittest.main()
