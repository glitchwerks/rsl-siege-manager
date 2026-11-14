#!/usr/bin/env python3
"""Read-only final gate between confirmed migrations and image promotion."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("release_migrate_for_promotion", HERE / "portable-vps-release-migrate.py")
MIGRATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATE)
PAUSE = MIGRATE.PAUSE
READY = MIGRATE.READY
GATE = MIGRATE.GATE
REMOTE = MIGRATE.REMOTE


def completed_step(directory, manifest, manifest_digest, plan_digest, readiness_digest, database):
    """Accept only the exact completed step in this stage, including its start marker."""
    name = f"migrate-{database}"
    if not READY.PROOF.accepted_report(directory, f"{name}-report.json", name):
        return False
    marker = json.loads(GATE.read_private(directory / f"{name}-start.json", 0),
                        object_pairs_hook=REMOTE.no_duplicate_keys)
    item = manifest["migrations"][database]
    return (isinstance(marker, dict) and marker.get("phase") == f"{name}-start"
            and marker.get("manifest_sha256") == manifest_digest
            and marker.get("plan_sha256") == plan_digest
            and marker.get("readiness_sha256") == readiness_digest
            and marker.get("service") == MIGRATE.SERVICES[database]
            and marker.get("image") == (manifest["siege"]["api_image"] if database == "siege"
                                         else manifest["mom"]["image"])
            and marker.get("from_revision") == item["from_revision"]
            and marker.get("to_revision") == item["to_revision"])


def inspect(data, directory, plan, manifest_digest, plan_digest, readiness_digest):
    checks = {"root_execution": os.geteuid() == 0}
    readiness = MIGRATE.accepted_readiness(directory, manifest_digest, plan_digest, readiness_digest)
    manifest = MIGRATE.candidate(directory, manifest_digest)
    order = manifest["migrations"]["order"]
    checks["readiness_bound"] = (readiness["migration_order"] == order
        and readiness["postgres_id"] == plan["ids"]["postgres"]
        and readiness["revisions"] == {name: manifest["migrations"][name]["from_revision"]
                                      for name in ("siege", "mom")})
    proof, proof_digest = READY.accepted_proof(directory, manifest_digest, plan_digest,
                                               plan["preflight_baseline_sha256"])
    checks["selected_recovery_proof_current"] = (proof_digest == readiness["recovery_proof_sha256"]
        and READY.proof_current(data, directory, plan, plan_digest, manifest_digest, proof))
    checks["writers_paused"] = PAUSE.paused(data, plan)
    checks["installed_preflight_current"] = PAUSE.bound_preflight(
        data, directory, manifest_digest, plan["preflight_baseline_sha256"],
        writers_paused=True)[0]
    for database in ("siege", "mom"):
        if database in order:
            checks[f"{database}_migration_accepted"] = completed_step(
                directory, manifest, manifest_digest, plan_digest, readiness_digest, database)
        else:
            checks[f"{database}_migration_not_attempted"] = not any(
                (directory / f"migrate-{database}-{suffix}.json").exists()
                for suffix in ("start", "report"))
    if not all(checks.values()):
        return checks, None
    expected = {name: manifest["migrations"][name]["to_revision"]
                for name in ("siege", "mom")}
    checks["final_database_revisions_exact"] = MIGRATE.revisions_match(readiness["postgres_id"], expected)
    if not checks["final_database_revisions_exact"]:
        return checks, None
    valid, _ = MIGRATE.candidate_config(data["project_directory"], manifest)
    checks["candidate_configuration_valid"] = valid
    checks["candidate_images_cached"] = all(MIGRATE.image_cached(image) for image in (
        manifest["siege"]["api_image"], manifest["siege"]["frontend_image"],
        manifest["mom"]["image"]))
    checks["maintenance_idle"] = PAUSE.no_extra_writers(plan["project"])
    if not all(checks.values()):
        return checks, None
    baseline = {"schema": 1, "phase": "release-promotion-readiness-baseline",
                "manifest_sha256": manifest_digest, "plan_sha256": plan_digest,
                "readiness_sha256": readiness_digest,
                "preflight_baseline_sha256": plan["preflight_baseline_sha256"],
                "recovery_proof_sha256": proof_digest, "postgres_id": readiness["postgres_id"],
                "revisions": expected, "migration_order": order}
    return checks, baseline


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--stage-run-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--readiness-sha256", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.report.is_absolute() or ".." in args.report.parts:
        print("STOP invalid_report_path")
        return 2
    try:
        if os.geteuid() != 0:
            raise ValueError("root required")
        data, profile_digest = PAUSE.profile(args.profile)
        if args.report.is_relative_to(data["project_directory"]):
            raise ValueError("report outside project required")
        if not GATE.protected(args.report.parent, 0, directory=True):
            raise ValueError("unsafe report directory")
        PAUSE.write_private(args.report, {"phase": "release-promotion-ready", "status": "INCOMPLETE"})
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError,
            subprocess.TimeoutExpired):
        print("STOP profile_preflight_failed")
        return 2
    directory = None
    checks = {"root_execution": True}
    try:
        directory = PAUSE.stage(data["project_directory"], args.stage_run_id, args.manifest_sha256)
        if any((directory / name).exists() for name in
               ("promotion-ready-report.json", "promotion-ready-baseline.json")):
            raise ValueError("promotion readiness already attempted")
        plan = PAUSE.load_plan(directory, args.plan_sha256, profile_digest, args.manifest_sha256)
        checks, baseline = inspect(data, directory, plan, args.manifest_sha256,
                                   args.plan_sha256, args.readiness_sha256)
        if baseline is not None and all(checks.values()):
            PAUSE.write_private(directory / "promotion-ready-baseline.json", baseline)
            checks["baseline_recorded"] = True
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError,
            subprocess.TimeoutExpired):
        checks["promotion_readiness_inspection"] = False
    result = {"phase": "release-promotion-ready",
              "status": "PASS" if checks and all(checks.values()) else "STOP", "checks": checks}
    try:
        if directory is not None:
            PAUSE.write_private(directory / "promotion-ready-report.json", result)
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
