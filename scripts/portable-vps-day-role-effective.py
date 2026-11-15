#!/usr/bin/env python3
"""Read-only comparison of reviewed day-role config with the running backend.

This command does not edit environment files, recreate containers, send a
webhook, or change Discord roles. Its report never contains environment values.
"""

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


SPEC = importlib.util.spec_from_file_location(
    "portable_preflight", Path(__file__).with_name("portable-preflight.py"))
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)

CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
DOCKER_ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/root", "LANG": "C",
              "DOCKER_HOST": "unix:///var/run/docker.sock"}
INSPECT = ('{"id":{{json .Id}},"project":{{json (index .Config.Labels '
           '"com.docker.compose.project")}},"service":{{json (index .Config.Labels '
           '"com.docker.compose.service")}},"image":{{json .Config.Image}},'
           '"running":{{json .State.Running}},"health":{{if .State.Health}}'
           '{{json .State.Health.Status}}{{else}}"none"{{end}},'
           '"started_at":{{json .State.StartedAt}},"restarts":{{json .RestartCount}},'
           '"oom":{{json .State.OOMKilled}},'
           '"env":{{json .Config.Env}}}')
CHECKS = ("reviewed_candidate", "single_backend", "container_identity",
          "running_healthy", "effective_environment", "enabled_flag",
          "role_id_presence", "role_ids_distinct", "container_stable")
ROLE_KEYS = ("DISCORD_DAY_1_ROLE_ID", "DISCORD_DAY_2_ROLE_ID")


def docker(arguments, limit=65536):
    try:
        result = subprocess.run(["docker", *arguments], env=DOCKER_ENV,
                                stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode or len(result.stdout) > limit:
        return None
    return result.stdout


def env_map(lines):
    if not isinstance(lines, list):
        return None
    result = {}
    for line in lines:
        if not isinstance(line, str) or "=" not in line:
            return None
        key, value = line.split("=", 1)
        if not key or key in result:
            return None
        result[key] = value
    return result


def inspect(model, expected_enabled):
    checks = {name: False for name in CHECKS}
    expected = model["services"]["backend"]
    expected_env = expected["environment"]
    checks["reviewed_candidate"] = (
        expected_env.get("DAY_ROLE_SYNC_ENABLED", "false") == expected_enabled
        and expected_env.get("ENVIRONMENT") == "production")
    if not checks["reviewed_candidate"]:
        return checks

    project = model["name"]
    raw = docker(["ps", "--all", "--no-trunc",
                  "--filter", f"label=com.docker.compose.project={project}",
                  "--filter", "label=com.docker.compose.service=backend",
                  "--format", "{{.ID}}"], 4096)
    ids = raw.splitlines() if raw is not None else []
    checks["single_backend"] = len(ids) == 1 and CONTAINER_ID.fullmatch(ids[0]) is not None
    if not checks["single_backend"]:
        return checks

    raw = docker(["inspect", "--format", INSPECT, ids[0]])
    try:
        info = json.loads(raw) if raw is not None else None
        if not isinstance(info, dict):
            return checks
        checks["container_identity"] = (
            info.get("id") == ids[0] and info.get("project") == project
            and info.get("service") == "backend"
            and info.get("image") == expected["image"])
        checks["running_healthy"] = (
            checks["container_identity"] and info.get("running") is True
            and info.get("health") == "healthy" and info.get("oom") is False
            and isinstance(info.get("started_at"), str)
            and type(info.get("restarts")) is int)
        if not checks["running_healthy"]:
            return checks
        actual_env = env_map(info.get("env"))
        if actual_env is None:
            return checks
        checks["effective_environment"] = all(
            isinstance(value, str) and actual_env.get(key) == value
            for key, value in expected_env.items())
        checks["enabled_flag"] = (
            actual_env.get("DAY_ROLE_SYNC_ENABLED", "false") == expected_enabled)
        checks["role_id_presence"] = (
            expected_enabled == "false" or all(actual_env.get(key) for key in ROLE_KEYS))
        checks["role_ids_distinct"] = (
            expected_enabled == "false" or (
                checks["role_id_presence"]
                and int(actual_env[ROLE_KEYS[0]]) != int(actual_env[ROLE_KEYS[1]])))
        after = docker(["inspect", "--format", INSPECT, ids[0]])
        again = docker(["ps", "--all", "--no-trunc",
                        "--filter", f"label=com.docker.compose.project={project}",
                        "--filter", "label=com.docker.compose.service=backend",
                        "--format", "{{.ID}}"], 4096)
        checks["container_stable"] = (
            after is not None and json.loads(after) == info and again == ids[0] + "\n")
        return checks
    except (ValueError, TypeError, KeyError):
        return checks


def publish(path, result):
    descriptor, temporary = tempfile.mkstemp(prefix=".day-role-effective-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(result, output, sort_keys=True)
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
    parser.add_argument("--project-directory", type=Path, required=True)
    parser.add_argument("--expected-enabled", choices=("true", "false"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--confirm-readonly", action="store_true")
    args = parser.parse_args(argv)
    result = {"schema": 1, "phase": "day-role-effective",
              "timestamp": datetime.now(timezone.utc).isoformat(),
              "status": "STOP", "checks": {name: False for name in CHECKS}}
    reserved = False
    published = False
    try:
        project = args.project_directory
        report = args.report
        if (os.geteuid() != 0 or not args.confirm_readonly
                or not project.is_absolute() or not report.is_absolute()
                or ".." in project.parts or ".." in report.parts
                or project.resolve() != project or report.resolve() != report
                or not PREFLIGHT.private_report_directory(report.parent)):
            raise ValueError("invalid invocation")
        project_info = project.lstat()
        if (not stat.S_ISDIR(project_info.st_mode)
                or project_info.st_uid != os.geteuid()
                or project_info.st_mode & 0o077):
            raise ValueError("unsafe project")
        descriptor = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        reserved = True
        with os.fdopen(descriptor, "w") as output:
            output.write('{"status":"INCOMPLETE"}\n')
            output.flush()
            os.fsync(output.fileno())

        stack = project / "stack.env"
        if (PREFLIGHT.private_runtime_files("mom", stack, project)
                and PREFLIGHT.deployment_bind_sources_current(project)):
            model = PREFLIGHT.render("mom", stack, project, {})
            approval = PREFLIGHT.read_mom_approval(project / "runtime/mom-image.json")
            if all(PREFLIGHT.validate(model, "mom", approval, project).values()):
                result["checks"] = inspect(model, args.expected_enabled)
        result["status"] = "PASS" if all(result["checks"].values()) else "STOP"
        publish(report, result)
        published = True
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        result["status"] = "STOP"
        try:
            if reserved:
                publish(args.report, result)
        except OSError:
            pass
    print(result["status"])
    return 0 if published and result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
