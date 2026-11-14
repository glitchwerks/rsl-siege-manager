"""Release preflight binds the candidate to reviewed live configuration."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-release-preflight.py"
SPEC = importlib.util.spec_from_file_location("vps_release_preflight", SCRIPT)
PHASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PHASE)


def manifest():
    return {"configuration": {"source_sha": "a" * 40},
            "siege": {"api_image": "ghcr.io/glitchwerks/siege-api@sha256:" + "b" * 64,
                      "frontend_image": "ghcr.io/glitchwerks/siege-frontend@sha256:" + "c" * 64},
            "mom": {"source_sha": "d" * 40,
                    "image": "ghcr.io/glitchwerks/mom-bot@sha256:" + "e" * 64}}


def model(backend, frontend, mom):
    return {"services": {name: {"image": image} for name, image in (
        ("backend", backend), ("migrate-siege", backend), ("frontend", frontend),
        ("mom", mom), ("migrate-mom", mom), ("postgres", "postgres@sha256:" + "f" * 64),
        ("proxy", "caddy@sha256:" + "0" * 64))}}


class ReleasePreflightTests(unittest.TestCase):
    def test_report_inside_stage_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            stage = project / "evidence" / "release-stages" / "123-1"
            stage.mkdir(parents=True)
            report = stage / "preflight-baseline.json"
            with patch.object(PHASE.PAUSE, "stage") as lookup:
                result = PHASE.main(["--project-directory", str(project),
                    "--stage-run-id", "123-1", "--manifest-sha256", "a" * 64,
                    "--report", str(report)])
            self.assertEqual(result, 2)
            self.assertFalse(report.exists())
            lookup.assert_not_called()

    def test_running_images_must_match_exact_healthy_containers(self):
        current = model("current-backend", "current-frontend", "current-mom")
        ids = {name: character * 64 for name, character in zip(PHASE.SERVICES, "abcde")}
        def identifier(project, service):
            self.assertEqual(project, "synthetic")
            return ids[service]
        def command(arguments, timeout=20):
            service = next(name for name, value in ids.items() if value == arguments[-1])
            if ".Config.Image" in arguments[3]:
                return json.dumps(current["services"][service]["image"])
            return json.dumps({"Running": True, "Status": "running",
                               "Health": {"Status": "healthy"}})
        with patch.object(PHASE.PAUSE, "identifiers", side_effect=identifier), patch.object(
            PHASE.PAUSE, "command", side_effect=command
        ):
            result = PHASE.running_services("synthetic", current)
        self.assertEqual({name: item["id"] for name, item in result.items()}, ids)
        current["services"]["mom"]["image"] = "different"
        with patch.object(PHASE.PAUSE, "identifiers", side_effect=identifier), patch.object(
            PHASE.PAUSE, "command", side_effect=command
        ):
            # The mock follows the reviewed rendering, so an explicit Docker drift
            # check below makes the mismatch observable.
            def drift(arguments, timeout=20):
                value = command(arguments, timeout)
                return json.dumps("current-mom") if arguments[-1] == ids["mom"] and ".Config.Image" in arguments[3] else value
            with patch.object(PHASE.PAUSE, "command", side_effect=drift):
                self.assertIsNone(PHASE.running_services("synthetic", current))

    def test_candidate_images_must_match_runtime_and_migration_services(self):
        candidate = manifest()
        good = model(candidate["siege"]["api_image"], candidate["siege"]["frontend_image"],
                     candidate["mom"]["image"])
        self.assertTrue(PHASE.candidate_images_match(good, candidate))
        good["services"]["migrate-mom"]["image"] = "different"
        self.assertFalse(PHASE.candidate_images_match(good, candidate))

    def test_reviewed_checkout_requires_exact_clean_source(self):
        with patch.object(PHASE.PAUSE, "command", side_effect=["a" * 40, ""]):
            self.assertTrue(PHASE.checkout_at("a" * 40))
        with patch.object(PHASE.PAUSE, "command", side_effect=["a" * 40, " M compose.yml"]):
            self.assertFalse(PHASE.checkout_at("a" * 40))
        with patch.object(PHASE.PAUSE, "command", side_effect=["b" * 40, ""]):
            self.assertFalse(PHASE.checkout_at("a" * 40))

    def test_preflight_records_only_private_baseline_metadata(self):
        candidate = manifest()
        raw = json.dumps(candidate).encode()
        expected = PHASE.digest(raw)
        current = model("current-backend", "current-frontend", "current-mom")
        proposed = model(candidate["siege"]["api_image"], candidate["siege"]["frontend_image"],
                         candidate["mom"]["image"])
        installed = {name: {"id": character * 64, "image": current["services"][name]["image"]}
                     for name, character in zip(PHASE.SERVICES, "abcde")}
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            stage = project / "evidence" / "release-stages" / "123-1"
            stage.mkdir(parents=True)
            for name in ("compose.yml", "compose.mom.yml", "Caddyfile", "init-databases.sql"):
                (project / name).write_text("reviewed fixture")
            def read(path, owner, limit=65536):
                if path.name == "candidate.json":
                    return raw
                if path.name == "stack.env":
                    return b"COMPOSE_PROJECT_NAME=synthetic\n"
                if path.name == "mom-image.json":
                    return b'{"image":"current-mom"}'
                if path.name in PHASE.RUNTIME_FILES:
                    return b"synthetic-private-runtime-content"
                raise AssertionError(path)
            with patch.object(PHASE.os, "geteuid", return_value=0), patch.object(
                PHASE.GATE, "protected", return_value=True
            ), patch.object(PHASE.GATE, "read_private", side_effect=read), patch.object(
                PHASE.REMOTE, "valid_manifest", return_value=True
            ), patch.object(PHASE, "checkout_at", return_value=True), patch.object(
                PHASE, "reviewed_config", return_value=True
            ), patch.object(PHASE.PREFLIGHT, "private_runtime_files", return_value=True), patch.object(
                PHASE.PREFLIGHT, "deployment_bind_sources_current", return_value=True
            ), patch.object(PHASE.PREFLIGHT, "render", side_effect=[current, proposed]) as render, patch.object(
                PHASE.PREFLIGHT, "read_mom_approval", return_value={"image": "current-mom", "source_commit": "f" * 40}
            ), patch.object(PHASE.GATE, "project_name", return_value="synthetic"), patch.object(
                PHASE.PAUSE, "no_extra_writers", return_value=True
            ), patch.object(
                PHASE, "running_services", return_value=installed
            ), patch.object(PHASE.PREFLIGHT, "validate", return_value={"valid": True}):
                checks, baseline = PHASE.inspect(project, stage, expected)
                render.side_effect = [current, proposed]
                with patch.object(PHASE, "runtime_hashes", side_effect=[
                    {"backend.env": "a" * 64}, {"backend.env": "b" * 64}
                ]):
                    drift_checks, drift_baseline = PHASE.inspect(project, stage, expected)
                render.side_effect = [current, proposed]
                with patch.object(PHASE, "config_hashes", side_effect=[
                    {"Caddyfile": "a" * 64}, {"Caddyfile": "b" * 64}
                ]):
                    bind_checks, bind_baseline = PHASE.inspect(project, stage, expected)
            self.assertTrue(all(checks.values()), checks)
            self.assertFalse(drift_checks["baseline_unchanged"])
            self.assertIsNone(drift_baseline)
            self.assertFalse(bind_checks["baseline_unchanged"])
            self.assertIsNone(bind_baseline)
            self.assertEqual(baseline["manifest_sha256"], expected)
            self.assertEqual(baseline["installed"], installed)
            self.assertNotIn("credential", json.dumps(baseline).lower())
            self.assertNotIn("synthetic-private-runtime-content", json.dumps(baseline))
            self.assertEqual(set(baseline["runtime_sha256"]), set(PHASE.RUNTIME_FILES))
            self.assertEqual(render.call_args_list[0].args[3], {})
            self.assertEqual(render.call_args_list[1].args[3]["MOM_IMAGE"], candidate["mom"]["image"])


if __name__ == "__main__":
    unittest.main()
