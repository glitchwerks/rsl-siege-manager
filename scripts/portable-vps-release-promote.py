#!/usr/bin/env python3
"""Confirm one candidate image switch after accepted, read-only promotion readiness."""

import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "release_promotion_ready_for_activation", HERE / "portable-vps-release-promotion-ready.py")
READY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(READY)
MIGRATE = READY.MIGRATE
PAUSE = READY.PAUSE
GATE = READY.GATE
REMOTE = READY.REMOTE
PROOF = READY.READY.PROOF
PREFLIGHT = MIGRATE.PREFLIGHT
ENVIRONMENT = MIGRATE.ENVIRONMENT
IMAGE_KEYS = {"BACKEND_IMAGE": ("siege", "api_image"),
              "FRONTEND_IMAGE": ("siege", "frontend_image"),
              "MOM_IMAGE": ("mom", "image")}
SERVICES = {"backend": "BACKEND_IMAGE", "frontend": "FRONTEND_IMAGE", "mom": "MOM_IMAGE"}
ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=")


def accepted_gate(directory, manifest_digest, plan_digest, readiness_digest):
    raw = GATE.read_private(directory / "promotion-ready-baseline.json", 0)
    value = json.loads(raw, object_pairs_hook=REMOTE.no_duplicate_keys)
    if not (PROOF.accepted_report(directory, "promotion-ready-report.json",
                                  "release-promotion-ready")
            and isinstance(value, dict) and set(value) == {
                "schema", "phase", "manifest_sha256", "plan_sha256", "readiness_sha256",
                "preflight_baseline_sha256", "recovery_proof_sha256", "postgres_id",
                "revisions", "migration_order"}
            and value["schema"] == 1 and value["phase"] == "release-promotion-readiness-baseline"
            and value["manifest_sha256"] == manifest_digest
            and value["plan_sha256"] == plan_digest
            and value["readiness_sha256"] == readiness_digest):
        raise ValueError("promotion readiness not accepted")
    return value, PAUSE.digest(raw)


def image_config(raw, manifest):
    """Change only the three unique active image assignments, preserving all other bytes."""
    text = raw.decode("utf-8")
    seen = set()
    result = []
    for line in text.splitlines(keepends=True):
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        body = line[:-len(ending)] if ending else line
        match = ASSIGNMENT.match(body) if not body.lstrip().startswith("#") else None
        if match and match.group(1) in IMAGE_KEYS:
            name = match.group(1)
            if name in seen or not body.startswith(name + "="):
                raise ValueError("ambiguous image assignment")
            seen.add(name)
            section, field = IMAGE_KEYS[name]
            result.append(f"{name}={manifest[section][field]}{ending}")
        else:
            result.append(line)
    if seen != set(IMAGE_KEYS):
        raise ValueError("missing image assignment")
    return "".join(result).encode("utf-8")


def approval_config(manifest):
    value = {"schema": 1, "image": manifest["mom"]["image"],
             "source_commit": manifest["mom"]["source_sha"],
             "capabilities": ["portable-auth-v1", "scheduler-progress-probe-v1"]}
    if not PREFLIGHT.valid_mom_approval(value):
        raise ValueError("invalid Mom approval")
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def write_exclusive(path, raw):
    if not GATE.protected(path.parent, 0, directory=True):
        raise ValueError("unsafe backup directory")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


@contextmanager
def project_lock(project):
    """Serialize every promotion attempt for this project across stage directories."""
    if not GATE.protected(project, 0, directory=True):
        raise ValueError("unsafe project directory")
    path = project / ".release-promotion.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077):
            raise ValueError("unsafe promotion lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def replace_bytes(path, raw):
    if not GATE.protected(path.parent, 0, directory=True) or not GATE.protected(path, 0):
        raise ValueError("unsafe protected target")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def compose_up(project):
    command = ["docker", "compose", "--env-file", str(project / "stack.env"),
               "--project-directory", str(project), "-f", str(project / "compose.yml"),
               "-f", str(project / "compose.mom.yml"), "up", "--no-deps",
               "--no-build", "--pull", "never", "--wait", "--wait-timeout", "240",
               "backend", "mom", "frontend"]
    try:
        result = subprocess.run(command, env=ENVIRONMENT, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def live_service(project_name, service, expected_image):
    identifier = PAUSE.identifiers(project_name, service)
    if identifier is None:
        return None
    image_raw = PAUSE.command(["docker", "inspect", "--format", "{{json .Config.Image}}", identifier])
    state_raw = PAUSE.command(["docker", "inspect", "--format", "{{json .State}}", identifier])
    try:
        image = json.loads(image_raw)
        state = json.loads(state_raw)
        if (image != expected_image or state.get("Running") is not True
                or state.get("Status") != "running"
                or state.get("Health", {}).get("Status") != "healthy"):
            return None
    except (TypeError, ValueError, KeyError, AttributeError):
        return None
    return identifier


def unchanged_foundation(plan, preflight):
    name = plan["project"]
    for service in ("postgres", "proxy"):
        identifier = PAUSE.identifiers(name, service)
        if identifier != plan["ids"][service] or identifier != preflight["installed"][service]["id"]:
            return False
        expected = preflight["installed"][service]["image"]
        image_raw = PAUSE.command(["docker", "inspect", "--format", "{{json .Config.Image}}", identifier])
        state_raw = PAUSE.command(["docker", "inspect", "--format", "{{json .State}}", identifier])
        try:
            state = json.loads(state_raw)
            if (json.loads(image_raw) != expected or state.get("Running") is not True
                    or state.get("Status") != "running"
                    or (service == "postgres" and state.get("Health", {}).get("Status") != "healthy")):
                return False
        except (TypeError, ValueError, KeyError, AttributeError):
            return False
    return True


def promote(data, directory, plan, manifest_digest, plan_digest, readiness_digest,
            promotion_digest):
    checks = {"root_execution": os.geteuid() == 0}
    gate, gate_digest = accepted_gate(directory, manifest_digest, plan_digest, readiness_digest)
    checks["promotion_gate_digest"] = gate_digest == promotion_digest
    current_checks, current_gate = READY.inspect(
        data, directory, plan, manifest_digest, plan_digest, readiness_digest)
    checks["promotion_gate_current"] = bool(current_gate == gate and all(current_checks.values()))
    if not all(checks.values()):
        return checks
    manifest = MIGRATE.candidate(directory, manifest_digest)
    project = data["project_directory"]
    stack = project / "stack.env"
    approval = project / "runtime" / "mom-image.json"
    old_stack = GATE.read_private(stack, 0)
    old_approval = GATE.read_private(approval, 0, 16384)
    new_stack = image_config(old_stack, manifest)
    new_approval = approval_config(manifest)
    checks["candidate_changes_installed_state"] = new_stack != old_stack or new_approval != old_approval
    preflight_current, baseline = PAUSE.bound_preflight(
        data, directory, manifest_digest, plan["preflight_baseline_sha256"],
        writers_paused=True)
    checks["installed_baseline_current"] = preflight_current
    if not all(checks.values()):
        return checks
    candidate_render = PREFLIGHT.render("mom", stack, project, {
        name: manifest[section][field] for name, (section, field) in IMAGE_KEYS.items()})
    checks["candidate_configuration_valid"] = MIGRATE.candidate_config(project, manifest)[0]
    if not checks["candidate_configuration_valid"]:
        return checks
    checks["foundation_unchanged_before_switch"] = unchanged_foundation(plan, baseline)
    checks["configuration_still_original"] = (GATE.read_private(stack, 0) == old_stack
        and GATE.read_private(approval, 0, 16384) == old_approval)
    if not all(checks.values()):
        return checks
    write_exclusive(directory / "installed-stack.env", old_stack)
    write_exclusive(directory / "installed-mom-image.json", old_approval)
    checks["original_configuration_preserved"] = True
    PAUSE.write_private(directory / "promote-start.json", {
        "phase": "promote-start", "manifest_sha256": manifest_digest,
        "plan_sha256": plan_digest, "promotion_ready_sha256": promotion_digest,
        "installed_stack_sha256": PAUSE.digest(old_stack),
        "installed_mom_approval_sha256": PAUSE.digest(old_approval),
        "started_at": PAUSE.stamp()})
    activation_started = False
    try:
        replace_bytes(stack, new_stack)
        replace_bytes(approval, new_approval)
        checks["candidate_files_written"] = (GATE.read_private(stack, 0) == new_stack
            and GATE.read_private(approval, 0, 16384) == new_approval)
        checks["candidate_render_exact"] = (checks["candidate_files_written"]
            and PREFLIGHT.render("mom", stack, project, {}) == candidate_render)
        checks["foundation_unchanged_before_activation"] = unchanged_foundation(plan, baseline)
        checks["writers_paused_before_activation"] = PAUSE.paused(data, plan)
        checks["revisions_exact_before_activation"] = MIGRATE.revisions_match(
            gate["postgres_id"], gate["revisions"])
        if not all(checks.values()):
            raise ValueError("candidate pre-activation drift")
        PAUSE.write_private(directory / "promote-activation-start.json", {
            "phase": "promote-activation-start", "manifest_sha256": manifest_digest,
            "started_at": PAUSE.stamp()})
        activation_started = True
        checks["targeted_compose_activation"] = compose_up(project)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError,
            subprocess.TimeoutExpired):
        checks["promotion_operation"] = False
    if not activation_started:
        try:
            replace_bytes(stack, old_stack)
            replace_bytes(approval, old_approval)
            checks["pre_activation_config_restored"] = (GATE.read_private(stack, 0) == old_stack
                and GATE.read_private(approval, 0, 16384) == old_approval)
        except (OSError, ValueError):
            checks["pre_activation_config_restored"] = False
        return checks
    # Once activation begins, never auto-restore images or databases: writers may have made new writes.
    checks["candidate_configuration_persisted"] = (GATE.read_private(stack, 0) == new_stack
        and GATE.read_private(approval, 0, 16384) == new_approval)
    checks["foundation_unchanged_after_activation"] = unchanged_foundation(plan, baseline)
    checks["one_healthy_candidate_per_service"] = all(
        live_service(plan["project"], service, manifest[section][field]) is not None
        for service, key in SERVICES.items() for section, field in (IMAGE_KEYS[key],))
    checks["maintenance_and_bundled_bot_idle"] = PAUSE.no_extra_writers(plan["project"])
    checks["database_revisions_unchanged"] = MIGRATE.revisions_match(
        gate["postgres_id"], gate["revisions"])
    return checks


def run_locked(data, profile_digest, args):
    """Hold the project lock until both private and external reports are complete."""
    directory = None
    checks = {"root_execution": True}
    try:
        directory = PAUSE.stage(data["project_directory"], args.stage_run_id, args.manifest_sha256)
        if any((directory / name).exists() for name in (
                "promote-start.json", "promote-report.json", "promote-activation-start.json",
                "installed-stack.env", "installed-mom-image.json")):
            raise ValueError("promotion already attempted")
        plan = PAUSE.load_plan(directory, args.plan_sha256, profile_digest, args.manifest_sha256)
        checks.update(promote(data, directory, plan, args.manifest_sha256, args.plan_sha256,
                              args.readiness_sha256, args.promotion_ready_sha256))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError,
            subprocess.TimeoutExpired):
        checks["promotion_preflight"] = False
    result = {"phase": "release-promote",
              "status": "PASS" if checks and all(checks.values()) else "STOP", "checks": checks}
    try:
        if directory is not None:
            PAUSE.write_private(directory / "promote-report.json", result)
    except OSError:
        result["status"] = "STOP"
        result["checks"]["stage_report_write"] = False
    try:
        PAUSE.replace_private(args.report, result)
    except OSError:
        print("STOP report_write_failed")
        return 2
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--stage-run-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--readiness-sha256", required=True)
    parser.add_argument("--promotion-ready-sha256", required=True)
    parser.add_argument("--confirm-promote", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    if (not args.report.is_absolute() or ".." in args.report.parts
            or args.confirm_promote != args.stage_run_id
            or not REMOTE.SHA256.fullmatch(args.promotion_ready_sha256)):
        print("STOP invalid_request")
        return 2
    try:
        if os.geteuid() != 0:
            raise ValueError("root required")
        data, profile_digest = PAUSE.profile(args.profile)
        if args.report.is_relative_to(data["project_directory"]):
            raise ValueError("report outside project required")
        if not GATE.protected(args.report.parent, 0, directory=True):
            raise ValueError("unsafe report directory")
        PAUSE.write_private(args.report, {"phase": "release-promote", "status": "INCOMPLETE"})
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError,
            subprocess.TimeoutExpired):
        print("STOP profile_preflight_failed")
        return 2
    try:
        with project_lock(data["project_directory"]):
            return run_locked(data, profile_digest, args)
    except (OSError, ValueError):
        result = {"phase": "release-promote", "status": "STOP", "checks": {"project_lock": False}}
        try:
            PAUSE.replace_private(args.report, result)
        except OSError:
            print("STOP report_write_failed")
            return 2
        print("STOP")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
