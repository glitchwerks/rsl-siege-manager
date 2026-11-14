#!/usr/bin/env python3
"""Read-only candidate configuration gate and installed VPS baseline capture."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess


HERE = Path(__file__).resolve().parent


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


PAUSE = module("release_pause", "portable-vps-release-pause-backup.py")
GATE = PAUSE.GATE
REMOTE = PAUSE.REMOTE
PREFLIGHT = module("portable_preflight", "portable-preflight.py")
SERVICES = ("backend", "frontend", "mom", "postgres", "proxy")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def reviewed_config(path, expected):
    try:
        if not path.is_absolute() or any(item.is_symlink() for item in (path, *path.parents)):
            return False
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022 or info.st_size > 65536):
            return False
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            opened = os.fstat(source.fileno())
            return (stat.S_ISREG(opened.st_mode) and opened.st_uid == 0
                    and not opened.st_mode & 0o022 and source.read(65537) == expected)
    except OSError:
        return False


def checkout_at(source_sha):
    head = PAUSE.command(["git", "-C", str(PREFLIGHT.ROOT), "rev-parse", "HEAD"])
    changed = PAUSE.command(["git", "-C", str(PREFLIGHT.ROOT), "status", "--porcelain",
                             "--untracked-files=no"])
    return head == source_sha and changed == ""


def running_services(project_name, rendered):
    actual = {}
    for service in SERVICES:
        identifier = PAUSE.identifiers(project_name, service)
        if identifier is None:
            return None
        image_raw = PAUSE.command(["docker", "inspect", "--format", "{{json .Config.Image}}", identifier])
        state_raw = PAUSE.command(["docker", "inspect", "--format", "{{json .State}}", identifier])
        try:
            image = json.loads(image_raw)
            state = json.loads(state_raw)
            expected = rendered["services"][service]["image"]
            if (image != expected or state.get("Running") is not True
                    or state.get("Status") != "running"
                    or (service != "proxy" and state.get("Health", {}).get("Status") != "healthy")):
                return None
        except (ValueError, TypeError, KeyError, AttributeError):
            return None
        actual[service] = {"id": identifier, "image": image}
    return actual


def candidate_images_match(rendered, manifest):
    services = rendered["services"]
    return (all(services[name]["image"] == manifest["siege"]["api_image"]
                for name in ("backend", "migrate-siege"))
            and services["frontend"]["image"] == manifest["siege"]["frontend_image"]
            and all(services[name]["image"] == manifest["mom"]["image"]
                    for name in ("mom", "migrate-mom")))


def inspect(project, stage_directory, manifest_digest):
    checks = {"root_execution": os.geteuid() == 0,
              "project_private": GATE.protected(project, 0, directory=True)}
    if not all(checks.values()):
        return checks, None
    raw = GATE.read_private(stage_directory / "candidate.json", 0, 16384)
    checks["candidate_digest"] = digest(raw) == manifest_digest and REMOTE.valid_manifest(raw)
    if not checks["candidate_digest"]:
        return checks, None
    candidate = json.loads(raw, object_pairs_hook=REMOTE.no_duplicate_keys)
    checks["reviewed_source_checkout"] = checkout_at(candidate["configuration"]["source_sha"])
    checks["reviewed_compose"] = all(reviewed_config(project / name,
        (PREFLIGHT.PORTABLE / name).read_bytes()) for name in ("compose.yml", "compose.mom.yml"))
    stack = project / "stack.env"
    checks["runtime_private"] = PREFLIGHT.private_runtime_files("mom", stack, project)
    checks["reviewed_bind_sources"] = PREFLIGHT.deployment_bind_sources_current(project)
    if not all(checks.values()):
        return checks, None
    stack_raw = GATE.read_private(stack, 0)
    approval_raw = GATE.read_private(project / "runtime" / "mom-image.json", 0)
    current = PREFLIGHT.render("mom", stack, project, {})
    approval = PREFLIGHT.read_mom_approval(project / "runtime" / "mom-image.json")
    checks["current_mom_approval"] = (approval is not None and
        current["services"]["mom"]["image"] == approval["image"])
    name = GATE.project_name(stack)
    checks["project_identity"] = name is not None
    if not all(checks.values()):
        return checks, None
    checks["no_extra_writers"] = PAUSE.no_extra_writers(name)
    if not checks["no_extra_writers"]:
        return checks, None
    installed = running_services(name, current)
    checks["installed_services_match_config"] = installed is not None
    if not checks["installed_services_match_config"]:
        return checks, None
    overrides = {"BACKEND_IMAGE": candidate["siege"]["api_image"],
                 "FRONTEND_IMAGE": candidate["siege"]["frontend_image"],
                 "MOM_IMAGE": candidate["mom"]["image"]}
    proposed = PREFLIGHT.render("mom", stack, project, overrides)
    proposed_approval = {"schema": 1, "image": candidate["mom"]["image"],
                         "source_commit": candidate["mom"]["source_sha"],
                         "capabilities": ["portable-auth-v1", "scheduler-progress-probe-v1"]}
    checks["candidate_images_exact"] = candidate_images_match(proposed, candidate)
    validation = PREFLIGHT.validate(proposed, "mom", proposed_approval)
    checks["candidate_configuration_valid"] = bool(validation) and all(validation.values())
    checks["baseline_unchanged"] = (digest(GATE.read_private(stack, 0)) == digest(stack_raw)
        and digest(GATE.read_private(project / "runtime" / "mom-image.json", 0)) == digest(approval_raw)
        and all(reviewed_config(project / file, (PREFLIGHT.PORTABLE / file).read_bytes())
                for file in ("compose.yml", "compose.mom.yml"))
        and PREFLIGHT.deployment_bind_sources_current(project)
        and PAUSE.no_extra_writers(name)
        and running_services(name, current) == installed)
    if not all(checks.values()):
        return checks, None
    baseline = {"schema": 1, "phase": "release-preflight-baseline",
                "manifest_sha256": manifest_digest,
                "candidate_source_sha": candidate["configuration"]["source_sha"],
                "installed_mom_source_sha": approval["source_commit"],
                "project": name, "stack_env_sha256": digest(stack_raw),
                "mom_approval_sha256": digest(approval_raw),
                "config_sha256": {file: digest((project / file).read_bytes()) for file in
                    ("compose.yml", "compose.mom.yml", "Caddyfile", "init-databases.sql")},
                "installed": installed}
    return checks, baseline


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-directory", required=True, type=Path)
    parser.add_argument("--stage-run-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if not GATE.protected(args.report.parent, 0, directory=True):
            raise OSError("unsafe report directory")
        PAUSE.write_private(args.report, {"phase": "release-preflight", "status": "INCOMPLETE"})
    except OSError:
        print("STOP report_write_failed")
        return 2
    directory = None
    checks = {"root_execution": os.geteuid() == 0}
    try:
        if not checks["root_execution"]:
            raise ValueError("root required")
        directory = PAUSE.stage(args.project_directory, args.stage_run_id, args.manifest_sha256)
        if args.report.is_relative_to(args.project_directory):
            raise ValueError("report outside project required")
        if any((directory / name).exists() for name in ("preflight-report.json", "preflight-baseline.json")):
            raise ValueError("preflight already attempted")
        checks, baseline = inspect(args.project_directory, directory, args.manifest_sha256)
        if baseline is not None and all(checks.values()):
            PAUSE.write_private(directory / "preflight-baseline.json", baseline)
            checks["baseline_recorded"] = True
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError,
            subprocess.TimeoutExpired):
        checks["preflight_inspection"] = False
    result = {"phase": "release-preflight",
              "status": "PASS" if checks and all(checks.values()) else "STOP", "checks": checks}
    try:
        if directory is not None:
            PAUSE.write_private(directory / "preflight-report.json", result)
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


if __name__ == "__main__":
    raise SystemExit(main())
