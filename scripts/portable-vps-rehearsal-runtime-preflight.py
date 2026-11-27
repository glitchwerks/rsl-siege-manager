#!/usr/bin/env python3
"""Read-only, sanitized gate before changing the isolated rehearsal VM runtime."""

import argparse
import importlib.util
import ipaddress
import json
from pathlib import Path
import shutil
import subprocess


INSPECTOR = Path(__file__).with_name("portable-vps-rehearsal-host-inspect.py")
CONFLICTS = ("docker.io", "docker-compose", "docker-doc", "docker-buildx",
             "podman-docker", "containerd", "runc")
RUNTIME_PATHS = {
    "docker_source": Path("/etc/apt/sources.list.d/docker.sources"),
    "docker_list": Path("/etc/apt/sources.list.d/docker.list"),
    "docker_key": Path("/etc/apt/keyrings/docker.asc"),
    "docker_daemon_config": Path("/etc/docker/daemon.json"),
    "docker_state": Path("/var/lib/docker"),
}


def host_inspection(expected_ip, expected_fingerprint):
    spec = importlib.util.spec_from_file_location("rehearsal_host_inspect", INSPECTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.inspect(expected_ip, expected_fingerprint)


def installed_packages():
    result = {}
    for name in CONFLICTS:
        try:
            process = subprocess.run(
                ["dpkg-query", "-W", "-f=${Status}", name],
                capture_output=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        status = process.stdout.strip()
        if process.returncode not in (0, 1):
            return None
        result[name] = process.returncode == 0 and status == b"install ok installed"
    return result


def inspect(expected_ip, expected_fingerprint):
    baseline = host_inspection(expected_ip, expected_fingerprint)
    checks = {
        "host_baseline": baseline.get("inspection_status") == "PASS",
        "fresh_container_state": baseline.get("existing_containers") == 0
                                 and baseline.get("docker_installed") is False,
        "package_tools": all(shutil.which(name) for name in ("apt-get", "dpkg-query", "curl", "gpg")),
    }
    packages = installed_packages()
    checks["package_inventory"] = packages is not None
    checks["no_conflicting_packages"] = packages is not None and not any(packages.values())
    paths = {name: path.exists() or path.is_symlink() for name, path in RUNTIME_PATHS.items()}
    checks["runtime_paths_unused"] = not any(paths.values())
    controls = baseline.get("network_controls")
    checks["firewall_inventory"] = isinstance(controls, dict) and controls.get("nft_available") is True
    report = {
        "schema": 1, "phase": "rehearsal-runtime-preflight",
        "status": "PASS" if all(value is True for value in checks.values()) else "STOP",
        "checks": checks, "conflicting_packages": packages, "preexisting_runtime_paths": paths,
        "network_controls": controls,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-readonly", action="store_true", required=True)
    parser.add_argument("--expected-ip", required=True)
    parser.add_argument("--expected-fingerprint", required=True)
    args = parser.parse_args()
    try:
        address = ipaddress.IPv4Address(args.expected_ip)
        if address not in ipaddress.IPv4Network("100.64.0.0/10"):
            raise ValueError("invalid_target")
        report = inspect(str(address), args.expected_fingerprint)
    except (OSError, ValueError, TypeError, AttributeError):
        report = {"schema": 1, "phase": "rehearsal-runtime-preflight", "status": "STOP",
                  "checks": {"inspection": False}}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
