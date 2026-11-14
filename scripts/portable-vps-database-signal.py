#!/usr/bin/env python3
"""Read-only local PostgreSQL signal for both portable application databases.

No scheduling, notification, restart, backup, migration or data modification.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time


CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"[A-Za-z0-9_]{1,64}\Z")
PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")
IMAGE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}\Z")
ENVIRONMENT = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/root", "LANG": "C",
               "DOCKER_HOST": "unix:///var/run/docker.sock"}
INSPECT = ('{"id":{{json .Id}},"project":{{json (index .Config.Labels '
           '"com.docker.compose.project")}},"service":{{json (index .Config.Labels '
           '"com.docker.compose.service")}},"image":{{json .Config.Image}},'
           '"running":{{json .State.Running}},"health":{{if .State.Health}}'
           '{{json .State.Health.Status}}{{else}}"none"{{end}},'
           '"oom":{{json .State.OOMKilled}},"restarts":{{json .RestartCount}},'
           '"started_at":{{json .State.StartedAt}}}')
MOUNT = '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{json .Source}}{{end}}{{end}}'
PSQL = '''case "$1" in
  siege) DB_USER=siege_app; DB_NAME=siege; PGPASSWORD="$SIEGE_DB_PASSWORD" ;;
  mom_bot) DB_USER=mom_app; DB_NAME=mom_bot; PGPASSWORD="$MOM_DB_PASSWORD" ;;
  *) exit 9 ;;
esac
test -n "$PGPASSWORD"
export PGPASSWORD
exec psql --no-psqlrc --quiet --no-align --tuples-only --field-separator='|' \\
  --set ON_ERROR_STOP=1 --host 127.0.0.1 --port 5432 \\
  --username "$DB_USER" --dbname "$DB_NAME" --command "$2"'''
QUERY = {name: ("BEGIN READ ONLY; SET LOCAL statement_timeout='3000ms'; "
    "SELECT current_user, current_database(), "
    "(SELECT version_num FROM public.alembic_version), "
    f"to_regclass('public.{table}') IS NOT NULL, "
    "pg_database_size(current_database()), "
    "(SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend'), "
    "current_setting('max_connections')::int, "
    "current_setting('transaction_read_only'); COMMIT;")
    for name, table in (("siege", "member"), ("mom_bot", "reminders"))}
CHECK_NAMES = ("postgres_exact", "postgres_running_healthy", "postgres_no_restart_or_oom",
               "storage_mount_exact", "storage_headroom", "siege_authenticated_schema",
               "mom_authenticated_schema", "siege_size_within_budget", "mom_size_within_budget",
               "connections_within_budget", "postgres_stable_after_probe")


def private(path, directory=False):
    metadata = path.lstat()
    valid = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if not valid or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise ValueError("unsafe private path")


def read_config(path):
    private(path.parent, directory=True)
    private(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        if os.fstat(source.fileno()).st_size > 8192:
            raise ValueError("invalid configuration")
        raw = source.read(8193)
    if len(raw) > 8192:
        raise ValueError("invalid configuration")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {
            "project", "postgres_image", "postgres_id", "postgres_started_at",
            "postgres_restarts", "storage_path", "minimum_disk_bytes",
            "max_database_bytes", "max_connection_percent", "revisions"}:
        raise ValueError("invalid configuration")
    if (not isinstance(value["project"], str) or not PROJECT.fullmatch(value["project"])
            or not isinstance(value["postgres_image"], str)
            or not IMAGE.fullmatch(value["postgres_image"])
            or not isinstance(value["postgres_id"], str)
            or not CONTAINER_ID.fullmatch(value["postgres_id"])
            or not isinstance(value["postgres_started_at"], str)
            or not re.fullmatch(r"\d{4}-\d\d-\d\dT[^\s]{1,40}", value["postgres_started_at"])
            or type(value["postgres_restarts"]) is not int or value["postgres_restarts"] < 0
            or not isinstance(value["storage_path"], str)
            or not Path(value["storage_path"]).is_absolute()
            or not Path(value["storage_path"]).is_dir()
            or Path(value["storage_path"]).resolve() != Path(value["storage_path"])
            or type(value["minimum_disk_bytes"]) is not int
            or not 1024**3 <= value["minimum_disk_bytes"] <= 1024**5
            or type(value["max_connection_percent"]) is not int
            or not 50 <= value["max_connection_percent"] <= 95
            or not isinstance(value["max_database_bytes"], dict)
            or set(value["max_database_bytes"]) != {"siege", "mom_bot"}
            or not all(type(item) is int and 1024**2 <= item <= 1024**5
                       for item in value["max_database_bytes"].values())
            or not isinstance(value["revisions"], dict)
            or set(value["revisions"]) != {"siege", "mom_bot"}
            or not all(isinstance(item, str) and REVISION.fullmatch(item)
                       for item in value["revisions"].values())):
        raise ValueError("invalid configuration")
    return value


def command(arguments, deadline, cap=20):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    try:
        result = subprocess.run(arguments, env=ENVIRONMENT, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=min(cap, remaining), check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 and len(result.stdout) <= 4096 else None


def postgres(cfg, deadline):
    project = cfg["project"]
    found = command(["docker", "ps", "--all", "--no-trunc",
        "--filter", f"label=com.docker.compose.project={project}",
        "--filter", "label=com.docker.compose.service=postgres", "--format", "{{.ID}}"], deadline)
    if found is None:
        return None
    ids = found.splitlines()
    if len(ids) != 1 or not CONTAINER_ID.fullmatch(ids[0]):
        return None
    raw = command(["docker", "inspect", "--format", INSPECT, ids[0]], deadline)
    if raw is None:
        return None
    try:
        info = json.loads(raw)
        if not isinstance(info, dict) or info.get("id") != ids[0]:
            return None
        return info
    except (ValueError, TypeError):
        return None


def storage_source(identifier, deadline):
    raw = command(["docker", "inspect", "--format", MOUNT, identifier], deadline)
    try:
        source = json.loads(raw)
        return Path(source) if isinstance(source, str) and Path(source).is_absolute() else None
    except (ValueError, TypeError):
        return None


def database(identifier, name, deadline):
    raw = command(["docker", "exec", "--user", "postgres", identifier, "sh", "-eu", "-c",
                   PSQL, "database-signal", name, QUERY[name]], deadline, cap=15)
    if raw is None or len(raw) > 512:
        return None
    lines = raw.splitlines()
    if len(lines) != 1:
        return None
    fields = lines[0].split("|")
    if len(fields) != 8:
        return None
    user, db, revision, table, size, connections, max_connections, readonly = fields
    try:
        if (user != ("siege_app" if name == "siege" else "mom_app") or db != name
                or not REVISION.fullmatch(revision) or table != "t" or readonly != "on"):
            return None
        metrics = tuple(int(item) for item in (size, connections, max_connections))
        if metrics[0] < 0 or metrics[1] < 0 or metrics[2] <= 0:
            return None
        return {"revision": revision, "bytes": metrics[0], "connections": metrics[1],
                "max_connections": metrics[2]}
    except ValueError:
        return None


def inspect(cfg):
    checks = {name: False for name in CHECK_NAMES}
    measurements = {"siege_bytes": None, "mom_bytes": None,
                    "disk_available_bytes": None, "connections": None, "max_connections": None}
    deadline = time.monotonic() + 60
    info = postgres(cfg, deadline)
    checks["postgres_exact"] = (info is not None and info.get("project") == cfg["project"]
        and info.get("service") == "postgres" and info.get("id") == cfg["postgres_id"]
        and info.get("image") == cfg["postgres_image"])
    if not checks["postgres_exact"]:
        return checks, measurements
    checks["postgres_running_healthy"] = info.get("running") is True and info.get("health") == "healthy"
    checks["postgres_no_restart_or_oom"] = (info.get("oom") is False
        and info.get("restarts") == cfg["postgres_restarts"]
        and info.get("started_at") == cfg["postgres_started_at"])
    if not checks["postgres_running_healthy"] or not checks["postgres_no_restart_or_oom"]:
        return checks, measurements
    source = storage_source(info["id"], deadline)
    checks["storage_mount_exact"] = (source is not None and source.resolve() == Path(cfg["storage_path"]))
    if checks["storage_mount_exact"]:
        filesystem = os.statvfs(source)
        measurements["disk_available_bytes"] = filesystem.f_bavail * filesystem.f_frsize
        checks["storage_headroom"] = measurements["disk_available_bytes"] >= cfg["minimum_disk_bytes"]
    rows = {name: database(info["id"], name, deadline) for name in ("siege", "mom_bot")}
    for name, label in (("siege", "siege"), ("mom_bot", "mom")):
        row = rows[name]
        checks[f"{label}_authenticated_schema"] = row is not None and row["revision"] == cfg["revisions"][name]
        if row is not None:
            measurements[f"{label}_bytes"] = row["bytes"]
            checks[f"{label}_size_within_budget"] = row["bytes"] <= cfg["max_database_bytes"][name]
    if all(rows.values()):
        measurements["connections"] = max(row["connections"] for row in rows.values())
        measurements["max_connections"] = min(row["max_connections"] for row in rows.values())
        checks["connections_within_budget"] = (measurements["connections"] * 100
            <= measurements["max_connections"] * cfg["max_connection_percent"])
    after = postgres(cfg, deadline)
    checks["postgres_stable_after_probe"] = after == info
    return checks, measurements


def publish(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=".database-signal-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(value, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--confirm-readonly-databases", action="store_true")
    args = parser.parse_args(argv)
    result = {"schema": 1, "phase": "database-signal", "timestamp": datetime.now(timezone.utc).isoformat(),
              "status": "STOP", "checks": {name: False for name in CHECK_NAMES}, "measurements": {}}
    try:
        if (os.geteuid() != 0 or not args.confirm_readonly_databases
                or not args.config.is_absolute() or not args.report.is_absolute()
                or ".." in args.config.parts or ".." in args.report.parts
                or args.config.resolve() == args.report.resolve()):
            raise ValueError("confirmation required")
        cfg = read_config(args.config)
        private(args.report.parent, directory=True)
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump({"status": "INCOMPLETE"}, output)
            output.flush()
            os.fsync(output.fileno())
        try:
            checks, measurements = inspect(cfg)
            result["checks"] = checks
            result["measurements"] = measurements
            result["status"] = "PASS" if all(checks.values()) else "STOP"
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            pass
        publish(args.report, result)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        print("STOP")
        return 2
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
