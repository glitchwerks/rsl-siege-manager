#!/usr/bin/env python3
"""Read-only proof of a newly initialized portable PostgreSQL instance."""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess


PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")
CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
ENVIRONMENT = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/tmp", "LANG": "C",
               "DOCKER_HOST": "unix:///var/run/docker.sock"}


def private(path, directory=False):
    try:
        if not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents)):
            return False
        info = path.lstat()
        return ((stat.S_ISDIR if directory else stat.S_ISREG)(info.st_mode)
                and info.st_uid == os.geteuid() and not info.st_mode & 0o077)
    except OSError:
        return False


def command(arguments):
    try:
        result = subprocess.run(arguments, env=ENVIRONMENT, capture_output=True,
                                text=True, timeout=20, check=False)
        return result.stdout.strip() if result.returncode == 0 and len(result.stdout) <= 4096 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def project_name(project):
    path = project / "stack.env"
    if not private(project, directory=True) or not private(path):
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_size > 65536):
                return None
            content = source.read(65537).decode("utf-8")
    except (OSError, UnicodeError):
        return None
    if len(content) > 65536:
        return None
    values = [line.split("=", 1)[1] for line in content.splitlines()
              if line.startswith("COMPOSE_PROJECT_NAME=")]
    return values[0] if len(values) == 1 and PROJECT.fullmatch(values[0]) else None


def postgres_container(project):
    raw = command(["docker", "ps", "--all", "--no-trunc",
                   "--filter", f"label=com.docker.compose.project={project}",
                   "--filter", "label=com.docker.compose.service=postgres",
                   "--format", "{{.ID}}"])
    if raw is None or len(raw.splitlines()) != 1 or not CONTAINER_ID.fullmatch(raw):
        return None
    state_raw = command(["docker", "inspect", "--format", "{{json .State}}", raw])
    labels_raw = command(["docker", "inspect", "--format", "{{json .Config.Labels}}", raw])
    try:
        state, labels = json.loads(state_raw), json.loads(labels_raw)
        if (state.get("Running") is not True or state.get("Status") != "running"
                or state.get("Health", {}).get("Status") != "healthy"
                or labels.get("com.docker.compose.project") != project
                or labels.get("com.docker.compose.service") != "postgres"):
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    return raw


def query(container, database, sql):
    return command(["docker", "exec", "--user", "postgres", container, "psql", "-X",
                    "-v", "ON_ERROR_STOP=1", "-h", "/var/run/postgresql", "-U", "postgres",
                    "-d", database, "-tA", "-c", sql])


def inspect(project):
    checks = {"project_private": private(project, directory=True)}
    name = project_name(project) if checks["project_private"] else None
    checks["project_identity"] = name is not None
    if not checks["project_identity"]:
        return checks
    container = postgres_container(name)
    checks["single_healthy_postgres"] = container is not None
    if not checks["single_healthy_postgres"]:
        return checks
    running = command(["docker", "ps", "--no-trunc", "--filter",
                       f"label=com.docker.compose.project={name}", "--format", "{{.ID}}"])
    checks["only_postgres_running"] = running == container
    if not checks["only_postgres_running"]:
        return checks
    for role in ("siege_app", "mom_app"):
        sql = ("SELECT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND "
               "NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls AND "
               "NOT EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member=pg_roles.oid) "
               f"FROM pg_roles WHERE rolname='{role}'")
        checks[f"{role}_least_privilege"] = query(container, "postgres", sql) == "t"
    for database, owner, other in (("siege", "siege_app", "mom_app"),
                                   ("mom_bot", "mom_app", "siege_app")):
        sql = (f"SELECT pg_get_userbyid(datdba)='{owner}' AND "
               f"has_database_privilege('{owner}','{database}','CONNECT') AND NOT EXISTS "
               "(SELECT 1 FROM aclexplode(COALESCE(datacl, acldefault('d', datdba))) "
               "WHERE grantee=0 AND privilege_type='CONNECT') AND "
               f"NOT has_database_privilege('{other}','{database}','CONNECT') "
               f"FROM pg_database WHERE datname='{database}'")
        checks[f"{database}_owner_and_isolation"] = query(container, "postgres", sql) == "t"
        empty = ("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                 "WHERE c.relkind IN ('r','p','v','m','f','S') AND n.nspname NOT IN "
                 "('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'")
        checks[f"{database}_empty"] = query(container, database, empty) == "0"
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-directory", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if not private(args.report.parent, directory=True):
            raise OSError("unsafe report directory")
        fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError:
        print("STOP report_write_failed")
        return 2
    try:
        checks = inspect(args.project_directory)
    except (OSError, ValueError, TypeError, UnicodeError):
        checks = {"inspection": False}
    report = {"phase": "bootstrap-readiness",
              "status": "PASS" if checks and all(checks.values()) else "STOP", "checks": checks}
    try:
        with os.fdopen(fd, "w") as output:
            output.write(json.dumps(report, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        print("STOP report_write_failed")
        return 2
    print(report["status"])
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
