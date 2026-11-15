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
import stat


def sibling(name, module_name):
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).with_name(name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


public = sibling("portable-vps-public-signal.py", "public_monitor_observer")
sender = sibling("portable-monitor.py", "public_monitor_sender")
SIGNALS = {
    "website": ("apex_home", "www_redirect", "apex_tls_expiry", "www_tls_expiry"),
    "api": ("api_health",),
    "identity": ("frontend_marker", "release_identity"),
}


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate observation key")
        result[key] = value
    return result


def completed_observation(path, code):
    """Accept only this invocation's complete, private fixed-label result."""
    if code not in (0, 1):
        return None
    sender.pilot.private(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        meta = os.fstat(source.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid() or meta.st_mode & 0o077:
            return None
        raw = source.read(4097)
    if len(raw) > 4096:
        return None
    value = json.loads(raw, object_pairs_hook=unique_pairs)
    if not isinstance(value, dict) or set(value) != {"schema", "phase", "timestamp", "status", "checks"}:
        return None
    checks = value["checks"]
    if (value["schema"] != 1 or value["phase"] != "public-signal"
            or not isinstance(value["timestamp"], str)
            or not isinstance(checks, dict) or set(checks) != set(public.CHECKS)
            or not all(type(item) is bool for item in checks.values())
            or value["status"] != ("PASS" if all(checks.values()) else "STOP")
            or (code == 0) != all(checks.values())):
        return None
    return {name: all(checks[label] for label in labels) for name, labels in SIGNALS.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--endpoint-directory", required=True, type=Path)
    parser.add_argument("--observation-report", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--confirm-external-readonly", action="store_true")
    parser.add_argument("--confirm-notification", action="store_true")
    args = parser.parse_args(argv)
    result = {"schema": 1, "phase": "public-monitor", "timestamp": datetime.now(timezone.utc).isoformat(),
              "status": "STOP", "observation_passed": False, "delivery_acknowledged": False,
              "signals": {name: {"healthy": False, "delivery_acknowledged": False} for name in SIGNALS}}
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
            endpoints = {name: args.endpoint_directory / f"{name}-url" for name in SIGNALS}
            paths = [args.config, args.observation_report, args.report, *endpoints.values()]
            sender.require(all(path.is_absolute() for path in paths)
                           and args.endpoint_directory.is_absolute()
                           and len({path.resolve() for path in paths}) == len(paths),
                           "distinct_absolute_paths_required")
            sender.pilot.private(args.config.parent, True)
            sender.pilot.private(args.config)
            public.config(args.config)  # Validate target before any notification or public request.
            sender.pilot.private(args.observation_report.parent, True)
            sender.require(not args.observation_report.exists()
                           and not args.observation_report.is_symlink(), "fresh_observation_required")
            sender.pilot.private(args.endpoint_directory, True)
            urls = {name: sender.endpoint(path) for name, path in endpoints.items()}
            sender.require(len(set(urls.values())) == len(urls), "distinct_receivers_required")
            with sender.locked(args.endpoint_directory):
                with redirect_stdout(io.StringIO()):
                    code = public.main(["--config", str(args.config),
                                        "--report", str(args.observation_report),
                                        "--confirm-external-readonly"])
                observed = completed_observation(args.observation_report, code)
                if observed is not None:
                    result["observation_passed"] = all(observed.values())
                    for name, healthy in observed.items():
                        result["signals"][name]["healthy"] = healthy
                        try:
                            sender.deliver(urls[name], healthy)
                            result["signals"][name]["delivery_acknowledged"] = True
                        except sender.Stop:
                            pass
                    result["delivery_acknowledged"] = all(
                        item["delivery_acknowledged"] for item in result["signals"].values())
                    if result["observation_passed"] and result["delivery_acknowledged"]:
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
