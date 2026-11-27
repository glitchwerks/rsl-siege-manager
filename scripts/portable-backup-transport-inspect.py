#!/usr/bin/env python3
"""One read-only, sanitized inspection of a protected backup transport.

Run as root on the VPS. Reads the installed private backup profile/config,
checks the NAS route, and lists only the configured remote directory with
rclone. Never uploads, downloads, modifies services, or writes local files.
"""

import argparse
import configparser
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess


PROFILE = Path("/srv/rsl-siege/backup-candidate/job.json")
BACKENDS = {"smb", "sftp", "s3", "azureblob"}


def private_bytes(path, limit):
    path = Path(path)
    if not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("unsafe_path")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > limit:
        raise ValueError("unsafe_file")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev != info.st_dev or opened.st_ino != info.st_ino or opened.st_uid != 0
                or not stat.S_ISREG(opened.st_mode) or opened.st_mode & 0o077):
            raise ValueError("changed_file")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("oversize_file")
    return raw


def rclone_failure(stderr):
    value = stderr[:8192].lower()
    if any(marker in value for marker in (b"logon failure", b"invalid credentials", b"bad password",
                                           b"authentication failed", b"bad username")):
        return "authentication"
    if any(marker in value for marker in (b"access denied", b"permission denied", b"access is denied")):
        return "remote_permission"
    if any(marker in value for marker in (b"no such host", b"name or service not known", b"dns lookup")):
        return "name_resolution"
    if any(marker in value for marker in (b"connection refused", b"network is unreachable", b"no route to host",
                                           b"i/o timeout", b"connection timed out", b"context deadline exceeded")):
        return "remote_connection"
    if any(marker in value for marker in (b"network name cannot be found", b"share not found")):
        return "remote_share"
    return "remote_read_unknown"


def tailscale_state():
    try:
        result = subprocess.run(["tailscale", "status", "--json"], capture_output=True, timeout=8, check=False)
        if result.returncode or len(result.stdout) > 2_000_000:
            return "UNKNOWN"
        value = json.loads(result.stdout)
        return "PASS" if value.get("BackendState") == "Running" and value.get("Self", {}).get("Online") else "STOP"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "UNKNOWN"


def resolved_addresses(host):
    """Bound resolver time and return only validated numeric addresses."""
    try:
        result = subprocess.run(["getent", "ahosts", host], capture_output=True, timeout=8, check=False)
        if result.returncode or len(result.stdout) > 8192:
            return []
        addresses = []
        for line in result.stdout.decode("ascii", "replace").splitlines():
            value = line.split()[0] if line.split() else ""
            try:
                address = str(ipaddress.ip_address(value))
            except ValueError:
                continue
            if address not in addresses:
                addresses.append(address)
        return addresses[:4]
    except (OSError, subprocess.TimeoutExpired):
        return []


def validated_remote(parser, prefix):
    """Mirror the backup copy path's accepted backend and credential settings."""
    if parser.sections() != ["backup"] or parser.defaults() or parser["backup"].get("type") not in BACKENDS:
        raise ValueError("invalid_config")
    remote = parser["backup"]
    kind = remote["type"]
    allowed = {
        "s3": {"provider", "access_key_id", "secret_access_key", "region", "endpoint", "location_constraint"},
        "azureblob": {"account", "key", "sas_url"},
        "smb": {"host", "user", "pass", "domain", "port"},
        "sftp": {"host", "user", "port", "pass", "key_file", "known_hosts_file", "disable_hashcheck", "shell_type"},
    }
    values = dict(remote)
    if not set(values) <= {"type"} | allowed[kind]:
        raise ValueError("unapproved_remote_setting")
    if kind == "s3":
        if not values.get("access_key_id") or not values.get("secret_access_key") or (
                values.get("endpoint") and not values["endpoint"].startswith("https://")):
            raise ValueError("invalid_s3_config")
    elif kind == "azureblob":
        if not ((values.get("account") and values.get("key")) or values.get("sas_url", "").startswith("https://")):
            raise ValueError("invalid_azure_config")
    elif kind == "smb":
        if (not values.get("host", "").strip() or not values.get("pass", "").strip()
                or not values.get("user", "").strip()
                or values["user"].strip().casefold() in {"guest", "anonymous"}
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", values["host"])):
            raise ValueError("invalid_smb_config")
    else:
        if (values.get("shell_type") != "none" or values.get("disable_hashcheck") != "true"
                or not values.get("host") or not values.get("user") or not values.get("known_hosts_file")
                or not (values.get("pass") or values.get("key_file"))):
            raise ValueError("invalid_sftp_config")
        private_bytes(Path(values["known_hosts_file"]), 65536)
        if values.get("key_file"):
            private_bytes(Path(values["key_file"]), 65536)
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*", prefix)
            or any(part in {".", ".."} for part in prefix.split("/"))):
        raise ValueError("invalid_prefix")
    return remote


def inspect(profile_path=PROFILE, expected_prefix=None, expected_host=None):
    report = {"schema": 1, "phase": "backup-transport-readonly", "inspection_status": "STOP",
              "checks": {}, "failure_class": None}
    stage = "root_execution"
    try:
        if os.geteuid() != 0:
            raise ValueError("root_required")
        stage = "private_profile"
        profile = json.loads(private_bytes(profile_path, 4096))
        config = Path(profile["remote_config"])
        prefix = profile["prefix"]
        if not isinstance(prefix, str) or not prefix or len(prefix) > 256:
            raise ValueError("invalid_prefix")
        stage = "private_transport_config"
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(private_bytes(config, 16384).decode())
        remote = validated_remote(parser, prefix)
        backend = remote["type"]
        if expected_prefix is not None:
            report["checks"]["expected_prefix"] = prefix == expected_prefix
        if expected_host is not None:
            report["checks"]["expected_host"] = (
                backend in {"smb", "sftp"} and remote["host"].casefold() == expected_host.casefold())
        if any(value is False for value in report["checks"].values()):
            report["failed_stage"] = "expected_remote"
            return report
        report.update(backend=backend, tailscale=tailscale_state() if backend in {"smb", "sftp"} else "SKIP")
        report["checks"]["private_inputs_readable"] = True
        if backend in {"smb", "sftp"}:
            stage = "network_route"
            host = remote["host"]
            port = int(remote.get("port", "445" if backend == "smb" else "22"))
            if not 0 < port < 65536:
                raise ValueError("invalid_port")
            addresses = resolved_addresses(host)
            report["checks"]["dns_resolved"] = bool(addresses)
            if not report["checks"]["dns_resolved"]:
                report["failure_class"] = "name_resolution"
                report["inspection_status"] = "PASS"
                return report
            report["checks"]["tcp_reachable"] = False
            for address in addresses:
                try:
                    with socket.create_connection((address, port), timeout=5):
                        report["checks"]["tcp_reachable"] = True
                        break
                except OSError:
                    continue
            if not report["checks"]["tcp_reachable"]:
                report["failure_class"] = "remote_connection"
                report["inspection_status"] = "PASS"
                return report
        stage = "remote_read"
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("RCLONE", "AGE", "AWS", "AZURE", "PG", "SSH_AUTH_SOCK"))}
        try:
            result = subprocess.run(
                ["rclone", "lsd", f"backup:{prefix}", "--config", str(config), "--log-level", "ERROR",
                 "--retries", "1", "--low-level-retries", "1", "--contimeout", "5s", "--timeout", "10s"],
                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, timeout=30, check=False,
            )
            report["checks"]["remote_read"] = result.returncode == 0
            if result.returncode:
                report["failure_class"] = rclone_failure(result.stderr)
        except subprocess.TimeoutExpired:
            report["checks"]["remote_read"] = False
            report["failure_class"] = "remote_read_timeout"
        except OSError:
            report["checks"]["remote_read"] = False
            report["failure_class"] = "rclone_unavailable"
        report["inspection_status"] = "PASS"
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, configparser.Error):
        report["checks"]["inspection"] = False
        report["failed_stage"] = stage
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-readonly", action="store_true", required=True)
    parser.add_argument("--profile", type=Path, default=PROFILE)
    parser.add_argument("--expected-prefix")
    parser.add_argument("--expected-host")
    parser.add_argument("--require-remote-read", action="store_true")
    args = parser.parse_args()
    report = inspect(args.profile, args.expected_prefix, args.expected_host)
    report["transport_ready"] = (report["inspection_status"] == "PASS"
                                 and report["checks"].get("remote_read") is True)
    print(json.dumps(report, sort_keys=True))
    return 0 if (report["transport_ready"] if args.require_remote_read
                 else report["inspection_status"] == "PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
