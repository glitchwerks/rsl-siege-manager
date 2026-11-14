"""Disposable Docker rehearsal for the read-only writer/backup ordering gate."""

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-writer-pause-gate.py"
SPEC = importlib.util.spec_from_file_location("writer_pause_docker", SCRIPT)
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)
SPEC = importlib.util.spec_from_file_location(
    "release_pause_docker", SCRIPT.with_name("portable-vps-release-pause-backup.py")
)
PHASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PHASE)
SPEC = importlib.util.spec_from_file_location(
    "release_preflight_docker", SCRIPT.with_name("portable-vps-release-preflight.py")
)
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


def private(path, content):
    path.write_bytes(content)
    path.chmod(0o600)
    return path


def directory(path):
    path.mkdir(mode=0o700)
    return path


def candidate():
    return {
        "schema_version": 2, "release_id": "ci-writer-fixture",
        "siege": {"source_sha": "1" * 40, "ci_run_id": 1,
                  "api_image": "ghcr.io/glitchwerks/siege-api@sha256:" + "a" * 64,
                  "frontend_image": "ghcr.io/glitchwerks/siege-frontend@sha256:" + "b" * 64},
        "mom": {"source_sha": "2" * 40, "ci_run_id": 2,
                "image": "ghcr.io/glitchwerks/mom-bot@sha256:" + "c" * 64,
                "portable_auth": True, "scheduler_progress": True},
        "configuration": {"source_sha": "1" * 40, "topology": "mom"},
        "migrations": {
            "siege": {"mode": "none", "from_revision": "0012", "to_revision": "0012"},
            "mom": {"mode": "none", "from_revision": "b6_member", "to_revision": "b6_member"},
            "order": [],
        },
    }


@unittest.skipUnless(os.environ.get("VPS_WRITER_DOCKER_TEST") == "1", "disposable CI daemon only")
class DisposableDockerWriterTests(unittest.TestCase):
    def test_confirmed_pause_targets_only_two_writers(self):
        self.assertEqual(os.geteuid(), 0)
        name = "pause-ci-" + uuid.uuid4().hex[:16]
        containers = {}
        try:
            with tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary)
                private(project / "stack.env", f"COMPOSE_PROJECT_NAME={name}\n".encode())
                for service in ("backend", "mom", "postgres", "proxy", "frontend"):
                    command = ["docker", "run", "--detach", "--network", "none",
                               "--label", f"com.docker.compose.project={name}",
                               "--label", f"com.docker.compose.service={service}"]
                    if service != "proxy":
                        command += ["--health-cmd", "true", "--health-interval", "1s"]
                    command += ["alpine:3.21", "sleep", "300"]
                    containers[service] = subprocess.run(command, check=True, capture_output=True,
                                                         text=True, timeout=90).stdout.strip()
                rendered = {"services": {service: {"image": "alpine:3.21"}
                                         for service in containers}}
                data = {"project_directory": project}
                with patch.object(PHASE, "timer_active", return_value=True), patch.object(
                    PHASE, "backup_idle", return_value=True
                ):
                    for _ in range(15):
                        value = PHASE.planned(data, "a" * 64)
                        live = RELEASE.running_services(name, rendered)
                        if value and live:
                            break
                        time.sleep(1)
                    self.assertIsNotNone(value)
                    self.assertIsNotNone(live)
                    self.assertEqual(value["ids"], {name: containers[name] for name in value["ids"]})
                    self.assertEqual({name: item["id"] for name, item in live.items()}, containers)
                    rendered["services"]["mom"]["image"] = "unreviewed:image"
                    self.assertIsNone(RELEASE.running_services(name, rendered))
                    value["preflight_baseline_sha256"] = "e" * 64
                    private(project / "pause-plan.json", json.dumps(value).encode())
                    with patch.object(PHASE, "bound_preflight", return_value=(True, {})):
                        checks = PHASE.pause(data, value, project)
                    self.assertTrue(all(checks.values()), checks)
                    self.assertIsNone(GATE.docker_state(name, "postgres"))
                    self.assertEqual(GATE.docker_state(name, "backend")[0], containers["backend"])
                    self.assertEqual(GATE.docker_state(name, "mom")[0], containers["mom"])
                    self.assertIsNotNone(PHASE.container(name, "postgres"))
                    self.assertTrue(PHASE.container(name, "postgres")["running"])
                    self.assertTrue(PHASE.container(name, "proxy")["running"])
        finally:
            for container in reversed(list(containers.values())):
                subprocess.run(["docker", "rm", "--force", container], check=False,
                               capture_output=True, timeout=30)

    def test_stopped_writer_identity_and_backup_ordering(self):
        self.assertEqual(os.geteuid(), 0)
        name = "siege-ci-" + uuid.uuid4().hex[:16]
        containers = []
        try:
            with tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary)
                evidence = directory(project / "evidence")
                stages = directory(evidence / "release-stages")
                stage = directory(stages / "123-1")
                private(project / "stack.env", f"COMPOSE_PROJECT_NAME={name}\n".encode())
                raw = json.dumps(candidate()).encode()
                self.assertTrue(GATE.REMOTE.valid_manifest(raw))
                digest = hashlib.sha256(raw).hexdigest()
                private(stage / "candidate.json", raw)
                private(stage / "report.json", b'{"phase":"stage-release","status":"PASS"}')
                backup_root = directory(project / "backups")
                runs = directory(backup_root / "runs")
                run_id = str(uuid.uuid4())
                run = directory(runs / run_id)
                backup_path = run / "job.json"

                for service in ("backend", "mom"):
                    result = subprocess.run([
                        "docker", "run", "--detach", "--network", "none",
                        "--label", f"com.docker.compose.project={name}",
                        "--label", f"com.docker.compose.service={service}",
                        "alpine:3.21", "sleep", "300",
                    ], check=True, capture_output=True, text=True, timeout=90)
                    containers.append(result.stdout.strip())

                active = GATE.inspect(project, "123-1", digest, "root", backup_root, backup_path)
                self.assertFalse(active["both_writers_stopped"])
                for container in containers:
                    subprocess.run(["docker", "stop", "--time", "3", container],
                                   check=True, capture_output=True, timeout=20)

                started = datetime.now(timezone.utc)
                report = {"schema": 1, "phase": "run", "result": "PASS", "run_id": run_id,
                          "timestamp": started.isoformat(), "finished_at": started.isoformat(),
                          "checks": {key: True for key in GATE.BACKUP.JOB_CHECKS}}
                private(backup_path, json.dumps(report).encode())
                stopped = GATE.inspect(project, "123-1", digest, "root", backup_root, backup_path)
                self.assertTrue(all(stopped.values()), stopped)

                subprocess.run(["docker", "start", containers[0]], check=True,
                               capture_output=True, timeout=20)
                restarted = GATE.inspect(project, "123-1", digest, "root", backup_root, backup_path)
                self.assertFalse(restarted["both_writers_stopped"])
        finally:
            for container in reversed(containers):
                subprocess.run(["docker", "rm", "--force", container], check=False,
                               capture_output=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
