#!/usr/bin/env python3
"""Phase-gated installation of an exact Siege checkout on the isolated VM."""

import argparse
import base64
import binascii
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess


SOURCE_URL = "https://github.com/glitchwerks/rsl-siege-manager.git"
TARGET = Path("/opt/rsl-siege-manager")
STAGE = Path("/opt/.rsl-siege-manager-install")
HOST_KEY = Path("/etc/ssh/ssh_host_ed25519_key.pub")
REQUIRED = ("scripts/portable-vps-remote.py", "scripts/portable-backup-job.py",
            "scripts/portable-vps-release-preflight.py")


def command(args, timeout=30):
    try:
        return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout, check=False,
                              env={"PATH": "/usr/bin:/bin", "GIT_TERMINAL_PROMPT": "0",
                                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"})
    except (OSError, subprocess.TimeoutExpired):
        return None


def success(args, timeout=30):
    result = command(args, timeout)
    return result is not None and result.returncode == 0


def protected_directory(path):
    try:
        info = path.lstat()
    except OSError:
        return False
    return (stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and not (info.st_mode & 0o022))


def unused(path):
    return not path.exists() and not path.is_symlink()


def host_fingerprint():
    try:
        info = HOST_KEY.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            return None
        parts = HOST_KEY.read_text().split()
        if len(parts) < 2 or parts[0] != "ssh-ed25519":
            return None
        blob = base64.b64decode(parts[1], validate=True)
        if (len(blob) != 51 or not blob.startswith(
                b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20")):
            return None
        return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    except (OSError, ValueError, binascii.Error):
        return None


def route_safe():
    queries = ((["ip", "-j", "route", "show", "default"], True),
               (["ip", "-j", "route", "get", "1.1.1.1"], True),
               (["ip", "-j", "-6", "route", "show", "table", "all"], False))
    for args, required in queries:
        result = command(args)
        if result is None or result.returncode or len(result.stdout) > 65536:
            return False
        try:
            routes = json.loads(result.stdout)
        except ValueError:
            return False
        if (not isinstance(routes, list) or (required and not routes)
                or not all(isinstance(route, dict) for route in routes)):
            return False
        if any(route.get("dev") == "tailscale0" and
               (args[-2:] == ["get", "1.1.1.1"] or route.get("dst") in
                ("default", "0.0.0.0/0", "0.0.0.0/1", "128.0.0.0/1", "::/0", "::/1", "8000::/1"))
               for route in routes):
            return False
    return True


def host_checks(ip, fingerprint):
    address = None
    try:
        address = ipaddress.IPv4Address(ip)
    except ValueError:
        pass
    status = command(["tailscale", "ip", "-4"])
    try:
        release = Path("/etc/os-release").read_text()
    except OSError:
        release = ""
    return {
        "root_execution": os.geteuid() == 0,
        "expected_identity_format": (address is not None
                                     and address in ipaddress.IPv4Network("100.64.0.0/10")
                                     and re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", fingerprint) is not None),
        "debian_trixie": re.search(r"^ID=debian$", release, re.M) is not None
                         and re.search(r"^VERSION_CODENAME=trixie$", release, re.M) is not None,
        "tailnet_identity": status is not None and status.returncode == 0
                            and status.stdout.decode("ascii", "replace").strip() == ip,
        "host_key_identity": host_fingerprint() == fingerprint,
        "ssh_active": success(["systemctl", "is-active", "--quiet", "ssh.service"]),
        "default_route_not_tailscale": route_safe(),
        "opt_protected": protected_directory(Path("/opt")),
        "git_available": success(["git", "--version"]),
    }


def checkout_checks(sha):
    checks = {"target_protected": protected_directory(TARGET),
              "git_directory_protected": protected_directory(TARGET / ".git")}
    if not all(checks.values()):
        return checks
    head = command(["git", "-C", str(TARGET), "rev-parse", "HEAD"])
    remote = command(["git", "-C", str(TARGET), "remote", "get-url", "origin"])
    status = command(["git", "-C", str(TARGET), "status", "--porcelain", "--untracked-files=all"])
    checks.update({
        "exact_head": head is not None and head.returncode == 0 and head.stdout.strip() == sha.encode(),
        "reviewed_origin": remote is not None and remote.returncode == 0
                           and remote.stdout.decode("utf-8", "replace").strip() == SOURCE_URL,
        "clean_checkout": status is not None and status.returncode == 0 and not status.stdout.strip(),
        "required_scripts_present": all((TARGET / name).is_file() and not (TARGET / name).is_symlink()
                                        for name in REQUIRED),
    })
    return checks


def inspect(ip, fingerprint):
    checks = host_checks(ip, fingerprint)
    checks["target_unused"] = unused(TARGET)
    checks["stage_unused"] = unused(STAGE)
    return checks


def install(ip, fingerprint, sha):
    checks = inspect(ip, fingerprint)
    if not all(checks.values()):
        return checks
    checks["clone"] = success(["git", "clone", "--no-checkout", "--filter=blob:none",
                               SOURCE_URL, str(STAGE)], 240)
    if not checks["clone"]:
        return checks
    checks["stage_protected"] = protected_directory(STAGE) and protected_directory(STAGE / ".git")
    if not checks["stage_protected"]:
        return checks
    checks["commit_on_main"] = success(["git", "-C", str(STAGE), "merge-base",
                                        "--is-ancestor", sha, "origin/main"])
    if not checks["commit_on_main"]:
        return checks
    checks["checkout"] = success(["git", "-C", str(STAGE), "checkout", "--detach", sha], 180)
    if not checks["checkout"]:
        return checks
    if not unused(TARGET):
        checks["target_still_unused"] = False
        return checks
    stage_head = command(["git", "-C", str(STAGE), "rev-parse", "HEAD"])
    stage_remote = command(["git", "-C", str(STAGE), "remote", "get-url", "origin"])
    stage_status = command(["git", "-C", str(STAGE), "status", "--porcelain", "--untracked-files=all"])
    checks["stage_exact_clean"] = (stage_head is not None and stage_head.returncode == 0
                                   and stage_head.stdout.strip() == sha.encode()
                                   and stage_status is not None and stage_status.returncode == 0
                                   and not stage_status.stdout.strip())
    checks["stage_origin_reviewed"] = (stage_remote is not None and stage_remote.returncode == 0
                                       and stage_remote.stdout.decode("utf-8", "replace").strip() == SOURCE_URL)
    checks["stage_scripts_present"] = all((STAGE / name).is_file() and not (STAGE / name).is_symlink()
                                          for name in REQUIRED)
    if not all(checks.values()):
        return checks
    try:
        STAGE.rename(TARGET)
    except OSError:
        checks["atomic_install"] = False
        return checks
    checks["atomic_install"] = True
    checks.update(checkout_checks(sha))
    return checks


def reserve_report(path):
    if not path.is_absolute() or path.parent != Path("/root") or not protected_directory(path.parent):
        return None
    try:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("inspect", "install", "verify"))
    parser.add_argument("--expected-ip", required=True)
    parser.add_argument("--expected-fingerprint", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--confirm", choices=("install",))
    parser.add_argument("--snapshot-confirmed", action="store_true")
    args = parser.parse_args()
    descriptor = reserve_report(args.report)
    if descriptor is None:
        print("STOP report_reservation_failed")
        return 2
    checks = {"source_sha_format": re.fullmatch(r"[0-9a-f]{40}", args.source_sha) is not None,
              "phase_confirmation": args.phase != "install" or args.confirm == "install",
              "snapshot_confirmed": args.phase != "install" or args.snapshot_confirmed}
    if all(checks.values()):
        try:
            if args.phase == "inspect":
                checks.update(inspect(args.expected_ip, args.expected_fingerprint))
            elif args.phase == "install":
                checks.update(install(args.expected_ip, args.expected_fingerprint, args.source_sha))
            else:
                checks.update(host_checks(args.expected_ip, args.expected_fingerprint))
                checks.update(checkout_checks(args.source_sha))
        except (OSError, ValueError, TypeError, AttributeError):
            checks["phase_execution"] = False
    report = {"schema": 1, "phase": f"rehearsal-checkout-{args.phase}",
              "status": "PASS" if all(value is True for value in checks.values()) else "STOP",
              "checks": checks}
    try:
        with os.fdopen(descriptor, "w") as stream:
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
