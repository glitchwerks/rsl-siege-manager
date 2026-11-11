#!/usr/bin/env python3
"""Read-only GitHub runner to VPS access check with sanitized evidence."""

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}\Z")
USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
FINGERPRINT = re.compile(r"SHA256:[A-Za-z0-9+/]{43}\Z")
HOST_KEY_TYPES = {"ssh-ed25519", "ecdsa-sha2-nistp256"}

def run(arguments):
    try:
        completed = subprocess.run(
            arguments, capture_output=True, text=True, timeout=25, check=False
        )
        return completed.returncode == 0, completed.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


def settings():
    host = os.environ.get("VPS_TAILSCALE_HOST", "")
    user = os.environ.get("VPS_SSH_USER", "")
    fingerprint = os.environ.get("VPS_SSH_HOST_FINGERPRINT", "")
    key = os.environ.get("VPS_DEPLOY_SSH_KEY", "")
    known_hosts = os.environ.get("VPS_SSH_KNOWN_HOSTS", "")
    checks = {
        "host_setting": bool(HOST.fullmatch(host)) and ".." not in host,
        "user_setting": bool(USER.fullmatch(user)),
        "fingerprint_setting": bool(FINGERPRINT.fullmatch(fingerprint)),
        "key_setting": 100 <= len(key) <= 16384 and "PRIVATE KEY" in key,
        "key_starts_with_header": key.startswith("-----BEGIN OPENSSH PRIVATE KEY-----\n"),
        "key_has_end_marker": key.rstrip().endswith("-----END OPENSSH PRIVATE KEY-----"),
        "key_has_multiple_lines": len(key.splitlines()) >= 3,
        "key_has_no_literal_newline_escapes": "\\n" not in key,
        "known_hosts_setting": 50 <= len(known_hosts) <= 4096,
    }
    return checks, host, user, fingerprint, key, known_hosts


def check():
    checks, host, user, expected, key, known_hosts = settings()
    if not all(checks.values()):
        return checks
    records = [line for line in known_hosts.splitlines() if line.strip()]
    checks["known_host_identity"] = (
        len(records) == 1
        and len(records[0].split()) == 3
        and records[0].split()[0] == host
        and records[0].split()[1] in HOST_KEY_TYPES
    )
    if not checks["known_host_identity"]:
        return checks
    old_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="vps-access-") as directory:
            private_key = Path(directory) / "key"
            known_hosts_file = Path(directory) / "known_hosts"
            private_key.write_text(key)
            known_hosts_file.write_text(known_hosts + "\n")
            key_valid, _ = run(["ssh-keygen", "-y", "-P", "", "-f", str(private_key)])
            checks["ssh_key"] = key_valid
            fp_valid, fp_output = run(
                ["ssh-keygen", "-lf", str(known_hosts_file), "-E", "sha256"]
            )
            words = fp_output.split()
            checks["host_fingerprint"] = fp_valid and len(words) >= 2 and words[1] == expected
            if not checks["ssh_key"] or not checks["host_fingerprint"]:
                return checks
            connected, response = run(
                [
                    "ssh", "-T", "-i", str(private_key),
                    "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                    "-o", "StrictHostKeyChecking=yes",
                    "-o", f"UserKnownHostsFile={known_hosts_file}",
                    "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
                    "-o", "LogLevel=QUIET", "-o", "NumberOfPasswordPrompts=0",
                    f"{user}@{host}", "access-check",
                ]
            )
            checks["read_only_vps_baseline"] = connected and response.strip() == "PASS"
    finally:
        os.umask(old_umask)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        checks = check()
    except (OSError, UnicodeError, ValueError):
        checks = {"local_material": False}
    result = {"status": "PASS" if all(checks.values()) else "STOP", "checks": checks}
    try:
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as report:
            json.dump(result, report, sort_keys=True)
            report.write("\n")
    except OSError:
        print("STOP report_write_failed")
        return 2
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
