"""Security regressions plus opt-in daemon-free Compose rendering in CI."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "portable-preflight.py"
SPEC = importlib.util.spec_from_file_location("portable_preflight", SCRIPT)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def fixture(directory):
    directory = Path(directory)
    runtime = directory / "runtime"
    runtime.mkdir()
    replacements = {
        "REPLACE_URL_ENCODED_PASSWORD": "synthetic-password-long-enough",
        "REPLACE_SIEGE_PASSWORD": "synthetic-password-long-enough",
        "REPLACE_MOM_PASSWORD": "synthetic-password-long-enough",
        "REPLACE_SHARED_SIDECAR_KEY": "synthetic-sidecar-key-" + "x" * 32,
        "REPLACE_REVERSE_CALL_KEY": "synthetic-reverse-key-" + "x" * 32,
        "REPLACE_RANDOM_SIGNING_KEY": "synthetic-signing-key-" + "x" * 32,
    }
    for name in ("backend", "database", "bundled", "mom"):
        content = (preflight.PORTABLE / f"{name}.env.example").read_text()
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
    services["backend"].update(environment=env, command=["--proxy-headers", "--forwarded-allow-ips", "172.30.60.2"])
    services["migrate-siege"].update(environment=copy.deepcopy(env), profiles=["maintenance"], restart="no")
    services["proxy"].update(environment={"PUBLIC_HOST": "pilot.example.com"},
        networks={"proxy": {"ipv4_address": "172.30.60.2"}}, ports=[
            {"published": "80", "target": 80}, {"published": "443", "target": 443}])
    services["postgres"].update(networks={"database": {}}, environment={
        "SIEGE_DB_PASSWORD": "synthetic-password-long-enough"})
    services["bot"]["environment"] = {"BOT_API_KEY": "k" * 32}
    return result


class SafetyTests(unittest.TestCase):
    def test_valid_configuration(self):
        self.assertTrue(all(preflight.validate(model(), "bundled").values()))

    def test_rejects_security_regressions(self):
        changes = [
            ("backend", "ports", [{"published": "8000", "target": 8000}]),
            ("postgres", "ports", [{"published": "5432", "target": 5432}]),
            ("bot", "image", "registry.example/app:latest"),
            ("backend", "command", ["--proxy-headers", "--forwarded-allow-ips", "*"]),
            ("backend", "privileged", True),
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
