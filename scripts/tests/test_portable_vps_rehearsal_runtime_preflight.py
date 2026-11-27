"""The runtime preflight stops on conflicting packages and preexisting state."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-rehearsal-runtime-preflight.py"
SPEC = importlib.util.spec_from_file_location("rehearsal_runtime_preflight", SCRIPT)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)

BASELINE = {
    "inspection_status": "PASS",
    "existing_containers": 0,
    "docker_installed": False,
    "network_controls": {"nft_available": True, "nft_mentions_tailscale": True},
}


class RehearsalRuntimePreflightTests(unittest.TestCase):
    def test_clean_host_passes_without_raw_host_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {"docker_state": Path(directory) / "docker"}
            with (patch.object(preflight, "RUNTIME_PATHS", paths),
                  patch.object(preflight, "host_inspection", return_value=BASELINE),
                  patch.object(preflight, "installed_packages", return_value={"docker.io": False}),
                  patch.object(preflight.shutil, "which", return_value="/usr/bin/tool")):
                report = preflight.inspect("100.93.16.52", "SHA256:" + "A" * 43)
        self.assertEqual(report["status"], "PASS")
        self.assertNotIn("100.93.16.52", str(report))

    def test_existing_runtime_or_conflicting_package_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "docker"
            existing.mkdir()
            with (patch.object(preflight, "RUNTIME_PATHS", {"docker_state": existing}),
                  patch.object(preflight, "host_inspection", return_value=BASELINE),
                  patch.object(preflight, "installed_packages", return_value={"docker.io": True}),
                  patch.object(preflight.shutil, "which", return_value="/usr/bin/tool")):
                report = preflight.inspect("100.93.16.52", "SHA256:" + "A" * 43)
        self.assertEqual(report["status"], "STOP")
        self.assertFalse(report["checks"]["no_existing_runtime_packages"])
        self.assertFalse(report["checks"]["runtime_paths_unused"])

    def test_package_inventory_uses_fixed_names(self):
        response = subprocess.CompletedProcess([], 0, b"install ok installed", b"")
        with patch.object(preflight.subprocess, "run", return_value=response) as run:
            packages = preflight.installed_packages()
        self.assertEqual(set(packages), set(preflight.CONFLICTS + preflight.OFFICIAL_PACKAGES))
        self.assertTrue(all(packages.values()))
        self.assertEqual(run.call_count, len(preflight.CONFLICTS + preflight.OFFICIAL_PACKAGES))


if __name__ == "__main__":
    unittest.main()
