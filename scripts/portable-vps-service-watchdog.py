#!/usr/bin/env python3
"""One bounded, host-local unhealthy application-service recovery decision.

No scheduler is installed here. Database, proxy and whole-stack restarts are forbidden.
"""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time


ALLOWED = frozenset({"backend", "frontend", "mom", "bot"})
MIN_UNHEALTHY_SECONDS = 90
COOLDOWN_SECONDS = 6 * 3600
WINDOW_SECONDS = 24 * 3600
MAX_RESTARTS = 2
IMAGE = re.compile(r"^[^\s]+@sha256:[a-f0-9]{64}$")
PROJECT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
CONTAINER_ID = re.compile(r"^[a-f0-9]{64}$")
DOCKER_ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/root", "LANG": "C",
              "DOCKER_HOST": "unix:///var/run/docker.sock"}
INSPECT = ('{"id":{{json .Id}},"project":{{json (index .Config.Labels '
           '"com.docker.compose.project")}},"service":{{json (index .Config.Labels '
           '"com.docker.compose.service")}},"image":{{json .Config.Image}},'
           '"running":{{json .State.Running}},"health":{{if .State.Health}}'
           '{{json .State.Health.Status}}{{else}}"none"{{end}},'
           '"oom":{{json .State.OOMKilled}},"restarts":{{json .RestartCount}}}')


def require_private(path, directory=False):
    info = path.lstat()
    valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("unsafe_private_path")


def exact_json(path, *, limit=8192):
    require_private(path.parent, True)
    require_private(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        if os.fstat(source.fileno()).st_size > limit:
            raise ValueError("oversized_input")
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("oversized_input")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def config(path):
    value = exact_json(path)
    if not isinstance(value, dict) or set(value) != {"project", "service", "image", "enabled"}:
        raise ValueError("invalid_config")
    if (not isinstance(value["project"], str) or not PROJECT.fullmatch(value["project"])
            or value["service"] not in ALLOWED or not isinstance(value["image"], str)
            or not IMAGE.fullmatch(value["image"]) or value["enabled"] is not True):
        raise ValueError("invalid_config")
    return value


def docker(*args):
    result = subprocess.run(["docker", "--host", "unix:///var/run/docker.sock", *args],
                            env=DOCKER_ENV, capture_output=True, text=True, timeout=15, check=True)
    if len(result.stdout) > 16384 or result.stderr:
        raise ValueError("unexpected_docker_output")
    return result.stdout


def snapshot(cfg):
    listed = docker("container", "ls", "--all", "--no-trunc",
                    "--filter", f"label=com.docker.compose.project={cfg['project']}",
                    "--filter", f"label=com.docker.compose.service={cfg['service']}", "--format", "{{.ID}}")
    ids = listed.splitlines()
    if len(ids) != 1 or not CONTAINER_ID.fullmatch(ids[0]):
        return None
    raw = docker("container", "inspect", "--format", INSPECT, ids[0])
    item = json.loads(raw)
    if not isinstance(item, dict):
        raise ValueError("invalid_inspect")
    if (set(item) != {"id", "project", "service", "image", "running", "health", "oom", "restarts"}
            or item["project"] != cfg["project"] or item["service"] != cfg["service"]
            or item["image"] != cfg["image"] or item["id"] != ids[0]):
        raise ValueError("container_identity_mismatch")
    if (type(item["running"]) is not bool or type(item["oom"]) is not bool
            or not isinstance(item["health"], str) or type(item["restarts"]) is not int
            or item["restarts"] < 0):
        raise ValueError("invalid_inspect")
    return {"id": item["id"], "running": item["running"], "health": item["health"],
            "oom": item["oom"], "restarts": item["restarts"]}


def read_state(path):
    if not path.exists():
        return {"first_unhealthy": None, "container_id": None, "restart_count": None, "attempts": []}
    value = exact_json(path)
    if (not isinstance(value, dict)
            or set(value) != {"first_unhealthy", "container_id", "restart_count", "attempts"}
            or not isinstance(value["attempts"], list)
            or any(type(x) not in (int, float) or x < 0 for x in value["attempts"])
            or value["attempts"] != sorted(value["attempts"])
            or value["first_unhealthy"] is not None and (type(value["first_unhealthy"]) not in (int, float)
                                                   or value["first_unhealthy"] < 0)
            or value["container_id"] is not None and (not isinstance(value["container_id"], str)
                                                   or not CONTAINER_ID.fullmatch(value["container_id"]))
            or value["restart_count"] is not None and (type(value["restart_count"]) is not int
                                                    or value["restart_count"] < 0)):
        raise ValueError("invalid_state")
    return value


def atomic(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=".watchdog-", dir=path.parent)
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


def decide(current, state, now):
    if any(x > now for x in state["attempts"]):
        return "ESCALATE"  # a backward clock correction must not erase an allowance
    attempts = [x for x in state["attempts"] if now - WINDOW_SECONDS <= x <= now]
    state["attempts"] = attempts
    if current is None or not current["running"] or current["oom"]:
        state.update(first_unhealthy=None, container_id=None, restart_count=None)
        return "ESCALATE"
    if current["health"] != "unhealthy":
        state.update(first_unhealthy=None, container_id=None, restart_count=None)
        return "NO_RECOVERY" if current["health"] == "healthy" else "ESCALATE"
    if type(current["restarts"]) is not int or current["restarts"] < 0:
        raise ValueError("invalid_restart_count")
    if state["container_id"] != current["id"] or state["restart_count"] != current["restarts"]:
        state.update(first_unhealthy=now, container_id=current["id"], restart_count=current["restarts"])
        return "OBSERVING"
    if state["first_unhealthy"] is not None and state["first_unhealthy"] > now:
        return "ESCALATE"
    if state["first_unhealthy"] is None or now - state["first_unhealthy"] >= WINDOW_SECONDS:
        state["first_unhealthy"] = now
        return "OBSERVING"
    if now - state["first_unhealthy"] < MIN_UNHEALTHY_SECONDS:
        return "OBSERVING"
    if len(attempts) >= MAX_RESTARTS or attempts and now - attempts[-1] < COOLDOWN_SECONDS:
        return "ESCALATE"
    return "ELIGIBLE"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--confirm-targeted-restart", action="store_true")
    args = parser.parse_args(argv)
    report = {"schema": 1, "phase": "service-watchdog", "status": "STOP", "decision": "INCOMPLETE",
              "restart_attempted": False, "timestamp": datetime.now(timezone.utc).isoformat()}
    reserved = False
    try:
        paths = (args.config, args.state, args.report)
        if (not args.confirm_targeted_restart or any(not p.is_absolute() or ".." in p.parts
                or p.resolve() != p for p in paths) or len(set(paths)) != 3):
            raise ValueError("confirmation_or_path")
        for path in (args.state, args.report):
            require_private(path.parent, True)
        with os.fdopen(os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w") as output:
            json.dump({"status": "INCOMPLETE"}, output)
            output.flush()
            os.fsync(output.fileno())
        reserved = True
        cfg = config(args.config)
        lock_path = args.state.parent / ".watchdog.lock"
        with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "r+") as lock:
            require_private(lock_path)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = read_state(args.state)
            current = snapshot(cfg)
            now = time.time()
            decision = decide(current, state, now)
            report["decision"] = decision
            if decision == "ELIGIBLE":
                latest = snapshot(cfg)
                if latest != current:
                    state.update(first_unhealthy=None, container_id=None, restart_count=None)
                    atomic(args.state, state)
                    report["decision"] = "CHANGED_BEFORE_RESTART"
                else:
                    state["attempts"].append(now)
                    state.update(first_unhealthy=None, container_id=None, restart_count=None)
                    atomic(args.state, state)  # consume allowance before a targeted Docker mutation
                    report["restart_attempted"] = True
                    docker("container", "restart", "--time", "10", current["id"])
                    report["decision"] = "RESTARTED"
            else:
                atomic(args.state, state)
            # A restart attempt is not recovery; only a subsequent healthy check can pass.
            report["status"] = "PASS" if report["decision"] == "NO_RECOVERY" else "STOP"
    except (Exception, KeyboardInterrupt):
        pass  # Fail closed; do not print Docker output, image names or private paths.
    if not reserved:
        print("STOP")
        return 2
    try:
        require_private(args.report.parent, True)
        atomic(args.report, report)
    except (OSError, ValueError):
        print("STOP")
        return 2
    print(report["status"])
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
