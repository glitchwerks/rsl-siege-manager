"""Disposable PostgreSQL rehearsal of the post-pause revision query."""

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-release-migration-ready.py"
SPEC = importlib.util.spec_from_file_location("migration_ready_docker", SCRIPT)
READY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(READY)


@unittest.skipUnless(os.environ.get("VPS_WRITER_DOCKER_TEST") == "1", "disposable CI daemon only")
class DisposableMigrationRevisionTests(unittest.TestCase):
    def test_postgres_16_reads_exact_revisions_through_secret(self):
        self.assertEqual(os.geteuid(), 0)
        identifier = None
        with tempfile.TemporaryDirectory() as temporary:
            secret = Path(temporary) / "postgres-admin"
            secret.write_text("disposable-ci-" + uuid.uuid4().hex)
            secret.chmod(0o444)
            try:
                launched = subprocess.run([
                    "docker", "run", "--detach", "--network", "none",
                    "--name", "migration-ci-" + uuid.uuid4().hex[:16],
                    "--env", "POSTGRES_PASSWORD_FILE=/run/secrets/postgres-admin",
                    "--env", "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256",
                    "--volume", f"{secret}:/run/secrets/postgres-admin:ro",
                    "postgres:16-alpine",
                ], check=True, capture_output=True, text=True, timeout=120)
                identifier = launched.stdout.strip()
                for _ in range(45):
                    ready = subprocess.run(["docker", "exec", identifier, "pg_isready",
                                            "-h", "127.0.0.1", "-U", "postgres"],
                                           capture_output=True, timeout=10, check=False)
                    if ready.returncode == 0:
                        break
                    time.sleep(1)
                else:
                    self.fail("disposable PostgreSQL did not become ready")
                def sql(database, statement):
                    result = subprocess.run(["docker", "exec", "--user", "0", identifier,
                        "sh", "-eu", "-c", READY.PSQL, "fixture", database, statement],
                        capture_output=True, text=True, timeout=30, check=False)
                    self.assertEqual(result.returncode, 0, "disposable SQL setup failed")
                for database, revision in (("siege", "0012"), ("mom_bot", "b6_member")):
                    sql("postgres", f"CREATE DATABASE {database};")
                    sql(database, "CREATE TABLE public.alembic_version (version_num varchar(64) NOT NULL);")
                    sql(database, f"INSERT INTO public.alembic_version (version_num) VALUES ('{revision}');")
                self.assertEqual(READY.current_revision(identifier, "siege"), "0012")
                self.assertEqual(READY.current_revision(identifier, "mom_bot"), "b6_member")
                sql("mom_bot", "INSERT INTO public.alembic_version (version_num) VALUES ('extra');")
                self.assertIsNone(READY.current_revision(identifier, "mom_bot"))
            finally:
                if identifier:
                    subprocess.run(["docker", "rm", "--force", identifier],
                                   capture_output=True, timeout=30, check=False)


if __name__ == "__main__":
    unittest.main()
