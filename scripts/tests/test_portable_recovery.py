"""Fail-closed unit checks and opt-in disposable PostgreSQL recovery rehearsal."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout, nullcontext
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("recovery", Path(__file__).parents[1] / "portable-recovery.py")
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


def files(root, dbnames=None, port="5432"):
    names = dbnames or {"siege": "siege_source", "mom": "mom_source"}
    services = root / "services.conf"
    services.write_text("".join(f"[{app}]\nhost=127.0.0.1\nport={port}\ndbname={names[app]}\n"
                              f"user={role}\nsslmode=disable\n" for app, role in recovery.APPS.items()))
    password = root / "pgpass"
    password.write_text("127.0.0.1:*:*:*:synthetic-password\n")
    for path in (services, password):
        path.chmod(0o600)
    return services, password


def bundle(root):
    directory = root / "bundle"
    directory.mkdir(mode=0o700)
    manifest = {"schema": 1, "postgres_major": 16, "bundle_id": str(uuid.uuid4()),
                "created_at": "2026-10-10T00:00:00+00:00", "databases": {}}
    for app in recovery.APPS:
        path = directory / f"{app}.dump"
        path.write_bytes(b"PGDMPsynthetic-archive-private-content")
        path.chmod(0o600)
        manifest["databases"][app] = {"sha256": recovery.digest(path), "bytes": path.stat().st_size,
                                      "controls": [{"schema": "public", "table": "private_table", "rows": 2}]}
    recovery.write_json(directory / "manifest.json", manifest)
    return directory, manifest


class SafetyTests(unittest.TestCase):
    def setUp(self):
        listing = patch.object(recovery, "archive_readable")
        listing.start()
        self.addCleanup(listing.stop)

    def run_phase(self, argv):
        with redirect_stdout(io.StringIO()) as output:
            status = recovery.main(argv)
        return status, json.loads(output.getvalue())

    def test_confirmations_and_connection_files_required_before_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(recovery, "Client") as client:
                status, report = self.run_phase(["backup", "--bundle", str(root / "new"),
                    "--report", str(root / "report.json")])
                self.assertEqual(status, 1)
                self.assertEqual(report["error"], "backup_confirmation_required")
                client.assert_not_called()
                self.assertFalse((root / "new").exists())
            source, _ = bundle(root)
            with patch.object(recovery, "restore") as restore:
                status, report = self.run_phase(["restore", "--bundle", str(source),
                    "--report", str(root / "restore.json")])
                self.assertEqual(status, 1)
                self.assertEqual(report["error"], "isolated_restore_confirmation_required")
                restore.assert_not_called()

    def test_existing_or_unsafe_report_prevents_database_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "report.json"
            report.write_text("preserve me")
            with patch.object(recovery, "backup") as backup:
                status, _ = self.run_phase(["backup", "--confirm-backup", "--bundle", str(root / "new"),
                                          "--report", str(report)])
                self.assertEqual(status, 1)
                backup.assert_not_called()
            self.assertEqual(report.read_text(), "preserve me")
            root.chmod(0o777)
            status, result = self.run_phase(["verify", "--bundle", str(root / "new"),
                                            "--report", str(root / "unsafe.json")])
            self.assertEqual(status, 1)
            self.assertEqual(result["error"], "report_write_failed")
            self.assertFalse((root / "unsafe.json").exists())
            root.chmod(0o700)

    def test_private_files_reject_symlink_modes_and_foreign_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "private"
            path.write_text("private-credential")
            path.chmod(0o644)
            with self.assertRaises(recovery.Stop):
                recovery.read_private(path)
            path.chmod(0o600)
            link = root / "link"
            link.symlink_to(path)
            with self.assertRaises(recovery.Stop):
                recovery.read_private(link)
            metadata = path.stat()
            with patch.object(Path, "lstat", return_value=type("Metadata", (), {
                    "st_mode": metadata.st_mode, "st_uid": os.geteuid() + 1})()):
                with self.assertRaises(recovery.Stop):
                    recovery.private_path(path)

    def test_service_file_rejects_overrides_and_ambient_postgres_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            services, passwords = files(root)
            with patch.dict(os.environ, {"PGHOST": "unapproved", "PGDATABASE": "live", "PGPASSWORD": "secret"}):
                client = recovery.Client(services, passwords)
            self.assertNotIn("PGHOST", client.env)
            self.assertNotIn("PGPASSWORD", client.env)
            self.assertNotIn("PGDATABASE", client.env)
            original = services.read_text()
            for extra in ("password=secret", "options=-c role=postgres", "service=other"):
                services.write_text(original + extra + "\n")
                with self.assertRaises(recovery.Stop):
                    recovery.Client(services, passwords)

    def test_corrupt_bundle_stops_before_any_restore_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = bundle(root)
            (source / "mom.dump").write_bytes(b"PGDMPchanged")
            with patch.object(recovery, "Client") as client:
                status, report = self.run_phase(["restore", "--bundle", str(source),
                    "--report", str(root / "report.json"), "--confirm-isolated-restore", "--run-id", str(uuid.uuid4())])
                self.assertEqual(status, 1)
                self.assertEqual(report["error"], "archive_integrity_failed")
                client.assert_not_called()

    def test_restore_checks_both_targets_before_first_write(self):
        run_id = uuid.uuid4()
        client = unittest.mock.Mock()
        client.settings = {app: {"dbname": f"rsl_restore_{run_id.hex}_{app}"} for app in recovery.APPS}
        client.identity.side_effect = [{"user": "siege_app", "superuser": False, "createdb": False, "createrole": False},
                                       {"user": "mom_app", "superuser": False, "createdb": False, "createrole": False}]
        client.empty_target.side_effect = [None, recovery.Stop("restore_target_not_empty")]
        with self.assertRaises(recovery.Stop):
            recovery.restore(client, Path("unused"), run_id, {}, {})
        client.run.assert_not_called()

    def test_live_name_or_privileged_role_cannot_restore(self):
        run_id = uuid.uuid4()
        for name, privileged in (("siege", False), (f"rsl_restore_{run_id.hex}_siege", True)):
            client = unittest.mock.Mock()
            client.settings = {"siege": {"dbname": name}}
            client.identity.return_value = {"user": "siege_app", "superuser": privileged,
                                            "createdb": False, "createrole": False}
            with self.assertRaises(recovery.Stop):
                recovery.restore(client, Path("unused"), run_id, {}, {})
            client.run.assert_not_called()

    def test_failed_restore_preserves_evidence_without_command_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = bundle(root)
            services, passwords = files(root)
            with patch.object(recovery, "restore", side_effect=OSError("secret-host-password")):
                status, report = self.run_phase(["restore", "--bundle", str(source), "--report", str(root / "report.json"),
                    "--confirm-isolated-restore", "--run-id", str(uuid.uuid4()),
                    "--services", str(services), "--passwords", str(passwords)])
            self.assertEqual(status, 1)
            self.assertEqual(report["result"], "STOP")
            self.assertNotIn("secret-host-password", json.dumps(report))
            self.assertEqual(json.loads((root / "report.json").read_text()), report)
            self.assertEqual((root / "report.json").stat().st_mode & 0o777, 0o600)
            self.assertTrue((source / "siege.dump").exists())

    def test_second_dump_failure_leaves_no_completed_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = unittest.mock.Mock()
            client.query.return_value = "1024"
            client.snapshot.side_effect = lambda app: nullcontext("00000001-00000001-1")
            client.controls.return_value = []
            def run(program, args=(), sql=None, output=None):
                if program == "pg_dump":
                    if "--dbname=service=mom" in args:
                        raise recovery.Stop("postgres_command_failed")
                    output.write(b"PGDMPsynthetic")
            client.run.side_effect = run
            with self.assertRaises(recovery.Stop):
                recovery.backup(client, root / "partial", {}, {})
            self.assertTrue((root / "partial/siege.dump").exists())
            self.assertFalse((root / "partial/manifest.json").exists())
            self.assertEqual((root / "partial").stat().st_mode & 0o777, 0o700)

    def test_command_failure_and_warning_output_is_suppressed(self):
        with tempfile.TemporaryDirectory() as directory:
            services, passwords = files(Path(directory))
            client = recovery.Client(services, passwords)
            for code, diagnostic in ((1, b"secret database diagnostic"), (0, b"warning contains token")):
                with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], code, b"", diagnostic)):
                    with self.assertRaises(recovery.Stop) as caught:
                        client.run("psql")
                self.assertNotIn("secret", str(caught.exception))
                self.assertNotIn("token", str(caught.exception))


@unittest.skipUnless(os.environ.get("PORTABLE_RECOVERY_TEST") == "1", "disposable PostgreSQL runs only in CI")
class RehearsalTests(unittest.TestCase):
    def test_real_backup_restore_and_fail_closed_retries(self):
        port = os.environ["PG_TEST_PORT"]
        admin_env = {**os.environ, "PGPASSWORD": "synthetic-admin-password"}
        def admin(sql, db="postgres"):
            result = subprocess.run(["psql", "--no-psqlrc", "--no-password", "-h", "127.0.0.1", "-p", port,
                "-U", "postgres", "-d", db, "-v", "ON_ERROR_STOP=1", "-qAt"], input=sql,
                capture_output=True, text=True, env=admin_env, check=True, timeout=30)
            return result.stdout.strip()
        run_id = uuid.uuid4()
        names = {app: f"rsl_restore_{run_id.hex}_{app}" for app in recovery.APPS}
        # This test requires a dedicated disposable CI server; it never uses a
        # deployed service or reads local operator configuration.
        self.assertEqual(admin("SELECT current_setting('server_version_num')::int / 10000"), "16")
        for app, role in recovery.APPS.items():
            admin(f"CREATE ROLE {role} LOGIN PASSWORD 'synthetic-password' NOSUPERUSER NOCREATEDB NOCREATEROLE;")
            admin(f"CREATE DATABASE {app}_source OWNER {role};")
            admin(f"CREATE DATABASE {names[app]} OWNER {role};")
            admin(f"REVOKE ALL ON DATABASE {names[app]} FROM PUBLIC;")
            admin(f"SET ROLE {role}; CREATE TABLE sample(id serial PRIMARY KEY, value text); "
                  "INSERT INTO sample(value) VALUES ('private workload'),('another private row'); "
                  "CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY); "
                  "INSERT INTO alembic_version VALUES ('synthetic_revision');", f"{app}_source")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            services, passwords = files(root, port=port)
            client = recovery.Client(services, passwords)
            report, checks = {}, {}
            recovery.backup(client, root / "bundle", report, checks)
            manifest = recovery.verified_manifest(root / "bundle", {})
            self.assertTrue(all(checks.values()))
            files(root, dbnames=names, port=port)
            target = recovery.Client(services, passwords)
            recovery.restore(target, root / "bundle", run_id, manifest, {})
            for app, role in recovery.APPS.items():
                self.assertEqual(admin("SELECT count(*) FROM sample", names[app]), "2")
                self.assertEqual(admin("SELECT relowner::regrole FROM pg_class WHERE relname='sample'", names[app]), role)
                self.assertEqual(admin("SELECT version_num FROM alembic_version", names[app]), "synthetic_revision")
                self.assertEqual(admin("INSERT INTO sample(value) VALUES ('sequence restored') RETURNING id", names[app]), "3")
            with self.assertRaises(recovery.Stop):
                recovery.restore(target, root / "bundle", run_id, manifest, {})
            (root / "bundle/mom.dump").write_bytes(b"PGDMPcorrupt")
            with self.assertRaises(recovery.Stop):
                recovery.verified_manifest(root / "bundle", {})
            self.assertEqual(admin("SELECT count(*) FROM sample", "siege_source"), "2")
            self.assertEqual(admin("SELECT count(*) FROM sample", "mom_source"), "2")
            # A concurrent external writer after snapshot export must not change
            # either that dump or its recorded controls.
            files(root, port=port)
            source_client = recovery.Client(services, passwords)
            original_controls = source_client.controls
            inserted = False
            def controls_with_writer(app, snapshot=None):
                nonlocal inserted
                result = original_controls(app, snapshot)
                if app == "siege" and not inserted:
                    admin("INSERT INTO sample(value) VALUES ('concurrent external writer')", "siege_source")
                    inserted = True
                return result
            with patch.object(source_client, "controls", side_effect=controls_with_writer):
                recovery.backup(source_client, root / "concurrent", {}, {})
            concurrent = recovery.verified_manifest(root / "concurrent", {})
            second_run = uuid.uuid4()
            second_names = {app: f"rsl_restore_{second_run.hex}_{app}" for app in recovery.APPS}
            for app, role in recovery.APPS.items():
                admin(f"CREATE DATABASE {second_names[app]} OWNER {role};")
                admin(f"REVOKE ALL ON DATABASE {second_names[app]} FROM PUBLIC;")
            files(root, dbnames=second_names, port=port)
            recovery.restore(recovery.Client(services, passwords), root / "concurrent", second_run, concurrent, {})
            self.assertEqual(admin("SELECT count(*) FROM sample", second_names["siege"]), "2")
            self.assertEqual(admin("SELECT count(*) FROM sample", "siege_source"), "3")
        # GitHub tears down the dedicated server after this test, including failed
        # tests. No DROP/clean command is part of the operator runner.


if __name__ == "__main__":
    unittest.main()
