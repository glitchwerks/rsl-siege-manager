"""Disposable Compose rehearsal of the exact one-off migration command."""

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-release-migrate.py"
SPEC = importlib.util.spec_from_file_location("release_migrate_docker", SCRIPT)
MIGRATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATE)


@unittest.skipUnless(os.environ.get("VPS_WRITER_DOCKER_TEST") == "1", "disposable CI daemon only")
class DisposableComposeMigrationTests(unittest.TestCase):
    def test_exact_one_off_command_does_not_start_dependency(self):
        self.assertEqual(os.geteuid(), 0)
        tag = "rsl-migration-ci:" + uuid.uuid4().hex[:16]
        project_name = "migration-ci-" + uuid.uuid4().hex[:16]
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "state").mkdir()
            script = project / "alembic"
            script.write_text("#!/bin/sh\n"
                "case \"$1\" in\n"
                "  heads) printf '0013 (head)\\n' ;;\n"
                "  upgrade) [ \"$2\" = 0013 ] || exit 7; printf '%s\\n' \"$2\" > /state/revision ;;\n"
                "  *) exit 8 ;;\n"
                "esac\n")
            script.chmod(0o755)
            (project / "Dockerfile").write_text("FROM alpine:3.21\n"
                "COPY alembic /app/.venv/bin/alembic\n"
                "ENTRYPOINT [\"/bin/false\"]\n")
            (project / "stack.env").write_text(f"COMPOSE_PROJECT_NAME={project_name}\n")
            (project / "compose.yml").write_text("services:\n"
                "  postgres:\n    image: alpine:3.21\n    network_mode: none\n"
                "    command: [sleep, '300']\n"
                "  migrate-siege:\n    image: ${BACKEND_IMAGE}\n"
                "    network_mode: none\n    depends_on: [postgres]\n"
                "    volumes: [./state:/state]\n")
            (project / "compose.mom.yml").write_text("services: {}\n")
            try:
                subprocess.run(["docker", "build", "--tag", tag, str(project)], check=True,
                               capture_output=True, timeout=120)
                overrides = {"BACKEND_IMAGE": tag, "FRONTEND_IMAGE": tag, "MOM_IMAGE": tag}
                self.assertEqual(MIGRATE.compose_run(project, "migrate-siege", "heads", None, overrides),
                                 "0013 (head)\n")
                self.assertEqual(MIGRATE.compose_run(project, "migrate-siege", "upgrade", "0013", overrides), "")
                self.assertEqual((project / "state" / "revision").read_text(), "0013\n")
                containers = subprocess.run(["docker", "ps", "--all", "--no-trunc",
                    "--filter", f"label=com.docker.compose.project={project_name}", "--format", "{{.ID}}"],
                    check=True, capture_output=True, text=True, timeout=20)
                self.assertEqual(containers.stdout.strip(), "")
            finally:
                remaining = subprocess.run(["docker", "ps", "--all", "--no-trunc",
                    "--filter", f"label=com.docker.compose.project={project_name}", "--format", "{{.ID}}"],
                    capture_output=True, text=True, timeout=20, check=False)
                if remaining.returncode == 0:
                    for container in remaining.stdout.splitlines():
                        if MIGRATE.PAUSE.CONTAINER_ID.fullmatch(container):
                            subprocess.run(["docker", "rm", "--force", container],
                                           capture_output=True, timeout=30, check=False)
                subprocess.run(["docker", "image", "rm", "--force", tag],
                               capture_output=True, timeout=30, check=False)


if __name__ == "__main__":
    unittest.main()
