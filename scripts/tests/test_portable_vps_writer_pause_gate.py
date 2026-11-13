"""The release backup must start after both Compose writers have stopped."""

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-writer-pause-gate.py"
SPEC = importlib.util.spec_from_file_location("writer_pause", SCRIPT)
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


def candidate():
    return {
        "schema_version": 2, "release_id": "test-candidate",
        "siege": {"source_sha": "1" * 40, "ci_run_id": 123,
                  "api_image": "ghcr.io/glitchwerks/siege-api@sha256:" + "a" * 64,
                  "frontend_image": "ghcr.io/glitchwerks/siege-frontend@sha256:" + "b" * 64},
        "mom": {"source_sha": "2" * 40, "ci_run_id": 456,
                "image": "ghcr.io/glitchwerks/mom-bot@sha256:" + "c" * 64,
                "portable_auth": True, "scheduler_progress": True},
        "configuration": {"source_sha": "1" * 40, "topology": "mom"},
        "migrations": {
            "siege": {"mode": "none", "from_revision": "0012", "to_revision": "0012"},
            "mom": {"mode": "none", "from_revision": "b6_member", "to_revision": "b6_member"},
            "order": [],
        },
    }


class WriterPauseTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)
        self.stopped = self.now - timedelta(minutes=40)
        self.manifest = json.dumps(candidate()).encode()
        self.digest = hashlib.sha256(self.manifest).hexdigest()
        self.backup = {
            "schema": 1, "phase": "run", "result": "PASS", "run_id": str(uuid.uuid4()),
            "timestamp": (self.now - timedelta(minutes=35)).isoformat(),
            "finished_at": (self.now - timedelta(minutes=25)).isoformat(),
            "checks": {name: True for name in GATE.BACKUP.JOB_CHECKS},
        }
        self.backup_root = Path("/protected/backup")
        self.backup_path = self.backup_root / "runs" / self.backup["run_id"] / "job.json"

    def read(self, path, owner, limit=65536):
        if path.name == "candidate.json":
            return self.manifest
        if path.name == "report.json":
            return b'{"phase":"stage-release","status":"PASS"}'
        if path.name == "stack.env":
            return b"COMPOSE_PROJECT_NAME=siege-pilot\n"
        return json.dumps(self.backup).encode()

    def inspect(self, writer_state=None):
        with patch.object(GATE.os, "geteuid", return_value=0), patch.object(
            GATE, "protected", return_value=True
        ), patch.object(GATE, "read_private", side_effect=self.read), patch.object(
            GATE.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1001)
        ), patch.object(GATE, "docker_state", side_effect=writer_state or [self.stopped] * 4):
            return GATE.inspect(Path("/protected/project"), "123-1", self.digest,
                                self.backup_root, self.backup_path, now=self.now)

    def test_matching_stopped_services_and_later_backup_pass(self):
        self.assertTrue(all(self.inspect().values()))

    def test_running_writer_or_earlier_backup_stops(self):
        self.assertFalse(self.inspect([self.stopped, None])["both_writers_stopped"])
        self.backup["timestamp"] = (self.stopped - timedelta(minutes=1)).isoformat()
        self.assertFalse(self.inspect()["backup_after_pause"])

    def test_restart_between_checks_stops(self):
        later = self.stopped + timedelta(minutes=1)
        checks = self.inspect([self.stopped, self.stopped, later, self.stopped])
        self.assertFalse(checks["writers_still_stopped"])

    def test_report_must_belong_to_managed_run(self):
        self.backup_path = self.backup_root / "unmanaged" / "job.json"
        self.assertFalse(self.inspect()["managed_report_path"])
        self.backup_path = self.backup_root / "runs" / str(uuid.uuid4()) / "job.json"
        self.assertFalse(self.inspect()["managed_backup"])

    def test_wrong_candidate_digest_stops_before_docker(self):
        with patch.object(GATE.os, "geteuid", return_value=0), patch.object(
            GATE, "protected", return_value=True
        ), patch.object(GATE, "read_private", side_effect=self.read), patch.object(
            GATE, "docker_state"
        ) as docker:
            checks = GATE.inspect(Path("/protected/project"), "123-1", "0" * 64,
                                  self.backup_root, self.backup_path, now=self.now)
        self.assertFalse(checks["staged_manifest"])
        docker.assert_not_called()

    def test_docker_state_uses_local_socket_and_requires_one_exited_container(self):
        identifier = "a" * 64
        state = {"Status": "exited", "Running": False, "FinishedAt": self.stopped.isoformat()}
        results = [SimpleNamespace(returncode=0, stdout=identifier + "\n"),
                   SimpleNamespace(returncode=0, stdout=json.dumps(state))]
        with patch.object(GATE.subprocess, "run", side_effect=results) as run:
            self.assertEqual(GATE.docker_state("siege-pilot", "backend"), self.stopped)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].kwargs["env"]["DOCKER_HOST"],
                         "unix:///var/run/docker.sock")
        self.assertEqual(run.call_args_list[0].args[0][:2], ["docker", "ps"])
        with patch.object(GATE.subprocess, "run", return_value=SimpleNamespace(
            returncode=0, stdout=identifier + "\n" + "b" * 64 + "\n"
        )):
            self.assertIsNone(GATE.docker_state("siege-pilot", "backend"))

    def test_report_is_private_and_boolean_only(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "pause-gate.json"
            with patch.object(GATE, "inspect", return_value={"both_writers_stopped": False}), patch.object(
                GATE, "protected", return_value=True
            ):
                code = GATE.main(["--project-directory", directory, "--stage-run-id", "123-1",
                                  "--manifest-sha256", self.digest,
                                  "--backup-root", str(Path(directory) / "backup"),
                                  "--backup-report", str(Path(directory) / "job.json"),
                                  "--report", str(report)])
            self.assertEqual(code, 1)
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(report.read_text())["checks"], {"both_writers_stopped": False})


if __name__ == "__main__":
    unittest.main()
