"""Post-promotion acceptance stays bound to exact images and hides credentials."""

from contextlib import ExitStack
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-release-accept.py"
SPEC = importlib.util.spec_from_file_location("release_accept", SCRIPT)
ACCEPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCEPT)


def manifest():
    return {"siege": {"source_sha": "a" * 40, "api_image": "api", "frontend_image": "frontend"},
            "mom": {"source_sha": "b" * 40, "image": "mom"}}


def versions():
    return {"backend_version": "1.7.0+123." + "a" * 7,
            "frontend_version": "1.7.0+123." + "a" * 7,
            "bot_version": "1.6.0+456." + "b" * 7,
            "git_sha": "a" * 40}


class ReleaseAcceptanceTests(unittest.TestCase):
    def test_public_response_read_is_inside_whole_request_deadline(self):
        events = []
        @contextmanager
        def deadline():
            events.append("enter")
            try:
                yield
            finally:
                events.append("exit")
        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, _limit):
                events.append("read")
                return b'{}'
        opener = SimpleNamespace(open=lambda *_args, **_kwargs: Response())
        with patch.object(ACCEPT.MONITOR, "delivery_deadline", side_effect=deadline), patch.object(
            ACCEPT.urllib.request, "build_opener", return_value=opener
        ):
            self.assertEqual(ACCEPT.get_public("https://prod.example.net", "/version.json"),
                             (200, {}))
        self.assertEqual(events, ["enter", "read", "exit"])

    def test_missing_target_confirmation_never_loads_profile_or_creates_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            with patch.object(ACCEPT.PAUSE, "profile") as profile:
                code = ACCEPT.main(["--profile", "/private/profile", "--stage-run-id", "123-1",
                    "--manifest-sha256", "a" * 64, "--plan-sha256", "b" * 64,
                    "--readiness-sha256", "c" * 64, "--promotion-ready-sha256", "d" * 64,
                    "--pilot-config", "/private/pilot.json", "--session-cookie-file", "/private/cookie",
                    "--report", str(report)])
            self.assertEqual(code, 2)
            self.assertFalse(report.exists())
            profile.assert_not_called()

    def test_complete_readonly_gate_records_candidate_and_rejects_runtime_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            directory = root / "stage"
            (project / "runtime").mkdir(parents=True)
            directory.mkdir()
            pilot_config = root / "pilot.json"
            cookie = root / "cookie"
            candidate = manifest()
            old_stack = b"old stack"
            old_mom = b"old mom"
            new_stack = b"candidate stack"
            new_mom = b"candidate mom"
            preflight = {"stack_env_sha256": ACCEPT.PAUSE.digest(old_stack),
                         "runtime_sha256": {"mom-image.json": ACCEPT.PAUSE.digest(old_mom),
                                            "backend.env": "1"},
                         "config_sha256": {"compose.yml": "2"},
                         "installed": {"postgres": {"image": "postgres"},
                                       "proxy": {"image": "proxy"}}}
            plan = {"project": "prod", "preflight_baseline_sha256": "c" * 64}
            gate = {"postgres_id": "f" * 64, "revisions": {"siege": "s2", "mom": "m1"}}
            cfg = {"project": "prod", "topology": "mom", "origin": "https://prod.example.net",
                   "images": {"backend": "api", "frontend": "frontend", "mom": "mom",
                              "postgres": "postgres", "proxy": "proxy"},
                   "versions": versions()}
            files = {project / "stack.env": new_stack,
                     project / "runtime" / "mom-image.json": new_mom,
                     pilot_config: b"private config"}
            ids = {"backend": "a" * 64, "frontend": "b" * 64, "mom": "c" * 64}
            runtime = {"mom-image.json": ACCEPT.PAUSE.digest(new_mom), "backend.env": "1"}
            patches = [
                patch.object(ACCEPT.os, "geteuid", return_value=0),
                patch.object(ACCEPT.MIGRATE, "candidate", return_value=candidate),
                patch.object(ACCEPT.PROMOTE, "accepted_gate", return_value=(gate, "d" * 64)),
                patch.object(ACCEPT, "accepted_promotion", return_value=(old_stack, old_mom)),
                patch.object(ACCEPT.PREFLIGHT, "accepted_baseline", return_value=(preflight, "c" * 64)),
                patch.object(ACCEPT.GATE, "read_private", side_effect=lambda path, *_args: files[path]),
                patch.object(ACCEPT.PROMOTE, "image_config", return_value=new_stack),
                patch.object(ACCEPT.PROMOTE, "approval_config", return_value=new_mom),
                patch.object(ACCEPT.PREFLIGHT, "config_hashes", return_value={"compose.yml": "2"}),
                patch.object(ACCEPT.PREFLIGHT, "runtime_hashes", side_effect=lambda *_args: runtime),
                patch.object(ACCEPT.PROMOTE, "unchanged_foundation", return_value=True),
                patch.object(ACCEPT.PAUSE, "identifiers", side_effect=lambda _project, service: ids[service]),
                patch.object(ACCEPT.PROMOTE, "live_service", side_effect=lambda _project, service, _image: ids[service]),
                patch.object(ACCEPT.PAUSE, "no_extra_writers", return_value=True),
                patch.object(ACCEPT.MIGRATE, "revisions_match", return_value=True),
                patch.object(ACCEPT.PAUSE, "timer_active", return_value=True),
                patch.object(ACCEPT.PILOT, "settings", return_value=cfg),
                patch.object(ACCEPT, "get_public", return_value=(200, {
                    "frontend_version": versions()["frontend_version"],
                    "git_sha": candidate["siege"]["source_sha"]})),
                patch.object(ACCEPT, "public_auth", return_value=True),
                patch.object(ACCEPT, "sidecar_auth", return_value=True),
            ]
            with ExitStack() as context:
                for item in patches:
                    context.enter_context(item)
                pilot = context.enter_context(patch.object(ACCEPT, "run_pilot", return_value=True))
                checks, baseline = ACCEPT.inspect({"project_directory": project}, directory, plan,
                    "a" * 64, "b" * 64, "e" * 64, "d" * 64, pilot_config, cookie)
                self.assertTrue(all(checks.values()), checks)
                self.assertEqual(baseline["container_ids"], ids)
                self.assertEqual(baseline["siege_source_sha"], candidate["siege"]["source_sha"])
                pilot.assert_called_once()
                runtime["backend.env"] = "changed"
                checks, baseline = ACCEPT.inspect({"project_directory": project}, directory, plan,
                    "a" * 64, "b" * 64, "e" * 64, "d" * 64, pilot_config, cookie)
                self.assertFalse(checks["reviewed_inputs_unchanged"])
                self.assertIsNone(baseline)

    def test_pilot_configuration_must_match_manifest_and_original_foundation(self):
        cfg = {"topology": "mom", "project": "prod", "images": {
            "backend": "api", "frontend": "frontend", "mom": "mom",
            "postgres": "postgres", "proxy": "proxy"}, "versions": versions()}
        plan = {"project": "prod"}
        preflight = {"installed": {"postgres": {"image": "postgres"},
                                    "proxy": {"image": "proxy"}}}
        self.assertTrue(ACCEPT.pilot_bound(cfg, manifest(), plan, preflight))
        self.assertFalse(ACCEPT.pilot_bound({**cfg, "images": {**cfg["images"],
            "postgres": "another"}}, manifest(), plan, preflight))
        self.assertFalse(ACCEPT.pilot_bound({**cfg, "versions": {**versions(),
            "bot_version": "1.6.0+456." + "a" * 7}}, manifest(), plan, preflight))
        self.assertFalse(ACCEPT.pilot_bound({**cfg, "project": "other"}, manifest(), plan, preflight))

    def test_public_cookie_auth_checks_anonymous_and_authenticated_without_output(self):
        secret = b"a" * 20 + b"." + b"b" * 20
        identity = {"member_id": 8, "name": "private", "role": "member"}
        with patch.object(ACCEPT.GATE, "read_private", return_value=secret), patch.object(
            ACCEPT, "get_public", side_effect=[(401, None), (200, identity)]
        ) as probe:
            self.assertTrue(ACCEPT.public_auth("https://site.example", Path("/private/cookie")))
        self.assertEqual(probe.call_args_list[0].args, ("https://site.example", "/api/auth/me"))
        self.assertEqual(probe.call_args_list[1].args,
                         ("https://site.example", "/api/auth/me", secret.decode()))
        with patch.object(ACCEPT.GATE, "read_private", return_value=b"Cookie: session=secret"):
            with self.assertRaises(ValueError):
                ACCEPT.public_auth("https://site.example", Path("/private/cookie"))

    def test_sidecar_probe_sends_no_secret_and_accepts_only_exact_version(self):
        result = {"checks": {name: True for name in
            ("health", "version", "missing_auth", "wrong_auth", "valid_auth")},
            "version": "1.6.0+456." + "b" * 7}
        with patch.object(ACCEPT.subprocess, "run", return_value=SimpleNamespace(
            returncode=0, stdout=json.dumps(result)
        )) as run:
            self.assertTrue(ACCEPT.sidecar_auth("f" * 64, result["version"]))
            self.assertFalse(ACCEPT.sidecar_auth("f" * 64, "wrong"))
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["docker", "exec", "f" * 64, "/app/.venv/bin/python"])
        self.assertEqual(command[4], "-c")
        self.assertNotIn("secret", str(run.call_args.kwargs["env"]).lower())
        self.assertEqual(run.call_args.kwargs["stdin"], ACCEPT.subprocess.DEVNULL)

    def test_promotion_evidence_requires_exact_saved_file_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            old_stack = b"private old stack"
            old_mom = b"private old approval"
            marker = {"phase": "promote-start", "manifest_sha256": "a" * 64,
                      "plan_sha256": "b" * 64, "promotion_ready_sha256": "c" * 64,
                      "installed_stack_sha256": ACCEPT.PAUSE.digest(old_stack),
                      "installed_mom_approval_sha256": ACCEPT.PAUSE.digest(old_mom)}
            activation = {"phase": "promote-activation-start", "manifest_sha256": "a" * 64}
            files = {directory / "promote-start.json": json.dumps(marker).encode(),
                     directory / "promote-activation-start.json": json.dumps(activation).encode(),
                     directory / "installed-stack.env": old_stack,
                     directory / "installed-mom-image.json": old_mom}
            with patch.object(ACCEPT.PROOF, "accepted_report", return_value=True), patch.object(
                ACCEPT.GATE, "read_private", side_effect=lambda path, *_args: files[path]
            ):
                self.assertEqual(ACCEPT.accepted_promotion(directory, "a" * 64,
                    "b" * 64, "c" * 64), (old_stack, old_mom))
                files[directory / "installed-stack.env"] = b"changed"
                with self.assertRaises(ValueError):
                    ACCEPT.accepted_promotion(directory, "a" * 64, "b" * 64, "c" * 64)

    def test_pilot_pass_requires_all_boolean_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            value = {"phase": "pilot-observation", "result": "PASS",
                     "pilot_accepted": False, "checks": {"runtime": True}}
            with patch.object(ACCEPT.subprocess, "run", return_value=SimpleNamespace(returncode=0)), patch.object(
                ACCEPT.GATE, "read_private", side_effect=lambda *_args: json.dumps(value).encode()
            ):
                self.assertTrue(ACCEPT.run_pilot(Path("/private/config"), report))
                value["checks"]["runtime"] = False
                self.assertFalse(ACCEPT.run_pilot(Path("/private/config"), report))


if __name__ == "__main__":
    unittest.main()
