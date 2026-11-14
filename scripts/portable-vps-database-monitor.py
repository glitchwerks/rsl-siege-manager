#!/usr/bin/env python3
"""Send one fresh local database observation to an independent receiver.

No schedule, monitor account setup, database write, restart, or direct Slack delivery.
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


database = sibling("portable-vps-database-signal.py", "database_monitor_observer")
sender = sibling("portable-monitor.py", "database_monitor_sender")


def root_required():
    return os.geteuid() == 0


def completed_observation(path, expected):
    database.private(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        if os.fstat(source.fileno()).st_size > 8192:
            return False
        raw = source.read(8193)
    if len(raw) > 8192:
        return False
    value = json.loads(raw)
    checks = value.get("checks") if isinstance(value, dict) else None
    return (type(value.get("schema")) is int and value["schema"] == 1
            and value.get("phase") == "database-signal"
            and value.get("status") == expected and isinstance(checks, dict)
            and set(checks) == set(database.CHECK_NAMES)
            and all(type(item) is bool for item in checks.values())
            and all(checks.values()) == (expected == "PASS"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--endpoint-file", required=True, type=Path)
    parser.add_argument("--observation-report", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--confirm-readonly-databases", action="store_true")
    parser.add_argument("--confirm-notification", action="store_true")
    args = parser.parse_args(argv)
    result = {"schema": 1, "phase": "database-monitor", "timestamp": datetime.now(timezone.utc).isoformat(),
              "status": "STOP", "observation_passed": False, "delivery_acknowledged": False}
    try:
        database.private(args.report.parent, directory=True)
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump({"status": "INCOMPLETE"}, output)
            output.flush()
            os.fsync(output.fileno())
        try:
            sender.require(root_required() and args.confirm_readonly_databases
                           and args.confirm_notification, "confirmation_required")
            paths = [args.config, args.endpoint_file, args.observation_report, args.report]
            sender.require(all(path.is_absolute() and ".." not in path.parts for path in paths)
                           and len({path.resolve() for path in paths}) == 4,
                           "distinct_absolute_paths_required")
            database.read_config(args.config)  # Fail before receiver access on invalid baseline.
            database.private(args.observation_report.parent, directory=True)
            sender.require(not args.observation_report.exists()
                           and not args.observation_report.is_symlink(), "fresh_observation_required")
            url = sender.endpoint(args.endpoint_file)
            with sender.locked(args.endpoint_file.parent):
                with redirect_stdout(io.StringIO()):
                    code = database.main(["--config", str(args.config),
                                          "--report", str(args.observation_report),
                                          "--confirm-readonly-databases"])
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
            pass  # No private baseline, credentials, URL or raw error enters evidence.
        database.publish(args.report, result)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        print("STOP")
        return 2
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
