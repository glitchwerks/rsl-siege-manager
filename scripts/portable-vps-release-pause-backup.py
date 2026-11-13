#!/usr/bin/env python3
"""Separately confirmed plan, writer pause, and managed backup phases."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import tempfile
import uuid


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("writer_gate", HERE / "portable-vps-writer-pause-gate.py")
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)
REMOTE = GATE.REMOTE
CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
UNIT = re.compile(r"rsl-(?:production|pilot)-backup\.service\Z")
STATE = "{{json .State}}"
ENVIRONMENT = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/root", "LANG": "C",
               "DOCKER_HOST": "unix:///var/run/docker.sock"}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def command(arguments, timeout=20):
    try:
        result = subprocess.run(arguments, env=ENVIRONMENT, capture_output=True,
                                text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 and len(result.stdout) <= 65536 else None


def write_private(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write((json.dumps(value, sort_keys=True) + "\n").encode())
        output.flush()
        os.fsync(output.fileno())


def replace_private(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write((json.dumps(value, sort_keys=True) + "\n").encode())
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def safe_unit(path, expected):
    try:
        if not path.is_absolute() or any(item.is_symlink() for item in (path, *path.parents)):
            return False
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 65536:
            return False
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            opened = os.fstat(source.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != 0
                    or opened.st_mode & 0o022 or opened.st_size != info.st_size):
                return False
            content = source.read(65537)
        return len(content) <= 65536 and digest(content) == expected
    except OSError:
        return False


def profile(path):
    raw = GATE.read_private(path, 0)
    data = json.loads(raw, object_pairs_hook=REMOTE.no_duplicate_keys)
    if not isinstance(data, dict) or set(data) != {
        "project_directory", "backup_owner", "backup_root", "backup_unit",
        "backup_unit_file", "backup_unit_sha256", "backup_unit_effective_sha256", "backup_timer",
    }:
        raise ValueError("invalid profile")
    if data["backup_owner"] not in {"root", "rsl-backup"}:
        raise ValueError("invalid backup owner")
    if not isinstance(data["backup_unit"], str) or not UNIT.fullmatch(data["backup_unit"]):
        raise ValueError("invalid backup unit")
    if data["backup_timer"] != data["backup_unit"].removesuffix(".service") + ".timer":
        raise ValueError("invalid backup timer")
    for name in ("backup_unit_sha256", "backup_unit_effective_sha256"):
        if not isinstance(data[name], str) or not REMOTE.SHA256.fullmatch(data[name]):
            raise ValueError("invalid unit digest")
    for name in ("project_directory", "backup_root", "backup_unit_file"):
        if not isinstance(data[name], str) or not Path(data[name]).is_absolute():
            raise ValueError("invalid profile path")
        data[name] = Path(data[name])
    if not GATE.protected(data["project_directory"], 0, directory=True):
        raise ValueError("unsafe project")
    owner = pwd.getpwnam(data["backup_owner"]).pw_uid
    if not (GATE.protected(data["backup_root"], owner, directory=True)
            and GATE.protected(data["backup_root"] / "runs", owner, directory=True)):
        raise ValueError("unsafe backup root")
    if not safe_unit(data["backup_unit_file"], data["backup_unit_sha256"]):
        raise ValueError("backup unit drift")
    fragment = command(["systemctl", "show", "--property=FragmentPath", "--value", data["backup_unit"]])
    if fragment != str(data["backup_unit_file"]):
        raise ValueError("active unit mismatch")
    if not effective_unit(data):
        raise ValueError("effective unit drift")
    return data, digest(raw)


def effective_unit(data):
    if command(["systemctl", "show", "--property=NeedDaemonReload", "--value",
                data["backup_unit"]]) != "no":
        return False
    loaded = command(["systemctl", "cat", data["backup_unit"]])
    return loaded is not None and digest(loaded.encode()) == data["backup_unit_effective_sha256"]


def stage(project, run_id, expected):
    if not REMOTE.RUN.fullmatch(run_id) or not REMOTE.SHA256.fullmatch(expected):
        raise ValueError("invalid stage selector")
    directory = project / "evidence" / "release-stages" / run_id
    if not GATE.protected(directory, 0, directory=True):
        raise ValueError("unsafe stage")
    raw = GATE.read_private(directory / "candidate.json", 0, 16384)
    report = json.loads(GATE.read_private(directory / "report.json", 0),
                        object_pairs_hook=REMOTE.no_duplicate_keys)
    if not (raw and digest(raw) == expected and REMOTE.valid_manifest(raw)
            and isinstance(report, dict) and report.get("phase") == "stage-release"
            and report.get("status") == "PASS"):
        raise ValueError("stage mismatch")
    return directory


def identifiers(project_name, service):
    output = command(["docker", "ps", "--all", "--no-trunc",
                      "--filter", f"label=com.docker.compose.project={project_name}",
                      "--filter", f"label=com.docker.compose.service={service}",
                      "--format", "{{.ID}}"])
    if output is None:
        return None
    ids = output.splitlines()
    return ids[0] if len(ids) == 1 and CONTAINER_ID.fullmatch(ids[0]) else None


def container(project_name, service):
    identifier = identifiers(project_name, service)
    if identifier is None:
        return None
    raw = command(["docker", "inspect", "--format", STATE, identifier])
    if raw is None:
        return None
    try:
        state = json.loads(raw, object_pairs_hook=REMOTE.no_duplicate_keys)
    except (ValueError, TypeError):
        return None
    if not isinstance(state, dict):
        return None
    return {"id": identifier, "running": state.get("Running"),
            "status": state.get("Status"),
            "healthy": (state.get("Health") or {}).get("Status") == "healthy",
            "finished_at": state.get("FinishedAt")}


def no_extra_writers(project_name):
    for service in ("migrate-siege", "migrate-mom", "bot"):
        output = command(["docker", "ps", "--no-trunc",
                          "--filter", f"label=com.docker.compose.project={project_name}",
                          "--filter", f"label=com.docker.compose.service={service}",
                          "--format", "{{.ID}}"])
        if output is None or output:
            return False
    return True


def service_baseline(data):
    name = GATE.project_name(data["project_directory"] / "stack.env")
    if name is None:
        return None
    services = {service: container(name, service) for service in ("backend", "mom", "postgres", "proxy")}
    if any(value is None for value in services.values()):
        return None
    return {"project": name, "services": services}


def timer_active(data):
    return command(["systemctl", "is-active", data["backup_timer"]]) == "active"


def backup_idle(data):
    return command(["systemctl", "show", "--property=ActiveState", "--value", data["backup_unit"]]) == "inactive"


def planned(data, expected):
    snapshot = service_baseline(data)
    if snapshot is None:
        return None
    services = snapshot["services"]
    if not all(services[name]["running"] is True and services[name]["status"] == "running"
               and services[name]["healthy"] for name in ("backend", "mom", "postgres")):
        return None
    if services["proxy"]["running"] is not True or services["proxy"]["status"] != "running":
        return None
    if not no_extra_writers(snapshot["project"]) or not timer_active(data) or not backup_idle(data):
        return None
    return {"schema": 1, "phase": "pause-plan", "manifest_sha256": expected,
            "project": snapshot["project"],
            "ids": {name: item["id"] for name, item in services.items()}}


def plan(data, profile_digest, directory, expected):
    checks = {"root_execution": os.geteuid() == 0, "plan_available": False}
    if not checks["root_execution"]:
        return checks
    value = planned(data, expected)
    checks["baseline_healthy"] = value is not None
    if not checks["baseline_healthy"]:
        return checks
    value["profile_sha256"] = profile_digest
    value["created_at"] = stamp()
    write_private(directory / "pause-plan.json", value)
    checks["plan_available"] = True
    return checks


def load_plan(directory, digest_expected, profile_digest, manifest_digest):
    if not REMOTE.SHA256.fullmatch(digest_expected):
        raise ValueError("invalid plan digest")
    raw = GATE.read_private(directory / "pause-plan.json", 0)
    value = json.loads(raw, object_pairs_hook=REMOTE.no_duplicate_keys)
    if not (digest(raw) == digest_expected and isinstance(value, dict)
            and value.get("schema") == 1 and value.get("phase") == "pause-plan"
            and value.get("profile_sha256") == profile_digest
            and value.get("manifest_sha256") == manifest_digest
            and isinstance(value.get("ids"), dict)
            and set(value["ids"]) == {"backend", "mom", "postgres", "proxy"}
            and all(isinstance(item, str) and CONTAINER_ID.fullmatch(item) for item in value["ids"].values())):
        raise ValueError("plan mismatch")
    report = json.loads(GATE.read_private(directory / "plan-report.json", 0),
                        object_pairs_hook=REMOTE.no_duplicate_keys)
    if not isinstance(report, dict) or report.get("phase") != "plan" or report.get("status") != "PASS":
        raise ValueError("plan not accepted")
    return value


def unchanged_running(data, value):
    current = planned(data, value["manifest_sha256"])
    return current is not None and current["project"] == value["project"] and current["ids"] == value["ids"]


def paused(data, value):
    current = service_baseline(data)
    if current is None or current["project"] != value["project"]:
        return False
    services = current["services"]
    if any(services[name]["id"] != value["ids"][name] for name in value["ids"]):
        return False
    return (all(services[name]["running"] is False and services[name]["status"] == "exited"
                for name in ("backend", "mom"))
            and services["postgres"]["running"] is True and services["postgres"]["healthy"]
            and services["proxy"]["running"] is True and timer_active(data)
            and no_extra_writers(current["project"]))


def pause(data, value, directory):
    checks = {"baseline_unchanged": unchanged_running(data, value)}
    if not checks["baseline_unchanged"]:
        return checks
    write_private(directory / "pause-start.json", {"phase": "pause-start", "plan_sha256": digest(
        GATE.read_private(directory / "pause-plan.json", 0)), "started_at": stamp()})
    for service in ("mom", "backend"):
        result = command(["docker", "stop", "--time", "30", value["ids"][service]], timeout=50)
        checks[f"{service}_stop"] = result is not None
        if not checks[f"{service}_stop"]:
            break
    checks["writers_stopped_unaffected_healthy"] = paused(data, value)
    return checks


def backup(data, value, directory, manifest_digest):
    checks = {"pause_pass": False}
    prior = json.loads(GATE.read_private(directory / "pause-report.json", 0),
                       object_pairs_hook=REMOTE.no_duplicate_keys)
    checks["pause_pass"] = (isinstance(prior, dict) and prior.get("phase") == "pause"
                            and prior.get("status") == "PASS")
    checks["writers_paused"] = paused(data, value)
    checks["backup_idle"] = backup_idle(data)
    checks["effective_unit_unchanged"] = effective_unit(data)
    if not all(checks.values()):
        return checks
    before = {item.name for item in (data["backup_root"] / "runs").iterdir()}
    write_private(directory / "backup-start.json", {"phase": "backup-start", "started_at": stamp(),
                                                    "plan_sha256": digest(GATE.read_private(directory / "pause-plan.json", 0))})
    checks["backup_service_success"] = command(["systemctl", "start", data["backup_unit"]], timeout=2100) is not None
    after = {item.name for item in (data["backup_root"] / "runs").iterdir()}
    created = after - before
    checks["exactly_one_new_run"] = len(created) == 1
    if not all(checks.values()):
        return checks
    run_id = next(iter(created))
    try:
        checks["run_id_valid"] = str(uuid.UUID(run_id)) == run_id
    except ValueError:
        checks["run_id_valid"] = False
    if not checks["run_id_valid"]:
        return checks
    report = data["backup_root"] / "runs" / run_id / "job.json"
    gate = GATE.inspect(data["project_directory"], directory.name, manifest_digest,
                        data["backup_owner"], data["backup_root"], report)
    checks["pause_backup_order_verified"] = bool(gate) and all(gate.values())
    checks["post_backup_baseline"] = paused(data, value)
    if checks["pause_backup_order_verified"] and checks["post_backup_baseline"]:
        write_private(directory / "backup-selector.json", {"schema": 1, "run_id": run_id})
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("plan", "pause", "backup"))
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--stage-run-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--confirm-pause", action="store_true")
    parser.add_argument("--confirm-backup", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not GATE.protected(args.report.parent, 0, directory=True):
            raise OSError("unsafe report directory")
        write_private(args.report, {"phase": args.phase, "status": "INCOMPLETE"})
    except OSError:
        print("STOP report_write_failed")
        return 2
    checks = {"root_execution": os.geteuid() == 0}
    directory = None
    try:
        if not checks["root_execution"]:
            raise ValueError("root required")
        data, profile_digest = profile(args.profile)
        directory = stage(data["project_directory"], args.stage_run_id, args.manifest_sha256)
        if args.report.is_relative_to(data["project_directory"]):
            raise ValueError("report outside project required")
        if (directory / f"{args.phase}-report.json").exists():
            raise ValueError("phase already attempted")
        if args.phase == "plan":
            if args.plan_sha256 or args.confirm_pause or args.confirm_backup:
                raise ValueError("invalid plan arguments")
            checks.update(plan(data, profile_digest, directory, args.manifest_sha256))
        else:
            if not args.plan_sha256 or (args.phase == "pause") != args.confirm_pause or (args.phase == "backup") != args.confirm_backup:
                raise ValueError("confirmation required")
            value = load_plan(directory, args.plan_sha256, profile_digest, args.manifest_sha256)
            checks.update(pause(data, value, directory) if args.phase == "pause"
                          else backup(data, value, directory, args.manifest_sha256))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        checks["phase_preflight"] = False
    result = {"phase": args.phase, "status": "PASS" if checks and all(checks.values()) else "STOP",
              "checks": checks}
    try:
        if directory is not None:
            write_private(directory / f"{args.phase}-report.json", result)
    except OSError:
        result["status"] = "STOP"
        result["checks"]["stage_report_write"] = False
    try:
        replace_private(args.report, result)
    except OSError:
        print("STOP report_write_failed")
        return 2
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
