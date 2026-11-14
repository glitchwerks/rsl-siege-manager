#!/usr/bin/env python3
"""Bounded public web/API/TLS observation from an independent Linux runner.

No host access, authentication, monitor activation, or notification delivery.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import re
import signal
import ssl
import stat
import time
from urllib.parse import urlsplit


VERSION_KEYS = {"backend_version", "frontend_version", "bot_version", "git_sha"}
CHECKS = ("apex_home", "frontend_marker", "www_redirect", "api_health", "release_identity",
          "apex_tls_expiry", "www_tls_expiry")
MAX_CONFIG = 4096
MAX_API = 4096


def config(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_CONFIG:
            raise ValueError("invalid configuration")
        raw = source.read(MAX_CONFIG + 1)
    if len(raw) > MAX_CONFIG:
        raise ValueError("invalid configuration")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"origin", "versions", "minimum_tls_days"}:
        raise ValueError("invalid configuration")
    origin = urlsplit(value["origin"])
    hostname = origin.hostname
    if (origin.scheme != "https" or not hostname or origin.netloc != hostname
            or origin.path or origin.query or origin.fragment
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{1,251}[a-z0-9])?", hostname)
            or "." not in hostname or hostname.startswith("www.")
            or hostname.endswith((".test", ".invalid", ".localhost"))
            or hostname in {"example.com", "example.net", "example.org"}
            or not isinstance(value["versions"], dict)
            or set(value["versions"]) != VERSION_KEYS
            or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9.+_-]{1,100}", item)
                       and item != "unknown" for item in value["versions"].values())
            or not re.fullmatch(r"[0-9a-f]{40}", value["versions"]["git_sha"])
            or type(value["minimum_tls_days"]) is not int
            or not 7 <= value["minimum_tls_days"] <= 60):
        raise ValueError("invalid configuration")
    return {"host": hostname, "versions": value["versions"],
            "minimum_tls_days": value["minimum_tls_days"]}


@contextmanager
def deadline(seconds=30):
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise ValueError("existing process timer")
    previous = signal.getsignal(signal.SIGALRM)

    def expired(*_args):
        raise TimeoutError("observation deadline")

    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield time.monotonic() + seconds
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def request(host, path, read_json=False):
    connection = http.client.HTTPSConnection(host, timeout=5, context=ssl.create_default_context())
    try:
        connection.connect()
        certificate = connection.sock.getpeercert()
        expiry = ssl.cert_time_to_seconds(certificate["notAfter"])
        result = {"status": None, "location": None, "json": None, "tls_expiry": expiry}
        try:
            connection.request("GET", path, headers={"Accept": "application/json" if read_json
                               else "text/html", "Connection": "close", "User-Agent": "Siege-public-signal/1"})
            response = connection.getresponse()
            result["status"] = response.status
            result["location"] = response.getheader("Location")
            if read_json:
                raw = response.read(MAX_API + 1)
                if len(raw) > MAX_API:
                    raise ValueError("API response too large")
                result["json"] = json.loads(raw)
        except (OSError, ValueError, TypeError, http.client.HTTPException, TimeoutError):
            # Preserve the verified certificate result even when HTTP fails.
            pass
        return result
    finally:
        connection.close()


def inspect(settings):
    checks = {name: False for name in CHECKS}
    tls_seen = set()
    probes = (("apex_home", settings["host"], "/", False),
              ("frontend_marker", settings["host"], "/version.json", True),
              ("www_redirect", "www." + settings["host"], "/", False),
              ("api_health", settings["host"], "/api/health", True),
              ("release_identity", settings["host"], "/api/version", True))
    with deadline() as end:
        for label, host, path, read_json in probes:
            if time.monotonic() >= end:
                break
            try:
                response = request(host, path, read_json)
                tls_label = "www_tls_expiry" if label == "www_redirect" else "apex_tls_expiry"
                fresh = response["tls_expiry"] - time.time() >= settings["minimum_tls_days"] * 86400
                checks[tls_label] = fresh if tls_label not in tls_seen else checks[tls_label] and fresh
                tls_seen.add(tls_label)
                if label == "apex_home":
                    checks[label] = response["status"] == 200
                elif label == "frontend_marker":
                    checks[label] = (response["status"] == 200 and response["json"] == {
                        "frontend_version": settings["versions"]["frontend_version"],
                        "git_sha": settings["versions"]["git_sha"]})
                elif label == "www_redirect":
                    checks[label] = (response["status"] in (301, 308)
                        and response["location"] == "https://" + settings["host"] + "/")
                elif label == "api_health":
                    checks[label] = response["status"] == 200 and response["json"] == {"status": "healthy"}
                else:
                    checks[label] = response["status"] == 200 and response["json"] == settings["versions"]
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
                    http.client.HTTPException, ssl.SSLError, TimeoutError):
                continue
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--confirm-external-readonly", action="store_true")
    args = parser.parse_args(argv)
    report = {"schema": 1, "phase": "public-signal", "timestamp": datetime.now(timezone.utc).isoformat(),
              "status": "STOP", "checks": {name: False for name in CHECKS}}
    try:
        if not args.confirm_external_readonly or args.config.resolve() == args.report.resolve():
            raise ValueError("confirmation or path mismatch")
        settings = config(args.config)
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump({"status": "INCOMPLETE"}, output)
            output.flush()
            os.fsync(output.fileno())
            try:
                report["checks"] = inspect(settings)
                report["status"] = "PASS" if all(report["checks"].values()) else "STOP"
            except (OSError, ValueError, TimeoutError):
                pass
            output.seek(0)
            output.truncate()
            json.dump(report, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        print("STOP")
        return 2
    print(report["status"])
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
