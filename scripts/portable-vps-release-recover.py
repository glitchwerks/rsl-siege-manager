#!/usr/bin/env python3
"""Classify release recovery and restore only pre-activation configuration."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "release_promote_for_recovery", HERE / "portable-vps-release-promote.py")
PROMOTE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROMOTE)
PAUSE = PROMOTE.PAUSE
GATE = PROMOTE.GATE
REMOTE = PROMOTE.REMOTE
MIGRATE = PROMOTE.MIGRATE
PREFLIGHT = importlib.util.module_from_spec(importlib.util.spec_from_file_location(
    "release_preflight_for_recovery", HERE / "portable-vps-release-preflight.py"))
PREFLIGHT.__spec__.loader.exec_module(PREFLIGHT)


def promotion_evidence(directory, manifest_digest, plan_digest, promotion_digest, preflight):
    marker = json.loads(GATE.read_private(directory / "promote-start.json", 0),
                        object_pairs_hook=REMOTE.no_duplicate_keys)
    old_stack = GATE.read_private(directory / "installed-stack.env", 0)
    old_mom = GATE.read_private(directory / "installed-mom-image.json", 0, 16384)
    if not (isinstance(marker, dict) and marker.get("phase") == "promote-start"
            and marker.get("manifest_sha256") == manifest_digest
            and marker.get("plan_sha256") == plan_digest
            and marker.get("promotion_ready_sha256") == promotion_digest
            and marker.get("installed_stack_sha256") == PAUSE.digest(old_stack)
            and marker.get("installed_mom_approval_sha256") == PAUSE.digest(old_mom)
            and preflight["stack_env_sha256"] == PAUSE.digest(old_stack)
            and preflight["runtime_sha256"]["mom-image.json"] == PAUSE.digest(old_mom)):
        raise ValueError("promotion preservation mismatch")
    return old_stack, old_mom


def migration_or_activation_started(directory):
    return any((directory / name).exists() for name in (
        "migrate-siege-start.json", "migrate-mom-start.json",
        "migrate-siege-report.json", "migrate-mom-report.json",
        "promote-activation-start.json"))


def unchanged_private_inputs(project, preflight, current_approval):
    if PREFLIGHT.config_hashes(project) != preflight["config_sha256"]:
        return False
    hashes = PREFLIGHT.runtime_hashes(project)
    return (hashes.get("mom-image.json") == PAUSE.digest(current_approval)
        and all(hashes.get(name) == old for name, old in preflight["runtime_sha256"].items()
                if name != "mom-image.json"))


def old_frontend_healthy(plan, preflight):
    old = preflight["installed"]["frontend"]
    return PROMOTE.live_service(plan["project"], "frontend", old["image"]) == old["id"]


def inspect(data, directory, plan, manifest_digest, plan_digest, readiness_digest,
            promotion_digest):
    checks = {"root_execution": os.geteuid() == 0}
    manifest = MIGRATE.candidate(directory, manifest_digest)
    gate, gate_digest = PROMOTE.accepted_gate(
        directory, manifest_digest, plan_digest, readiness_digest)
    checks["promotion_gate_exact"] = gate_digest == promotion_digest
    preflight, preflight_digest = PREFLIGHT.accepted_baseline(directory, manifest_digest)
    checks["preflight_baseline_exact"] = preflight_digest == plan["preflight_baseline_sha256"]
    old_stack, old_mom = promotion_evidence(
        directory, manifest_digest, plan_digest, promotion_digest, preflight)
    checks["preserved_configuration_exact"] = True
    checks["no_migration_or_activation"] = not migration_or_activation_started(directory)
    # A PASS promotion report requires activation and is never eligible for this restore.
    if (directory / "promote-report.json").exists():
        report = json.loads(GATE.read_private(directory / "promote-report.json", 0),
                            object_pairs_hook=REMOTE.no_duplicate_keys)
        checks["no_completed_activation"] = not (isinstance(report, dict)
            and report.get("phase") == "release-promote" and report.get("status") == "PASS")
    else:
        checks["no_completed_activation"] = True
    if not all(checks.values()):
        return checks, None
    project = data["project_directory"]
    stack = project / "stack.env"
    approval = project / "runtime" / "mom-image.json"
    current_stack = GATE.read_private(stack, 0)
    current_mom = GATE.read_private(approval, 0, 16384)
    candidate_stack = PROMOTE.image_config(old_stack, manifest)
    candidate_mom = PROMOTE.approval_config(manifest)
    checks["only_known_configuration_bytes"] = (
        current_stack in (old_stack, candidate_stack)
        and current_mom in (old_mom, candidate_mom))
    checks["protected_inputs_unchanged"] = unchanged_private_inputs(
        project, preflight, current_mom)
    checks["candidate_configuration_valid"] = MIGRATE.candidate_config(project, manifest)[0]
    checks["writers_still_paused"] = PAUSE.paused(data, plan)
    checks["original_frontend_healthy"] = old_frontend_healthy(plan, preflight)
    checks["foundation_unchanged"] = PROMOTE.unchanged_foundation(plan, preflight)
    checks["database_revisions_original"] = MIGRATE.revisions_match(
        plan["ids"]["postgres"], {name: manifest["migrations"][name]["from_revision"]
                                   for name in ("siege", "mom")})
    checks["no_other_writers"] = PAUSE.no_extra_writers(plan["project"])
    if not all(checks.values()):
        return checks, None
    context = {"old_stack": old_stack, "old_mom": old_mom,
               "candidate_stack": candidate_stack, "candidate_mom": candidate_mom,
               "current_stack": current_stack, "current_mom": current_mom,
               "manifest": manifest, "preflight": preflight}
    return checks, context


def restore(data, directory, plan, manifest_digest, plan_digest, readiness_digest,
            promotion_digest):
    checks, context = inspect(data, directory, plan, manifest_digest, plan_digest,
                              readiness_digest, promotion_digest)
    if context is None:
        return checks
    project = data["project_directory"]
    stack = project / "stack.env"
    approval = project / "runtime" / "mom-image.json"
    # Recheck the boundary immediately before the first protected write.
    checks["no_activation_or_migration_before_write"] = not migration_or_activation_started(directory)
    if not checks["no_activation_or_migration_before_write"]:
        return checks
    checks["writers_paused_before_write"] = PAUSE.paused(data, plan)
    checks["revisions_original_before_write"] = MIGRATE.revisions_match(
        plan["ids"]["postgres"], {name: context["manifest"]["migrations"][name]["from_revision"]
                                   for name in ("siege", "mom")})
    checks["configuration_unchanged_before_write"] = (
        GATE.read_private(stack, 0) == context["current_stack"]
        and GATE.read_private(approval, 0, 16384) == context["current_mom"])
    if not all(checks.values()):
        return checks
    PAUSE.write_private(directory / "recover-preactivation-start.json", {
        "phase": "recover-preactivation-start", "manifest_sha256": manifest_digest,
        "plan_sha256": plan_digest, "promotion_ready_sha256": promotion_digest,
        "started_at": PAUSE.stamp()})
    try:
        PROMOTE.replace_bytes(stack, context["old_stack"])
        PROMOTE.replace_bytes(approval, context["old_mom"])
    except (OSError, ValueError):
        checks["protected_configuration_restore"] = False
        return checks
    checks["protected_configuration_restored"] = (
        GATE.read_private(stack, 0) == context["old_stack"]
        and GATE.read_private(approval, 0, 16384) == context["old_mom"])
    checks["installed_preflight_restored"] = PREFLIGHT.baseline_current(
        project, context["preflight"], writers_paused=True)
    checks["writers_remain_paused"] = PAUSE.paused(data, plan)
    checks["frontend_and_foundation_unchanged"] = (
        old_frontend_healthy(plan, context["preflight"])
        and PROMOTE.unchanged_foundation(plan, context["preflight"]))
    checks["database_revisions_remain_original"] = MIGRATE.revisions_match(
        plan["ids"]["postgres"], {name: context["manifest"]["migrations"][name]["from_revision"]
                                   for name in ("siege", "mom")})
    checks["no_activation_or_migration_after_write"] = not migration_or_activation_started(directory)
    return checks


def run_locked(data, profile_digest, args):
    directory = None
    checks = {"root_execution": True}
    try:
        directory = PAUSE.stage(data["project_directory"], args.stage_run_id, args.manifest_sha256)
        report_name = ("recover-inspect-report.json" if args.phase == "inspect"
                       else "recover-preactivation-report.json")
        if (directory / report_name).exists() or (args.phase == "restore-preactivation" and
                (directory / "recover-preactivation-start.json").exists()):
            raise ValueError("recovery phase already attempted")
        plan = PAUSE.load_plan(directory, args.plan_sha256, profile_digest, args.manifest_sha256)
        operation = inspect if args.phase == "inspect" else restore
        outcome = operation(data, directory, plan, args.manifest_sha256, args.plan_sha256,
                            args.readiness_sha256, args.promotion_ready_sha256)
        checks = outcome[0] if args.phase == "inspect" else outcome
    except Exception:
        checks["recovery_inspection"] = False
    result = {"phase": "release-recover-" + args.phase,
              "status": "PASS" if checks and all(checks.values()) else "STOP",
              "manual_reconciliation_required": not (checks and all(checks.values())),
              "checks": checks}
    try:
        if directory is not None:
            PAUSE.write_private(directory / report_name, result)
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
    parser.add_argument("phase", choices=("inspect", "restore-preactivation"))
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--stage-run-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--readiness-sha256", required=True)
    parser.add_argument("--promotion-ready-sha256", required=True)
    parser.add_argument("--confirm-restore", default="")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    if (not args.report.is_absolute() or ".." in args.report.parts
            or not REMOTE.SHA256.fullmatch(args.promotion_ready_sha256)
            or (args.phase == "restore-preactivation" and args.confirm_restore != args.stage_run_id)
            or (args.phase == "inspect" and args.confirm_restore)):
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
        PAUSE.write_private(args.report, {"phase": "release-recover-" + args.phase,
                                         "status": "INCOMPLETE"})
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError,
            subprocess.TimeoutExpired):
        print("STOP profile_preflight_failed")
        return 2
    try:
        with PROMOTE.project_lock(data["project_directory"]):
            return run_locked(data, profile_digest, args)
    except (OSError, ValueError):
        try:
            PAUSE.replace_private(args.report, {"phase": "release-recover-" + args.phase,
                "status": "STOP", "checks": {"project_lock": False},
                "manual_reconciliation_required": True})
        except OSError:
            print("STOP report_write_failed")
            return 2
        print("STOP")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
