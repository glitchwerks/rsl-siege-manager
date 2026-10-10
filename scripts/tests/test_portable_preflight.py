"""Security regressions plus opt-in daemon-free Compose rendering in CI."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "portable-preflight.py"
SPEC = importlib.util.spec_from_file_location("portable_preflight", SCRIPT)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def fixture(directory):
    directory = Path(directory)
    runtime = directory / "runtime"
    runtime.mkdir()
    replacements = {
        "REPLACE_SIEGE_PASSWORD": "synthetic-siege-password-long-enough",
        "REPLACE_MOM_PASSWORD": "synthetic-mom-password-long-enough",
        "REPLACE_SHARED_SIDECAR_KEY": "synthetic-sidecar-key-" + "x" * 32,
        "REPLACE_REVERSE_CALL_KEY": "synthetic-reverse-key-" + "x" * 32,
        "REPLACE_RANDOM_SIGNING_KEY": "synthetic-signing-key-" + "x" * 32,
        "REPLACE_TEST_GUILD": "123456789012345678",
    }
    for name in ("backend", "database", "bundled", "mom"):
        content = (preflight.PORTABLE / f"{name}.env.example").read_text()
        content = content.replace("DISCORD_GUILD_ID=REPLACE\n", "DISCORD_GUILD_ID=123456789012345678\n")
        content = content.replace("REPLACE_URL_ENCODED_PASSWORD",
                                  f"synthetic-{'mom' if name == 'mom' else 'siege'}-password-long-enough")
        for old, new in replacements.items():
            content = content.replace(old, new)
        content = content.replace("REPLACE", "synthetic")
        (runtime / f"{name}.env").write_text(content)
    (runtime / "postgres-admin").write_text("synthetic-admin-password-long-enough")
    stack = (preflight.PORTABLE / "stack.env.example").read_text().replace("REPLACE", "a" * 64)
    (directory / "stack.env").write_text(stack)
    return directory / "stack.env"


def model():
    env = {
        "ENVIRONMENT": "production", "AUTH_DISABLED": "false",
        "SESSION_SECRET": "s" * 32, "DISCORD_BOT_API_KEY": "k" * 32,
        "BOT_SERVICE_TOKEN": "r" * 32, "DISCORD_CLIENT_ID": "123",
        "DISCORD_GUILD_ID": "123456789012345678",
        "DISCORD_CLIENT_SECRET": "synthetic", "DISCORD_BOT_API_URL": "http://bot:8001",
        "DISCORD_REDIRECT_URI": "https://pilot.example.com/api/auth/callback",
        "ALLOWED_ORIGINS": "https://pilot.example.com",
        "DATABASE_URL": "postgresql+asyncpg://siege_app:synthetic-password-long-enough@postgres:5432/siege",
    }
    result = {"services": {}, "networks": {
        "database": {"internal": True},
        "proxy": {"ipam": {"config": [{"subnet": "172.30.60.0/28"}]}},
    }}
    for name in ("backend", "frontend", "proxy", "postgres", "bot", "migrate-siege"):
        result["services"][name] = {
            "image": "registry.example/app@sha256:" + "a" * 64,
            "mem_limit": 134217728, "cpus": 0.5, "pids_limit": 64,
            "logging": {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}},
        }
    services = result["services"]
    services["backend"].update(environment=env, entrypoint=[], command=["--proxy-headers", "--forwarded-allow-ips", "172.30.60.2"])
    services["migrate-siege"].update(environment=copy.deepcopy(env), entrypoint=[], command=["alembic", "upgrade", "head"], profiles=["maintenance"], restart="no")
    services["proxy"].update(environment={"PUBLIC_HOST": "pilot.example.com"},
        networks={"proxy": {"ipv4_address": "172.30.60.2"}}, ports=[
            {"published": "80", "target": 80}, {"published": "443", "target": 443}])
    services["postgres"].update(networks={"database": {}}, environment={
        "SIEGE_DB_PASSWORD": "synthetic-password-long-enough",
        "MOM_DB_PASSWORD": "synthetic-mom-password-long-enough"})
    services["bot"]["environment"] = {"BOT_API_KEY": "k" * 32,
        "DISCORD_TOKEN": "synthetic-test-token", "DISCORD_GUILD_ID": "123456789012345678"}
    return result


def mom_model():
    data = model()
    services = data["services"]
    mom = services.pop("bot")
    env = services["backend"]["environment"]
    env["DISCORD_BOT_API_URL"] = "http://mom:8001"
    services["migrate-siege"]["environment"] = copy.deepcopy(env)
    mom["environment"] = {
        "MOM_BOT_DATABASE_URL": "postgresql+psycopg://mom_app:synthetic-mom-password-long-enough@postgres:5432/mom_bot",
        "MOM_BOT_SECRET_DISCORD_BOT_API_KEY": env["DISCORD_BOT_API_KEY"],
        "MOM_BOT_SECRET_SOURCE": "environment", "MOM_BOT_DATABASE_AUTH": "password",
        "MOM_BOT_ENV": "prod", "MOM_BOT_SECRET_SIEGE_WEB_URL": "http://backend:8000",
        "MOM_BOT_SECRET_DISCORD_TOKEN": "synthetic-test-token",
        "MOM_BOT_SECRET_GUILD_ID": "123456789012345678",
        "MOM_BOT_SECRET_SIEGE_WEB_BOT_TOKEN": env["BOT_SERVICE_TOKEN"],
    }
    services["mom"] = mom
    services["migrate-mom"] = copy.deepcopy(mom)
    services["migrate-mom"].update(profiles=["maintenance"], restart="no", entrypoint=[],
                                command=["/app/.venv/bin/alembic", "upgrade", "head"])
    return data


class SafetyTests(unittest.TestCase):
    def test_rejects_unconfigured_bot_identity_for_both_topologies(self):
        for topology, factory, keys in (
            ("bundled", model, ("DISCORD_TOKEN", "DISCORD_GUILD_ID")),
            ("mom", mom_model, ("MOM_BOT_SECRET_DISCORD_TOKEN", "MOM_BOT_SECRET_GUILD_ID")),
        ):
            bot = "mom" if topology == "mom" else "bot"
            for key in keys:
                for value in (None, "", " ", "REPLACE_TEST_BOT_TOKEN", "REPLACE_TEST_GUILD"):
                    with self.subTest(topology=topology, key=key, value=value):
                        data = factory()
                        data["services"][bot]["environment"][key] = value
                        self.assertFalse(preflight.validate(data, topology)["discord_bot_identity_configured"])
            for value in ("invalid-id", "0", "-1"):
                data = factory()
                data["services"][bot]["environment"][keys[1]] = value
                self.assertFalse(preflight.validate(data, topology)["discord_bot_identity_configured"])
            data = factory()
            data["services"]["backend"]["environment"]["DISCORD_GUILD_ID"] = "987654321"
            self.assertFalse(preflight.validate(data, topology)["discord_bot_identity_configured"])

    def test_valid_configuration(self):
        self.assertTrue(all(preflight.validate(model(), "bundled").values()))
        self.assertTrue(all(preflight.validate(mom_model(), "mom").values()))

    def test_mom_migration_requires_cleared_entrypoint(self):
        for entrypoint in (None, ["/app/migrate.sh"]):
            with self.subTest(entrypoint=entrypoint):
                data = mom_model()
                data["services"]["migrate-mom"]["entrypoint"] = entrypoint
                self.assertFalse(preflight.validate(data, "mom")["mom_migration_clears_entrypoint"])

    def test_rejects_missing_and_placeholder_oauth_credentials(self):
        for key in ("DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET"):
            for value in (None, "", " ", "REPLACE", "replace_client_secret"):
                with self.subTest(key=key, value=value):
                    data = model()
                    data["services"]["backend"]["environment"][key] = value
                    self.assertFalse(preflight.validate(data, "bundled")["oauth_public_origin"])

    def test_rejects_equal_database_passwords_even_when_urls_match(self):
        for topology, data in (("bundled", model()), ("mom", mom_model())):
            with self.subTest(topology=topology):
                password = data["services"]["postgres"]["environment"]["SIEGE_DB_PASSWORD"]
                data["services"]["postgres"]["environment"]["MOM_DB_PASSWORD"] = password
                if topology == "mom":
                    for service in ("mom", "migrate-mom"):
                        data["services"][service]["environment"]["MOM_BOT_DATABASE_URL"] = (
                            f"postgresql+psycopg://mom_app:{password}@postgres:5432/mom_bot")
                checks = preflight.validate(data, topology)
                self.assertTrue(checks["siege_database_credentials_match"])
                if topology == "mom":
                    self.assertTrue(checks["mom_database_credentials_match"])
                self.assertFalse(checks["distinct_application_database_passwords"])
                self.assertNotIn(password, json.dumps(checks))

    def test_rejects_security_regressions(self):
        changes = [
            ("backend", "ports", [{"published": "8000", "target": 8000}]),
            ("postgres", "ports", [{"published": "5432", "target": 5432}]),
            ("bot", "image", "registry.example/app:latest"),
            ("backend", "command", ["--proxy-headers", "--forwarded-allow-ips", "*"]),
            ("backend", "privileged", True),
            ("backend", "entrypoint", ["./entrypoint.sh"]),
        ]
        for service, key, value in changes:
            with self.subTest(service=service, key=key):
                data = model()
                data["services"][service][key] = value
                self.assertFalse(all(preflight.validate(data, "bundled").values()))

    def test_rejects_second_bot(self):
        data = model()
        data["services"]["mom"] = {}
        self.assertFalse(preflight.validate(data, "bundled")["exactly_one_topology"])

    def test_rejects_mismatched_database_password_without_leak(self):
        data = model()
        data["services"]["postgres"]["environment"]["SIEGE_DB_PASSWORD"] = "different-secret-" * 4
        checks = preflight.validate(data, "bundled")
        self.assertFalse(checks["siege_database_credentials_match"])
        self.assertNotIn("different-secret", json.dumps(checks))

    def test_rejects_public_oauth_mismatch(self):
        data = model()
        data["services"]["backend"]["environment"]["DISCORD_REDIRECT_URI"] = "http://pilot.example.com/api/auth/callback"
        self.assertFalse(preflight.validate(data, "bundled")["oauth_public_origin"])


class ReportTests(unittest.TestCase):
    def run_preflight(self, report_path):
        stdout = io.StringIO()
        with patch("sys.argv", ["portable-preflight", "--topology", "bundled",
                               "--stack-env", "unused", "--report", str(report_path)]), \
                patch.object(preflight, "render", return_value=model()), redirect_stdout(stdout):
            status = preflight.main()
        return status, json.loads(stdout.getvalue())

    def test_existing_report_is_preserved_and_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text("earlier evidence")
            status, report = self.run_preflight(path)
            self.assertEqual(status, 1)
            self.assertEqual(report["result"], "STOP")
            self.assertEqual(report["error"], "report_write_failed")
            self.assertEqual(path.read_text(), "earlier evidence")
            self.assertNotIn(str(path), json.dumps(report))

    def test_missing_report_directory_stops_without_creating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing" / "report.json"
            status, report = self.run_preflight(path)
            self.assertEqual(status, 1)
            self.assertFalse(report["checks"]["report_write"])
            self.assertFalse(path.parent.exists())

    def test_permission_error_does_not_expose_os_diagnostics(self):
        with patch.object(preflight.os, "open", side_effect=PermissionError("private diagnostic")):
            status, report = self.run_preflight("unused")
        self.assertEqual(status, 1)
        self.assertEqual(report["result"], "STOP")
        self.assertNotIn("private diagnostic", json.dumps(report))

    def test_new_report_is_private_and_matches_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            status, report = self.run_preflight(path)
            self.assertEqual(status, 0)
            self.assertEqual(report, json.loads(path.read_text()))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


@unittest.skipUnless(os.environ.get("PORTABLE_COMPOSE_TEST") == "1", "Compose rendering runs in CI")
class ComposeTests(unittest.TestCase):
    def test_both_actual_topologies_render_and_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            stack = fixture(directory)
            for topology in ("bundled", "mom"):
                with self.subTest(topology=topology):
                    data = preflight.render(topology, stack, Path(directory))
                    checks = preflight.validate(data, topology)
                    self.assertTrue(all(checks.values()), [k for k, v in checks.items() if not v])


if __name__ == "__main__":
    unittest.main()
