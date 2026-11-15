"""Disposable paired HTTP/Compose rehearsal; never targets the production VPS."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-bearer-rotation.py"
SPEC = importlib.util.spec_from_file_location("bearer_rotation_docker", SCRIPT)
ROTATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ROTATE)

SERVER = '''import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/health":
            code = 200
        elif os.environ["ROLE"] == "mom" and self.path == "/api/members":
            code = 200 if self.headers.get("Authorization") == "Bearer " + os.environ["MOM_BOT_SECRET_DISCORD_BOT_API_KEY"] else 401
        elif os.environ["ROLE"] == "backend" and self.path == "/api/members/me/preferences":
            code = 400 if self.headers.get("Authorization") == "Bearer " + os.environ["BOT_SERVICE_TOKEN"] and self.headers.get("X-Acting-Discord-Id") == "invalid" else 401
        else:
            code = 404
        self.send_response(code)
        self.end_headers()
    def log_message(self, *args):
        pass
ThreadingHTTPServer(("0.0.0.0", 8001 if os.environ["ROLE"] == "mom" else 8000), Handler).serve_forever()
'''


@unittest.skipUnless(os.environ.get("VPS_WRITER_DOCKER_TEST") == "1", "disposable CI daemon only")
class DisposableBearerRotationTests(unittest.TestCase):
    def test_two_pairs_and_routine_rollback(self):
        self.assertEqual(os.geteuid(), 0)
        suffix = uuid.uuid4().hex[:16]
        tag = "rsl-bearer-test:" + suffix
        name = "bearer-ci-" + suffix
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            runtime = project / "runtime"
            runtime.mkdir(mode=0o700)
            evidence = project / "evidence"
            evidence.mkdir(mode=0o700)
            (project / "server.py").write_text(SERVER)
            (project / "Dockerfile").write_text(
                "FROM python:3.12-alpine\n"
                "RUN mkdir -p /app/.venv/bin && ln -s /usr/local/bin/python /app/.venv/bin/python\n"
                "COPY server.py /app/server.py\n")
            (project / "stack.env").write_text(f"COMPOSE_PROJECT_NAME={name}\n")
            (project / "stack.env").chmod(0o600)
            (runtime / "backend.env").write_text(
                "DISCORD_BOT_API_KEY=synthetic-sidecar-old\n"
                "BOT_SERVICE_TOKEN=synthetic-reverse-old\nUNRELATED=preserve\n")
            (runtime / "mom.env").write_text(
                "MOM_BOT_SECRET_DISCORD_BOT_API_KEY=synthetic-sidecar-old\n"
                "MOM_BOT_SECRET_SIEGE_WEB_BOT_TOKEN=synthetic-reverse-old\n"
                "OTHER=preserve\n")
            for item in runtime.iterdir():
                item.chmod(0o600)
            health = ("    healthcheck:\n"
                      "      test: [CMD, python, -c, \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)\"]\n"
                      "      interval: 1s\n      retries: 5\n")
            mom_health = health.replace("8000", "8001")
            simple_health = ("    healthcheck:\n      test: [CMD, 'true']\n"
                             "      interval: 1s\n      retries: 5\n")
            (project / "compose.yml").write_text(
                "services:\n"
                f"  postgres:\n    image: {tag}\n    command: [sleep, '300']\n" + simple_health +
                f"  proxy:\n    image: {tag}\n    command: [sleep, '300']\n" + simple_health +
                f"  frontend:\n    image: {tag}\n    command: [sleep, '300']\n" + simple_health +
                f"  backend:\n    image: {tag}\n    command: [python, /app/server.py]\n"
                "    environment: {ROLE: backend}\n    env_file: ./runtime/backend.env\n" + health)
            (project / "compose.mom.yml").write_text(
                f"services:\n  mom:\n    image: {tag}\n    command: [python, /app/server.py]\n"
                "    environment: {ROLE: mom}\n    env_file: ./runtime/mom.env\n" + mom_health)
            compose = ["docker", "compose", "--env-file", str(project / "stack.env"),
                       "--project-directory", str(project), "-f", str(project / "compose.yml"),
                       "-f", str(project / "compose.mom.yml")]

            def snapshot(target, owner):
                files = ROTATE.read_runtime(target, owner)
                self.assertTrue(ROTATE.pairs_match(files))
                installed = {}
                for service in ("backend", "mom", "postgres", "proxy", "frontend"):
                    identifier = ROTATE.PAUSE.identifiers(name, service)
                    state = ROTATE.PAUSE.container(name, service)
                    self.assertIsNotNone(identifier)
                    self.assertTrue(state["running"] and state["healthy"])
                    image = ROTATE.PAUSE.command(
                        ["docker", "inspect", "--format", "{{json .Config.Image}}", identifier])
                    installed[service] = {"id": identifier, "image": json.loads(image)}
                return files, {"project": name, "installed": installed}

            try:
                subprocess.run(["docker", "build", "--tag", tag, str(project)],
                               check=True, capture_output=True, timeout=180)
                subprocess.run(compose + ["up", "-d", "--wait", "--wait-timeout", "60"],
                               check=True, capture_output=True, timeout=90)
                with patch.object(ROTATE, "project_snapshot", side_effect=snapshot):
                    for pair in ("sidecar", "reverse-api"):
                        before, baseline = snapshot(project, 0)
                        stage = ROTATE.stage(project, pair, evidence)
                        self.assertEqual(ROTATE.read_runtime(project, 0), before)
                        activated = ROTATE.activate(project, evidence / stage["run_id"])
                        self.assertEqual(activated["status"], "PASS", activated["checks"])
                        after, installed = snapshot(project, 0)
                        self.assertNotEqual(ROTATE.pair_values(after)[pair],
                                            ROTATE.pair_values(before)[pair])
                        for service in ("frontend", "postgres", "proxy"):
                            self.assertEqual(installed["installed"][service], baseline["installed"][service])

                    old, _ = snapshot(project, 0)
                    stage = ROTATE.stage(project, "sidecar", evidence)
                    with patch.object(ROTATE, "verified", return_value=False):
                        failed = ROTATE.activate(project, evidence / stage["run_id"])
                    self.assertEqual(failed["status"], "STOP")
                    self.assertTrue(ROTATE.isolated(name))
                    with patch.object(ROTATE, "configuration_current", return_value=True):
                        recovered = ROTATE.recover_routine(
                            project, evidence / stage["run_id"], old_bearer_safe=True)
                    self.assertEqual(recovered["status"], "PASS")
                    self.assertEqual(ROTATE.read_runtime(project, 0), old)

                    stage = ROTATE.stage(project, "reverse-api", evidence)
                    with patch.object(ROTATE, "verified", return_value=False):
                        failed = ROTATE.activate(project, evidence / stage["run_id"])
                    self.assertEqual(failed["status"], "STOP")
                    self.assertTrue(ROTATE.isolated(name))
                    with patch.object(ROTATE, "configuration_current", return_value=True):
                        fresh_stage = ROTATE.stage_fresh(project, evidence / stage["run_id"])
                        self.assertEqual(fresh_stage["status"], "PASS")
                        fresh = ROTATE.activate_fresh(project, evidence / stage["run_id"])
                    self.assertEqual(fresh["status"], "PASS", fresh["checks"])
                    self.assertTrue(ROTATE.pairs_match(ROTATE.read_runtime(project, 0)))
            finally:
                subprocess.run(compose + ["down", "--remove-orphans"],
                               capture_output=True, timeout=60, check=False)
                subprocess.run(["docker", "image", "rm", "--force", tag],
                               capture_output=True, timeout=30, check=False)


if __name__ == "__main__":
    unittest.main()
