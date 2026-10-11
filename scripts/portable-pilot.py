#!/usr/bin/env python3
"""Bounded read-only observation of an already approved isolated portable pilot.

No container execution, restart, deployment, database queries, notifications or
Discord writes. PASS covers this observation only, never full pilot acceptance.
"""
import argparse
from datetime import datetime, timezone
import http.client
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit

MAX_JSON = 65536
DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
INSPECT = '{"id":{{json .Id}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},"service":{{json (index .Config.Labels "com.docker.compose.service")}},"image":{{json .Config.Image}},"running":{{json .State.Running}},"oom":{{json .State.OOMKilled}},"restarts":{{json .RestartCount}},"memory_limit":{{json .HostConfig.Memory}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"none"{{end}}}'
DEADLINE = None
STATS = '{"id":{{json .ID}},"memory":{{json .MemUsage}},"cpu":{{json .CPUPerc}}}'


class Stop(Exception):
    pass


def require(condition, label):
    if not condition:
        raise Stop(label)


def private(path, directory=False):
    meta = path.lstat()
    require((stat.S_ISDIR(meta.st_mode) if directory else stat.S_ISREG(meta.st_mode))
            and meta.st_uid == os.geteuid() and not meta.st_mode & 0o077, "unsafe_private_path")


def settings(path):
    private(path.parent, True)
    private(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        meta = os.fstat(source.fileno())
        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.geteuid()
                and not meta.st_mode & 0o077 and meta.st_size <= MAX_JSON, "unsafe_private_path")
        raw = source.read(MAX_JSON + 1)
    require(len(raw) <= MAX_JSON, "invalid_configuration")
    cfg = json.loads(raw)
    require(set(cfg) == {"project", "topology", "origin", "images", "versions", "storage_path",
                         "samples", "interval_seconds", "minimum_disk_bytes", "minimum_available_memory_bytes"},
            "invalid_configuration")
    require(cfg["topology"] in {"mom", "bundled"}
            and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", cfg["project"]), "invalid_configuration")
    bot = "mom" if cfg["topology"] == "mom" else "bot"
    cfg["services"] = ["postgres", "backend", "frontend", "proxy", bot]
    require(set(cfg["images"]) == set(cfg["services"]) and all(
        isinstance(value, str) and re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", value)
        for value in cfg["images"].values()), "invalid_images")
    require(set(cfg["versions"]) == {"backend_version", "frontend_version", "bot_version", "git_sha"}
            and all(isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9.+_-]{1,100}", value)
                    and value != "unknown" for value in cfg["versions"].values()), "invalid_versions")
    origin = urlsplit(cfg["origin"])
    require(origin.scheme == "https" and origin.netloc == origin.hostname
            and not origin.path and not origin.query and not origin.fragment
            and re.fullmatch(r"[a-z0-9.-]+", origin.hostname or "")
            and "." in origin.hostname and not origin.hostname.endswith((".test", ".invalid", ".localhost"))
            and not any(origin.hostname == host or origin.hostname.endswith("." + host)
                        for host in ("example.com", "example.net", "example.org")), "invalid_origin")
    for name, low, high in (("samples", 2, 30), ("interval_seconds", 1, 10),
                            ("minimum_disk_bytes", 1024 ** 3, 1024 ** 5),
                            ("minimum_available_memory_bytes", 128 * 1024 ** 2, 1024 ** 4)):
        require(type(cfg[name]) is int and low <= cfg[name] <= high, "invalid_configuration")
    storage = Path(cfg["storage_path"])
    require(storage.is_absolute() and storage.is_dir() and storage.resolve() == storage,
            "invalid_storage_path")
    return cfg


def budget(cap):
    remaining = cap if DEADLINE is None else min(cap, DEADLINE - time.monotonic())
    require(remaining > 0, "observation_deadline_exceeded")
    return remaining


def command(args):
    result = subprocess.run([*DOCKER, *args], capture_output=True, text=True, timeout=budget(15), check=False)
    require(result.returncode == 0 and len(result.stdout) <= MAX_JSON, "docker_read_failed")
    return result.stdout


def inventory(cfg):
    ids = command(["ps", "--all", "--quiet", "--no-trunc", "--filter",
                   "label=com.docker.compose.project=" + cfg["project"]]).splitlines()
    require(0 < len(ids) <= 16 and all(re.fullmatch(r"[0-9a-f]{64}", value) for value in ids),
            "invalid_container_inventory")
    rows = [json.loads(line) for line in command(["inspect", "--format", INSPECT, *ids]).splitlines()]
    require(len(rows) == len(ids) and {row["id"] for row in rows} == set(ids), "invalid_container_inventory")
    services = {}
    seen = set()
    maintenance = {"migrate-siege"} | ({"migrate-mom"} if cfg.get("topology") == "mom" else set())
    for row in rows:
        require(row["project"] == cfg["project"] and row["service"] not in seen,
                "invalid_container_inventory")
        seen.add(row["service"])
        if row["service"] in maintenance:
            require(row["running"] is False, "maintenance_running")
            continue
        require(row["service"] in cfg["services"], "unexpected_service")
        services[row["service"]] = row
    require(set(services) == set(cfg["services"]), "missing_service")
    return services


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise Stop("https_redirect_refused")


def endpoint(origin, route):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(origin + route, timeout=budget(5)) as response:
        require(response.status == 200, "https_probe_failed")
        deadline = time.monotonic() + budget(10)
        raw = b""
        while len(raw) <= MAX_JSON:
            require(time.monotonic() < deadline, "https_probe_timeout")
            budget(5)
            block = response.read1(min(8192, MAX_JSON + 1 - len(raw)))
            if not block:
                break
            raw += block
    require(len(raw) <= MAX_JSON, "https_response_too_large")
    return json.loads(raw)


def memory_bytes(value):
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(B|KiB|MiB|GiB|TiB)", value)
    require(match is not None, "invalid_resource_sample")
    return math.ceil(float(match[1]) * 1024 ** ("B", "KiB", "MiB", "GiB", "TiB").index(match[2]))


def resource_sample(rows):
    ids = [row["id"] for row in rows.values()]
    stats = [json.loads(line) for line in command(["stats", "--no-stream", "--no-trunc", "--format", STATS, *ids]).splitlines()]
    require(len(stats) == len(ids) and {row["id"] for row in stats} == set(ids), "invalid_resource_sample")
    by_id = {}
    for row in stats:
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)%", row["cpu"])
        require(match is not None, "invalid_resource_sample")
        cpu = float(match[1])
        require(math.isfinite(cpu), "invalid_resource_sample")
        by_id[row["id"]] = {"memory_bytes": memory_bytes(row["memory"].split(" / ")[0]),
                            "cpu_percent": cpu}
    return {name: by_id[row["id"]] for name, row in rows.items()}


def host_sample(cfg, rows):
    data = command(["inspect", "--format", '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{json .Source}}{{end}}{{end}}', rows["postgres"]["id"]])
    require(Path(json.loads(data)).resolve() == Path(cfg["storage_path"]), "storage_mount_mismatch")
    mem = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    available = int(mem["MemAvailable"].split()[0]) * 1024
    filesystem = os.statvfs(cfg["storage_path"])
    return {"available_memory_bytes": available, "disk_available_bytes": filesystem.f_bavail * filesystem.f_frsize,
            "host_memory_bytes": int(mem["MemTotal"].split()[0]) * 1024, "logical_cpus": os.cpu_count()}


def observe(cfg, report):
    checks = report["checks"]
    peaks = report["observed_maximums"] = {name: {"memory_bytes": 0, "cpu_percent": 0.0} for name in cfg["services"]}
    report["samples_planned"] = cfg["samples"]
    report["interval_seconds"] = cfg["interval_seconds"]
    first = None
    for number in range(cfg["samples"]):
        rows = inventory(cfg)
        identity = {name: (row["id"], row["restarts"]) for name, row in rows.items()}
        if first is None:
            first = identity
        checks["stable_containers"] = checks.get("stable_containers", True) and first == identity
        for name, row in rows.items():
            good = (row["running"] is True and row["oom"] is False and row["image"] == cfg["images"][name]
                    and type(row["memory_limit"]) is int and row["memory_limit"] > 0
                    and (row["health"] == "healthy" if name != "proxy" else True))
            checks[name + "_runtime"] = checks.get(name + "_runtime", True) and good
        resources = resource_sample(rows)
        for name, values in resources.items():
            for metric, value in values.items():
                peaks[name][metric] = max(peaks[name][metric], value)
            checks[name + "_memory_headroom"] = checks.get(name + "_memory_headroom", True) and (
                values["memory_bytes"] < rows[name]["memory_limit"] * 0.85)
        host = host_sample(cfg, rows)
        report["host"] = host
        checks["host_memory_headroom"] = checks.get("host_memory_headroom", True) and host["available_memory_bytes"] >= cfg["minimum_available_memory_bytes"]
        checks["database_disk_headroom"] = checks.get("database_disk_headroom", True) and host["disk_available_bytes"] >= cfg["minimum_disk_bytes"]
        checks["https_database_health"] = checks.get("https_database_health", True) and endpoint(cfg["origin"], "/api/health") == {"status": "healthy"}
        checks["expected_component_versions"] = checks.get("expected_component_versions", True) and endpoint(cfg["origin"], "/api/version") == cfg["versions"]
        report["samples_completed"] = number + 1
        if number + 1 < cfg["samples"]:
            time.sleep(cfg["interval_seconds"])
    report["result"] = "PASS" if checks and all(checks.values()) else "STOP"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--confirm-readonly-pilot", action="store_true")
    args = parser.parse_args(argv)
    report = {"schema": 1, "phase": "pilot-observation", "timestamp": datetime.now(timezone.utc).isoformat(),
              "checks": {}, "result": "STOP", "samples_completed": 0, "pilot_accepted": False}
    started = time.monotonic()
    global DEADLINE
    DEADLINE = started + 600
    try:
        private(args.report.parent, True)
        with os.fdopen(os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w") as output:
            json.dump({"result": "INCOMPLETE", "pilot_accepted": False}, output)
            output.flush()
            os.fsync(output.fileno())
            try:
                require(args.confirm_readonly_pilot, "readonly_pilot_confirmation_required")
                observe(settings(args.config), report)
            except (Stop, OSError, ValueError, KeyError, TypeError, AttributeError, ArithmeticError, subprocess.SubprocessError, http.client.HTTPException, KeyboardInterrupt) as error:
                report["error"] = str(error) if isinstance(error, Stop) else "observation_failed"
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            output.seek(0)
            output.truncate()
            json.dump(report, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    except (Stop, OSError):
        report["result"] = "STOP"
        report["error"] = "report_write_failed"
    print(json.dumps(report))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
