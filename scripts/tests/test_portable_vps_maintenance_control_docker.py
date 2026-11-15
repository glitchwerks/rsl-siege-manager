"""Disposable two-peer maintenance rehearsal; never targets a live host."""

from argparse import Namespace
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid

SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-maintenance-control.py"
SPEC = importlib.util.spec_from_file_location("maintenance_control_docker", SCRIPT)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)

SERVER = """import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/maintenance/status":
            self.send_response(404); self.end_headers(); return
        expected = os.environ["BOT_SERVICE_TOKEN" if os.environ["ROLE"] == "backend"
                              else "MOM_BOT_SECRET_DISCORD_BOT_API_KEY"]
        if self.headers.get("Authorization") != "Bearer " + expected:
            self.send_response(401); self.end_headers(); return
        with open("/run/rsl-maintenance/state", "rb") as source:
            paused = source.read() != b"active\\n"
        body = json.dumps({"paused": paused, "inflight": 0}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
ThreadingHTTPServer(("0.0.0.0", 8000 if os.environ["ROLE"] == "backend" else 8001), Handler).serve_forever()
"""


def _run(command, *, timeout=180):
    return subprocess.run(command, check=True, capture_output=True, timeout=timeout)


def _report(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    path.chmod(0o600)


@unittest.skipUnless(
    os.environ.get("VPS_WRITER_DOCKER_TEST") == "1", "disposable CI daemon only"
)
class DisposableMaintenanceControlTests(unittest.TestCase):
    def test_pause_recreate_and_evidence_gated_resume(self):
        self.assertEqual(os.geteuid(), 0)
        suffix = uuid.uuid4().hex[:16]
        name = "maintenance-ci-" + suffix
        tag = "rsl-maintenance-test:" + suffix
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "control").mkdir(mode=0o755)
            (project / "evidence").mkdir(mode=0o700)
            (project / "control/state").write_bytes(b"active\n")
            (project / "control/history.json").write_text(
                '{"schema":1,"pause_started_at":null,"last_resumed_at":null}\n'
            )
            for path in (project / "control/state", project / "control/history.json"):
                path.chmod(0o644)
            (project / "stack.env").write_text(f"COMPOSE_PROJECT_NAME={name}\n")
            (project / "stack.env").chmod(0o600)
            (project / "server.py").write_text(SERVER)
            (project / "Dockerfile").write_text(
                "FROM python:3.12-alpine\n"
                "RUN mkdir -p /app/.venv/bin && ln -s /usr/local/bin/python /app/.venv/bin/python\n"
                "COPY server.py /app/server.py\n"
            )
            (project / "compose.yml").write_text(
                "services:\n"
                f"  backend:\n    image: {tag}\n"
                "    command: [python, /app/server.py]\n"
                "    environment: {ROLE: backend, BOT_SERVICE_TOKEN: synthetic-backend-token}\n"
                "    volumes: ['./control:/run/rsl-maintenance:ro']\n"
                f"  mom:\n    image: {tag}\n"
                "    command: [python, /app/server.py]\n"
                "    environment: {ROLE: mom, MOM_BOT_SECRET_DISCORD_BOT_API_KEY: synthetic-mom-token}\n"
                "    volumes: ['./control:/run/rsl-maintenance:ro']\n"
            )
            compose = [
                "docker",
                "compose",
                "--env-file",
                str(project / "stack.env"),
                "--project-directory",
                str(project),
                "-f",
                str(project / "compose.yml"),
            ]

            def phase(which, **kwargs):
                return CONTROL.run(
                    Namespace(
                        phase=which,
                        confirm=which if which in {"pause", "resume"} else None,
                        project_directory=project,
                        bearer_report=kwargs.get("bearer_report", []),
                        monitor_report=kwargs.get("monitor_report"),
                    )
                )

            try:
                _run(["docker", "build", "--tag", tag, str(project)])
                _run(
                    compose + ["up", "-d", "--wait", "--wait-timeout", "60"], timeout=90
                )
                before = {
                    service: CONTROL.PAUSE.identifiers(name, service)
                    for service in ("backend", "mom")
                }
                self.assertTrue(all(before.values()))
                self.assertEqual(phase("inspect")["status"], "PASS")
                self.assertEqual(phase("pause")["status"], "PASS")
                self.assertEqual(CONTROL.state(project), "paused")
                self.assertTrue(CONTROL.history(project)["pause_started_at"])
                self.assertEqual(phase("resume")["status"], "STOP")
                self.assertEqual(CONTROL.state(project), "paused")

                _run(
                    compose
                    + [
                        "up",
                        "-d",
                        "--force-recreate",
                        "--no-deps",
                        "--wait",
                        "backend",
                        "mom",
                    ],
                    timeout=90,
                )
                after = {
                    service: CONTROL.PAUSE.identifiers(name, service)
                    for service in ("backend", "mom")
                }
                self.assertTrue(
                    all(after[service] != before[service] for service in before)
                )
                self.assertEqual(phase("drain")["status"], "PASS")
                self.assertEqual(CONTROL.state(project), "paused")

                evidence = project / "evidence"
                reports = []
                for pair in ("sidecar", "reverse-api"):
                    path = evidence / f"{pair}.json"
                    _report(
                        path,
                        {
                            "phase": "bearer-activate",
                            "status": "PASS",
                            "pair": pair,
                            "checks": {
                                key: True
                                for key in (
                                    "baseline_current",
                                    "writers_stopped",
                                    "candidate_installed",
                                    "paired_verification",
                                    "public_health",
                                )
                            },
                            "verification": {
                                key: True
                                for key in (
                                    "snapshot",
                                    "exact_images_and_targeted_recreation",
                                    "sidecar_accepted",
                                    "reverse-api_accepted",
                                    f"{pair}_stale_rejected",
                                )
                            },
                        },
                    )
                    reports.append(path)
                time.sleep(0.05)
                monitor = evidence / "monitor.json"
                _report(
                    monitor,
                    {
                        "phase": "public-monitor",
                        "status": "PASS",
                        "observation_passed": True,
                        "delivery_acknowledged": True,
                    },
                )
                self.assertEqual(
                    phase("resume", bearer_report=reports, monitor_report=monitor)[
                        "status"
                    ],
                    "PASS",
                )
                self.assertEqual(CONTROL.state(project), "active")
                self.assertTrue(CONTROL.history(project)["last_resumed_at"])
            finally:
                subprocess.run(
                    compose + ["down", "--remove-orphans"],
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
                subprocess.run(
                    ["docker", "image", "rm", "--force", tag],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )


if __name__ == "__main__":
    unittest.main()
