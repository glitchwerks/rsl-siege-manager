#!/usr/bin/env python3
"""Separately gated isolated sidecar acceptance; no activation or automatic cleanup."""
import argparse
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import struct
import zlib
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

SPEC = importlib.util.spec_from_file_location("signal_helpers", Path(__file__).with_name("portable-monitor.py"))
helpers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helpers)
Stop, require = helpers.Stop, helpers.require
MAX_RESPONSE = 1024 * 1024
def png_chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)


PNG = (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
       + png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x80\xff")) + png_chunk(b"IEND", b""))


def read_private(path, limit):
    helpers.pilot.private(path.parent, True)
    helpers.pilot.private(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        meta = os.fstat(source.fileno())
        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.geteuid()
                and not meta.st_mode & 0o077 and meta.st_size <= limit, "unsafe_input")
        value = source.read(limit + 1)
    require(len(value) <= limit, "oversized_input")
    return value


def origin(value, sidecar=False):
    require(isinstance(value, str), "invalid_target")
    url = urllib.parse.urlsplit(value)
    local = sidecar and value == "http://127.0.0.1:8001"
    require(local or (url.scheme == "https" and url.netloc == url.hostname
                     and re.fullmatch(r"[a-z0-9.-]+", url.hostname or "")
                     and "." in url.hostname and not url.path and not url.query and not url.fragment
                     and not url.hostname.endswith((".invalid", ".localhost", ".test"))
                     and not any(url.hostname == host or url.hostname.endswith("." + host)
                                 for host in ("example.com", "example.net", "example.org"))), "invalid_target")
    return value


def settings(path, token_path):
    cfg = json.loads(read_private(path, 16384))
    require(isinstance(cfg, dict) and set(cfg) == {"schema", "dedicated_test_identity", "origin",
            "sidecar_origin", "versions", "member_id", "username", "channel_name"}, "invalid_configuration")
    require(type(cfg["schema"]) is int and cfg["schema"] == 1
            and cfg["dedicated_test_identity"] is True, "dedicated_identity_required")
    origin(cfg["origin"])
    origin(cfg["sidecar_origin"], True)
    keys = {"backend_version", "frontend_version", "bot_version", "git_sha"}
    require(isinstance(cfg["versions"], dict) and set(cfg["versions"]) == keys
            and all(isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9.+_-]{1,100}", value)
                    and value != "unknown" for value in cfg["versions"].values()), "invalid_versions")
    require(isinstance(cfg["member_id"], str) and re.fullmatch(r"[0-9]{17,20}", cfg["member_id"])
            and isinstance(cfg["username"], str) and re.fullmatch(r"[a-z0-9_.]{2,32}", cfg["username"])
            and isinstance(cfg["channel_name"], str)
            and re.fullmatch(r"rsl-smoke-[a-z0-9-]{1,60}", cfg["channel_name"]), "invalid_test_destinations")
    token = read_private(token_path, 4096).decode().strip()
    require(re.fullmatch(r"[A-Za-z0-9._~+/-]{16,1024}=*", token) is not None, "invalid_token")
    return cfg, token


class Client:
    def __init__(self, cfg, token):
        self.cfg, self.token = cfg, token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), helpers.NoRedirect())
        self.deadline = time.monotonic() + 150

    def request(self, target, path, expected=200, auth=True, data=None, content_type=None):
        require(time.monotonic() < self.deadline, "integration_deadline_exceeded")
        headers = {"Authorization": "Bearer " + self.token} if auth else {}
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.cfg[target] + path, data=data, headers=headers,
                                         method="GET" if data is None else "POST")
        try:
            with helpers.delivery_deadline():
                try:
                    response = self.opener.open(request, timeout=10)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    require(response.status == expected, "unexpected_http_status")
                    raw = response.read(MAX_RESPONSE + 1)
                    require(len(raw) <= MAX_RESPONSE, "oversized_response")
                    return json.loads(raw)
        except Stop:
            raise
        except Exception:
            raise Stop("request_failed") from None


def check(report, name, condition):
    require(condition, name)
    report["checks"][name] = True


def observe(client, cfg, report):
    health = client.request("origin", "/api/health", auth=False)
    check(report, "siege_health", isinstance(health, dict) and health.get("status") == "healthy")
    versions = client.request("origin", "/api/version", auth=False)
    check(report, "siege_identity", isinstance(versions, dict)
          and all(versions.get(key) == value for key, value in cfg["versions"].items()))
    health = client.request("sidecar_origin", "/api/health", auth=False)
    check(report, "sidecar_connected", isinstance(health, dict) and health.get("status") == "healthy"
          and health.get("bot_connected") is True)
    version = client.request("sidecar_origin", "/api/version", auth=False)
    check(report, "sidecar_identity", isinstance(version, dict)
          and version.get("version") == cfg["versions"]["bot_version"])
    client.request("sidecar_origin", "/api/members", expected=403, auth=False)
    check(report, "missing_auth_rejected", True)
    original = client.token
    try:
        client.token = "invalid-smoke-" + report["correlation_id"]
        client.request("sidecar_origin", "/api/members", expected=401)
    finally:
        client.token = original
    check(report, "wrong_auth_rejected", True)
    members = client.request("sidecar_origin", "/api/members")
    valid = isinstance(members, list) and all(isinstance(row, dict)
        and set(row) == {"id", "username", "display_name"}
        and all(isinstance(value, str) for value in row.values())
        and re.fullmatch(r"[0-9]{17,20}", row["id"]) for row in members)
    check(report, "member_list_shape", valid)
    matches = [row for row in members if row["username"].lower() == cfg["username"].lower()]
    check(report, "unique_test_recipient", len(matches) == 1 and matches[0]["id"] == cfg["member_id"])
    member = client.request("sidecar_origin", "/api/members/" + cfg["member_id"])
    check(report, "member_lookup", isinstance(member, dict) and member.get("is_member") is True
          and member.get("discord_id") == cfg["member_id"] and member.get("username") == cfg["username"]
          and isinstance(member.get("display_name"), str)
          and all(isinstance(member.get(key), list) and all(isinstance(v, str) for v in member[key])
                  for key in ("roles", "role_names")))


def write_checks(client, cfg, report):
    # Invalid body is rejected by validation before any Discord send.
    result = client.request("sidecar_origin", "/api/post-message", expected=422,
                            data=b"{}", content_type="application/json")
    check(report, "controlled_validation_error", isinstance(result, dict) and isinstance(result.get("detail"), list)
          and len(result["detail"]) > 0)
    text = "[rsl-smoke " + report["correlation_id"] + "] isolated integration test"
    for label, route, payload in (
        ("dm_accepted", "/api/notify", {"username": cfg["username"], "message": text}),
        ("text_accepted", "/api/post-message", {"channel_name": cfg["channel_name"], "message": text}),
    ):
        report["writes_attempted"] += 1
        report["cleanup_required"] = True
        result = client.request("sidecar_origin", route, data=json.dumps(payload).encode(), content_type="application/json")
        check(report, label, isinstance(result, dict) and result.get("status") == "sent")
    boundary = "rsl-smoke-" + report["correlation_id"]
    filename = boundary + ".png"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"channel_name\"\r\n\r\n"
            f"{cfg['channel_name']}\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{filename}\"\r\nContent-Type: image/png\r\n\r\n").encode() + PNG + f"\r\n--{boundary}--\r\n".encode()
    report["writes_attempted"] += 1
    report["cleanup_required"] = True
    result = client.request("sidecar_origin", "/api/post-image", data=body,
                            content_type="multipart/form-data; boundary=" + boundary)
    check(report, "image_accepted", isinstance(result, dict) and result.get("status") == "sent"
          and isinstance(result.get("url"), str) and result["url"].startswith("https://"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("readonly", "full"))
    for name in ("config", "token-file", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--confirm-isolated-target", action="store_true")
    parser.add_argument("--confirm-discord-writes", action="store_true")
    args = parser.parse_args(argv)
    report = {"schema": 1, "phase": "integration-smoke", "mode": args.mode,
              "timestamp": datetime.now(timezone.utc).isoformat(), "correlation_id": str(uuid.uuid4()),
              "checks": {}, "writes_attempted": 0, "cleanup_required": False,
              "pilot_accepted": False, "delivery_observed": False, "result": "STOP"}
    try:
        helpers.pilot.private(args.report.parent, True)
        with os.fdopen(os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w") as output:
            json.dump({**report, "result": "INCOMPLETE", "cleanup_required": args.mode == "full",
                       "writes_attempted": None}, output)
            output.flush()
            os.fsync(output.fileno())
            try:
                require(args.confirm_isolated_target, "isolated_target_confirmation_required")
                require(args.mode != "full" or args.confirm_discord_writes, "discord_write_confirmation_required")
                require(len({p.resolve() for p in (args.config, args.token_file, args.report)}) == 3,
                        "distinct_paths_required")
                cfg, token = settings(args.config, args.token_file)
                lock = args.config.parent / ".integration-smoke.lock"
                fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
                try:
                    meta = os.fstat(fd)
                    require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.geteuid()
                            and not meta.st_mode & 0o077, "unsafe_lock")
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise Stop("integration_already_running") from None
                    client = Client(cfg, token)
                    observe(client, cfg, report)
                    if args.mode == "full":
                        write_checks(client, cfg, report)
                    report["result"] = "PASS"
                finally:
                    os.close(fd)
            except (Stop, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as error:
                report["error"] = str(error) if isinstance(error, Stop) else "integration_failed"
            output.seek(0)
            output.truncate()
            json.dump(report, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    except (Stop, OSError):
        report.update(result="STOP", error="report_write_failed")
    print(json.dumps(report))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
