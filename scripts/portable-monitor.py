#!/usr/bin/env python3
"""Opt-in fresh portable observation and independent missed-signal delivery.

No scheduling, restart, deployment or direct Slack message. Notification and
read-only pilot approval are separate required gates on every invocation.
"""
import argparse
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
import fcntl
import importlib.util
import io
import json
import os
import stat
import signal
from pathlib import Path
import urllib.parse
import urllib.request

SPEC = importlib.util.spec_from_file_location("monitor_pilot", Path(__file__).with_name("portable-pilot.py"))
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)
require, Stop = pilot.require, pilot.Stop


def endpoint(path):
    pilot.private(path.parent, True)
    pilot.private(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        meta = os.fstat(source.fileno())
        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.geteuid()
                and not meta.st_mode & 0o077 and meta.st_size <= 4096, "unsafe_endpoint_file")
        raw = source.read(4097)
    require(len(raw) <= 4096, "invalid_endpoint")
    value = raw.decode("utf-8").strip()
    url = urllib.parse.urlsplit(value)
    require(url.scheme == "https" and url.hostname and url.netloc == url.hostname
            and not url.query and not url.fragment and url.path not in {"", "/"}
            and not url.path.rstrip("/").endswith(("/fail", "/start", "/log"))
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value), "invalid_endpoint")
    return value.rstrip("/")


@contextmanager
def locked(directory):
    fd = os.open(directory / ".portable-monitor.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        meta = os.fstat(fd)
        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.geteuid()
                and not meta.st_mode & 0o077, "unsafe_monitor_lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop("monitor_already_running") from None
        yield
    finally:
        os.close(fd)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Stop("signal_redirect_refused")


@contextmanager
def delivery_deadline():
    # Bound the whole request as well as individual socket operations on Linux.
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "existing_process_timer")
    previous = signal.getsignal(signal.SIGALRM)

    def expired(*args):
        raise Stop("signal_delivery_timeout")

    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, 10)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def deliver(url, success):
    # Bearer-like endpoint stays private. No diagnostic body, headers or retries.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with delivery_deadline(), opener.open(urllib.request.Request(url + ("" if success else "/fail"),
                                               data=b"", method="POST"), timeout=10) as response:
            # Some receivers return HTTP 200 for unknown/rate-limited checks.
            require(response.status == 200, "signal_not_acknowledged")
            body = response.read(33)
            require(len(body) <= 32 and body.strip() == b"OK",
                    "signal_not_acknowledged")
    except Stop:
        raise
    except Exception:
        raise Stop("signal_delivery_failed") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--endpoint-file", type=Path, required=True)
    parser.add_argument("--observation-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--confirm-readonly-pilot", action="store_true")
    parser.add_argument("--confirm-notification", action="store_true")
    args = parser.parse_args(argv)
    report = {"schema": 1, "phase": "monitor-signal", "timestamp": datetime.now(timezone.utc).isoformat(),
              "observation_passed": False, "delivery_acknowledged": False,
              "pilot_accepted": False, "result": "STOP"}
    try:
        pilot.private(args.report.parent, True)
        with os.fdopen(os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w") as output:
            json.dump({"result": "INCOMPLETE", "pilot_accepted": False}, output)
            output.flush()
            os.fsync(output.fileno())
            try:
                require(args.confirm_readonly_pilot, "readonly_pilot_confirmation_required")
                require(args.confirm_notification, "notification_confirmation_required")
                # Prevent an old PASS from being reused or any input being overwritten.
                paths = [args.config, args.endpoint_file, args.observation_report, args.report]
                require(len({path.resolve() for path in paths}) == 4, "distinct_paths_required")
                pilot.private(args.observation_report.parent, True)
                require(not args.observation_report.exists() and not args.observation_report.is_symlink(),
                        "fresh_observation_report_required")
                url = endpoint(args.endpoint_file)
                with locked(args.endpoint_file.parent):
                    # Reuse the reviewed observation, not an operator-supplied PASS file.
                    # Suppress its stdout so only this fixed summary reaches the caller.
                    with redirect_stdout(io.StringIO()):
                        code = pilot.main(["--config", str(args.config), "--report", str(args.observation_report),
                                           "--confirm-readonly-pilot"])
                    report["observation_passed"] = code == 0
                    deliver(url, code == 0)
                    report["delivery_acknowledged"] = True
                    if code == 0:
                        report["result"] = "PASS"
                    else:
                        report["error"] = "observation_failed"
            except (Stop, OSError, ValueError, TypeError, KeyboardInterrupt) as error:
                report["error"] = str(error) if isinstance(error, Stop) else "monitor_failed"
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
