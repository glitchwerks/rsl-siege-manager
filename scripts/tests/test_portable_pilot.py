"""Sanitized, read-only pilot observation; no live daemon/HTTPS/Discord access."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("pilot", Path(__file__).parents[1] / "portable-pilot.py")
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


def config(root):
    return {"project": "isolated-pilot", "topology": "mom", "origin": "https://pilot.fixture-domain.net",
            "images": {name: "registry.fixture/app@sha256:" + "a" * 64
                       for name in ("postgres", "backend", "frontend", "proxy", "mom")},
            "versions": {"backend_version": "1.4.3", "frontend_version": "1.5.0", "bot_version": "1.6.0",
                         "git_sha": "a" * 40}, "storage_path": str(root), "samples": 2,
            "interval_seconds": 1, "minimum_disk_bytes": 1024 ** 3,
            "minimum_available_memory_bytes": 128 * 1024 ** 2}


def rows(cfg):
    return {name: {"id": format(index, "064x"), "project": cfg["project"], "service": name,
                   "image": cfg["images"][name], "running": True, "oom": False, "restarts": 0,
                   "public_host": "pilot.fixture-domain.net" if name == "proxy" else None,
                   "started_at": "2026-10-11T00:00:00Z",
                   "memory_limit": 1024 ** 3, "health": "healthy" if name != "proxy" else "none"}
            for index, name in enumerate(cfg["images"], 1)}


class PilotTests(unittest.TestCase):
    def setUp(self):
        pilot.DEADLINE = None

    def run_main(self, args):
        with redirect_stdout(io.StringIO()) as output:
            code = pilot.main(args)
        return code, json.loads(output.getvalue())

    def test_confirmation_and_report_reservation_before_any_io(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "report.json"
            with patch.object(pilot, "settings") as settings:
                code, result = self.run_main(["--config", str(root / "missing"), "--report", str(report)])
            self.assertEqual(code, 1)
            self.assertEqual(result["error"], "readonly_pilot_confirmation_required")
            settings.assert_not_called()
            self.assertFalse(result["pilot_accepted"])
            previous = report.read_bytes()
            with patch.object(pilot, "settings") as settings:
                code, result = self.run_main(["--config", str(root / "missing"), "--report", str(report),
                                              "--confirm-readonly-pilot"])
            settings.assert_not_called()
            self.assertEqual(result["error"], "report_write_failed")
            self.assertEqual(report.read_bytes(), previous)

    def test_protected_config_shape_origin_and_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "pilot.json"
            cfg = config(root)
            path.write_text(json.dumps(cfg)); path.chmod(0o600)
            self.assertEqual(pilot.settings(path)["services"][-1], "mom")
            for key, value in (("origin", "http://unsafe"), ("origin", "https://host.net?token=secret"),
                               ("origin", "https://user:secret@host.net"), ("samples", 1000),
                               ("interval_seconds", True), ("project", "--all")):
                bad = {**cfg, key: value}
                path.write_text(json.dumps(bad))
                with self.assertRaises(pilot.Stop):
                    pilot.settings(path)
            path.write_text(json.dumps(cfg)); path.chmod(0o644)
            with self.assertRaises(pilot.Stop):
                pilot.settings(path)
            path.chmod(0o600)
            link = root / "link"; link.symlink_to(path)
            with self.assertRaises(pilot.Stop):
                pilot.settings(link)

    def test_inventory_scopes_project_and_refuses_missing_duplicate_or_running_maintenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(Path(tmp)); cfg["services"] = list(cfg["images"])
            current = list(rows(cfg).values())
            def collect(values):
                with patch.object(pilot, "command", side_effect=["\n".join(row["id"] for row in values),
                                                                 "\n".join(json.dumps(row) for row in values)]) as command:
                    result = pilot.inventory(cfg)
                self.assertEqual(command.call_args_list[0].args[0][-1],
                                 "label=com.docker.compose.project=isolated-pilot")
                return result
            self.assertEqual(set(collect(current)), set(cfg["services"]))
            variants = [current[:-1], current + [current[0]],
                        [{**row, "project": "production"} for row in current],
                        current + [{**current[0], "id": "f" * 64, "service": "migrate-siege", "running": True}]]
            for variant in variants:
                with self.assertRaises(pilot.Stop):
                    collect(variant)

    def test_docker_commands_are_local_readonly_and_suppress_diagnostics(self):
        with patch.object(pilot.subprocess, "run") as run:
            run.return_value.returncode = 0; run.return_value.stdout = "[]"
            pilot.command(["inspect", "abc"])
            argv = run.call_args.args[0]
            self.assertEqual(argv[:3], ["docker", "--host", "unix:///var/run/docker.sock"])
            self.assertEqual(argv[3], "inspect")
            run.return_value.returncode = 1; run.return_value.stderr = "secret host token"
            with self.assertRaises(pilot.Stop) as error:
                pilot.command(["stats"])
            self.assertEqual(str(error.exception), "docker_read_failed")

    def test_stats_require_complete_ids_and_numeric_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            current = rows(config(Path(tmp)))
            values = [{"id": row["id"], "memory": "100MiB / 1GiB", "cpu": "12.5%"}
                      for row in current.values()]
            with patch.object(pilot, "command", return_value="\n".join(json.dumps(row) for row in values)):
                result = pilot.resource_sample(current)
            self.assertEqual(result["mom"]["memory_bytes"], 100 * 1024 ** 2)
            for variant in (values[:-1], [{**row, "cpu": "NaN%"} for row in values]):
                with patch.object(pilot, "command", return_value="\n".join(json.dumps(row) for row in variant)):
                    with self.assertRaises(pilot.Stop):
                        pilot.resource_sample(current)

    def observe_fixture(self, cfg, inventories=None, health=None, resources=None):
        cfg = {**cfg, "services": list(cfg["images"])}
        current = rows(cfg)
        sample = {name: {"memory_bytes": 100 * 1024 ** 2, "cpu_percent": 10.0} for name in current}
        host = {"available_memory_bytes": 1024 ** 3, "disk_available_bytes": 20 * 1024 ** 3,
                "host_memory_bytes": 4 * 1024 ** 3, "logical_cpus": 2}
        report = {"checks": {}, "result": "STOP", "pilot_accepted": False}
        inventories = inventories or [current, copy.deepcopy(current)]
        if len(inventories) == cfg["samples"]:
            inventories = [*inventories, copy.deepcopy(inventories[-1])]
        with patch.object(pilot, "inventory", side_effect=inventories), \
             patch.object(pilot, "resource_sample", return_value=resources or sample), \
             patch.object(pilot, "host_sample", return_value=host), \
             patch.object(pilot, "endpoint", side_effect=health or [{"status": "healthy"}, cfg["versions"]] * 2), \
             patch.object(pilot.time, "sleep"):
            pilot.observe(cfg, report)
        return report

    def test_pass_only_observation_and_reports_no_identifiers_or_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(Path(tmp))
            report = self.observe_fixture(cfg)
            self.assertEqual(report["result"], "PASS")
            self.assertFalse(report["pilot_accepted"])
            raw = json.dumps(report)
            for private in (cfg["project"], cfg["origin"], cfg["storage_path"], "registry.fixture", "a" * 64):
                self.assertNotIn(private, raw)
            self.assertEqual(report["samples_completed"], 2)

    def test_unhealthy_oom_restart_image_mismatch_and_memory_pressure_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(Path(tmp)); first = rows(cfg)
            for key, value in (("oom", True), ("running", False), ("health", "unhealthy"),
                               ("image", "unexpected"), ("restarts", 1), ("started_at", "2026-10-11T01:00:00Z")):
                second = copy.deepcopy(first); second["backend"][key] = value
                self.assertEqual(self.observe_fixture(cfg, [first, second])["result"], "STOP")
            sample = {name: {"memory_bytes": 950 * 1024 ** 2, "cpu_percent": 5.0} for name in first}
            self.assertEqual(self.observe_fixture(cfg, resources=sample)["result"], "STOP")
            self.assertEqual(self.observe_fixture(cfg, health=[{"status": "unhealthy"}, cfg["versions"], cfg["versions"]])["result"], "STOP")

    def test_failure_evidence_sanitized_and_deadline_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cfg = root / "pilot.json"; cfg.write_text(json.dumps(config(root))); cfg.chmod(0o600)
            with patch.object(pilot, "observe", side_effect=OSError("sensitive workload secret")):
                code, report = self.run_main(["--config", str(cfg), "--report", str(root / "report.json"),
                                              "--confirm-readonly-pilot"])
            self.assertEqual(code, 1)
            self.assertNotIn("sensitive", json.dumps(report))
            self.assertEqual(json.loads((root / "report.json").read_text()), report)
            self.assertEqual((root / "report.json").stat().st_mode & 0o777, 0o600)
        pilot.DEADLINE = pilot.time.monotonic() - 1
        with self.assertRaises(pilot.Stop):
            pilot.budget(15)

    def test_final_inventory_detects_restart_replacement_and_oom_during_last_probes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(Path(tmp)); first = rows(cfg)
            for key, value in (("restarts", 1), ("id", "f" * 64), ("oom", True), ("health", "unhealthy"),
                               ("started_at", "2026-10-11T01:00:00Z")):
                with self.subTest(key=key):
                    final = copy.deepcopy(first); final["backend"][key] = value
                    report = self.observe_fixture(cfg, [first, copy.deepcopy(first), final])
                    self.assertEqual(report["samples_completed"], 2)
                    self.assertEqual(report["result"], "STOP")

    def test_wrong_proxy_hostname_stops_before_https_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(Path(tmp)); cfg["services"] = list(cfg["images"])
            current = rows(cfg)
            current["proxy"]["public_host"] = "other.fixture-domain.net"
            with patch.object(pilot, "inventory", return_value=current), \
                 patch.object(pilot, "endpoint") as endpoint:
                with self.assertRaises(pilot.Stop) as error:
                    pilot.observe(cfg, {"checks": {}, "result": "STOP"})
            self.assertEqual(str(error.exception), "origin_proxy_mismatch")
            endpoint.assert_not_called()

    def test_http_protocol_failure_saves_sanitized_stop_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "pilot.json"
            cfg.write_text(json.dumps(config(root))); cfg.chmod(0o600)
            for index, error in enumerate((pilot.http.client.IncompleteRead(b"sensitive-body", 100),
                                           pilot.http.client.BadStatusLine("sensitive-header"))):
                response = unittest.mock.MagicMock()
                response.__enter__.return_value = response
                response.status = 200
                response.read1.side_effect = error
                report_path = root / f"report-{index}.json"
                with patch.object(pilot.urllib.request, "build_opener") as opener, \
                     patch.object(pilot, "observe", side_effect=lambda cfg, report:
                                  pilot.endpoint(cfg["origin"], "/api/health")):
                    opener.return_value.open.return_value = response
                    code, report = self.run_main(["--config", str(cfg), "--report", str(report_path),
                                                  "--confirm-readonly-pilot"])
                self.assertEqual(code, 1)
                self.assertEqual(report["result"], "STOP")
                self.assertEqual(report["error"], "observation_failed")
                self.assertNotIn("sensitive", json.dumps(report))
                self.assertEqual(json.loads(report_path.read_text()), report)

    def test_https_rejects_redirects_and_oversized_json(self):
        with self.assertRaises(pilot.Stop):
            pilot.NoRedirect().redirect_request(None, None, None, None, None, None)
        response = unittest.mock.MagicMock(); response.__enter__.return_value = response
        response.status = 200; response.read1.side_effect = [b"x" * (pilot.MAX_JSON + 1)]
        with patch.object(pilot.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = response
            with self.assertRaises(pilot.Stop):
                pilot.endpoint("https://pilot.fixture-domain.net", "/api/health")
            self.assertEqual(opener.call_args.args[0].proxies, {})


@unittest.skipUnless(__import__("os").environ.get("PORTABLE_PILOT_TEST") == "1", "disposable CI daemon only")
class DisposableDockerTests(unittest.TestCase):
    def test_selected_inspect_stats_and_actual_data_filesystem(self):
        import subprocess
        import time
        import uuid
        pilot.DEADLINE = None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            project = "observer-ci-" + uuid.uuid4().hex
            container = None
            proxy_container = None
            try:
                container = subprocess.run([*pilot.DOCKER, "run", "--detach", "--label",
                    "com.docker.compose.project=" + project, "--label", "com.docker.compose.service=postgres",
                    "--memory", "128m", "--health-cmd", "true", "--health-interval", "1s",
                    "--mount", f"type=bind,src={root},dst=/var/lib/postgresql/data",
                    "alpine:3.21", "sleep", "300"], check=True, capture_output=True, text=True, timeout=90).stdout.strip()
                cfg = {"project": project, "services": ["postgres"], "storage_path": str(root)}
                for _ in range(15):
                    current = pilot.inventory(cfg)
                    if current["postgres"]["health"] == "healthy":
                        break
                    time.sleep(1)
                self.assertEqual(current["postgres"]["health"], "healthy")
                self.assertEqual(current["postgres"]["memory_limit"], 128 * 1024 ** 2)
                resource = pilot.resource_sample(current)
                self.assertGreaterEqual(resource["postgres"]["memory_bytes"], 0)
                host = pilot.host_sample(cfg, current)
                self.assertGreater(host["disk_available_bytes"], 0)
                self.assertGreater(host["host_memory_bytes"], 0)
                self.assertFalse(current["postgres"]["oom"])
                self.assertEqual(current["postgres"]["restarts"], 0)
                proxy_container = subprocess.run([*pilot.DOCKER, "run", "--detach", "--label",
                    "com.docker.compose.project=" + project, "--label", "com.docker.compose.service=proxy",
                    "--memory", "128m", "--env", "PUBLIC_HOST=pilot.fixture-domain.net",
                    "--env", "SECRET=synthetic-private-credential", "alpine:3.21", "sleep", "300"],
                    check=True, capture_output=True, text=True, timeout=90).stdout.strip()
                current = pilot.inventory({**cfg, "services": ["postgres", "proxy"]})
                self.assertEqual(current["proxy"]["public_host"], "pilot.fixture-domain.net")
                self.assertNotIn("synthetic-private-credential", json.dumps(current))
            finally:
                if proxy_container:
                    subprocess.run([*pilot.DOCKER, "rm", "--force", proxy_container], check=True,
                                   capture_output=True, timeout=30)
                if container:
                    subprocess.run([*pilot.DOCKER, "rm", "--force", container], check=True,
                                   capture_output=True, timeout=30)
