#!/usr/bin/env python3
"""Read-only VPS memory, OOM and local storage pressure observation.

No schedule, receiver notification, service restart, backup or deletion.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import stat
import tempfile
import uuid


MIB = 1024 ** 2
GIB = 1024 ** 3
CHECKS = ("same_boot", "no_new_oom", "memory_headroom", "swap_headroom",
          "database_disk_headroom", "backup_disk_headroom")


def unique_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("invalid_configuration")
        value[key] = item
    return value


def private(path, directory=False):
    meta = path.lstat()
    kind = stat.S_ISDIR(meta.st_mode) if directory else stat.S_ISREG(meta.st_mode)
    if not kind or meta.st_uid != os.geteuid() or meta.st_mode & 0o077:
        raise ValueError("unsafe_private_path")


def configuration(path):
    private(path.parent, True)
    private(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        if os.fstat(source.fileno()).st_size > 8192:
            raise ValueError("invalid_configuration")
        raw = source.read(8193)
    if len(raw) > 8192:
        raise ValueError("invalid_configuration")
    value = json.loads(raw, object_pairs_hook=unique_keys)
    keys = {"database_storage_path", "backup_state_root", "minimum_available_memory_bytes",
            "maximum_swap_used_bytes", "minimum_database_disk_bytes", "minimum_backup_disk_bytes",
            "baseline_boot_id", "baseline_oom_kills"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("invalid_configuration")
    for name in ("database_storage_path", "backup_state_root"):
        item = value[name]
        if not isinstance(item, str) or not Path(item).is_absolute() or ".." in Path(item).parts:
            raise ValueError("invalid_configuration")
        path_value = Path(item)
        if not path_value.is_dir() or path_value.resolve() != path_value:
            raise ValueError("invalid_configuration")
    for name, lower, upper in (("minimum_available_memory_bytes", 128 * MIB, 1024 * GIB),
                               ("maximum_swap_used_bytes", 0, 1024 * GIB),
                               ("minimum_database_disk_bytes", GIB, 1024 ** 5),
                               ("minimum_backup_disk_bytes", GIB, 1024 ** 5),
                               ("baseline_oom_kills", 0, 10 ** 12)):
        if type(value[name]) is not int or not lower <= value[name] <= upper:
            raise ValueError("invalid_configuration")
    try:
        if str(uuid.UUID(value["baseline_boot_id"])) != value["baseline_boot_id"]:
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise ValueError("invalid_configuration") from None
    return value


def counters(raw, labels):
    found = {}
    for line in raw.splitlines():
        match = re.fullmatch(r"([A-Za-z_]+):?\s+(\d+)(?:\s+kB)?", line)
        if match and match[1] in labels:
            if match[1] in found:
                raise ValueError("invalid_host_counters")
            found[match[1]] = int(match[2])
    if set(found) != set(labels):
        raise ValueError("invalid_host_counters")
    return found


def available(path):
    info = os.statvfs(path)
    return info.f_bavail * info.f_frsize


@contextmanager
def deadline():
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise ValueError("existing_process_timer")
    previous = signal.getsignal(signal.SIGALRM)

    def expired(*_):
        raise TimeoutError

    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, 10)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def inspect(cfg, *, meminfo=None, vmstat=None, boot_id=None, disk=None):
    memory = counters(Path("/proc/meminfo").read_text() if meminfo is None else meminfo,
                      ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"))
    pressure = counters(Path("/proc/vmstat").read_text() if vmstat is None else vmstat,
                        ("oom_kill",))
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip() if boot_id is None else boot_id
    if memory["MemAvailable"] > memory["MemTotal"] or memory["SwapFree"] > memory["SwapTotal"]:
        raise ValueError("invalid_host_counters")
    free_space = available if disk is None else disk
    metrics = {"available_memory_bytes": memory["MemAvailable"] * 1024,
               "swap_used_bytes": (memory["SwapTotal"] - memory["SwapFree"]) * 1024,
               "oom_kills_since_boot": pressure["oom_kill"],
               "database_disk_available_bytes": free_space(cfg["database_storage_path"]),
               "backup_disk_available_bytes": free_space(cfg["backup_state_root"])}
    checks = {"same_boot": boot == cfg["baseline_boot_id"],
              "no_new_oom": boot == cfg["baseline_boot_id"]
                            and pressure["oom_kill"] == cfg["baseline_oom_kills"],
              "memory_headroom": metrics["available_memory_bytes"] >= cfg["minimum_available_memory_bytes"],
              "swap_headroom": metrics["swap_used_bytes"] <= cfg["maximum_swap_used_bytes"],
              "database_disk_headroom": metrics["database_disk_available_bytes"] >= cfg["minimum_database_disk_bytes"],
              "backup_disk_headroom": metrics["backup_disk_available_bytes"] >= cfg["minimum_backup_disk_bytes"]}
    return checks, metrics


def publish(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=".host-signal-", dir=path.parent)
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
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--confirm-readonly-host", action="store_true")
    args = parser.parse_args(argv)
    result = {"schema": 1, "phase": "host-signal", "timestamp": datetime.now(timezone.utc).isoformat(),
              "status": "STOP", "checks": {name: False for name in CHECKS}, "measurements": {}}
    try:
        if (not args.confirm_readonly_host or not args.config.is_absolute() or not args.report.is_absolute()
                or ".." in args.config.parts or ".." in args.report.parts
                or args.config.resolve() != args.config
                or args.report.parent.resolve() != args.report.parent
                or args.config.resolve() == args.report.resolve()):
            raise ValueError("confirmation_required")
        cfg = configuration(args.config)
        private(args.report.parent, True)
        with os.fdopen(os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w") as output:
            json.dump({"status": "INCOMPLETE"}, output)
            output.flush()
            os.fsync(output.fileno())
        try:
            with deadline():
                checks, measurements = inspect(cfg)
            result["checks"] = checks
            result["measurements"] = measurements
            result["status"] = "PASS" if all(checks.values()) else "STOP"
        except (OSError, ValueError, TypeError, KeyError, TimeoutError):
            result["status"] = "INCOMPLETE"
        publish(args.report, result)
    except (OSError, ValueError, TypeError, KeyError):
        print("STOP")
        return 2
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1 if result["status"] == "STOP" else 2


if __name__ == "__main__":
    raise SystemExit(main())
