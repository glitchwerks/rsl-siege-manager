#!/usr/bin/env python3
"""Opt-in portable backup job and separately gated local retention.

No scheduler installation, remote deletion, database restore, or application activation.
"""
import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone, timedelta
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import uuid

SPEC = importlib.util.spec_from_file_location("copies", Path(__file__).with_name("portable-backup-copy.py"))
copies = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(copies)
require, Stop = copies.require, copies.Stop
DATA = {"source": copies.NAMES, "seal": {"bundle.tar", "bundle.tar.age", "receipt.json"},
        "publish": {"download.tar.age", "receipt.json"}}


def timestamp():
    return datetime.now(timezone.utc)


def settings(path):
    value = json.loads(copies.read(path))
    required = {"root", "services", "passwords", "recipients", "remote_config", "prefix", "heartbeat_file",
                "timeout_seconds", "keep_days", "keep_successful"}
    require(set(value) == required, "invalid_job_config")
    for key in ("timeout_seconds", "keep_days", "keep_successful"):
        require(type(value[key]) is int, "invalid_job_policy")
    require(60 <= value["timeout_seconds"] <= 1800 and 7 <= value["keep_days"] <= 365
            and 24 <= value["keep_successful"] <= 10000, "invalid_job_policy")
    for key in required - {"prefix", "timeout_seconds", "keep_days", "keep_successful"}:
        require(isinstance(value[key], str) and Path(value[key]).is_absolute(), "absolute_config_paths_required")
        value[key] = Path(value[key])
    copies.protected(value["root"], True)
    copies.protected(value["root"] / "runs", True)
    for key in ("services", "passwords", "recipients", "remote_config", "heartbeat_file"):
        require(not value[key].is_relative_to(value["root"] / "runs"), "credentials_outside_runs_required")
        copies.read(value[key])
    copies.remote(value["remote_config"], value["prefix"], str(uuid.uuid4()))
    heartbeat_url(value["heartbeat_file"])
    value["config_sha256"] = copies.hash_file(path)
    return value


@contextmanager
def locked(root):
    path = root / "job.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        copies.protected(path)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop("backup_job_already_running") from None
        yield
    finally:
        os.close(fd)


def heartbeat_url(path):
    value = copies.read(path).decode().strip()
    parsed = urllib.parse.urlsplit(value)
    require(parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment and parsed.path not in {"", "/"}
            and not any(c.isspace() for c in value), "invalid_heartbeat_url")
    return value.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Stop("heartbeat_redirect_refused")


def ping(path, success):
    url = heartbeat_url(path) + ("" if success else "/fail")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(urllib.request.Request(url, data=b"", method="POST"), timeout=10) as response:
            require(response.status == 200 and response.read(32).strip() == b"OK", "heartbeat_not_acknowledged")
    except Stop:
        raise
    except Exception:
        raise Stop("heartbeat_failed") from None


def child(script, arguments, report_path, deadline, phase):
    remaining = deadline - time.monotonic()
    require(remaining > 0, "backup_deadline_exceeded")
    # Own process group: on timeout/interrupt terminate precisely this phase and its
    # psql/pg_dump/age/rclone descendants, never an operator's other processes.
    process = subprocess.Popen([sys.executable, str(Path(__file__).with_name(script)), *map(str, arguments),
                               "--report", str(report_path)], start_new_session=True,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        require(process.wait(timeout=remaining) == 0, "backup_phase_failed")
    except subprocess.TimeoutExpired:
        raise Stop("backup_deadline_exceeded") from None
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        # Also remove orphaned descendants if the phase parent already exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
    evidence = json.loads(copies.read(report_path))
    require(evidence.get("result") == "PASS" and evidence.get("phase") == phase, "backup_phase_not_verified")
    return evidence


def catalog(config, package, work, deadline):
    require(time.monotonic() < deadline, "backup_deadline_exceeded")
    value = copies.receipt(package / "receipt.json")
    # The small receipt has no workload/schema/connection details. Its independently
    # protected remote copy is essential when the local VM/catalog is lost.
    target = copies.remote(config["remote_config"], config["prefix"], value["copy_id"])
    target = target.removesuffix(".tar.age") + ".receipt.json"
    copies.transfer(config["remote_config"], str((package / "receipt.json").absolute()), target)
    size = (package / "receipt.json").stat().st_size
    downloaded = work / "catalog-readback.json"
    copies.download(config["remote_config"], target, downloaded, size)
    require(downloaded.stat().st_size == size
            and copies.hash_file(downloaded) == copies.hash_file(package / "receipt.json"), "catalog_readback_failed")
    return value


def run_job(config, directory, report):
    deadline = time.monotonic() + config["timeout_seconds"]
    backup = child("portable-recovery.py", ["backup", "--bundle", directory / "source", "--services", config["services"],
        "--passwords", config["passwords"], "--confirm-backup"], directory / "backup.json", deadline, "backup")
    report["checks"]["database_backup"] = True
    seal = child("portable-backup-copy.py", ["seal", "--bundle", directory / "source", "--recipients", config["recipients"],
        "--work", directory / "seal"], directory / "seal.json", deadline, "seal")
    require(backup["bundle_id"] == seal["bundle_id"], "job_bundle_identity_mismatch")
    report["checks"]["encrypted_bundle"] = True
    publish = child("portable-backup-copy.py", ["publish", "--package", directory / "seal", "--config", config["remote_config"],
        "--prefix", config["prefix"], "--work", directory / "publish", "--confirm-publish"],
        directory / "publish.json", deadline, "publish")
    require(publish["copy_id"] == seal["copy_id"] and publish["bundle_id"] == backup["bundle_id"], "job_copy_identity_mismatch")
    report["checks"]["encrypted_remote_readback"] = True
    # Catalog work runs in a separate bounded child as well (same group cleanup).
    catalog_report = child("portable-backup-job.py", ["catalog", "--config", config["config_file"],
        "--package", directory / "publish", "--work", directory, "--confirm-run"], directory / "catalog.json", deadline, "catalog")
    require(catalog_report["copy_id"] == publish["copy_id"] and catalog_report["bundle_id"] == backup["bundle_id"],
            "catalog_identity_mismatch")
    report["checks"]["remote_receipt_readback"] = True
    require(time.monotonic() < deadline, "backup_deadline_exceeded")
    report.update(copy_id=publish["copy_id"], bundle_id=backup["bundle_id"])
    ping(config["heartbeat_file"], True)
    report["checks"]["independent_monitor_acknowledged"] = True


def proof_paths(args):
    recovered = json.loads(copies.read(args.recover_report))
    restored = json.loads(copies.read(args.restore_report))
    require(recovered.get("result") == restored.get("result") == "PASS"
            and recovered.get("phase") == "recover" and restored.get("phase") == "restore"
            and recovered["bundle_id"] == restored["bundle_id"]
            and recovered["checks"].get("recovered_bundle_verified") is True
            and all(restored["checks"].get(f"{app}_restored_controls_match") is True for app in copies.recovery.APPS),
            "verified_restore_proof_required")
    uuid.UUID(recovered["copy_id"])
    return recovered["copy_id"]


def inventory(directory):
    entries = {}
    for folder, names in DATA.items():
        parent = directory / folder
        copies.protected(parent, True)
        require({p.name for p in parent.iterdir()} == names, "unknown_retention_artifact")
        for name in sorted(names):
            path = parent / name
            copies.protected(path)
            entries[f"{folder}/{name}"] = {"bytes": path.stat().st_size, "sha256": copies.hash_file(path)}
    return entries


def retention_plan(config, anchor, now):
    successes = []
    for directory in (config["root"] / "runs").iterdir():
        require(str(uuid.UUID(directory.name)) == directory.name, "unknown_run_directory")
        copies.protected(directory, True)
        evidence = json.loads(copies.read(directory / "job.json"))
        if evidence.get("result") != "PASS":
            continue  # Failed/interrupted runs are always preserved, never age-deleted.
        require(evidence.get("phase") == "run" and all(evidence.get("checks", {}).get(check) is True for check in
            ("database_backup", "encrypted_bundle", "encrypted_remote_readback", "remote_receipt_readback",
             "independent_monitor_acknowledged")), "invalid_successful_job")
        captured = datetime.fromisoformat(evidence["timestamp"])
        finished = datetime.fromisoformat(evidence["finished_at"])
        require(captured.tzinfo and finished.tzinfo and captured <= finished <= now, "invalid_job_timestamp")
        successes.append((captured, finished, directory, evidence["copy_id"]))
    require(any(item[3] == anchor for item in successes), "restore_anchor_job_required")
    require(successes and now - max(item[1] for item in successes) <= timedelta(hours=2), "fresh_verified_backup_required")
    newest = sorted(successes, reverse=True)[:config["keep_successful"]]
    keep = {item[2].name for item in newest}
    entries = {}
    for captured, _, directory, copy_id in successes:
        if (directory.name in keep or copy_id == anchor or now - captured < timedelta(days=config["keep_days"])
                or (directory / "pruned.json").exists()):
            continue
        entries[directory.name] = inventory(directory)
    return {"schema": 1, "created_at": now.isoformat(), "anchor_copy_id": anchor,
            "root": str(config["root"]), "config_sha256": config["config_sha256"],
            "keep_days": config["keep_days"], "keep_successful": config["keep_successful"], "entries": entries}


def retain(config, args, report):
    require(args.recover_report and args.restore_report and args.plan, "retention_inputs_required")
    anchor = proof_paths(args)
    if args.phase == "plan":
        plan = retention_plan(config, anchor, timestamp())
        copies.recovery.write_json(args.plan, plan)
    else:
        require(args.confirm_retention, "retention_confirmation_required")
        plan = json.loads(copies.read(args.plan))
        planned = datetime.fromisoformat(plan["created_at"])
        require(planned.tzinfo and timedelta(0) <= timestamp() - planned <= timedelta(hours=1), "retention_plan_expired")
        # Recompute against the reviewed policy/anchor and hash every file before
        # the first unlink. A changed or edited plan never grants deletion authority.
        require(plan == retention_plan(config, anchor, planned), "retention_plan_changed")
        require(timestamp() - max(datetime.fromisoformat(json.loads(copies.read(d / "job.json"))["finished_at"])
                for d in (config["root"] / "runs").iterdir()
                if json.loads(copies.read(d / "job.json")).get("result") == "PASS") <= timedelta(hours=2),
                "fresh_verified_backup_required")
        for run_id, entries in plan["entries"].items():
            directory = config["root"] / "runs" / run_id
            # No recursive deletion, remote deletion, database cleanup, or following links.
            for relative in entries:
                (directory / relative).unlink()
            for folder in DATA:
                (directory / folder).rmdir()
            copies.recovery.write_json(directory / "pruned.json", {"schema": 1, "result": "PASS", "timestamp": timestamp().isoformat()})
    report["checks"]["local_retention_plan_verified"] = True
    report["candidate_runs"] = len(plan["entries"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["run", "catalog", "plan", "apply"])
    parser.add_argument("--config", type=Path, required=True)
    for name in ("report", "package", "work", "plan", "recover-report", "restore-report"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--confirm-run", action="store_true")
    parser.add_argument("--confirm-retention", action="store_true")
    args = parser.parse_args(argv)
    report = {"schema": 1, "phase": args.phase, "timestamp": timestamp().isoformat(), "result": "STOP", "checks": {}}
    config = None
    try:
        config = settings(args.config)
        config["config_file"] = args.config.absolute()
        if args.phase == "run":
            require(args.confirm_run, "backup_job_confirmation_required")
            require(args.report is None, "job_report_location_is_managed")
        elif args.phase == "apply":
            require(args.confirm_retention, "retention_confirmation_required")
        # Catalog is part of the already-gated job and must still explicitly opt in.
        if args.phase == "catalog":
            require(args.confirm_run, "catalog_confirmation_required")
            require(args.work and args.package and args.report, "catalog_inputs_required")
            copies.protected(args.work, True)
            args.work.relative_to(config["root"] / "runs")
            require(args.work.name == str(uuid.UUID(args.work.name))
                    and args.work.parent == config["root"] / "runs"
                    and args.package == args.work / "publish"
                    and args.report == args.work / "catalog.json", "managed_catalog_paths_required")
        with (nullcontext() if args.phase == "catalog" else locked(config["root"])):
            if args.phase == "run":
                run_id = str(uuid.uuid4())
                directory = config["root"] / "runs" / run_id
                directory.mkdir(mode=0o700)
                args.report = directory / "job.json"
                report["run_id"] = run_id
            require(args.report, "report_required")
            copies.protected(args.report.parent, True)
            with copies.recovery.create_private(args.report) as output:
                output.write(b'{"result":"INCOMPLETE"}\n')
                output.flush()
                os.fsync(output.fileno())
                try:
                    if args.phase == "run":
                        run_job(config, directory, report)
                    elif args.phase == "catalog":
                        value = catalog(config, args.package, args.work, time.monotonic() + config["timeout_seconds"])
                        report.update(copy_id=value["copy_id"], bundle_id=value["bundle_id"])
                        report["checks"]["remote_receipt_readback"] = True
                    else:
                        retain(config, args, report)
                    report["result"] = "PASS"
                except (Exception, KeyboardInterrupt) as error:
                    report["error"] = str(error) if isinstance(error, Stop) else "operation_failed"
                    if args.phase == "run":
                        try:
                            ping(config["heartbeat_file"], False)
                            report["checks"]["independent_failure_acknowledged"] = True
                        except Exception:
                            report["checks"]["independent_failure_acknowledged"] = False
                report["finished_at"] = timestamp().isoformat()
                output.seek(0)
                output.truncate()
                output.write((json.dumps(report, indent=2) + "\n").encode())
                output.flush()
                os.fsync(output.fileno())
    except (Exception, KeyboardInterrupt) as error:
        report.update(result="STOP", error=str(error) if isinstance(error, Stop) else "operation_failed")
    print(json.dumps(report))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    # Allow the child process-group cleanup path to handle a targeted service stop.
    def terminated(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, terminated)
    raise SystemExit(main())
