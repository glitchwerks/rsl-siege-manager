"""Disposable Compose rehearsal for the day-role configuration-only phase."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid


SCRIPTS = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("day_role_config_docker", SCRIPTS / "portable-vps-day-role-config.py")
CONFIG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIG)
SPEC = importlib.util.spec_from_file_location("bearer_fixture", Path(__file__).with_name("test_portable_vps_bearer_rotation_docker.py"))
BEARER_FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BEARER_FIXTURE)


@unittest.skipUnless(os.environ.get("VPS_WRITER_DOCKER_TEST") == "1", "disposable CI daemon only")
class DisposableDayRoleConfigTests(unittest.TestCase):
    def test_targeted_enable_and_failed_disable_recovery(self):
        self.assertEqual(os.geteuid(), 0)
        suffix = uuid.uuid4().hex[:16]
        tag = "rsl-day-role-test:" + suffix
        name = "day-role-ci-" + suffix
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            runtime = project / "runtime"
            runtime.mkdir(mode=0o700)
            control = project / "control"
            control.mkdir(mode=0o755)
            (control / "state").write_text("active\n")
            (control / "state").chmod(0o644)
            (control / "history.json").write_text(
                '{"schema":1,"pause_started_at":null,"last_resumed_at":null}\n')
            (control / "history.json").chmod(0o644)
            evidence = project / "evidence"
            evidence.mkdir(mode=0o700)
            (project / "server.py").write_text(BEARER_FIXTURE.SERVER)
            (project / "Dockerfile").write_text(
                "FROM python:3.12-alpine\n"
                "RUN mkdir -p /app/.venv/bin && ln -s /usr/local/bin/python /app/.venv/bin/python\n"
                "COPY server.py /app/server.py\n")
            (project / "stack.env").write_text(f"COMPOSE_PROJECT_NAME={name}\n")
            (project / "stack.env").chmod(0o600)
            (runtime / "backend.env").write_text(
                "DISCORD_BOT_API_KEY=synthetic-sidecar\n"
                "BOT_SERVICE_TOKEN=synthetic-reverse\n"
                "DAY_ROLE_SYNC_ENABLED=false\nUNRELATED=preserve\n")
            (runtime / "mom.env").write_text(
                "MOM_BOT_SECRET_DISCORD_BOT_API_KEY=synthetic-sidecar\n"
                "MOM_BOT_SECRET_SIEGE_WEB_BOT_TOKEN=synthetic-reverse\n")
            for file in ("database.env", "postgres-admin", "mom-image.json"):
                (runtime / file).write_text("synthetic\n")
            for item in runtime.iterdir():
                item.chmod(0o600)
            for file in ("Caddyfile", "init-databases.sql"):
                (project / file).write_text("synthetic\n")
            health = ("    healthcheck:\n"
                      "      test: [CMD, python, -c, \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)\"]\n"
                      "      interval: 1s\n      retries: 5\n")
            mom_health = health.replace("8000", "8001")
            simple_health = ("    healthcheck:\n      test: [CMD, 'true']\n"
                             "      interval: 1s\n      retries: 5\n")
            (project / "compose.yml").write_text(
                "services:\n"
                f"  postgres:\n    image: {tag}\n    command: [sleep, '900']\n" + simple_health +
                f"  proxy:\n    image: {tag}\n    command: [sleep, '900']\n" + simple_health +
                f"  frontend:\n    image: {tag}\n    command: [sleep, '900']\n" + simple_health +
                f"  backend:\n    image: {tag}\n    command: [python, /app/server.py]\n"
                "    environment: {ROLE: backend, ENVIRONMENT: production}\n"
                "    env_file: ./runtime/backend.env\n"
                "    volumes: [./control:/run/rsl-maintenance:ro]\n" + health +
                f"  migrate-siege:\n    image: {tag}\n    command: [sleep, '900']\n"
                "    profiles: [maintenance]\n    env_file: ./runtime/backend.env\n")
            (project / "compose.mom.yml").write_text(
                f"services:\n  mom:\n    image: {tag}\n    command: [python, /app/server.py]\n"
                "    environment: {ROLE: mom}\n    env_file: ./runtime/mom.env\n"
                "    volumes: [./control:/run/rsl-maintenance:ro]\n" + mom_health)
            compose = ["docker", "compose", "--env-file", str(project / "stack.env"),
                       "--project-directory", str(project), "-f", str(project / "compose.yml"),
                       "-f", str(project / "compose.mom.yml")]

            def rendered(*_args, **_kwargs):
                result = subprocess.run(compose + ["--profile", "maintenance", "config", "--format", "json"],
                                        check=True, capture_output=True, timeout=30)
                return json.loads(result.stdout)

            def model(target):
                return rendered(), {}

            def snapshot(target, owner):
                files = CONFIG.ROTATION.read_runtime(target, owner)
                self.assertTrue(CONFIG.ROTATION.pairs_match(files))
                installed = {}
                for service in ("backend", "mom", "postgres", "proxy", "frontend"):
                    identifier = CONFIG.PAUSE.identifiers(name, service)
                    state = CONFIG.PAUSE.container(name, service)
                    self.assertIsNotNone(identifier)
                    self.assertTrue(state["running"] and state["healthy"])
                    image = CONFIG.PAUSE.command(
                        ["docker", "inspect", "--format", "{{json .Config.Image}}", identifier])
                    installed[service] = {"id": identifier, "image": json.loads(image)}
                return files, {"project": name, "installed": installed, "source_sha": "a" * 40}

            try:
                subprocess.run(["docker", "build", "--tag", tag, str(project)],
                               check=True, capture_output=True, timeout=180)
                subprocess.run(compose + ["up", "-d", "--wait", "--wait-timeout", "60"],
                               check=True, capture_output=True, timeout=90)
                boundary = {"schema": 1, "pause_started_at": "2026-11-15T00:00:00+00:00",
                            "last_resumed_at": None}
                self.assertTrue(CONFIG.ROTATION.MAINT.replace_history(project, boundary))
                self.assertTrue(CONFIG.ROTATION.MAINT.replace_state(project, "paused"))
                self.assertTrue(CONFIG.ROTATION.MAINT.drain(project, attempts=3, interval=0))
                decision = evidence / "decision.json"
                decision.write_text(json.dumps({"schema": 1, "enabled": True,
                    "day_1_role_id": "12345678901234567", "day_2_role_id": "12345678901234568",
                    "receiver_url": "https://siege.example.net/api/internal/role-sync"}))
                decision.chmod(0o600)
                with (patch.object(CONFIG, "model", side_effect=model),
                      patch.object(CONFIG.PREFLIGHT, "render", side_effect=rendered),
                      patch.object(CONFIG.PREFLIGHT, "validate", return_value={"valid": True}),
                      patch.object(CONFIG.ROTATION, "project_snapshot", side_effect=snapshot),
                      patch.object(CONFIG.ROTATION, "public_healthy", return_value=True),
                      patch.object(CONFIG, "proof_current", return_value=True),
                      patch.object(CONFIG, "preflight_current", return_value=True)):
                    before, baseline = snapshot(project, 0)
                    first = CONFIG.stage(project, decision, evidence)
                    self.assertEqual(CONFIG.ROTATION.read_runtime(project, 0), before)
                    activated = CONFIG.activate(project, first, evidence / "public.json",
                                                evidence / "backup-profile.json")
                    self.assertEqual(activated["status"], "PASS", activated["checks"])
                    _, after = snapshot(project, 0)
                    self.assertNotEqual(after["installed"]["backend"]["id"], baseline["installed"]["backend"]["id"])
                    for service in ("mom", "postgres", "proxy", "frontend"):
                        self.assertEqual(after["installed"][service], baseline["installed"][service])
                    self.assertTrue(all(CONFIG.EFFECTIVE.inspect(rendered(), "true").values()))

                    decision.write_text('{"schema":1,"enabled":false}')
                    second = CONFIG.stage(project, decision, evidence)
                    with patch.object(CONFIG, "verify", return_value=False):
                        failed = CONFIG.activate(project, second, evidence / "public.json",
                                                 evidence / "backup-profile.json")
                    self.assertEqual(failed["status"], "STOP")
                    self.assertFalse(CONFIG.PAUSE.container(name, "backend")["running"])
                    with patch.object(CONFIG.ROTATION, "configuration_current", return_value=True):
                        recovered = CONFIG.recover(project, second, evidence / "public.json",
                                                   restore_enabled_safe=True)
                    self.assertEqual(recovered["status"], "PASS", recovered["checks"])
                    self.assertTrue(all(CONFIG.EFFECTIVE.inspect(rendered(), "true").values()))
            finally:
                subprocess.run(compose + ["down", "--remove-orphans"],
                               capture_output=True, timeout=60, check=False)
                subprocess.run(["docker", "image", "rm", "--force", tag],
                               capture_output=True, timeout=30, check=False)


if __name__ == "__main__":
    unittest.main()
