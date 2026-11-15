"""Disposable policy tests; Docker is always mocked and no service is restarted."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("service_watchdog", Path(__file__).parents[1] / "portable-vps-service-watchdog.py")
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)
IMAGE = "example.test/backend@sha256:" + "a" * 64
CID = "b" * 64


def unhealthy(*, cid=CID, oom=False, restarts=0, health="unhealthy", running=True,
              started_at="2026-11-15T00:00:00Z"):
    return {"id": cid, "running": running, "health": health, "oom": oom,
            "restarts": restarts, "started_at": started_at}


def initial():
    return {"first_unhealthy": None, "container_id": None, "restart_count": None,
            "started_at": None, "attempts": []}


class PolicyTests(unittest.TestCase):
    def test_two_stable_observations_and_cooldown(self):
        state = initial()
        self.assertEqual(watchdog.decide(unhealthy(), state, 1000), "OBSERVING")
        self.assertEqual(watchdog.decide(unhealthy(), state, 1089), "OBSERVING")
        self.assertEqual(watchdog.decide(unhealthy(), state, 1090), "ELIGIBLE")
        state["attempts"] = [1090]
        state.update(first_unhealthy=1100, container_id=CID, restart_count=1,
                     started_at="2026-11-15T00:00:00Z")
        self.assertEqual(watchdog.decide(unhealthy(restarts=1), state, 1190), "ESCALATE")
        state["attempts"] = [1090, 1090 + watchdog.COOLDOWN_SECONDS]
        self.assertEqual(watchdog.decide(unhealthy(restarts=1), state, 1090 + watchdog.COOLDOWN_SECONDS + 1), "ESCALATE")

    def test_no_restart_for_missing_healthy_stopped_oom_or_changed_container(self):
        for current in (None, unhealthy(running=False), unhealthy(oom=True)):
            state = initial()
            self.assertEqual(watchdog.decide(current, state, 1000), "ESCALATE")
        state = initial()
        self.assertEqual(watchdog.decide(unhealthy(health="healthy"), state, 1000), "NO_RECOVERY")
        state = initial()
        watchdog.decide(unhealthy(), state, 1000)
        self.assertEqual(watchdog.decide(unhealthy(cid="c" * 64), state, 2000), "OBSERVING")
        self.assertEqual(state["first_unhealthy"], 2000)

    def test_manual_restart_resets_observation_even_with_same_id_and_count(self):
        state = initial()
        self.assertEqual(watchdog.decide(unhealthy(), state, 1000), "OBSERVING")
        new_process = unhealthy(started_at="2026-11-15T00:01:00Z")
        self.assertEqual(watchdog.decide(new_process, state, 1090), "OBSERVING")
        self.assertEqual(state["first_unhealthy"], 1090)
        self.assertEqual(watchdog.decide(new_process, state, 1180), "ELIGIBLE")

    def test_stale_observation_restarts_window_without_erasing_allowances(self):
        state = initial()
        watchdog.decide(unhealthy(), state, 1000)
        self.assertEqual(watchdog.decide(unhealthy(), state, 1000 + watchdog.WINDOW_SECONDS), "OBSERVING")
        self.assertEqual(state["first_unhealthy"], 1000 + watchdog.WINDOW_SECONDS)
        self.assertEqual(watchdog.decide(unhealthy(), state, 1000 + watchdog.WINDOW_SECONDS + 90), "ELIGIBLE")

    def test_backward_clock_fails_closed_without_erasing_attempt(self):
        state = initial()
        state["attempts"] = [1000]
        self.assertEqual(watchdog.decide(unhealthy(), state, 900), "ESCALATE")
        self.assertEqual(state["attempts"], [1000])
        self.assertEqual(watchdog.decide(unhealthy(), state, 1000), "OBSERVING")
        self.assertEqual(watchdog.decide(unhealthy(), state, 1090), "ESCALATE")


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.config = root / "config.json"
        self.state = root / "state.json"
        self.report = root / "report.json"
        self.config.write_text(json.dumps({"project": "siege", "service": "backend", "image": IMAGE, "enabled": True}))
        self.config.chmod(0o600)
        self.args = ["--config", str(self.config), "--state", str(self.state), "--report", str(self.report),
                     "--confirm-targeted-restart"]

    def run_main(self, now, current, *, latest=None):
        self.report.unlink(missing_ok=True)
        with patch.object(watchdog.time, "time", return_value=now), patch.object(
                watchdog, "snapshot", side_effect=[current, current if latest is None else latest]) as observe, patch.object(
                watchdog, "docker") as docker, redirect_stdout(io.StringIO()) as output:
            result = watchdog.main(self.args)
        self.assertIn(output.getvalue().strip(), ("PASS", "STOP"))
        report = json.loads(self.report.read_text())
        self.assertNotIn(IMAGE, self.report.read_text())
        self.assertNotIn(CID, self.report.read_text())
        return result, report, observe.call_count, docker

    def test_stable_unhealthy_service_only_restarts_once(self):
        result, report, count, docker = self.run_main(1000, unhealthy())
        self.assertEqual((result, report["decision"], count), (1, "OBSERVING", 1))
        docker.assert_not_called()
        result, report, count, docker = self.run_main(1090, unhealthy())
        self.assertEqual((result, report["decision"], count), (1, "RESTARTED", 2))
        docker.assert_called_once_with("container", "restart", "--time", "10", CID)
        self.assertEqual(json.loads(self.state.read_text())["attempts"], [1090])
        result, report, _, docker = self.run_main(1200, unhealthy(restarts=1))
        self.assertEqual(report["decision"], "OBSERVING")
        docker.assert_not_called()
        result, report, _, docker = self.run_main(1290, unhealthy(restarts=1))
        self.assertEqual((result, report["decision"]), (1, "ESCALATE"))
        docker.assert_not_called()

    def test_identity_change_before_restart_fails_closed(self):
        self.run_main(1000, unhealthy())
        result, report, count, docker = self.run_main(1090, unhealthy(), latest=unhealthy(health="healthy"))
        self.assertEqual((result, report["decision"], count), (1, "CHANGED_BEFORE_RESTART", 2))
        docker.assert_not_called()
        self.assertIsNone(json.loads(self.state.read_text())["first_unhealthy"])
        result, report, _, docker = self.run_main(1100, unhealthy())
        self.assertEqual((result, report["decision"]), (1, "OBSERVING"))
        docker.assert_not_called()

    def test_confirmation_allowlist_and_private_paths(self):
        with patch.object(watchdog, "snapshot") as observe, redirect_stdout(io.StringIO()):
            self.assertEqual(watchdog.main(self.args[:-1]), 2)
        observe.assert_not_called()
        self.assertFalse(self.report.exists())
        for service in ("postgres", "proxy", "migrate-siege"):
            self.config.write_text(json.dumps({"project": "siege", "service": service, "image": IMAGE, "enabled": True}))
            with patch.object(watchdog, "snapshot") as observe, redirect_stdout(io.StringIO()):
                self.assertEqual(watchdog.main(self.args), 1)
            observe.assert_not_called()
            self.report.unlink()
        self.config.chmod(0o644)
        with patch.object(watchdog, "snapshot") as observe, redirect_stdout(io.StringIO()):
            self.assertEqual(watchdog.main(self.args), 1)
        observe.assert_not_called()

    def test_snapshot_requires_exact_compose_identity_and_image(self):
        cfg = {"project": "siege", "service": "backend", "image": IMAGE, "enabled": True}
        details = {"id": CID, "project": "siege", "service": "backend", "image": IMAGE,
                   "running": True, "health": "unhealthy", "oom": False, "restarts": 0,
                   "started_at": "2026-11-15T00:00:00Z"}
        with patch.object(watchdog, "docker", side_effect=[CID + "\n", json.dumps(details)]) as docker:
            self.assertEqual(watchdog.snapshot(cfg), unhealthy())
        self.assertEqual(docker.call_count, 2)
        self.assertIn("--format", docker.call_args.args)
        details["image"] = "other.test/image@sha256:" + "c" * 64
        with patch.object(watchdog, "docker", side_effect=[CID + "\n", json.dumps(details)]):
            with self.assertRaises(ValueError):
                watchdog.snapshot(cfg)
        with patch.object(watchdog, "docker", return_value=CID + "\n" + "c" * 64 + "\n") as docker:
            self.assertIsNone(watchdog.snapshot(cfg))
        docker.assert_called_once()

    def test_docker_client_ignores_inherited_remote_context(self):
        class Done:
            stdout = CID + "\n"
            stderr = ""

        with patch.object(watchdog.subprocess, "run", return_value=Done()) as run:
            self.assertEqual(watchdog.docker("container", "ls"), CID + "\n")
        self.assertEqual(run.call_args.args[0][:3], ["docker", "--host", "unix:///var/run/docker.sock"])
        self.assertEqual(run.call_args.kwargs["env"], watchdog.DOCKER_ENV)
        self.assertNotIn("DOCKER_CONTEXT", run.call_args.kwargs["env"])


if __name__ == "__main__":
    unittest.main()
