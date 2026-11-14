#!/usr/bin/env python3
"""Run one explicitly confirmed candidate migration while both app writers remain stopped."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


READY = module("release_ready", "portable-vps-release-migration-ready.py")
PAUSE = READY.PAUSE
GATE = READY.GATE
REMOTE = READY.REMOTE
PREFLIGHT = module("portable_preflight_migrate", "portable-preflight.py")
SERVICES = {"siege": "migrate-siege", "mom": "migrate-mom"}
DATABASES = {"siege": "siege", "mom": "mom_bot"}
ENVIRONMENT = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/root", "LANG": "C",
               "DOCKER_HOST": "unix:///var/run/docker.sock"}


def accepted_readiness(directory, manifest_digest, plan_digest, readiness_digest):
    if not REMOTE.SHA256.fullmatch(readiness_digest):
        raise ValueError("invalid readiness digest")
    raw = GATE.read_private(directory / "migration-ready-baseline.json", 0)
    value = json.loads(raw, object_pairs_hook=REMOTE.no_duplicate_keys)
    if not (PAUSE.digest(raw) == readiness_digest
            and READY.PROOF.accepted_report(directory, "migration-ready-report.json", "release-migration-ready")
            and isinstance(value, dict) and set(value) == {
                "schema", "phase", "manifest_sha256", "plan_sha256", "recovery_proof_sha256",
                "postgres_id", "revisions", "migration_order"}
            and value["schema"] == 1 and value["phase"] == "release-migration-readiness-baseline"
            and value["manifest_sha256"] == manifest_digest and value["plan_sha256"] == plan_digest
            and isinstance(value["recovery_proof_sha256"], str)
            and REMOTE.SHA256.fullmatch(value["recovery_proof_sha256"])
            and isinstance(value["postgres_id"], str)
            and PAUSE.CONTAINER_ID.fullmatch(value["postgres_id"])
            and isinstance(value["revisions"], dict)
            and set(value["revisions"]) == {"siege", "mom"}
            and all(isinstance(item, str) and READY.REVISION.fullmatch(item)
                    for item in value["revisions"].values())
            and isinstance(value["migration_order"], list)
            and len(value["migration_order"]) == len(set(value["migration_order"]))
            and set(value["migration_order"]) <= {"siege", "mom"}):
        raise ValueError("readiness mismatch")
    return value


def candidate(directory, manifest_digest):
    raw = GATE.read_private(directory / "candidate.json", 0, 16384)
    if PAUSE.digest(raw) != manifest_digest or not REMOTE.valid_manifest(raw):
        raise ValueError("candidate mismatch")
    return json.loads(raw, object_pairs_hook=REMOTE.no_duplicate_keys)


def expected_revisions(manifest, database):
    order = manifest["migrations"]["order"]
    if database not in order:
        raise ValueError("database has no migration")
    previous = set(order[:order.index(database)])
    return {name: manifest["migrations"][name][
        "to_revision" if name in previous else "from_revision"] for name in ("siege", "mom")}


def revisions_match(postgres_id, expected):
    return all(READY.current_revision(postgres_id, DATABASES[name]) == expected[name]
               for name in ("siege", "mom"))


def candidate_config(project, manifest):
    overrides = {"BACKEND_IMAGE": manifest["siege"]["api_image"],
                 "FRONTEND_IMAGE": manifest["siege"]["frontend_image"],
                 "MOM_IMAGE": manifest["mom"]["image"]}
    rendered = PREFLIGHT.render("mom", project / "stack.env", project, overrides)
    services = rendered["services"]
    images = {"backend": overrides["BACKEND_IMAGE"], "migrate-siege": overrides["BACKEND_IMAGE"],
              "frontend": overrides["FRONTEND_IMAGE"], "mom": overrides["MOM_IMAGE"],
              "migrate-mom": overrides["MOM_IMAGE"]}
    approval = {"schema": 1, "image": overrides["MOM_IMAGE"],
                "source_commit": manifest["mom"]["source_sha"],
                "capabilities": ["portable-auth-v1", "scheduler-progress-probe-v1"]}
    checks = PREFLIGHT.validate(rendered, "mom", approval)
    return (bool(checks) and all(checks.values())
            and all(services[name]["image"] == image for name, image in images.items())), overrides


def image_cached(image):
    raw = PAUSE.command(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image])
    try:
        digests = json.loads(raw)
        return isinstance(digests, list) and image in digests
    except (TypeError, ValueError):
        return False


def compose_run(project, service, operation, revision, overrides):
    if service not in SERVICES.values() or operation not in {"heads", "upgrade"}:
        raise ValueError("invalid maintenance command")
    command = ["docker", "compose", "--env-file", str(project / "stack.env"),
               "--project-directory", str(project), "-f", str(project / "compose.yml"),
               "-f", str(project / "compose.mom.yml"), "--profile", "maintenance",
               "run", "--no-deps", "--rm", "--pull", "never", "-T",
               "--entrypoint", "/app/.venv/bin/alembic", service, operation]
    if operation == "upgrade":
        if not READY.REVISION.fullmatch(revision):
            raise ValueError("invalid target revision")
        command.append(revision)
    environment = {**ENVIRONMENT, **overrides}
    try:
        result = subprocess.run(command, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if operation == "heads" else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, text=True,
            timeout=60 if operation == "heads" else 900, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout if operation == "heads" and len(result.stdout) <= 1024 else ("" if operation == "upgrade" else None)


def inspect(data, directory, plan, manifest_digest, plan_digest, readiness_digest, database):
    checks = {"root_execution": os.geteuid() == 0}
    readiness = accepted_readiness(directory, manifest_digest, plan_digest, readiness_digest)
    manifest = candidate(directory, manifest_digest)
    order = manifest["migrations"]["order"]
    checks["selected_order_exact"] = readiness["migration_order"] == order and database in order
    checks["readiness_bound"] = (readiness["postgres_id"] == plan["ids"]["postgres"]
        and readiness["revisions"] == {name: manifest["migrations"][name]["from_revision"]
                                        for name in ("siege", "mom")})
    proof, proof_digest = READY.accepted_proof(directory, manifest_digest, plan_digest,
                                               plan["preflight_baseline_sha256"])
    checks["recovery_proof_bound"] = (proof_digest == readiness["recovery_proof_sha256"]
        and READY.proof_current(data, directory, plan, plan_digest, manifest_digest, proof))
    if not all(checks.values()):
        return checks, None
    index = order.index(database)
    if index:
        previous = order[index - 1]
        checks["previous_step_accepted"] = READY.PROOF.accepted_report(
            directory, f"migrate-{previous}-report.json", f"migrate-{previous}")
        if not checks["previous_step_accepted"]:
            return checks, None
    expected = expected_revisions(manifest, database)
    checks["live_revisions_exact"] = revisions_match(readiness["postgres_id"], expected)
    checks["writers_paused"] = PAUSE.paused(data, plan)
    if not all(checks.values()):
        return checks, None
    valid, overrides = candidate_config(data["project_directory"], manifest)
    checks["candidate_configuration_valid"] = valid
    image = manifest["siege"]["api_image"] if database == "siege" else manifest["mom"]["image"]
    checks["candidate_image_cached"] = image_cached(image)
    if not all(checks.values()):
        return checks, None
    return checks, {"readiness": readiness, "manifest": manifest, "proof": proof,
                    "expected": expected, "overrides": overrides, "image": image}


def migrate(data, directory, plan, manifest_digest, plan_digest, readiness_digest, database):
    checks, context = inspect(data, directory, plan, manifest_digest, plan_digest,
                              readiness_digest, database)
    if context is None:
        return checks
    service = SERVICES[database]
    target = context["manifest"]["migrations"][database]["to_revision"]
    heads = compose_run(data["project_directory"], service, "heads", None, context["overrides"])
    checks["candidate_migration_head_exact"] = heads is not None and heads.strip() == f"{target} (head)"
    checks["maintenance_idle_after_heads"] = PAUSE.no_extra_writers(plan["project"])
    checks["revisions_exact_before_write"] = revisions_match(
        context["readiness"]["postgres_id"], context["expected"])
    checks["proof_current_before_write"] = READY.proof_current(
        data, directory, plan, plan_digest, manifest_digest, context["proof"])
    if not all(checks.values()):
        return checks
    PAUSE.write_private(directory / f"migrate-{database}-start.json", {
        "phase": f"migrate-{database}-start", "manifest_sha256": manifest_digest,
        "plan_sha256": plan_digest, "readiness_sha256": readiness_digest,
        "service": service, "image": context["image"],
        "from_revision": context["expected"][database], "to_revision": target,
        "started_at": PAUSE.stamp()})
    checks["migration_command_success"] = compose_run(
        data["project_directory"], service, "upgrade", target, context["overrides"]) is not None
    checks["maintenance_idle_after_upgrade"] = PAUSE.no_extra_writers(plan["project"])
    if not checks["migration_command_success"] or not checks["maintenance_idle_after_upgrade"]:
        return checks
    after = {**context["expected"], database: target}
    checks["target_revision_exact"] = revisions_match(context["readiness"]["postgres_id"], after)
    checks["proof_current_after_write"] = READY.proof_current(
        data, directory, plan, plan_digest, manifest_digest, context["proof"])
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--stage-run-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--readiness-sha256", required=True)
    parser.add_argument("--database", choices=tuple(SERVICES), required=True)
    parser.add_argument("--confirm-migrate", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.report.is_absolute() or ".." in args.report.parts or args.confirm_migrate != args.database:
        print("STOP invalid_request")
        return 2
    try:
        if os.geteuid() != 0:
            raise ValueError("root required")
        data, profile_digest = PAUSE.profile(args.profile)
        if args.report.is_relative_to(data["project_directory"]):
            raise ValueError("report outside project required")
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError,
            subprocess.TimeoutExpired):
        print("STOP profile_preflight_failed")
        return 2
    try:
        if not GATE.protected(args.report.parent, 0, directory=True):
            raise OSError("unsafe report directory")
        PAUSE.write_private(args.report, {"phase": f"migrate-{args.database}", "status": "INCOMPLETE"})
    except OSError:
        print("STOP report_write_failed")
        return 2
    directory = None
    checks = {"root_execution": os.geteuid() == 0}
    try:
        directory = PAUSE.stage(data["project_directory"], args.stage_run_id, args.manifest_sha256)
        if any((directory / name).exists() for name in
               (f"migrate-{args.database}-report.json", f"migrate-{args.database}-start.json")):
            raise ValueError("migration already attempted")
        plan = PAUSE.load_plan(directory, args.plan_sha256, profile_digest, args.manifest_sha256)
        checks.update(migrate(data, directory, plan, args.manifest_sha256,
                              args.plan_sha256, args.readiness_sha256, args.database))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError,
            subprocess.TimeoutExpired):
        checks["migration_preflight"] = False
    result = {"phase": f"migrate-{args.database}",
              "status": "PASS" if checks and all(checks.values()) else "STOP", "checks": checks}
    try:
        if directory is not None:
            PAUSE.write_private(directory / f"migrate-{args.database}-report.json", result)
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
