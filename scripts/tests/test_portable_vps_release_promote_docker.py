"""Disposable rehearsal of targeted Compose activation after writer pause."""

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-release-promote.py"
SPEC = importlib.util.spec_from_file_location("release_promote_docker", SCRIPT)
PROMOTE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROMOTE)


@unittest.skipUnless(os.environ.get("VPS_WRITER_DOCKER_TEST") == "1", "disposable CI daemon only")
class DisposableComposePromotionTests(unittest.TestCase):
    def test_only_application_services_change_and_paused_writers_restart(self):
        self.assertEqual(os.geteuid(), 0)
        suffix = uuid.uuid4().hex[:16]
        old_tag = "rsl-promotion-old:" + suffix
        new_tag = "rsl-promotion-new:" + suffix
        project_name = "promotion-ci-" + suffix
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "Dockerfile").write_text("FROM alpine:3.21\nCMD [\"sleep\", \"300\"]\n")
            (project / "stack.env").write_text(f"COMPOSE_PROJECT_NAME={project_name}\n"
                f"BACKEND_IMAGE={old_tag}\nFRONTEND_IMAGE={old_tag}\nMOM_IMAGE={old_tag}\n")
            service = ("    command: [sleep, '300']\n"
                       "    healthcheck:\n      test: [CMD, 'true']\n"
                       "      interval: 1s\n      retries: 5\n")
            (project / "compose.yml").write_text("services:\n"
                f"  postgres:\n    image: {old_tag}\n{service}"
                f"  proxy:\n    image: {old_tag}\n{service}"
                f"  backend:\n    image: ${{BACKEND_IMAGE}}\n    depends_on: [postgres]\n{service}"
                f"  frontend:\n    image: ${{FRONTEND_IMAGE}}\n{service}")
            (project / "compose.mom.yml").write_text("services:\n"
                f"  mom:\n    image: ${{MOM_IMAGE}}\n    depends_on: [postgres]\n{service}")
            common = ["docker", "compose", "--env-file", str(project / "stack.env"),
                      "--project-directory", str(project), "-f", str(project / "compose.yml"),
                      "-f", str(project / "compose.mom.yml")]
            try:
                subprocess.run(["docker", "build", "--tag", old_tag, str(project)], check=True,
                               capture_output=True, timeout=120)
                subprocess.run(["docker", "tag", old_tag, new_tag], check=True,
                               capture_output=True, timeout=30)
                subprocess.run(common + ["up", "-d", "--wait", "--wait-timeout", "60"],
                               check=True, capture_output=True, timeout=90)
                ids_before = {name: PROMOTE.PAUSE.identifiers(project_name, name)
                              for name in ("postgres", "proxy", "backend", "mom", "frontend")}
                self.assertTrue(all(ids_before.values()))
                for name in ("backend", "mom"):
                    subprocess.run(["docker", "stop", ids_before[name]], check=True,
                                   capture_output=True, timeout=30)
                (project / "stack.env").write_text(f"COMPOSE_PROJECT_NAME={project_name}\n"
                    f"BACKEND_IMAGE={new_tag}\nFRONTEND_IMAGE={new_tag}\nMOM_IMAGE={new_tag}\n")
                self.assertTrue(PROMOTE.compose_up(project))
                ids_after = {name: PROMOTE.PAUSE.identifiers(project_name, name)
                             for name in ids_before}
                self.assertEqual(ids_after["postgres"], ids_before["postgres"])
                self.assertEqual(ids_after["proxy"], ids_before["proxy"])
                for name in ("backend", "mom", "frontend"):
                    self.assertNotEqual(ids_after[name], ids_before[name])
                    self.assertIsNotNone(PROMOTE.live_service(project_name, name, new_tag))
            finally:
                subprocess.run(common + ["down", "--remove-orphans"],
                               capture_output=True, timeout=60, check=False)
                for tag in (old_tag, new_tag):
                    subprocess.run(["docker", "image", "rm", "--force", tag],
                                   capture_output=True, timeout=30, check=False)


if __name__ == "__main__":
    unittest.main()
