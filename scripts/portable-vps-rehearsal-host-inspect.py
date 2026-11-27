#!/usr/bin/env python3
"""Sanitized, read-only baseline for a new isolated VPS rehearsal VM.

Run from the VM console before installing the restricted CI deployment key.
No host state, service, route, container, or credential is changed.
"""

import argparse
import base64
import binascii
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess


HOST_KEY = Path("/etc/ssh/ssh_host_ed25519_key.pub")
TAILNET = ipaddress.IPv4Network("100.64.0.0/10")


def command(args, timeout=8):
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def os_release():
    values = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    return values


def host_fingerprint():
    info = HOST_KEY.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
        raise ValueError("invalid_host_public_key")
    parts = HOST_KEY.read_text().strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519" or not re.fullmatch(r"[A-Za-z0-9+/=]+", parts[1]):
        raise ValueError("invalid_host_public_key")
    encoded = parts[1] + "=" * (-len(parts[1]) % 4)
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) != 51 or not raw.startswith(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"):
        raise ValueError("invalid_host_public_key")
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")


def service_state(name):
    result = command(["systemctl", "show", name, "-p", "LoadState", "-p", "ActiveState", "--no-pager"])
    if result is None or result.returncode:
        return None
    fields = dict(line.split("=", 1) for line in result.stdout.decode("ascii", "replace").splitlines() if "=" in line)
    return fields.get("LoadState") == "loaded" and fields.get("ActiveState") == "active"


def tailscale_state(expected):
    result = command(["tailscale", "status", "--json"])
    if result is None or result.returncode or len(result.stdout) > 2_000_000:
        return None
    value = json.loads(result.stdout)
    self_node = value.get("Self") or {}
    addresses = self_node.get("TailscaleIPs") or []
    return value.get("BackendState") == "Running" and self_node.get("Online") is True and expected in addresses


def default_route_safe():
    result = command(["ip", "-j", "route", "show", "default"])
    if result is None or result.returncode or len(result.stdout) > 65536:
        return None
    routes = json.loads(result.stdout)
    return bool(routes) and all(route.get("dev") != "tailscale0" for route in routes)


def network_controls():
    """Summarize firewall ownership without printing rules or addresses."""
    result = {}
    for name in ("ufw", "firewalld"):
        result[f"{name}_active"] = service_state(f"{name}.service")
    for name in ("iptables-save", "ip6tables-save", "nft"):
        if shutil.which(name) is None:
            result[f"{name.replace('-', '_')}_available"] = False
            continue
        args = [name, "-t", "filter"] if name.endswith("tables-save") else [name, "list", "ruleset"]
        query = command(args)
        if query is None or query.returncode or len(query.stdout) > 2_000_000:
            return None
        result[f"{name.replace('-', '_')}_available"] = True
        result[f"{name.replace('-', '_')}_has_rules"] = (
            any(line.startswith(b"-A ") for line in query.stdout.splitlines())
            if name.endswith("tables-save") else bool(query.stdout.strip())
        )
        result[f"{name.replace('-', '_')}_mentions_tailscale"] = b"ts-" in query.stdout or b"tailscale" in query.stdout
    return result


def listening_ports():
    result = command(["ss", "-H", "-ltn"])
    if result is None or result.returncode or len(result.stdout) > 65536:
        return None
    ports = set()
    for line in result.stdout.decode("ascii", "replace").splitlines():
        fields = line.split()
        if len(fields) < 4:
            return None
        port = fields[3].rsplit(":", 1)[-1]
        if not port.isdigit():
            return None
        ports.add(int(port))
    return sorted(ports)


def listener_details(expected_ip):
    result = command(["ss", "-H", "-ltnp"])
    if result is None or result.returncode or len(result.stdout) > 65536:
        return None
    entries = []
    for line in result.stdout.decode("ascii", "replace").splitlines():
        fields = line.split()
        if len(fields) < 5:
            return None
        address = fields[3]
        host, sep, port = address.rpartition(":")
        if not sep or not port.isdigit():
            return None
        host = host.strip("[]")
        if host in {"0.0.0.0", "::", "*"}:
            scope = "all_interfaces"
        elif host.startswith("127.") or host == "::1":
            scope = "loopback"
        elif host == expected_ip:
            scope = "tailnet"
        else:
            scope = "other_interface"
        process = re.search(r'users:\(\("([A-Za-z0-9_.@+-]{1,64})"', line)
        entries.append({"port": int(port), "scope": scope,
                        "process": process.group(1) if process else "unknown"})
    return sorted(entries, key=lambda entry: (entry["port"], entry["scope"], entry["process"])) if len(entries) <= 128 else None


def container_count():
    if shutil.which("docker") is None:
        return 0, False
    result = command(["docker", "ps", "-aq"], timeout=10)
    if result is None or result.returncode or len(result.stdout) > 65536:
        return None, True
    return len(result.stdout.splitlines()), True


def unit_names(kind, state):
    result = command(["systemctl", "list-units", f"--type={kind}", f"--state={state}",
                      "--plain", "--no-legend", "--no-pager"])
    if result is None or result.returncode or len(result.stdout) > 65536:
        return None
    names = []
    for line in result.stdout.decode("ascii", "replace").splitlines():
        fields = line.split()
        if not fields:
            continue
        if not re.fullmatch(r"[A-Za-z0-9@_.-]+\.(?:service|timer)", fields[0]):
            return None
        names.append(fields[0])
    return sorted(names) if len(names) <= 128 else None


def inspect(expected_ip, expected_fingerprint):
    checks = {"root_execution": os.geteuid() == 0}
    report = {"schema": 1, "phase": "rehearsal-host-readonly", "inspection_status": "STOP", "checks": checks}
    if not checks["root_execution"]:
        return report
    try:
        release = os_release()
        checks["debian_trixie"] = release.get("ID") == "debian" and release.get("VERSION_CODENAME") == "trixie"
        checks["amd64"] = platform.machine() in {"x86_64", "amd64"}
        checks["ssh_active"] = service_state("ssh.service")
        checks["host_fingerprint_matches"] = host_fingerprint() == expected_fingerprint
        checks["tailscale_online_at_expected_ip"] = tailscale_state(expected_ip)
        checks["default_route_not_tailscale"] = default_route_safe()
        controls = network_controls()
        checks["network_controls_query"] = controls is not None
        report["network_controls"] = controls
        ports = listening_ports()
        checks["listener_query"] = ports is not None
        checks["ssh_port_22_listening"] = ports is not None and 22 in ports
        report["tcp_listen_ports"] = ports
        details = listener_details(expected_ip)
        checks["listener_ownership_query"] = details is not None
        report["listeners"] = details
        count, docker_installed = container_count()
        checks["container_query"] = count is not None
        report["existing_containers"] = count
        report["docker_installed"] = docker_installed
        services = unit_names("service", "running")
        timers = unit_names("timer", "active")
        checks["service_inventory"] = services is not None
        checks["timer_inventory"] = timers is not None
        report["running_services"] = services
        report["active_timers"] = timers
        report["preexisting_portable_paths"] = {
            "deployment": Path("/srv/rsl-siege").exists() or Path("/srv/rsl-siege").is_symlink(),
            "backup_config": Path("/etc/rsl-backup").exists() or Path("/etc/rsl-backup").is_symlink(),
            "backup_state": Path("/var/lib/rsl-backup").exists() or Path("/var/lib/rsl-backup").is_symlink(),
        }
        memory = next(int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()
                      if line.startswith("MemTotal:"))
        disk = shutil.disk_usage("/")
        report["capacity"] = {"cpu_count": os.cpu_count(), "memory_bytes": memory,
                              "root_disk_total_bytes": disk.total, "root_disk_free_bytes": disk.free}
        report["inspection_status"] = "PASS" if all(value is True for value in checks.values()) else "STOP"
    except (OSError, ValueError, TypeError, KeyError, AttributeError, binascii.Error, StopIteration):
        checks["inspection"] = False
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-readonly", action="store_true", required=True)
    parser.add_argument("--expected-ip", required=True)
    parser.add_argument("--expected-fingerprint", required=True)
    args = parser.parse_args()
    try:
        address = ipaddress.IPv4Address(args.expected_ip)
        if address not in TAILNET or not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", args.expected_fingerprint):
            raise ValueError("invalid_expected_identity")
        report = inspect(str(address), args.expected_fingerprint)
    except ValueError:
        report = {"schema": 1, "phase": "rehearsal-host-readonly", "inspection_status": "STOP",
                  "checks": {"expected_identity_valid": False}}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["inspection_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
