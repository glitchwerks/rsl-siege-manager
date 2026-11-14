"""Disposable PostgreSQL proof of least-privilege database signal queries."""

import importlib.util
import os
from pathlib import Path
import subprocess
import time
import unittest
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-database-signal.py"
SPEC = importlib.util.spec_from_file_location("database_signal_docker", SCRIPT)
SIGNAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SIGNAL)


@unittest.skipUnless(os.environ.get("VPS_WRITER_DOCKER_TEST") == "1", "disposable CI daemon only")
class DisposableDatabaseSignalTests(unittest.TestCase):
    def test_both_app_roles_need_valid_passwords_and_expected_tables(self):
        self.assertEqual(os.geteuid(), 0)
        admin = "ci-" + uuid.uuid4().hex
        siege_password = "ci-" + uuid.uuid4().hex
        mom_password = "ci-" + uuid.uuid4().hex
        identifier = None
        try:
            launched = subprocess.run([
                "docker", "run", "--detach", "--network", "none",
                "--name", "database-signal-ci-" + uuid.uuid4().hex[:16],
                "--env", "POSTGRES_PASSWORD=" + admin,
                "--env", "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256",
                "--env", "SIEGE_DB_PASSWORD=" + siege_password,
                "--env", "MOM_DB_PASSWORD=" + mom_password,
                "postgres:16-alpine"], capture_output=True, text=True, check=True, timeout=120)
            identifier = launched.stdout.strip()
            for _ in range(45):
                ready = subprocess.run(["docker", "exec", identifier, "pg_isready", "-h",
                    "127.0.0.1", "-U", "postgres"], capture_output=True, timeout=10, check=False)
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                self.fail("disposable PostgreSQL did not become ready")

            def setup(database, sql):
                result = subprocess.run(["docker", "exec", "-i", "--user", "postgres",
                    identifier, "psql", "--no-psqlrc", "--set", "ON_ERROR_STOP=1",
                    "--dbname", database], input=sql, capture_output=True, text=True,
                    timeout=30, check=False)
                self.assertEqual(result.returncode, 0, "disposable setup failed")

            setup("postgres", f"CREATE ROLE siege_app LOGIN PASSWORD '{siege_password}';")
            setup("postgres", f"CREATE ROLE mom_app LOGIN PASSWORD '{mom_password}';")
            setup("postgres", "CREATE DATABASE siege OWNER siege_app;")
            setup("postgres", "CREATE DATABASE mom_bot OWNER mom_app;")
            setup("siege", "SET ROLE siege_app; CREATE TABLE public.alembic_version (version_num varchar(64)); "
                  "INSERT INTO public.alembic_version VALUES ('0012'); CREATE TABLE public.member (id integer);")
            setup("mom_bot", "SET ROLE mom_app; CREATE TABLE public.alembic_version (version_num varchar(64)); "
                  "INSERT INTO public.alembic_version VALUES ('b6_member'); CREATE TABLE public.reminders (id integer);")
            deadline = time.monotonic() + 60
            self.assertEqual(SIGNAL.database(identifier, "siege", deadline)["revision"], "0012")
            self.assertEqual(SIGNAL.database(identifier, "mom_bot", deadline)["revision"], "b6_member")
            # Both unprivileged roles must observe the same cluster-wide pressure.
            for name in ("siege", "mom_bot"):
                sleeper = subprocess.run(["docker", "exec", "--detach", "--user", "postgres",
                    identifier, "sh", "-eu", "-c", SIGNAL.PSQL, "database-signal", name,
                    "SELECT pg_sleep(12);"], capture_output=True, timeout=20, check=False)
                self.assertEqual(sleeper.returncode, 0, "disposable sleeper failed to start")
            time.sleep(1)
            siege = SIGNAL.database(identifier, "siege", time.monotonic() + 30)
            mom = SIGNAL.database(identifier, "mom_bot", time.monotonic() + 30)
            self.assertGreaterEqual(siege["connections"], 3)
            self.assertGreaterEqual(mom["connections"], 3)
            setup("postgres", "ALTER ROLE siege_app PASSWORD 'no-longer-matches-container';")
            self.assertIsNone(SIGNAL.database(identifier, "siege", time.monotonic() + 30))
        finally:
            if identifier:
                subprocess.run(["docker", "rm", "--force", identifier],
                               capture_output=True, timeout=30, check=False)


if __name__ == "__main__":
    unittest.main()
