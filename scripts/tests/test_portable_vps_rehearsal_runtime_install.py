"""Safety gates for the isolated VM's phase-scoped Docker setup."""

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-rehearsal-runtime-install.py"
SPEC = importlib.util.spec_from_file_location("rehearsal_runtime_install", SCRIPT)
install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install)


class RehearsalRuntimeInstallTests(unittest.TestCase):
    def test_apt_plan_rejects_removal_and_existing_package_upgrade(self):
        lines = [f"Inst {name} (1.0 Docker:stable [amd64])" for name in install.PACKAGES]
        lines += ["Remv openssh-server [1.0]", "Inst curl [1.0] (2.0 Debian:stable [amd64])"]
        result = subprocess.CompletedProcess([], 0, "\n".join(lines).encode(), b"")
        with patch.object(install, "command", return_value=result):
            plan = install.apt_plan()
        self.assertTrue(plan["packages_planned"])
        self.assertFalse(plan["no_removals"])
        self.assertFalse(plan["no_upgrades"])

    def test_repo_phase_stops_before_download_when_preflight_fails(self):
        class Preflight:
            @staticmethod
            def inspect(ip, fingerprint):
                return {"status": "STOP"}
        with (patch.object(install, "module", return_value=Preflight),
              patch.object(install, "safe_directory", return_value=True),
              patch.object(install, "success") as run,
              patch.object(install, "write_new") as write):
            checks = install.prepare_repo("100.93.16.52", "SHA256:" + "A" * 43)
        self.assertFalse(checks["preflight_pass"])
        run.assert_not_called()
        write.assert_not_called()

    def test_path_phase_refuses_preexisting_directory(self):
        state = {"inspection_status": "PASS", "docker_installed": True,
                 "existing_containers": 0}
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "deployment"
            existing.mkdir()
            with (patch.object(install, "PRIVATE_PATHS", (existing,)),
                  patch.object(install, "baseline", return_value=state),
                  patch.object(install, "safe_directory", return_value=True),
                  patch.object(install, "success", return_value=True)):
                checks = install.prepare_paths("100.93.16.52", "SHA256:" + "A" * 43)
        self.assertFalse(checks["paths_unused"])
        self.assertNotIn("path_creation", checks)

    def test_report_path_must_be_root_private(self):
        self.assertIsNone(install.reserve_report(Path("/tmp/report.json")))

    def test_report_reservation_uses_exclusive_flags(self):
        with patch.object(install, "safe_directory", return_value=True), \
             patch.object(install.os, "open", return_value=7) as opened:
            self.assertEqual(install.reserve_report(Path("/root/new-report.json")), 7)
        self.assertEqual(opened.call_args.args[1] & (os.O_EXCL | os.O_NOFOLLOW),
                         os.O_EXCL | os.O_NOFOLLOW)
        self.assertEqual(opened.call_args.args[2], 0o600)


if __name__ == "__main__":
    unittest.main()
