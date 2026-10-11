#!/usr/bin/env python3
"""Phase-scoped PostgreSQL 16 backup/verification/isolated restore.

Uses approved libpq service/password files and existing client tools only. Never
installs software, activates Compose, changes roles, drops databases, or starts bots.
Raw command output, archive contents, and connection details stay out of reports.
"""
import argparse
import configparser
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import stat
import subprocess
import time
import uuid

APPS = {"siege": "siege_app", "mom": "mom_app"}
TIMEOUT = 300
MAX_METADATA = 4 * 1024 * 1024


class Stop(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise Stop(reason)


def private_path(path, directory=False):
    metadata = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    require(kind(metadata.st_mode) and metadata.st_uid == os.geteuid()
            and not metadata.st_mode & 0o077, "unsafe_private_path")
    return metadata


def read_private(path, limit=MAX_METADATA):
    private_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        metadata = os.fstat(source.fileno())
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.geteuid()
                and not metadata.st_mode & 0o077 and metadata.st_size <= limit,
                "unsafe_private_file")
        result = source.read(limit + 1)
        require(len(result) <= limit, "metadata_too_large")
        return result


def create_private(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600), "wb")


def write_json(path, value):
    with create_private(path) as output:
        output.write((json.dumps(value, indent=2) + "\n").encode())
        output.flush()
        os.fsync(output.fileno())


def digest(path):
    private_path(path)
    result = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        require(stat.S_ISREG(os.fstat(source.fileno()).st_mode), "unsafe_archive")
        require(source.read(5) == b"PGDMP", "not_custom_archive")
        source.seek(0)
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


class Client:
    def __init__(self, services, passwords):
        private_path(services.parent, directory=True)
        private_path(passwords.parent, directory=True)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(read_private(services).decode())
        require(set(parser.sections()) == set(APPS) and not parser.defaults(), "invalid_services")
        self.settings = {}
        allowed = {"host", "port", "dbname", "user", "sslmode", "sslrootcert"}
        for app in APPS:
            values = dict(parser[app])
            require(set(values) <= allowed and all(values.get(k, "").strip()
                    for k in ("host", "port", "dbname", "user", "sslmode")), "invalid_service")
            require(re.fullmatch(r"[a-zA-Z0-9_]{1,63}", values["dbname"])
                    and values["dbname"] not in {"postgres", "template0", "template1"}, "invalid_database")
            require(values["sslmode"] in {"verify-full", "disable"}
                    and values["port"].isdigit() and 0 < int(values["port"]) < 65536, "invalid_connection")
            self.settings[app] = values
        read_private(passwords)
        # Ambient PGHOST/PGOPTIONS/PGDATABASE must not override reviewed targets.
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        self.env.update(PGSERVICEFILE=str(services.resolve()), PGPASSFILE=str(passwords.resolve()),
                        PGCONNECT_TIMEOUT="10", PGAPPNAME="portable-recovery",
                        PGOPTIONS="-c statement_timeout=300000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=900000")

    def run(self, program, args=(), sql=None, output=None):
        try:
            result = subprocess.run([program, *args], env=self.env, input=sql.encode() if sql else None,
                                    stdout=output if output is not None else subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise Stop("postgres_command_failed") from None
        require(result.returncode == 0, "postgres_command_failed")
        # Warnings are not silently accepted as backup/restore proof.
        require(not result.stderr.strip(), "postgres_command_warning")
        return result.stdout.decode() if output is None else None

    def versions(self):
        for program in ("psql", "pg_dump", "pg_restore"):
            require(re.search(r"\(PostgreSQL\) 16(?:\.|\b)", self.run(program, ["--version"])),
                    "postgres_16_client_required")

    def query(self, app, sql):
        return self.run("psql", ["--no-psqlrc", "--no-password", "--quiet", "--tuples-only",
                                "--no-align", "--set=ON_ERROR_STOP=1", f"--dbname=service={app}"], sql)

    def identity(self, app):
        result = json.loads(self.query(app, "SELECT json_build_object('database',current_database(),"
            "'user',current_user,'major',current_setting('server_version_num')::int / 10000,"
            "'superuser',rolsuper,'createdb',rolcreatedb,'createrole',rolcreaterole) "
            "FROM pg_roles WHERE rolname=current_user;"))
        require(result["major"] == 16 and result["database"] == self.settings[app]["dbname"]
                and result["user"] == self.settings[app]["user"], "database_identity_mismatch")
        return result

    @contextmanager
    def snapshot(self, app):
        # A separate read-only connection keeps the exported snapshot alive while
        # pg_dump and control queries import the exact same per-database snapshot.
        process = subprocess.Popen(["psql", "--no-psqlrc", "--no-password", "--quiet", "--tuples-only",
                                    "--no-align", "--set=ON_ERROR_STOP=1", f"--dbname=service={app}"],
                                   env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
        try:
            process.stdin.write(b"BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\nSELECT pg_export_snapshot();\n")
            process.stdin.flush()
            require(select.select([process.stdout], [], [], 30)[0], "snapshot_unavailable")
            value = process.stdout.readline().decode().strip()
            require(re.fullmatch(r"[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+", value), "snapshot_unavailable")
            yield value
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for pipe in (process.stdin, process.stdout):
                pipe.close()

    def controls(self, app, snapshot=None):
        # Only control totals/schema revisions, never workload rows. Identifiers
        # stay inside the private manifest, not the operator-facing report.
        sql = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
        if snapshot:
            require(re.fullmatch(r"[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+", snapshot), "invalid_snapshot")
            sql += f"SET TRANSACTION SNAPSHOT '{snapshot}';\n"
        sql += r"""
SELECT format('SELECT json_build_object(''schema'',%L,''table'',%L,''rows'',count(*)%s) FROM %I.%I;',
 n.nspname,c.relname,
 CASE WHEN c.relname='alembic_version' THEN ',''versions'',coalesce(json_agg(version_num ORDER BY version_num),''[]''::json)' ELSE '' END,
 n.nspname,c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE c.relkind IN ('r','p') AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
ORDER BY n.nspname,c.relname
\gexec
COMMIT;
"""
        lines = self.query(app, sql).splitlines()
        require(len(lines) <= 10000, "too_many_control_records")
        return [json.loads(line) for line in lines]

    def empty_target(self, app):
        # Objects besides tables can also conflict with or execute during restore.
        value = self.query(app, "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON "
            "n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema') "
            "AND NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema') "
            "AND NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' "
            "AND nspname NOT IN ('public','information_schema')) "
            "AND NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
            "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema') "
            "AND NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname <> 'plpgsql') "
            "AND (SELECT datdba=(SELECT oid FROM pg_roles WHERE rolname=current_user) "
            "FROM pg_database WHERE datname=current_database()) "
            "AND NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)) "
            "AND NOT EXISTS (SELECT 1 FROM pg_database d, LATERAL "
            "aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a "
            "WHERE d.datname=current_database() AND a.grantee=0);")
        require(value.strip() == "t", "restore_target_not_empty")


def backup(client, directory, report, checks):
    private_path(directory.parent, directory=True)
    # Exclusive mkdir; keep partial files private for diagnosis on failure.
    directory.mkdir(mode=0o700)
    checks["new_private_bundle"] = True
    client.versions()
    manifest = {"schema": 1, "postgres_major": 16, "bundle_id": str(uuid.uuid4()),
                "created_at": datetime.now(timezone.utc).isoformat(), "databases": {}}
    for app in APPS:
        client.identity(app)
        checks[f"{app}_source_identity"] = True
        size = int(client.query(app, "SELECT pg_database_size(current_database());").strip())
        require(shutil.disk_usage(directory).free > size * 2 + 32 * 1024 * 1024, "insufficient_backup_space")
        started = datetime.now(timezone.utc).isoformat()
        with client.snapshot(app) as snapshot:
            controls = client.controls(app, snapshot)
            with create_private(directory / f"{app}.dump") as output:
                client.run("pg_dump", ["--no-password", f"--dbname=service={app}", "--format=custom",
                           "--no-acl", f"--snapshot={snapshot}", "--lock-wait-timeout=10000"], output=output)
                output.flush()
                os.fsync(output.fileno())
        archive = directory / f"{app}.dump"
        client.run("pg_restore", ["--list", str(archive)])
        manifest["databases"][app] = {"sha256": digest(archive), "bytes": archive.stat().st_size,
            "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(), "controls": controls}
        checks[f"{app}_snapshot_dump"] = True
    # Written last; incomplete bundles never get a completed manifest.
    write_json(directory / "manifest.json", manifest)
    checks["complete_manifest"] = True
    report["bundle_id"] = manifest["bundle_id"]


def archive_readable(path):
    try:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        version = subprocess.run(["pg_restore", "--version"], env=env, capture_output=True, timeout=30)
        require(version.returncode == 0 and re.search(rb"\(PostgreSQL\) 16(?:\.|\b)", version.stdout),
                "postgres_16_client_required")
        result = subprocess.run(["pg_restore", "--list", str(path)], env=env, capture_output=True, timeout=TIMEOUT)
        require(result.returncode == 0 and not result.stderr.strip(), "archive_unreadable")
    except (OSError, subprocess.TimeoutExpired):
        raise Stop("archive_unreadable") from None


def verified_manifest(directory, checks):
    private_path(directory, directory=True)
    manifest = json.loads(read_private(directory / "manifest.json"))
    require(manifest["schema"] == 1 and manifest["postgres_major"] == 16
            and set(manifest["databases"]) == set(APPS), "invalid_manifest")
    uuid.UUID(manifest["bundle_id"])
    datetime.fromisoformat(manifest["created_at"])
    for app in APPS:
        entry = manifest["databases"][app]
        require(isinstance(entry["controls"], list) and len(entry["controls"]) <= 10000,
                "invalid_controls")
        archive = directory / f"{app}.dump"
        require(entry["sha256"] == digest(archive) and entry["bytes"] == archive.stat().st_size,
                "archive_integrity_failed")
        archive_readable(archive)
        checks[f"{app}_archive_integrity"] = True
    return manifest


def restore(client, directory, run_id, manifest, checks):
    # Both databases pass every identity/emptiness check before the first write.
    client.versions()
    for app, role in APPS.items():
        require(client.settings[app]["dbname"] == f"rsl_restore_{run_id.hex}_{app}", "isolated_target_required")
        identity = client.identity(app)
        require(identity["user"] == role and not any(identity[k] for k in
                ("superuser", "createdb", "createrole")), "least_privilege_target_required")
        client.empty_target(app)
        checks[f"{app}_isolated_empty_target"] = True
    for app in APPS:
        # No --create, --clean, role credentials, or Azure privilege restoration.
        client.run("pg_restore", ["--no-password", f"--dbname=service={app}", "--no-owner", "--no-acl",
                                 "--exit-on-error", "--single-transaction", str(directory / f"{app}.dump")])
        checks[f"{app}_restore_transaction"] = True
        require(client.controls(app) == manifest["databases"][app]["controls"], "restore_controls_mismatch")
        checks[f"{app}_restored_controls_match"] = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["backup", "verify", "restore"])
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--services", type=Path)
    parser.add_argument("--passwords", type=Path)
    parser.add_argument("--confirm-backup", action="store_true")
    parser.add_argument("--confirm-isolated-restore", action="store_true")
    parser.add_argument("--run-id", type=uuid.UUID)
    args = parser.parse_args(argv)
    started = time.monotonic()
    checks = {}
    report = {"schema": 1, "phase": args.phase, "timestamp": datetime.now(timezone.utc).isoformat(),
              "checks": checks, "result": "STOP"}
    # Reserve evidence before any DB operation; never overwrite old reports.
    try:
        private_path(args.report.parent, directory=True)
        with create_private(args.report) as output:
            output.write(b'{"result":"INCOMPLETE"}\n')
            output.flush()
            os.fsync(output.fileno())
            try:
                require(args.report.parent.resolve() != args.bundle.resolve(), "report_outside_bundle_required")
                if args.phase == "backup":
                    require(args.confirm_backup, "backup_confirmation_required")
                    require(args.services and args.passwords, "connection_files_required")
                    backup(Client(args.services, args.passwords), args.bundle, report, checks)
                else:
                    manifest = verified_manifest(args.bundle, checks)
                    report["bundle_id"] = manifest["bundle_id"]
                    if args.phase == "restore":
                        require(args.confirm_isolated_restore and args.run_id, "isolated_restore_confirmation_required")
                        require(args.services and args.passwords, "connection_files_required")
                        restore(Client(args.services, args.passwords), args.bundle, args.run_id, manifest, checks)
                report["result"] = "PASS"
            except (Stop, OSError, ValueError, KeyError, TypeError, UnicodeError, KeyboardInterrupt) as error:
                report["error"] = str(error) if isinstance(error, Stop) else "operation_failed"
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            output.seek(0)
            output.truncate()
            output.write((json.dumps(report, indent=2) + "\n").encode())
            output.flush()
            os.fsync(output.fileno())
    except (Stop, OSError):
        report["result"] = "STOP"
        report["error"] = "report_write_failed"
    print(json.dumps(report))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
