#!/usr/bin/env python3
"""Send one fresh local VPS host observation to an independent receiver.

No schedule, direct Slack delivery, service restart, backup or deletion.
"""

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path


def sibling(name, module_name):
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).with_name(name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = sibling("portable-vps-host-signal.py", "host_monitor_observer")
sender = sibling("portable-monitor.py", "host_monitor_sender")


def completed_observation(path, expected):
    host.private(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        if os.fstat(source.fileno()).st_size > 8192:
            return False
        raw = source.read(8193)
    if len(raw) > 8192:
        return False
    value = json.loads(raw)
    if not isinstance(value, dict):
        return False
    checks = value.get("checks")
    measurements = value.get("measurements")
    return (value.get("schema") == 1 and value.get("phase") == "host-signal"
            and value.get("status") == expected
            and isinstance(checks, dict) and set(checks) == set(host.CHECKS)
            and all(type(item) is bool for item in checks.values())
            and all(checks.values()) == (expected == "PASS")
            and isinstance(measurements, dict)
            and set(measurements) == {"available_memory_bytes", "swap_used_bytes",
                                      "oom_kills_since_boot", "database_disk_available_bytes",
                                      "backup_disk_available_bytes"}
            and all(type(item) is int and item >= 0 for item in measurements.values()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--endpoint-file", type=Path, required=True)
    parser.add_argument("--observation-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--confirm-readonly-host", action="store_true")
    parser.add_argument("--confirm-notification", action="store_true")
    args = parser.parse_args(argv)
    result = {"schema": 1, "phase": "host-monitor", "timestamp": datetime.now(timezone.utc).isoformat(),
              "status": "STOP", "observation_passed": False, "delivery_acknowledged": False}
    try:
        host.private(args.report.parent, directory=True)
        with os.fdopen(os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w") as output:
            json.dump({"status": "INCOMPLETE"}, output)
            output.flush()
            os.fsync(output.fileno())
        try:
            sender.require(args.confirm_readonly_host and args.confirm_notification,
                           "confirmation_required")
            paths = [args.config, args.endpoint_file, args.observation_report, args.report]
            sender.require(all(path.is_absolute() and ".." not in path.parts
                               and path.resolve() == path for path in paths)
                           and len({path.resolve() for path in paths}) == 4,
                           "distinct_absolute_paths_required")
            host.configuration(args.config)
            host.private(args.observation_report.parent, directory=True)
            sender.require(not args.observation_report.exists()
                           and not args.observation_report.is_symlink(), "fresh_observation_required")
            url = sender.endpoint(args.endpoint_file)
            with sender.locked(args.endpoint_file.parent):
                with redirect_stdout(io.StringIO()):
                    code = host.main(["--config", str(args.config),
                                      "--report", str(args.observation_report),
                                      "--confirm-readonly-host"])
                if code in (0, 1):
                    passed = code == 0
                    sender.require(completed_observation(args.observation_report,
                                                         "PASS" if passed else "STOP"),
                                   "observation_incomplete")
                    result["observation_passed"] = passed
                    sender.deliver(url, passed)
                    result["delivery_acknowledged"] = True
                    if passed:
                        result["status"] = "PASS"
        except (Exception, KeyboardInterrupt):
            pass  # Only fixed booleans/status, never private host or URL details.
        host.publish(args.report, result)
    except (OSError, ValueError, TypeError, KeyError):
        print("STOP")
        return 2
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
