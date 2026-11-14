#!/usr/bin/env python3
"""Send a fresh off-host public observation to an independent missed-signal receiver.

No schedule, monitor account setup, VPS access, restart, or direct Slack delivery.
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


public = sibling("portable-vps-public-signal.py", "public_monitor_observer")
sender = sibling("portable-monitor.py", "public_monitor_sender")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--endpoint-file", required=True, type=Path)
    parser.add_argument("--observation-report", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--confirm-external-readonly", action="store_true")
    parser.add_argument("--confirm-notification", action="store_true")
    args = parser.parse_args(argv)
    result = {"schema": 1, "phase": "public-monitor", "timestamp": datetime.now(timezone.utc).isoformat(),
              "status": "STOP", "observation_passed": False, "delivery_acknowledged": False}
    try:
        sender.pilot.private(args.report.parent, True)
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump({"status": "INCOMPLETE"}, output)
            output.flush()
            os.fsync(output.fileno())
        try:
            sender.require(args.confirm_external_readonly and args.confirm_notification,
                           "confirmation_required")
            paths = [args.config, args.endpoint_file, args.observation_report, args.report]
            sender.require(all(path.is_absolute() for path in paths)
                           and len({path.resolve() for path in paths}) == 4, "distinct_absolute_paths_required")
            sender.pilot.private(args.config.parent, True)
            sender.pilot.private(args.config)
            public.config(args.config)  # Validate target before any notification or public request.
            sender.pilot.private(args.observation_report.parent, True)
            sender.require(not args.observation_report.exists()
                           and not args.observation_report.is_symlink(), "fresh_observation_required")
            url = sender.endpoint(args.endpoint_file)
            with sender.locked(args.endpoint_file.parent):
                with redirect_stdout(io.StringIO()):
                    code = public.main(["--config", str(args.config),
                                        "--report", str(args.observation_report),
                                        "--confirm-external-readonly"])
                if code in (0, 1):
                    result["observation_passed"] = code == 0
                    sender.deliver(url, code == 0)
                    result["delivery_acknowledged"] = True
                    if code == 0:
                        result["status"] = "PASS"
        except (Exception, KeyboardInterrupt):
            pass  # Report only fixed booleans; never expose target or endpoint details.
        public.publish_report(args.report, result)
    except (OSError, ValueError, sender.Stop):
        print("STOP")
        return 2
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
