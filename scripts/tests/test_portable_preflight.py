"""Security regressions plus opt-in daemon-free Compose rendering in CI."""
import copy
import importlib.util
import io
import json
import os
import subprocess
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
    for name in ("Caddyfile", "init-databases.sql"):
        (directory / name).write_bytes((preflight.PORTABLE / name).read_bytes())
    runtime = directory / "runtime"
    runtime.mkdir(mode=0o700)
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
        content = content.replace("siege-pilot.example.com", "siege-ci.fixture-domain.net")
        (runtime / f"{name}.env").write_text(content)
        (runtime / f"{name}.env").chmod(0o600)
    (runtime / "postgres-admin").write_text("synthetic-admin-password-long-enough")
    (runtime / "postgres-admin").chmod(0o600)
    stack = (preflight.PORTABLE / "stack.env.example").read_text().replace("REPLACE", "a" * 64)
    stack = stack.replace("siege-pilot.example.com", "siege-ci.fixture-domain.net")
    (directory / "stack.env").write_text(stack)
    (directory / "stack.env").chmod(0o600)
    return directory / "stack.env"


def model():
    env = {
        "ENVIRONMENT": "production", "AUTH_DISABLED": "false",
        "SESSION_SECRET": "s" * 32, "DISCORD_BOT_API_KEY": "k" * 32,
        "BOT_SERVICE_TOKEN": "r" * 32, "DISCORD_CLIENT_ID": "123",
        "DISCORD_GUILD_ID": "123456789012345678",
        "DISCORD_CLIENT_SECRET": "synthetic", "DISCORD_BOT_API_URL": "http://bot:8001",
        "DISCORD_REDIRECT_URI": "https://pilot.fixture-domain.net/api/auth/callback",
        "ALLOWED_ORIGINS": "https://pilot.fixture-domain.net",
        "DATABASE_URL": "postgresql+asyncpg://siege_app:synthetic-password-long-enough@postgres:5432/siege",
    }
    result = {"services": {}, "networks": {
        "database": {"internal": True},
        "proxy": {"ipam": {"config": [{"subnet": "172.30.60.0/28", "ip_range": "172.30.60.8/29"}]}},
    }}
    for name in ("backend", "frontend", "proxy", "postgres", "bot", "migrate-siege"):
        result["services"][name] = {
            "image": "registry.example/app@sha256:" + "a" * 64,
            "mem_limit": 134217728, "cpus": 0.5, "pids_limit": 64,
            "logging": {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}},
        }
    services = result["services"]
    services["backend"]["networks"] = {"application": {}, "database": {}, "proxy": {"aliases": ["api-proxy"]}}
    services["backend"].update(environment=env, entrypoint=[], command=["--proxy-headers", "--forwarded-allow-ips", "172.30.60.2"])
    services["migrate-siege"].update(environment=copy.deepcopy(env), entrypoint=[], command=["alembic", "upgrade", "head"], profiles=["maintenance"], restart="no")
    services["proxy"].update(environment={"PUBLIC_HOST": "pilot.fixture-domain.net",
        "ROLE_SYNC_UPSTREAM": "api-proxy:8000", "ROLE_SYNC_PATH": "/api/internal/role-sync"},
        networks={"application": {}, "proxy": {"ipv4_address": "172.30.60.2"}}, ports=[
            {"published": "80", "target": 80}, {"published": "443", "target": 443}])
    services["postgres"].update(networks={"database": {}}, environment={
        "SIEGE_DB_PASSWORD": "synthetic-password-long-enough",
        "MOM_DB_PASSWORD": "synthetic-mom-password-long-enough",
        "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres-admin"},
        secrets=[{"source": "postgres-admin", "target": "postgres-admin"}])
    result["secrets"] = {"postgres-admin": {"file": "/synthetic/postgres-admin"}}
    services["bot"]["networks"] = {"application": {}}
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
    services["proxy"]["environment"].update(ROLE_SYNC_UPSTREAM="mom:8001",
        ROLE_SYNC_PATH="/api/internal/role-sync")
    services["mom"] = mom
    services["migrate-mom"] = copy.deepcopy(mom)
    services["migrate-mom"].update(profiles=["maintenance"], restart="no", entrypoint=[],
                                command=["/app/.venv/bin/alembic", "upgrade", "head"])
    return data


class SafetyTests(unittest.TestCase):
    def setUp(self):
        reader = patch.object(preflight, "read_secret_file", return_value="synthetic-admin-password-long-enough")
        reader.start()
        self.addCleanup(reader.stop)

    def test_role_sync_requires_public_https_and_connected_selected_receiver(self):
        for topology, factory in (("bundled", model), ("mom", mom_model)):
            data = factory()
            env = data["services"]["backend"]["environment"]
            env["DAY_ROLE_SYNC_ENABLED"] = "true"
            valid = "https://pilot.fixture-domain.net/api/internal/role-sync"
            for url in (None, "", "http://mom:8001/api/internal/role-sync",
                        "https://other.fixture-domain.net/api/internal/role-sync",
                        "https://pilot.fixture-domain.net/api/role-sync", valid):
                with self.subTest(topology=topology, url=url):
                    env["DAY_ROLE_SYNC_URL"] = url
                    checks = preflight.validate(data, topology)
                    self.assertEqual(checks["role_sync_https_configuration"],
                                     topology == "mom" and url == valid)
                    self.assertTrue(checks["role_sync_receiver_route"])
            env["DAY_ROLE_SYNC_ENABLED"] = "typo"
            self.assertFalse(preflight.validate(data, topology)["role_sync_https_configuration"])
            for field, value in (("ROLE_SYNC_UPSTREAM", "other:8001"),
                                 ("ROLE_SYNC_PATH", "/api/members")):
                bad = factory()
                bad["services"]["proxy"]["environment"][field] = value
                self.assertFalse(preflight.validate(bad, topology)["role_sync_receiver_route"])
            for name in ("proxy", "mom" if topology == "mom" else "bot"):
                bad = factory()
                bad["services"][name]["networks"].pop("application")
                self.assertFalse(preflight.validate(bad, topology)["role_sync_receiver_route"])

    def test_dynamic_proxy_range_cannot_allocate_fixed_proxy_address(self):
        for topology, factory in (("bundled", model), ("mom", mom_model)):
            for dynamic in (None, "172.30.60.0/28", "172.30.60.0/29",
                            "172.30.61.0/29", "172.30.60.8/32", "invalid", "172.30.60.8/29"):
                data = factory()
                config = data["networks"]["proxy"]["ipam"]["config"][0]
                if dynamic is None:
                    config.pop("ip_range")
                else:
                    config["ip_range"] = dynamic
                self.assertEqual(preflight.validate(data, topology)["trust_only_fixed_proxy"],
                                 dynamic == "172.30.60.8/29")

    def test_database_placeholder_case_variants_are_rejected_with_matching_urls(self):
        for topology, factory in (("bundled", model), ("mom", mom_model)):
            for key in ("SIEGE_DB_PASSWORD", "MOM_DB_PASSWORD"):
                for password in ("replace_application_password_long_enough",
                                 "RePlAcE_application_password_long_enough"):
                    data = factory()
                    database_env = data["services"]["postgres"]["environment"]
                    original = database_env[key]
                    database_env[key] = password
                    if key == "SIEGE_DB_PASSWORD":
                        env = data["services"]["backend"]["environment"]
                        env["DATABASE_URL"] = env["DATABASE_URL"].replace(original, password)
                        check = "siege_database_credentials_match"
                    elif topology == "mom":
                        env = data["services"]["mom"]["environment"]
                        env["MOM_BOT_DATABASE_URL"] = env["MOM_BOT_DATABASE_URL"].replace(original, password)
                        check = "mom_database_credentials_match"
                    else:
                        check = "distinct_application_database_passwords"
                    self.assertFalse(preflight.validate(data, topology)[check])

    def test_runtime_session_placeholders_are_rejected(self):
        for topology, factory in (("bundled", model), ("mom", mom_model)):
            for value in ("changeme-use-a-long-random-string-in-production",
                          "CHANGEME-use-a-long-random-string-in-production", " " * 40):
                data = factory()
                data["services"]["backend"]["environment"]["SESSION_SECRET"] = value
                self.assertFalse(preflight.validate(data, topology)["secure_auth_configuration"])

    def test_supplied_day_role_ids_are_numeric_snowflakes_even_when_sync_disabled(self):
        for topology, factory in (("bundled", model), ("mom", mom_model)):
            for key in ("DISCORD_DAY_1_ROLE_ID", "DISCORD_DAY_2_ROLE_ID"):
                for value in (None, "", "abc", "-1", "0", "123", "0" * 17, str(2**64),
                              "123456789012345678"):
                    with self.subTest(topology=topology, key=key, value=value):
                        data = factory()
                        data["services"]["backend"]["environment"][key] = value
                        self.assertEqual(preflight.validate(data, topology)["optional_day_role_ids_valid"],
                                         value == "123456789012345678")

    def test_rejects_ambiguous_proxy_alias(self):
        for topology, factory in (("bundled", model), ("mom", mom_model)):
            for name, network in (("backend", "application"), ("migrate-siege", "proxy")):
                data = factory()
                data["services"][name].setdefault("networks", {})[network] = {"aliases": ["api-proxy"]}
                self.assertFalse(preflight.validate(data, topology)["backend_proxy_alias_unambiguous"])
            data = factory()
            data["services"]["backend"]["networks"]["proxy"] = {}
            self.assertFalse(preflight.validate(data, topology)["backend_proxy_alias_unambiguous"])

    def test_rejects_reserved_hostnames_even_with_matching_oauth(self):
        for host in ("siege-pilot.example.com", "EXAMPLE.NET", "pilot.example.org",
                     "site.example", "site.invalid", "site.test", "site.localhost",
                     "127.0.0.1", "broken..hostname.net", "-bad.hostname.net"):
            with self.subTest(host=host):
                data = model()
                data["services"]["proxy"]["environment"]["PUBLIC_HOST"] = host
                env = data["services"]["backend"]["environment"]
                env["DISCORD_REDIRECT_URI"] = f"https://{host}/api/auth/callback"
                env["ALLOWED_ORIGINS"] = f"https://{host}"
                checks = preflight.validate(data, "bundled")
                self.assertTrue(checks["oauth_public_origin"])
                self.assertFalse(checks["public_hostname"])

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

    def test_rejects_reused_directional_or_session_keys(self):
        for topology, factory in (("bundled", model), ("mom", mom_model)):
            for first, second in (("DISCORD_BOT_API_KEY", "BOT_SERVICE_TOKEN"),
                                  ("SESSION_SECRET", "DISCORD_BOT_API_KEY"),
                                  ("SESSION_SECRET", "BOT_SERVICE_TOKEN")):
                with self.subTest(topology=topology, first=first, second=second):
                    data = factory()
                    env = data["services"]["backend"]["environment"]
                    env[second] = env[first]
                    data["services"]["migrate-siege"]["environment"] = copy.deepcopy(env)
                    if topology == "bundled":
                        data["services"]["bot"]["environment"]["BOT_API_KEY"] = env["DISCORD_BOT_API_KEY"]
                    else:
                        for service in ("mom", "migrate-mom"):
                            bot_env = data["services"][service]["environment"]
                            bot_env["MOM_BOT_SECRET_DISCORD_BOT_API_KEY"] = env["DISCORD_BOT_API_KEY"]
                            bot_env["MOM_BOT_SECRET_SIEGE_WEB_BOT_TOKEN"] = env["BOT_SERVICE_TOKEN"]
                    checks = preflight.validate(data, topology)
                    self.assertTrue(checks["secure_auth_configuration"])
                    self.assertTrue(checks["sidecar_auth_matches"])
                    self.assertFalse(checks["authentication_keys_distinct"])

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
    def setUp(self):
        guard = patch.object(preflight, "private_runtime_files", return_value=True)
        guard.start()
        self.addCleanup(guard.stop)
        binds = patch.object(preflight, "deployment_bind_sources_current", return_value=True)
        binds.start()
        self.addCleanup(binds.stop)
        reader = patch.object(preflight, "read_secret_file", return_value="synthetic-admin-password-long-enough")
        reader.start()
        self.addCleanup(reader.stop)

    def run_preflight(self, report_path):
        stdout = io.StringIO()
        with patch("sys.argv", ["portable-preflight", "--topology", "bundled",
                               "--stack-env", "unused", "--report", str(report_path)]), \
                patch.object(preflight, "render", return_value=model()), redirect_stdout(stdout):
            status = preflight.main()
        return status, json.loads(stdout.getvalue())

    def test_report_directory_must_be_private_owned_and_not_symlinked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for mode in (0o777, 0o770, 0o755):
                root.chmod(mode)
                status, report = self.run_preflight(root / "report.json")
                self.assertEqual(status, 1)
                self.assertFalse(report["checks"]["report_directory_private"])
                self.assertFalse((root / "report.json").exists())
            root.chmod(0o700)
            link = root / "linked"
            link.symlink_to(root, target_is_directory=True)
            self.assertFalse(preflight.private_report_directory(link))
            metadata = root.stat()
            with patch.object(Path, "lstat") as mocked:
                mocked.return_value = type("Metadata", (), {
                    "st_mode": metadata.st_mode, "st_uid": os.geteuid() + 1})()
                self.assertFalse(preflight.private_report_directory(root))

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


class RuntimePermissionTests(unittest.TestCase):
    def test_runtime_permissions_reject_exposed_or_nonregular_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stack = fixture(root)
            for topology in ("mom", "bundled"):
                self.assertTrue(preflight.private_runtime_files(topology, stack, root))
                for path in (root / "runtime", stack, root / "runtime/backend.env",
                             root / "runtime/database.env", root / f"runtime/{topology}.env"):
                    mode = path.stat().st_mode & 0o777
                    path.chmod(0o755 if path.is_dir() else 0o644)
                    self.assertFalse(preflight.private_runtime_files(topology, stack, root))
                    path.chmod(mode)
            path = root / "runtime/backend.env"
            target = root / "private.env"
            path.rename(target)
            path.symlink_to(target)
            self.assertFalse(preflight.private_runtime_files("mom", stack, root))

    def test_runtime_paths_must_be_owned_by_invoking_user(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stack = fixture(root)
            original = Path.lstat
            for path in (root, root / "runtime", stack, root / "runtime/backend.env",
                         root / "runtime/database.env", root / "runtime/mom.env",
                         root / "runtime/postgres-admin"):
                def metadata(candidate):
                    info = original(candidate)
                    if candidate == path:
                        return type("Metadata", (), {"st_mode": info.st_mode, "st_uid": os.geteuid() + 1})()
                    return info
                with patch.object(Path, "lstat", metadata):
                    self.assertFalse(preflight.private_runtime_files("mom", stack, root))

    def test_missing_stale_or_nonregular_bind_sources_stop_before_render(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stack = fixture(root)
            for name in ("Caddyfile", "init-databases.sql"):
                path = root / name
                content = path.read_bytes()
                for invalid in ("missing", "directory", "stale", "symlink"):
                    with self.subTest(name=name, invalid=invalid):
                        path.unlink()
                        if invalid == "directory":
                            path.mkdir()
                        elif invalid == "stale":
                            path.write_text("unreviewed configuration")
                        elif invalid == "symlink":
                            path.symlink_to(preflight.PORTABLE / name)
                        with patch("sys.argv", ["preflight", "--topology", "mom", "--stack-env", str(stack),
                                "--project-directory", str(root), "--report", str(root / f"{name}-{invalid}.json")]), \
                                patch.object(preflight, "render") as render, redirect_stdout(io.StringIO()) as output:
                            self.assertEqual(preflight.main(), 1)
                            render.assert_not_called()
                            self.assertFalse(json.loads(output.getvalue())["checks"]["deployment_bind_sources_current"])
                        if path.is_dir():
                            path.rmdir()
                        elif path.is_symlink() or path.exists():
                            path.unlink()
                        path.write_bytes(content)
                self.assertTrue(preflight.deployment_bind_sources_current(root))

    def test_exposed_credentials_stop_before_render(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stack = fixture(root)
            (root / "runtime/backend.env").chmod(0o644)
            with patch("sys.argv", ["preflight", "--topology", "mom", "--stack-env", str(stack),
                    "--project-directory", str(root), "--report", str(root / "report.json")]), \
                    patch.object(preflight, "render") as render, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(preflight.main(), 1)
                render.assert_not_called()
                self.assertFalse(json.loads(output.getvalue())["checks"]["runtime_credentials_private"])


class AdministratorSecretTests(unittest.TestCase):
    def test_admin_file_must_be_usable_and_separate_without_disclosure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "postgres-admin"
            data = model()
            data["secrets"]["postgres-admin"]["file"] = str(path)
            self.assertFalse(preflight.validate(data, "bundled")["postgres_admin_secret_valid"])
            for value, valid in (("", False), (" \n", False), ("REPLACE_ADMIN_PASSWORD", False),
                                 ("synthetic-password-long-enough", False),
                                 ("synthetic-mom-password-long-enough", False),
                                 ("unique-private-admin-password\n", True)):
                with self.subTest(valid=valid):
                    path.write_text(value)
                    path.chmod(0o600)
                    checks = preflight.validate(data, "bundled")
                    self.assertEqual(checks["postgres_admin_secret_valid"], valid)
                    if value.strip():
                        self.assertNotIn(value.strip(), json.dumps(checks))
            path.chmod(0o644)
            self.assertFalse(preflight.validate(data, "bundled")["postgres_admin_secret_valid"])

    def test_admin_file_reader_rejects_directory_symlink_and_oversize(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "admin"
            path.write_text("x" * 4097)
            path.chmod(0o600)
            link = Path(directory) / "link"
            link.symlink_to(path)
            for invalid in (Path(directory), path, link):
                self.assertIsNone(preflight.read_secret_file(invalid))


@unittest.skipUnless(os.environ.get("PORTABLE_COMPOSE_TEST") == "1", "Compose rendering runs in CI")
class ComposeTests(unittest.TestCase):
    def test_caddy_routes_only_role_sync_post_before_backend(self):
        for bot, path in (("mom", "/api/internal/role-sync"), ("api-proxy", "/api/internal/role-sync")):
            with self.subTest(bot=bot):
                result = subprocess.run([
                    "docker", "run", "--rm", "--network", "none",
                    "-e", "PUBLIC_HOST=pilot.fixture-domain.net",
                    "-e", "ACME_EMAIL=ci@fixture-domain.net",
                    "-e", f"ROLE_SYNC_UPSTREAM={bot}:{8001 if bot == 'mom' else 8000}", "-e", f"ROLE_SYNC_PATH={path}",
                    "-v", f"{preflight.PORTABLE / 'Caddyfile'}:/etc/caddy/Caddyfile:ro",
                    "caddy:2", "caddy", "adapt", "--config", "/etc/caddy/Caddyfile"],
                    capture_output=True, text=True, check=True, timeout=120)
                config = json.loads(result.stdout)
                def routes(value):
                    if isinstance(value, dict):
                        if "routes" in value:
                            yield value["routes"]
                        for child in value.values():
                            yield from routes(child)
                    elif isinstance(value, list):
                        for child in value:
                            yield from routes(child)
                ordered = next(group for group in routes(config) if len(group) == 3
                               and group[0].get("match") == [{
                                   "method": ["POST"], "path": ["/api/internal/role-sync"]}])
                handlers = [handler for route in ordered[0]["handle"][0]["routes"]
                            for handler in route["handle"]]
                self.assertEqual(handlers[0], {"handler": "rewrite", "uri": path})
                self.assertEqual(handlers[1]["upstreams"], [{"dial": f"{bot}:{8001 if bot == 'mom' else 8000}"}])
                self.assertIn("api-proxy:8000", json.dumps(ordered[1]))
                # No other route reaches either bot's sidecar.
                all_config = json.dumps(config)
                self.assertEqual(all_config.count("mom:8001"), 1 if bot == "mom" else 0)
                self.assertNotIn("bot:8001", all_config)

    def test_both_actual_topologies_render_and_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            stack = fixture(directory)
            for topology in ("bundled", "mom"):
                with self.subTest(topology=topology):
                    self.assertTrue(preflight.private_runtime_files(topology, stack, Path(directory)))
                    self.assertTrue(preflight.deployment_bind_sources_current(Path(directory)))
                    backend_file = Path(directory) / "runtime/backend.env"
                    content = backend_file.read_text()
                    if topology == "mom":
                        content = content.replace("DAY_ROLE_SYNC_ENABLED=false", "DAY_ROLE_SYNC_ENABLED=true")
                        content = content.replace("# DAY_ROLE_SYNC_URL=", "DAY_ROLE_SYNC_URL=")
                        backend_file.write_text(content)
                    data = preflight.render(topology, stack, Path(directory))
                    checks = preflight.validate(data, topology)
                    self.assertTrue(all(checks.values()), [k for k, v in checks.items() if not v])


if __name__ == "__main__":
    unittest.main()
