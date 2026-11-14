#!/usr/bin/env python3
"""Read-only revision and same-copy proof gate before a VPS release migration."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("release_proof", HERE / "portable-vps-release-recovery-proof.py")
PROOF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROOF)
PAUSE = PROOF.PAUSE
GATE = PROOF.GATE
REMOTE = PROOF.REMOTE
REVISION = re.compile(r"[a-zA-Z0-9_]{1,64}\Z")
QUERY = "SELECT version_num FROM public.alembic_version;"
PSQL = '''export PGPASSWORD="$(cat /run/secrets/postgres-admin)"
exec psql --no-psqlrc --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \\
  --host 127.0.0.1 --port 5432 --username postgres --dbname "$1" --command "$2"'''
ENVIRONMENT = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/root", "LANG": "C",
               "DOCKER_HOST": "unix:///var/run/docker.sock"}


def current_revision(postgres_id, database):
    if not PAUSE.CONTAINER_ID.fullmatch(postgres_id) or database not in {"siege", "mom_bot"}:
        return None
    command = ["docker", "exec", "--user", "0", postgres_id, "sh", "-eu", "-c",
               PSQL, "release-revision", database, QUERY]
    try:
        result = subprocess.run(command, env=ENVIRONMENT, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or len(result.stdout) > 256:
        return None
    lines = result.stdout.splitlines()
    return lines[0] if len(lines) == 1 and REVISION.fullmatch(lines[0]) else None


def accepted_proof(directory, manifest_digest, plan_digest, preflight_digest):
    raw = GATE.read_private(directory / "recovery-proof-baseline.json", 0)
    baseline = json.loads(raw, object_pairs_hook=REMOTE.no_duplicate_keys)
    if not PROOF.accepted_report(directory, "recovery-proof-report.json", "release-recovery-proof"):
        raise ValueError("recovery proof not accepted")
    if not (isinstance(baseline, dict) and set(baseline) == {
            "schema", "phase", "manifest_sha256", "plan_sha256", "preflight_baseline_sha256",
            "backup_selector_sha256", "backup_run_id", "proofs"}
            and baseline["schema"] == 1 and baseline["phase"] == "release-recovery-proof-baseline"
            and baseline["manifest_sha256"] == manifest_digest
            and baseline["plan_sha256"] == plan_digest
            and baseline["preflight_baseline_sha256"] == preflight_digest
            and isinstance(baseline["backup_selector_sha256"], str)
            and REMOTE.SHA256.fullmatch(baseline["backup_selector_sha256"])
            and isinstance(baseline["proofs"], dict)
            and set(baseline["proofs"]) == {"backup", "recover", "restore"}):
        raise ValueError("recovery proof mismatch")
    for item in baseline["proofs"].values():
        if not (isinstance(item, dict) and set(item) == {"path", "sha256"}
                and isinstance(item["path"], str) and Path(item["path"]).is_absolute()
                and ".." not in Path(item["path"]).parts and isinstance(item["sha256"], str)
                and REMOTE.SHA256.fullmatch(item["sha256"])):
            raise ValueError("invalid proof path")
    return baseline, PAUSE.digest(raw)


def proof_current(data, directory, value, plan_digest, manifest_digest, baseline):
    recover_path = Path(baseline["proofs"]["recover"]["path"])
    restore_path = Path(baseline["proofs"]["restore"]["path"])
    checks, current = PROOF.inspect(data, directory, value, plan_digest, manifest_digest,
                                    recover_path, restore_path)
    return bool(checks) and all(checks.values()) and current == baseline


def inspect(data, directory, value, plan_digest, manifest_digest):
    checks = {"root_execution": os.geteuid() == 0}
    baseline, baseline_digest = accepted_proof(directory, manifest_digest, plan_digest,
                                               value["preflight_baseline_sha256"])
    checks["selected_backup_proof_current"] = proof_current(
        data, directory, value, plan_digest, manifest_digest, baseline)
    if not all(checks.values()):
        return checks, None
    raw = GATE.read_private(directory / "candidate.json", 0, 16384)
    candidate = json.loads(raw, object_pairs_hook=REMOTE.no_duplicate_keys)
    checks["candidate_exact"] = PAUSE.digest(raw) == manifest_digest and REMOTE.valid_manifest(raw)
    if not checks["candidate_exact"]:
        return checks, None
    postgres_id = value["ids"]["postgres"]
    revisions = {name: current_revision(postgres_id, database)
                 for name, database in (("siege", "siege"), ("mom", "mom_bot"))}
    for name in ("siege", "mom"):
        checks[f"{name}_revision_exact"] = (revisions[name] is not None
            and revisions[name] == candidate["migrations"][name]["from_revision"])
    if not all(checks.values()):
        return checks, None
    checks["selected_backup_proof_still_current"] = proof_current(
        data, directory, value, plan_digest, manifest_digest, baseline)
    checks["revisions_still_exact"] = all(current_revision(postgres_id, database) == revisions[name]
        for name, database in (("siege", "siege"), ("mom", "mom_bot")))
    if not all(checks.values()):
        return checks, None
    ready = {"schema": 1, "phase": "release-migration-readiness-baseline",
             "manifest_sha256": manifest_digest, "plan_sha256": plan_digest,
             "recovery_proof_sha256": baseline_digest,
             "postgres_id": postgres_id, "revisions": revisions,
             "migration_order": candidate["migrations"]["order"]}
    return checks, ready


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--stage-run-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.report.is_absolute() or ".." in args.report.parts:
        print("STOP invalid_report_path")
        return 2
    try:
        if not GATE.protected(args.report.parent, 0, directory=True):
            raise OSError("unsafe report directory")
        PAUSE.write_private(args.report, {"phase": "release-migration-ready", "status": "INCOMPLETE"})
    except OSError:
        print("STOP report_write_failed")
        return 2
    directory = None
    checks = {"root_execution": os.geteuid() == 0}
    try:
        if not checks["root_execution"]:
            raise ValueError("root required")
        data, profile_digest = PAUSE.profile(args.profile)
        directory = PAUSE.stage(data["project_directory"], args.stage_run_id, args.manifest_sha256)
        if args.report.is_relative_to(data["project_directory"]):
            raise ValueError("report outside project required")
        if any((directory / name).exists() for name in
               ("migration-ready-report.json", "migration-ready-baseline.json")):
            raise ValueError("readiness already attempted")
        value = PAUSE.load_plan(directory, args.plan_sha256, profile_digest, args.manifest_sha256)
        checks, baseline = inspect(data, directory, value, args.plan_sha256, args.manifest_sha256)
        if baseline is not None and all(checks.values()):
            PAUSE.write_private(directory / "migration-ready-baseline.json", baseline)
            checks["baseline_recorded"] = True
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError,
            subprocess.TimeoutExpired):
        checks["readiness_inspection"] = False
    result = {"phase": "release-migration-ready",
              "status": "PASS" if checks and all(checks.values()) else "STOP", "checks": checks}
    try:
        if directory is not None:
            PAUSE.write_private(directory / "migration-ready-report.json", result)
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
