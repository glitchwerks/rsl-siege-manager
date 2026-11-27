#!/usr/bin/env python3
"""Phase-gated Docker apt setup for the isolated Debian rehearsal VM only."""

import argparse
import atexit
import fcntl
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


SCRIPT_DIR = Path(__file__).resolve().parent
KEYRING = Path("/etc/apt/keyrings/docker.asc")
SOURCE = Path("/etc/apt/sources.list.d/docker.sources")
DOCKER_DIR = Path("/etc/docker")
DAEMON = DOCKER_DIR / "daemon.json"
LOCK = Path("/root/.rsl-rehearsal-runtime.lock")
PRIVATE_PATHS = (Path("/srv/rsl-siege"), Path("/srv/rsl-siege/runtime"),
                 Path("/srv/rsl-siege/evidence"), Path("/etc/rsl-backup"),
                 Path("/var/lib/rsl-backup"))
PACKAGES = ("docker-ce", "docker-ce-cli", "containerd.io",
            "docker-buildx-plugin", "docker-compose-plugin")
KEY_FINGERPRINT = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
SOURCE_BYTES = (b"Types: deb\n"
                b"URIs: https://download.docker.com/linux/debian\n"
                b"Suites: trixie\n"
                b"Components: stable\n"
                b"Architectures: amd64\n"
                b"Signed-By: /etc/apt/keyrings/docker.asc\n")
DAEMON_BYTES = (b'{"log-driver":"json-file","log-opts":'
                b'{"max-size":"10m","max-file":"3"}}\n')


def module(name):
    script = SCRIPT_DIR / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), script)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def command(args, timeout=30):
    try:
        return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout, check=False,
                              env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                                   "DEBIAN_FRONTEND": "noninteractive"})
    except (OSError, subprocess.TimeoutExpired):
        return None


def success(args, timeout=30):
    result = command(args, timeout)
    return result is not None and result.returncode == 0


def safe_directory(path):
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not (info.st_mode & 0o022)


def private_directory(path):
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700


def exact_file(path, expected, mode=None):
    try:
        info = path.lstat()
        return (stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and not (info.st_mode & 0o022)
                and (mode is None or stat.S_IMODE(info.st_mode) == mode)
                and path.read_bytes() == expected)
    except OSError:
        return False


def write_new(path, data, mode):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, mode)


def key_fingerprint(path):
    result = command(["gpg", "--show-keys", "--with-colons", "--fingerprint", str(path)])
    if result is None or result.returncode or len(result.stdout) > 65536:
        return None
    for line in result.stdout.decode("ascii", "replace").splitlines():
        fields = line.split(":")
        if fields[0] == "fpr":
            return fields[9]
    return None


def apt_plan(packages=PACKAGES):
    result = command(["apt-get", "-s", "--no-install-recommends", "install", *packages], 120)
    if result is None or result.returncode or len(result.stdout) > 2_000_000:
        return None
    lines = result.stdout.decode("utf-8", "replace").splitlines()
    installed = [line for line in lines if line.startswith("Inst ")]
    removed = [line for line in lines if line.startswith(("Remv ", "Purg "))]
    upgraded = [line for line in installed if re.match(r"^Inst \S+ \[[^]]+\]", line)]
    names = {line.split()[1] for line in installed if len(line.split()) > 1}
    return {"packages_planned": set(PACKAGES) <= names,
            "no_removals": not removed, "no_upgrades": not upgraded,
            "planned_install_count": len(installed),
            "planned_install_names": sorted(names),
            "removed_names": sorted({line.split()[1] for line in removed if len(line.split()) > 1}),
            "upgraded_names": sorted({line.split()[1] for line in upgraded if len(line.split()) > 1})}


def official_candidate_versions():
    """Get exact candidate versions only if no alternate source offers them."""
    candidates = {}
    for package in PACKAGES:
        policy = command(["apt-cache", "policy", package])
        versions = command(["apt-cache", "madison", package])
        if (policy is None or versions is None or policy.returncode or versions.returncode
                or len(policy.stdout) > 65536 or len(versions.stdout) > 65536):
            return None
        match = re.search(r"^\s*Candidate:\s*(\S+)\s*$", policy.stdout.decode("utf-8", "replace"), re.M)
        if match is None or match.group(1) == "(none)":
            return None
        candidate = match.group(1)
        sources = []
        for line in versions.stdout.decode("utf-8", "replace").splitlines():
            parts = [part.strip() for part in line.split("|")]
            if len(parts) != 3:
                return None
            if parts[0] == package and parts[1] == candidate:
                sources.append(parts[2])
        if not sources or not all(source.startswith(
                "https://download.docker.com/linux/debian trixie/stable amd64 Packages")
                for source in sources):
            return None
        candidates[package] = candidate
    return candidates


def repo_ready():
    return (safe_directory(KEYRING.parent) and safe_directory(SOURCE.parent)
            and exact_file(SOURCE, SOURCE_BYTES, 0o644)
            and safe_keyring_file()
            and key_fingerprint(KEYRING) == KEY_FINGERPRINT)


def safe_keyring_file():
    try:
        info = KEYRING.lstat()
    except OSError:
        return False
    return (stat.S_ISREG(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o644 and info.st_size <= 65536)


def baseline(ip, fingerprint):
    host = module("portable-vps-rehearsal-host-inspect.py")
    return host.inspect(ip, fingerprint)


def prepare_repo(ip, fingerprint, details=None):
    preflight = module("portable-vps-rehearsal-runtime-preflight.py").inspect(ip, fingerprint)
    checks = {"preflight_pass": preflight.get("status") == "PASS",
              "apt_directories_safe": safe_directory(KEYRING.parent) and safe_directory(SOURCE.parent)}
    if not all(checks.values()):
        return checks
    with tempfile.TemporaryDirectory(prefix="docker-key-") as temporary:
        key = Path(temporary) / "docker.asc"
        checks["key_download"] = success(
            ["curl", "-fsSL", "--max-time", "30",
             "https://download.docker.com/linux/debian/gpg", "-o", str(key)], 35)
        checks["key_fingerprint"] = (checks["key_download"]
                                     and key.stat().st_size <= 65536
                                     and key_fingerprint(key) == KEY_FINGERPRINT)
        if not all(checks.values()):
            return checks
        try:
            write_new(KEYRING, key.read_bytes(), 0o644)
            write_new(SOURCE, SOURCE_BYTES, 0o644)
        except OSError:
            checks["repo_write"] = False
            return checks
    checks["repo_exact"] = repo_ready()
    if not checks["repo_exact"]:
        return checks
    checks["apt_update"] = success(["apt-get", "update", "-qq"], 600)
    if not checks["apt_update"]:
        return checks
    candidates = official_candidate_versions()
    checks["official_package_candidates"] = candidates is not None
    if not checks["official_package_candidates"]:
        return checks
    plan = apt_plan(tuple(f"{name}={candidates[name]}" for name in PACKAGES))
    checks["apt_simulation"] = plan is not None
    if plan is not None:
        checks.update({name: plan[name] for name in
                       ("packages_planned", "no_removals", "no_upgrades")})
        if details is not None:
            details["apt_plan"] = {name: plan[name] for name in
                                   ("planned_install_count", "planned_install_names",
                                    "removed_names", "upgraded_names")}
    return checks


def install_runtime(ip, fingerprint, details=None):
    before = baseline(ip, fingerprint)
    candidates = official_candidate_versions()
    checks = {
        "host_baseline": before.get("inspection_status") == "PASS",
        "repo_exact": repo_ready(),
        "official_package_candidates": candidates is not None,
        "runtime_paths_unused": not any(path.exists() or path.is_symlink()
                                         for path in (DOCKER_DIR, Path("/var/lib/docker"))),
        "docker_absent": before.get("docker_installed") is False
                         and before.get("existing_containers") == 0,
    }
    if not all(checks.values()):
        return checks
    plan = apt_plan(tuple(f"{name}={candidates[name]}" for name in PACKAGES))
    checks["apt_simulation"] = plan is not None
    if plan is not None:
        checks.update({name: plan[name] for name in
                       ("packages_planned", "no_removals", "no_upgrades")})
        if details is not None:
            details["apt_plan"] = {name: plan[name] for name in
                                   ("planned_install_count", "planned_install_names",
                                    "removed_names", "upgraded_names")}
    if not all(checks.values()):
        return checks
    try:
        DOCKER_DIR.mkdir(mode=0o755)
        write_new(DAEMON, DAEMON_BYTES, 0o644)
    except OSError:
        checks["daemon_configuration"] = False
        return checks
    checks["daemon_configuration"] = exact_file(DAEMON, DAEMON_BYTES, 0o644)
    if not checks["daemon_configuration"]:
        return checks
    checks["apt_install"] = success(
        ["apt-get", "-y", "--no-install-recommends", "--no-remove", "install",
         *(f"{name}={candidates[name]}" for name in PACKAGES)], 1800)
    if not checks["apt_install"]:
        return checks
    checks["docker_info"] = success(["docker", "info"], 30)
    checks["compose_version"] = success(["docker", "compose", "version"], 30)
    checks["docker_enabled"] = success(["systemctl", "is-enabled", "--quiet", "docker.service"])
    after = baseline(ip, fingerprint)
    checks["host_baseline_after"] = after.get("inspection_status") == "PASS"
    checks["zero_containers"] = after.get("existing_containers") == 0
    critical = {"ssh.service", "tailscaled.service", "NetworkManager.service",
                "gdm.service", "cups.service", "open-vm-tools.service"}
    checks["existing_services_preserved"] = (
        isinstance(before.get("running_services"), list)
        and isinstance(after.get("running_services"), list)
        and (critical & set(before["running_services"])) <= set(after["running_services"]))
    checks["existing_timers_preserved"] = (
        isinstance(before.get("active_timers"), list)
        and isinstance(after.get("active_timers"), list)
        and set(before["active_timers"]) <= set(after["active_timers"]))
    checks["tailscale_firewall_preserved"] = (
        isinstance(after.get("network_controls"), dict)
        and all(after["network_controls"].get(name) is True for name in
                ("iptables_save_mentions_tailscale", "ip6tables_save_mentions_tailscale",
                 "nft_mentions_tailscale")))
    return checks


def prepare_paths(ip, fingerprint):
    state = baseline(ip, fingerprint)
    critical = {"ssh.service", "tailscaled.service", "NetworkManager.service",
                "gdm.service", "cups.service", "open-vm-tools.service"}
    checks = {
        "host_baseline": state.get("inspection_status") == "PASS",
        "docker_installed": state.get("docker_installed") is True,
        "zero_containers": state.get("existing_containers") == 0,
        "repo_exact": repo_ready(),
        "daemon_configuration": exact_file(DAEMON, DAEMON_BYTES, 0o644),
        "critical_services_active": isinstance(state.get("running_services"), list)
                                    and critical <= set(state["running_services"]),
        "docker_info": success(["docker", "info"]),
        "compose_version": success(["docker", "compose", "version"]),
        "parent_directories_safe": all(safe_directory(path) for path in
                                       (Path("/srv"), Path("/etc"), Path("/var/lib"))),
        "paths_unused": not any(path.exists() or path.is_symlink() for path in PRIVATE_PATHS),
    }
    if not all(checks.values()):
        return checks
    try:
        for path in PRIVATE_PATHS:
            path.mkdir(mode=0o700)
    except OSError:
        checks["path_creation"] = False
        return checks
    checks["private_paths"] = all(private_directory(path) for path in PRIVATE_PATHS)
    checks["host_baseline_after"] = baseline(ip, fingerprint).get("inspection_status") == "PASS"
    return checks


def rollback_repo(ip, fingerprint):
    before = baseline(ip, fingerprint)
    source_present = SOURCE.exists() or SOURCE.is_symlink()
    key_present = KEYRING.exists() or KEYRING.is_symlink()
    checks = {"host_baseline": before.get("inspection_status") == "PASS",
              "docker_absent": before.get("docker_installed") is False
                               and before.get("existing_containers") == 0,
              "repo_present": source_present or key_present,
              "source_exact_or_absent": not source_present or exact_file(SOURCE, SOURCE_BYTES, 0o644),
              "key_exact_or_absent": not key_present or
                                     (safe_keyring_file() and key_fingerprint(KEYRING) == KEY_FINGERPRINT),
              "daemon_absent": not DOCKER_DIR.exists() and not DOCKER_DIR.is_symlink()}
    if not all(checks.values()):
        return checks
    try:
        if source_present:
            SOURCE.unlink()
        if key_present:
            KEYRING.unlink()
    except OSError:
        checks["repo_removal"] = False
        return checks
    checks["repo_removal"] = not SOURCE.exists() and not KEYRING.exists()
    return checks


def reserve_report(path):
    if not path.is_absolute() or path.parent != Path("/root") or not safe_directory(path.parent):
        return None
    try:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError:
        return None


def phase_lock():
    descriptor = None
    try:
        descriptor = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            os.close(descriptor)
            return None
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        atexit.register(os.close, descriptor)
        return descriptor
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare-repo", "install-runtime", "prepare-paths", "rollback-repo"))
    parser.add_argument("--expected-ip", required=True)
    parser.add_argument("--expected-fingerprint", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--confirm", choices=("prepare-repo", "install-runtime", "prepare-paths", "rollback-repo"))
    parser.add_argument("--snapshot-confirmed", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0 or phase_lock() is None:
        print("STOP root_or_lock_failed")
        return 2
    report_fd = reserve_report(args.report)
    if report_fd is None:
        print("STOP report_reservation_failed")
        return 2
    checks = {"root_execution": os.geteuid() == 0,
              "phase_confirmation": args.confirm == args.phase,
              "snapshot_confirmed": args.snapshot_confirmed,
              "report_reserved": True}
    try:
        address = ipaddress.IPv4Address(args.expected_ip)
        checks["expected_identity_format"] = (
            address in ipaddress.IPv4Network("100.64.0.0/10")
            and re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", args.expected_fingerprint) is not None)
    except ValueError:
        checks["expected_identity_format"] = False
    if all(checks.values()):
        details = {}
        try:
            if args.phase == "prepare-repo":
                checks.update(prepare_repo(args.expected_ip, args.expected_fingerprint, details))
            elif args.phase == "install-runtime":
                checks.update(install_runtime(args.expected_ip, args.expected_fingerprint, details))
            elif args.phase == "prepare-paths":
                checks.update(prepare_paths(args.expected_ip, args.expected_fingerprint))
            else:
                checks.update(rollback_repo(args.expected_ip, args.expected_fingerprint))
        except (OSError, ValueError, TypeError, AttributeError):
            checks["phase_execution"] = False
    else:
        details = {}
    report = {"schema": 1, "phase": f"rehearsal-runtime-{args.phase}",
              "status": "PASS" if all(value is True for value in checks.values()) else "STOP",
              "checks": checks, "details": details}
    try:
        with os.fdopen(report_fd, "w") as stream:
            json.dump(report, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        print("STOP report_write_failed")
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
