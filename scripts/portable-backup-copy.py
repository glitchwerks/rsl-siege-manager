#!/usr/bin/env python3
"""Encrypt, publish/verify, and recover portable backups in separately gated phases.

Existing age, rclone and PostgreSQL clients only; never installs, schedules,
removes backups, activates applications or connects to a database.
"""
import argparse
import configparser
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time
import uuid

SPEC = importlib.util.spec_from_file_location("recovery", Path(__file__).with_name("portable-recovery.py"))
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)
Stop, require = recovery.Stop, recovery.require
NAMES = {"manifest.json", "siege.dump", "mom.dump"}
TIMEOUT = 900


def protected(path, directory=False):
    # Reject symlinks throughout the path, including ancestor directories.
    path = path.absolute()
    for ancestor in (path, *path.parents):
        require(not ancestor.is_symlink(), "symlink_path_rejected")
    return recovery.private_path(path, directory)


def read(path):
    protected(path.parent, True)
    protected(path)
    return recovery.read_private(path)


def hash_file(path):
    protected(path)
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def command(args, output=None):
    # No ambient rclone settings/credentials, age plugins or interactive prompts.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("RCLONE", "AGE", "AWS", "AZURE", "PG", "SSH_AUTH_SOCK"))}
    try:
        result = subprocess.run(args, env=env, stdin=subprocess.DEVNULL,
                                stdout=output if output else subprocess.DEVNULL,
                                stderr=subprocess.PIPE, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Stop("tool_failed") from None
    if result.returncode:
        # Fixed categories aid diagnosis without exposing raw endpoint/credential output.
        diagnostic = result.stderr.lower()
        for marker, reason in ((b"unknown flag", "tool_option_unsupported"),
                               (b"immutable", "immutable_transfer_refused"),
                               (b"not found", "transfer_object_unavailable"),
                               (b"does not exist", "transfer_object_unavailable"),
                               (b"permission denied", "transfer_permission_denied"),
                               (b"no such file", "transfer_object_unavailable")):
            if marker in diagnostic:
                raise Stop(reason)
        raise Stop("tool_failed")
    # rclone progress/info is suppressed with --log-level ERROR; errors stay private.
    require(not result.stderr.strip(), "tool_warning")


def key_file(path, identity=False):
    lines = [line.strip() for line in read(path).decode().splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    pattern = r"AGE-SECRET-KEY-1[0-9A-Z]+" if identity else r"age1[0-9a-z]+"
    require(0 < len(lines) <= 10 and all(re.fullmatch(pattern, line) for line in lines),
            "native_age_keys_required")


def receipt(path):
    value = json.loads(read(path))
    require(set(value) == {"schema", "copy_id", "bundle_id", "created_at", "sha256", "bytes"}
            and value["schema"] == 1, "invalid_receipt")
    for name in ("copy_id", "bundle_id"):
        require(str(uuid.UUID(value[name])) == value[name], "invalid_receipt")
    datetime.fromisoformat(value["created_at"])
    require(re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
            and type(value["bytes"]) is int and value["bytes"] > 0, "invalid_receipt")
    return value


def cipher_check(path, value):
    protected(path)
    require(path.stat().st_size == value["bytes"] and hash_file(path) == value["sha256"],
            "ciphertext_integrity_failed")
    with path.open("rb") as source:
        require(source.read(22) == b"age-encryption.org/v1\n", "age_ciphertext_required")


def remote(config, prefix, copy_id):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(read(config).decode())
    # No local/alias/union/plugin backend can silently redirect copies onto the VM.
    require(not parser.defaults() and parser.sections() == ["backup"]
            and parser["backup"].get("type") in {"s3", "azureblob", "sftp"}, "invalid_remote_config")
    values = dict(parser["backup"])
    common = {"type"}
    allowed = {
        "s3": {"provider", "access_key_id", "secret_access_key", "region", "endpoint", "location_constraint"},
        "azureblob": {"account", "key", "sas_url"},
        "sftp": {"host", "user", "port", "pass", "key_file", "known_hosts_file", "disable_hashcheck"},
    }
    kind = values["type"]
    require(set(values) <= common | allowed[kind], "unapproved_remote_setting")
    if kind == "s3":
        require(values.get("access_key_id") and values.get("secret_access_key"), "explicit_credentials_required")
        require(not values.get("endpoint") or values["endpoint"].startswith("https://"), "remote_tls_required")
    elif kind == "azureblob":
        require((values.get("account") and values.get("key")) or values.get("sas_url", "").startswith("https://"),
                "explicit_credentials_required")
    else:
        require(values.get("host") and values.get("user") and values.get("known_hosts_file")
                and (values.get("pass") or values.get("key_file")), "explicit_sftp_trust_required")
        require(Path(values["known_hosts_file"]).is_absolute(), "absolute_trust_path_required")
        read(Path(values["known_hosts_file"]))
        if values.get("key_file"):
            require(Path(values["key_file"]).is_absolute(), "absolute_key_path_required")
            read(Path(values["key_file"]))
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*", prefix)
            and all(part not in {".", ".."} for part in prefix.split("/")), "invalid_remote_prefix")
    return f"backup:{prefix}/{copy_id}.tar.age"


def transfer(config, source, destination, limit=None):
    command(["rclone", "copyto", source, destination, "--config", str(config.absolute()),
             "--immutable", "--log-level", "ERROR", "--retries", "1",
             "--low-level-retries", "1", "--contimeout", "10s", "--timeout", "60s"]
            + (["--max-size", str(limit)] if limit else []))


def space(work, size):
    require(shutil.disk_usage(work).free >= size, "insufficient_workspace_storage")


def seal(args, checks):
    require(args.bundle and args.recipients, "seal_inputs_required")
    protected(args.bundle, True)
    require({p.name for p in args.bundle.iterdir()} == NAMES, "unexpected_bundle_files")
    for name in NAMES:
        protected(args.bundle / name)
    key_file(args.recipients)
    manifest = recovery.verified_manifest(args.bundle, checks)
    total = sum((args.bundle / name).stat().st_size for name in NAMES)
    space(args.work, total * 2 + 32 * 1024 * 1024)
    archive = args.work / "bundle.tar"
    with recovery.create_private(archive) as output:
        with tarfile.open(fileobj=output, mode="w") as tar:
            for name in sorted(NAMES):
                tar.add(args.bundle / name, arcname=name, recursive=False)
    # Compare the packaged bytes with the verified snapshot manifest, detecting
    # source edits during packaging before any encryption/publication acceptance.
    with tarfile.open(archive, "r:") as tar:
        with tar.extractfile("manifest.json") as source:
            require(json.loads(source.read(recovery.MAX_METADATA + 1)) == manifest, "source_changed_during_seal")
        for app in recovery.APPS:
            member = tar.getmember(f"{app}.dump")
            digest = hashlib.sha256()
            with tar.extractfile(member) as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            require(member.size == manifest["databases"][app]["bytes"]
                    and digest.hexdigest() == manifest["databases"][app]["sha256"], "source_changed_during_seal")
    ciphertext = args.work / "bundle.tar.age"
    with recovery.create_private(ciphertext) as output:
        command(["age", "--encrypt", "--recipients-file", str(args.recipients.absolute()),
                 str(archive)], output)
        output.flush()
        os.fsync(output.fileno())
    value = {"schema": 1, "copy_id": str(uuid.uuid4()), "bundle_id": manifest["bundle_id"],
             "created_at": datetime.now(timezone.utc).isoformat(),
             "sha256": hash_file(ciphertext), "bytes": ciphertext.stat().st_size}
    recovery.write_json(args.work / "receipt.json", value)
    checks["encrypted_bundle_created"] = True
    return value


def publish(args, checks):
    require(args.confirm_publish, "publish_confirmation_required")
    require(args.package and args.config and args.prefix, "publish_inputs_required")
    protected(args.package, True)
    value = receipt(args.package / "receipt.json")
    ciphertext = args.package / "bundle.tar.age"
    cipher_check(ciphertext, value)
    target = remote(args.config, args.prefix, value["copy_id"])
    space(args.work, value["bytes"] + 32 * 1024 * 1024)
    transfer(args.config, str(ciphertext.absolute()), target)
    checks["encrypted_object_uploaded"] = True
    downloaded = args.work / "download.tar.age"
    transfer(args.config, target, str(downloaded.absolute()), limit=value["bytes"])
    downloaded.chmod(0o600)
    cipher_check(downloaded, value)
    checks["remote_readback_matches"] = True
    recovery.write_json(args.work / "receipt.json", value)
    return value


def unpack(archive, directory, limit):
    require(archive.stat().st_size <= limit, "decrypted_archive_too_large")
    with tarfile.open(archive, mode="r:") as tar:
        # Scan headers before writing anything. Never extract links, paths or devices.
        members = []
        for member in tar:
            require(member.name in NAMES and member.isfile()
                    and member.name not in {m.name for m in members}
                    and member.size <= limit, "unsafe_archive_member")
            members.append(member)
            require(len(members) <= 3, "unsafe_archive_member")
        require({m.name for m in members} == NAMES
                and sum(m.size for m in members) <= limit, "incomplete_archive")
        directory.mkdir(mode=0o700)
        for member in members:
            with tar.extractfile(member) as source, recovery.create_private(directory / member.name) as output:
                shutil.copyfileobj(source, output, 1024 * 1024)


def recover(args, checks):
    require(args.receipt and args.identity and args.config and args.prefix, "recover_inputs_required")
    value = receipt(args.receipt)
    key_file(args.identity, identity=True)
    target = remote(args.config, args.prefix, value["copy_id"])
    space(args.work, value["bytes"] * 4 + 64 * 1024 * 1024)
    downloaded = args.work / "download.tar.age"
    transfer(args.config, target, str(downloaded.absolute()), limit=value["bytes"])
    downloaded.chmod(0o600)
    cipher_check(downloaded, value)
    checks["remote_readback_matches"] = True
    archive = args.work / "recovered.tar"
    with recovery.create_private(archive) as output:
        command(["age", "--decrypt", "--identity", str(args.identity.absolute()), str(downloaded)], output)
    checks["recovery_key_decrypts"] = True
    directory = args.work / "bundle"
    unpack(archive, directory, value["bytes"] * 2 + 32 * 1024 * 1024)
    manifest = recovery.verified_manifest(directory, checks)
    require(manifest["bundle_id"] == value["bundle_id"], "bundle_identity_mismatch")
    checks["recovered_bundle_verified"] = True
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["seal", "publish", "recover"])
    for name in ("work", "report", "bundle", "recipients", "package", "config", "receipt", "identity"):
        parser.add_argument(f"--{name}", type=Path, required=name in {"work", "report"})
    parser.add_argument("--prefix")
    parser.add_argument("--confirm-publish", action="store_true")
    args = parser.parse_args(argv)
    started = time.monotonic()
    report = {"schema": 1, "phase": args.phase, "timestamp": datetime.now(timezone.utc).isoformat(),
              "result": "STOP", "checks": {}}
    try:
        protected(args.report.parent, True)
        with recovery.create_private(args.report) as output:
            output.write(b'{"result":"INCOMPLETE"}\n')
            output.flush()
            os.fsync(output.fileno())
            try:
                if args.phase == "publish":
                    require(args.confirm_publish, "publish_confirmation_required")
                protected(args.work.parent, True)
                # Every phase has a fresh workspace; no overwrite/retry into old files.
                args.work.mkdir(mode=0o700)
                value = globals()[args.phase](args, report["checks"])
                report.update(result="PASS", copy_id=value["copy_id"], bundle_id=value["bundle_id"])
            except (Stop, OSError, ValueError, KeyError, TypeError, UnicodeError, configparser.Error, tarfile.TarError, KeyboardInterrupt) as error:
                report["error"] = str(error) if isinstance(error, Stop) else "operation_failed"
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            output.seek(0)
            output.truncate()
            output.write((json.dumps(report, indent=2) + "\n").encode())
            output.flush()
            os.fsync(output.fileno())
    except (Stop, OSError):
        report.update(result="STOP", error="report_write_failed")
    print(json.dumps(report))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
