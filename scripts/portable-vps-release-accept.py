#!/usr/bin/env python3
"""Read-only, candidate-bound acceptance checks after confirmed VPS promotion."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request


HERE = Path(__file__).resolve().parent


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


PROMOTE = module("release_promote_for_acceptance", "portable-vps-release-promote.py")
PILOT = module("portable_pilot_for_acceptance", "portable-pilot.py")
PAUSE = PROMOTE.PAUSE
GATE = PROMOTE.GATE
REMOTE = PROMOTE.REMOTE
MIGRATE = PROMOTE.MIGRATE
PROOF = PROMOTE.PROOF
PREFLIGHT = module("release_preflight_for_acceptance", "portable-vps-release-preflight.py")
SIDECAR_PROBE = '''import json, os, urllib.error, urllib.request
root = "http://mom:8001"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path, key=None):
    headers = {} if key is None else {"Authorization": "Bearer " + key}
    try:
        with opener.open(urllib.request.Request(root + path, headers=headers), timeout=5) as response:
            return response.status, response.read(1048577)
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read(1048577)
key = os.environ.get("DISCORD_BOT_API_KEY", "")
try:
    health_status, health_raw = get("/api/health")
    version_status, version_raw = get("/api/version")
    missing, _ = get("/api/members")
    wrong, _ = get("/api/members", "invalid-release-acceptance-key")
    valid, members_raw = get("/api/members", key)
    health = json.loads(health_raw) if len(health_raw) <= 1048576 else {}
    version = json.loads(version_raw) if len(version_raw) <= 1048576 else {}
    members = json.loads(members_raw) if len(members_raw) <= 1048576 else None
    checks = {"health": health_status == 200 and health.get("status") == "healthy" and health.get("bot_connected") is True,
              "version": version_status == 200 and isinstance(version.get("version"), str),
              "missing_auth": missing == 403, "wrong_auth": wrong == 401,
              "valid_auth": bool(key) and valid == 200 and isinstance(members, list)}
    print(json.dumps({"checks": checks, "version": version.get("version") if checks["version"] else None}))
except Exception:
    print(json.dumps({"checks": {"health": False, "version": False, "missing_auth": False,
                                 "wrong_auth": False, "valid_auth": False}, "version": None}))
'''


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError("redirect refused")


def get_public(origin, path, cookie=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    headers = {"Accept": "application/json"}
    if cookie is not None:
        headers["Cookie"] = "session=" + cookie
    request = urllib.request.Request(origin + path, headers=headers, method="GET")
    try:
        response = opener.open(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        if response.status != 200:
            return response.status, None
        raw = response.read(65537)
    return 200, json.loads(raw) if len(raw) <= 65536 else None


def public_auth(origin, cookie_path):
    raw = GATE.read_private(cookie_path, 0, 4096).strip()
    if not re.fullmatch(rb"[A-Za-z0-9._-]{16,4096}", raw):
        raise ValueError("invalid session cookie")
    token = raw.decode("ascii")
    anonymous, _ = get_public(origin, "/api/auth/me")
    authenticated, identity = get_public(origin, "/api/auth/me", token)
    return (anonymous == 401 and authenticated == 200 and isinstance(identity, dict)
            and type(identity.get("member_id")) is int and identity["member_id"] > 0
            and isinstance(identity.get("name"), str) and bool(identity["name"])
            and isinstance(identity.get("role"), str))


def sidecar_auth(backend_id, expected_version):
    if not PAUSE.CONTAINER_ID.fullmatch(backend_id):
        return False
    try:
        result = subprocess.run(["docker", "exec", backend_id, "/app/.venv/bin/python",
            "-c", SIDECAR_PROBE], env=MIGRATE.ENVIRONMENT, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=35, check=False)
        if result.returncode or len(result.stdout) > 1024:
            return False
        value = json.loads(result.stdout, object_pairs_hook=REMOTE.no_duplicate_keys)
        checks = value["checks"]
        return (isinstance(checks, dict) and set(checks) == {
            "health", "version", "missing_auth", "wrong_auth", "valid_auth"}
            and all(item is True for item in checks.values())
            and value.get("version") == expected_version)
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        return False


def version_bound(versions, manifest):
    if not isinstance(versions, dict) or set(versions) != {
            "backend_version", "frontend_version", "bot_version", "git_sha"}:
        return False
    siege = manifest["siege"]["source_sha"]
    mom = manifest["mom"]["source_sha"]
    return (versions["git_sha"] == siege
        and all(isinstance(versions[key], str)
                and re.fullmatch(r"[^+]+\+[0-9]+\." + sha[:7], versions[key])
                for key, sha in (("backend_version", siege), ("frontend_version", siege),
                                ("bot_version", mom))))


def accepted_promotion(directory, manifest_digest, plan_digest, promotion_digest):
    if not PROOF.accepted_report(directory, "promote-report.json", "release-promote"):
        raise ValueError("promotion not accepted")
    marker = json.loads(GATE.read_private(directory / "promote-start.json", 0),
                        object_pairs_hook=REMOTE.no_duplicate_keys)
    activation = json.loads(GATE.read_private(directory / "promote-activation-start.json", 0),
                            object_pairs_hook=REMOTE.no_duplicate_keys)
    old_stack = GATE.read_private(directory / "installed-stack.env", 0)
    old_mom = GATE.read_private(directory / "installed-mom-image.json", 0, 16384)
    if not (isinstance(marker, dict) and isinstance(activation, dict)
            and marker.get("phase") == "promote-start"
            and marker.get("manifest_sha256") == manifest_digest
            and marker.get("plan_sha256") == plan_digest
            and marker.get("promotion_ready_sha256") == promotion_digest
            and marker.get("installed_stack_sha256") == PAUSE.digest(old_stack)
            and marker.get("installed_mom_approval_sha256") == PAUSE.digest(old_mom)
            and activation.get("phase") == "promote-activation-start"
            and activation.get("manifest_sha256") == manifest_digest):
        raise ValueError("promotion evidence mismatch")
    return old_stack, old_mom


def pilot_bound(cfg, manifest, plan, preflight):
    return (cfg["topology"] == "mom" and cfg["project"] == plan["project"]
        and cfg["images"] == {
            "backend": manifest["siege"]["api_image"],
            "frontend": manifest["siege"]["frontend_image"],
            "mom": manifest["mom"]["image"],
            "postgres": preflight["installed"]["postgres"]["image"],
            "proxy": preflight["installed"]["proxy"]["image"]}
        and version_bound(cfg["versions"], manifest))


def unchanged_private_inputs(project, preflight, expected_approval):
    if PREFLIGHT.config_hashes(project) != preflight["config_sha256"]:
        return False
    current = PREFLIGHT.runtime_hashes(project)
    return (current.get("mom-image.json") == PAUSE.digest(expected_approval)
            and all(current.get(name) == old for name, old in preflight["runtime_sha256"].items()
                    if name != "mom-image.json"))


def run_pilot(config_path, report_path):
    try:
        result = subprocess.run(["python3", str(HERE / "portable-pilot.py"),
            "--config", str(config_path), "--report", str(report_path),
            "--confirm-readonly-pilot"], env=MIGRATE.ENVIRONMENT,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=660, check=False)
        if result.returncode != 0:
            return False
        value = json.loads(GATE.read_private(report_path, 0),
                           object_pairs_hook=REMOTE.no_duplicate_keys)
        return (value.get("phase") == "pilot-observation" and value.get("result") == "PASS"
                and value.get("pilot_accepted") is False
                and isinstance(value.get("checks"), dict)
                and value["checks"] and all(item is True for item in value["checks"].values()))
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        return False


def inspect(data, directory, plan, manifest_digest, plan_digest, readiness_digest,
            promotion_digest, pilot_config, cookie_path):
    checks = {"root_execution": os.geteuid() == 0}
    manifest = MIGRATE.candidate(directory, manifest_digest)
    gate, digest = PROMOTE.accepted_gate(directory, manifest_digest, plan_digest, readiness_digest)
    checks["promotion_gate_bound"] = digest == promotion_digest
    old_stack, old_mom = accepted_promotion(directory, manifest_digest, plan_digest, promotion_digest)
    preflight, preflight_digest = PREFLIGHT.accepted_baseline(directory, manifest_digest)
    checks["installed_baseline_bound"] = (preflight_digest == plan["preflight_baseline_sha256"]
        and PAUSE.digest(old_stack) == preflight["stack_env_sha256"]
        and PAUSE.digest(old_mom) == preflight["runtime_sha256"]["mom-image.json"])
    if not all(checks.values()):
        return checks, None
    project = data["project_directory"]
    stack = project / "stack.env"
    approval = project / "runtime" / "mom-image.json"
    expected_stack = PROMOTE.image_config(old_stack, manifest)
    expected_approval = PROMOTE.approval_config(manifest)
    checks["candidate_configuration_persisted"] = (GATE.read_private(stack, 0) == expected_stack
        and GATE.read_private(approval, 0, 16384) == expected_approval)
    checks["reviewed_inputs_unchanged"] = unchanged_private_inputs(project, preflight,
                                                                    expected_approval)
    checks["foundation_unchanged"] = PROMOTE.unchanged_foundation(plan, preflight)
    installed = {service: PAUSE.identifiers(plan["project"], service)
                 for service in PROMOTE.SERVICES}
    checks["candidate_services_healthy"] = all(
        installed[service] is not None and PROMOTE.live_service(
            plan["project"], service, manifest[section][field]) == installed[service]
        for service, key in PROMOTE.SERVICES.items()
        for section, field in (PROMOTE.IMAGE_KEYS[key],))
    checks["only_approved_gateway"] = PAUSE.no_extra_writers(plan["project"])
    checks["database_revisions_exact"] = MIGRATE.revisions_match(
        gate["postgres_id"], gate["revisions"])
    checks["backup_timer_active"] = PAUSE.timer_active(data)
    if not all(checks.values()):
        return checks, None
    raw_config = GATE.read_private(pilot_config, 0)
    cfg = PILOT.settings(pilot_config)
    checks["observer_configuration_bound"] = pilot_bound(cfg, manifest, plan, preflight)
    if not checks["observer_configuration_bound"]:
        return checks, None
    checks["bounded_public_runtime_observation"] = run_pilot(
        pilot_config, directory / "accept-pilot-report.json")
    checks["observer_configuration_unchanged"] = GATE.read_private(pilot_config, 0) == raw_config
    if not all(checks.values()):
        return checks, None
    marker_status, marker = get_public(cfg["origin"], "/version.json")
    checks["frontend_marker_exact"] = (marker_status == 200 and isinstance(marker, dict)
        and marker == {"frontend_version": cfg["versions"]["frontend_version"],
                       "git_sha": manifest["siege"]["source_sha"]})
    checks["public_session_authentication"] = public_auth(cfg["origin"], cookie_path)
    checks["sidecar_authentication"] = sidecar_auth(
        installed["backend"], cfg["versions"]["bot_version"])
    checks["candidate_configuration_still_persisted"] = (GATE.read_private(stack, 0) == expected_stack
        and GATE.read_private(approval, 0, 16384) == expected_approval)
    checks["reviewed_inputs_still_unchanged"] = unchanged_private_inputs(project, preflight,
                                                                          expected_approval)
    checks["foundation_still_unchanged"] = PROMOTE.unchanged_foundation(plan, preflight)
    checks["candidate_services_still_healthy"] = all(
        PROMOTE.live_service(plan["project"], service, manifest[section][field]) == installed[service]
        for service, key in PROMOTE.SERVICES.items()
        for section, field in (PROMOTE.IMAGE_KEYS[key],))
    checks["database_revisions_still_exact"] = MIGRATE.revisions_match(
        gate["postgres_id"], gate["revisions"])
    checks["only_approved_gateway_still_running"] = PAUSE.no_extra_writers(plan["project"])
    checks["backup_timer_still_active"] = PAUSE.timer_active(data)
    checks["observer_configuration_still_unchanged"] = (
        GATE.read_private(pilot_config, 0) == raw_config)
    if not all(checks.values()):
        return checks, None
    baseline = {"schema": 1, "phase": "release-acceptance-baseline",
                "manifest_sha256": manifest_digest, "plan_sha256": plan_digest,
                "promotion_ready_sha256": promotion_digest,
                "siege_source_sha": manifest["siege"]["source_sha"],
                "mom_source_sha": manifest["mom"]["source_sha"],
                "images": {service: manifest[section][field]
                           for service, key in PROMOTE.SERVICES.items()
                           for section, field in (PROMOTE.IMAGE_KEYS[key],)},
                "container_ids": installed,
                "versions": cfg["versions"],
                "config_sha256": PREFLIGHT.config_hashes(project),
                "stack_env_sha256": PAUSE.digest(expected_stack),
                "mom_approval_sha256": PAUSE.digest(expected_approval),
                "pilot_config_sha256": PAUSE.digest(raw_config)}
    return checks, baseline


def run_locked(data, profile_digest, args):
    """Hold the project lock across acceptance checks and both reports."""
    directory = None
    checks = {"root_execution": True}
    try:
        directory = PAUSE.stage(data["project_directory"], args.stage_run_id, args.manifest_sha256)
        if any((directory / name).exists() for name in
               ("accept-report.json", "accept-baseline.json", "accept-pilot-report.json")):
            raise ValueError("acceptance already attempted")
        plan = PAUSE.load_plan(directory, args.plan_sha256, profile_digest, args.manifest_sha256)
        checks, baseline = inspect(data, directory, plan, args.manifest_sha256,
            args.plan_sha256, args.readiness_sha256, args.promotion_ready_sha256,
            args.pilot_config, args.session_cookie_file)
        if baseline is not None and all(checks.values()):
            PAUSE.write_private(directory / "accept-baseline.json", baseline)
            checks["baseline_recorded"] = True
    except Exception:
        checks["acceptance_inspection"] = False
    result = {"phase": "release-accept", "status": "PASS" if checks and all(checks.values()) else "STOP",
              "checks": checks}
    try:
        if directory is not None:
            PAUSE.write_private(directory / "accept-report.json", result)
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
    parser.add_argument("--pilot-config", required=True, type=Path)
    parser.add_argument("--session-cookie-file", required=True, type=Path)
    parser.add_argument("--confirm-readonly-acceptance", action="store_true")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    if (not args.report.is_absolute() or ".." in args.report.parts
            or not args.confirm_readonly_acceptance):
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
        PAUSE.write_private(args.report, {"phase": "release-accept", "status": "INCOMPLETE"})
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError,
            subprocess.TimeoutExpired):
        print("STOP profile_preflight_failed")
        return 2
    try:
        with PROMOTE.project_lock(data["project_directory"]):
            return run_locked(data, profile_digest, args)
    except (OSError, ValueError):
        try:
            PAUSE.replace_private(args.report, {"phase": "release-accept", "status": "STOP",
                                                "checks": {"project_lock": False}})
        except OSError:
            print("STOP report_write_failed")
            return 2
        print("STOP")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
