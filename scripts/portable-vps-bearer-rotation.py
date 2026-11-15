#!/usr/bin/env python3
"""Inspect and stage one portable bearer pair without changing live inputs.

Activation and recovery are intentionally separate phases. This module never
stops a service, writes a runtime file, or sends a credential over a network.
"""

import argparse
from datetime import datetime, timezone
import hmac
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import uuid


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("rotation_release_preflight", HERE / "portable-vps-release-preflight.py")
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)
GATE = PREFLIGHT.GATE
PAUSE = PREFLIGHT.PAUSE

PAIRS = {
    "sidecar": ("DISCORD_BOT_API_KEY", "MOM_BOT_SECRET_DISCORD_BOT_API_KEY"),
    "reverse-api": ("BOT_SERVICE_TOKEN", "MOM_BOT_SECRET_SIEGE_WEB_BOT_TOKEN"),
}
FILES = ("backend.env", "mom.env")
MAX_FILE = 65536
ENVIRONMENT = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/root", "LANG": "C",
               "DOCKER_HOST": "unix:///var/run/docker.sock"}
PROBE = """import json, sys, urllib.error, urllib.request
value = json.load(sys.stdin)
request = urllib.request.Request(value['url'], headers={'Authorization': 'Bearer ' + value['token'],
    'X-Acting-Discord-Id': 'invalid'})
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
try:
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=5) as response:
        code = response.status
except urllib.error.HTTPError as error:
    code = error.code
except Exception:
    sys.exit(2)
print(code)
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def private(path, owner, *, directory=False):
    return GATE.protected(path, owner, directory=directory)


def parse_env(raw, required):
    """Find unique literal assignments without normalizing unrelated bytes."""
    result = {}
    for line in raw.splitlines(keepends=True):
        body = line.rstrip(b"\r\n")
        if body.lstrip().startswith(b"#") or b"=" not in body:
            continue
        key, value = body.split(b"=", 1)
        try:
            name = key.decode("ascii")
        except UnicodeDecodeError:
            continue
        if name not in required:
            continue
        if name in result or not value or key != name.encode():
            raise ValueError("ambiguous or empty bearer assignment")
        if any(byte in value for byte in (b"\x00", b"\r", b"\n")):
            raise ValueError("invalid bearer assignment")
        result[name] = value
    if set(result) != set(required):
        raise ValueError("missing bearer assignment")
    return result


def replace_assignment(raw, key, value):
    """Replace exactly one assignment and preserve every other byte."""
    result = []
    matches = 0
    prefix = key.encode() + b"="
    for line in raw.splitlines(keepends=True):
        if line.startswith(prefix):
            ending = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
            result.append(prefix + value + ending)
            matches += 1
        else:
            result.append(line)
    if matches != 1:
        raise ValueError("ambiguous bearer assignment")
    return b"".join(result)


def read_runtime(project, owner):
    runtime = project / "runtime"
    if not private(project, owner, directory=True) or not private(runtime, owner, directory=True):
        raise ValueError("unsafe project or runtime directory")
    return {name: GATE.read_private(runtime / name, owner, MAX_FILE) for name in FILES}


def pair_values(files):
    backend = parse_env(files["backend.env"], {pair[0] for pair in PAIRS.values()})
    mom = parse_env(files["mom.env"], {pair[1] for pair in PAIRS.values()})
    return {name: (backend[keys[0]], mom[keys[1]]) for name, keys in PAIRS.items()}


def pairs_match(files):
    return all(hmac.compare_digest(left, right) for left, right in pair_values(files).values())


def project_snapshot(project, owner):
    """Capture protected inputs and exact healthy container identities."""
    files = read_runtime(project, owner)
    if not pairs_match(files):
        raise ValueError("bearer pair mismatch")
    stack = project / "stack.env"
    name = GATE.project_name(stack)
    if not name or not PAUSE.no_extra_writers(name):
        raise ValueError("invalid project topology")
    if not all(PREFLIGHT.reviewed_config(project / file, (PREFLIGHT.PREFLIGHT.PORTABLE / file).read_bytes())
               for file in ("compose.yml", "compose.mom.yml")):
        raise ValueError("unreviewed Compose configuration")
    if not PREFLIGHT.PREFLIGHT.deployment_bind_sources_current(project):
        raise ValueError("unexpected bind source")
    rendered = PREFLIGHT.PREFLIGHT.render("mom", stack, project, {})
    installed = PREFLIGHT.running_services(name, rendered)
    if installed is None or not PAUSE.no_extra_writers(name):
        raise ValueError("service topology or image mismatch")
    return files, {"project": name, "installed": installed}


def write_private(path, raw, owner):
    if not private(path.parent, owner, directory=True):
        raise ValueError("unsafe evidence directory")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def report(path, phase, checks, owner):
    result = {"phase": phase, "status": "PASS" if all(checks.values()) else "STOP",
              "checks": checks, "timestamp": now()}
    write_private(path, (json.dumps(result, sort_keys=True) + "\n").encode(), owner)
    return result


def inspect(project, report_path, owner=0):
    checks = {"project_private": private(project, owner, directory=True),
              "report_directory_private": private(report_path.parent, owner, directory=True)}
    if all(checks.values()):
        try:
            before, baseline = project_snapshot(project, owner)
            checks["bearer_pairs_equal"] = pairs_match(before)
            checks["healthy_exact_images"] = bool(baseline["installed"])
            after, again = project_snapshot(project, owner)
            checks["baseline_stable"] = before == after and baseline == again
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            checks["inspection"] = False
    return report(report_path, "bearer-inspect", checks, owner)


def stage(project, pair, evidence, owner=0):
    """Create rollback and candidate copies; refuse drift without touching runtime."""
    if pair not in PAIRS or not private(evidence, owner, directory=True):
        raise ValueError("invalid pair or evidence directory")
    before, baseline = project_snapshot(project, owner)
    run_id = str(uuid.uuid4())
    directory = evidence / run_id
    directory.mkdir(mode=0o700)
    if not private(directory, owner, directory=True):
        raise ValueError("unsafe stage directory")
    checks = {"initial_pair_equal": pairs_match(before), "baseline_stable": False,
              "staged_pair_equal": False, "unselected_pair_unchanged": False}
    write_private(directory / "report.json", (json.dumps({"phase": "bearer-stage", "status": "INCOMPLETE",
        "checks": checks, "timestamp": now()}, sort_keys=True) + "\n").encode(), owner)
    try:
        candidate = secrets.token_urlsafe(48).encode("ascii")
        selected = PAIRS[pair]
        proposed = {"backend.env": replace_assignment(before["backend.env"], selected[0], candidate),
                    "mom.env": replace_assignment(before["mom.env"], selected[1], candidate)}
        proposed_pairs = pair_values(proposed)
        original_pairs = pair_values(before)
        checks["staged_pair_equal"] = hmac.compare_digest(*proposed_pairs[pair])
        other = next(name for name in PAIRS if name != pair)
        checks["unselected_pair_unchanged"] = proposed_pairs[other] == original_pairs[other]
        current, current_baseline = project_snapshot(project, owner)
        checks["baseline_stable"] = current == before and current_baseline == baseline
        if not all(checks.values()):
            raise ValueError("staging validation failed")
        for name in FILES:
            write_private(directory / f"rollback-{name}", before[name], owner)
            write_private(directory / f"candidate-{name}", proposed[name], owner)
        write_private(directory / "baseline.json", (json.dumps(baseline, sort_keys=True) + "\n").encode(), owner)
        if read_runtime(project, owner) != before:
            raise ValueError("runtime drift after staging")
        final = {"phase": "bearer-stage", "status": "PASS", "checks": checks,
                 "timestamp": now(), "run_id": run_id, "pair": pair}
        # The initial report remains as an interruption marker; final evidence is separate.
        write_private(directory / "accepted.json", (json.dumps(final, sort_keys=True) + "\n").encode(), owner)
        return final
    except Exception:
        write_private(directory / "stopped.json", (json.dumps({"phase": "bearer-stage", "status": "STOP",
            "checks": checks, "timestamp": now()}, sort_keys=True) + "\n").encode(), owner)
        raise


def staged(project, directory, owner):
    """Load an accepted stage without reading any value into a report."""
    if not private(directory, owner, directory=True):
        raise ValueError("unsafe stage directory")
    accepted = json.loads(GATE.read_private(directory / "accepted.json", owner),
                          object_pairs_hook=PREFLIGHT.REMOTE.no_duplicate_keys)
    baseline = json.loads(GATE.read_private(directory / "baseline.json", owner),
                          object_pairs_hook=PREFLIGHT.REMOTE.no_duplicate_keys)
    pair = accepted.get("pair") if isinstance(accepted, dict) else None
    if not (isinstance(accepted, dict)
            and accepted.get("phase") == "bearer-stage" and accepted.get("status") == "PASS"
            and accepted.get("run_id") == directory.name and pair in PAIRS
            and isinstance(baseline, dict) and isinstance(baseline.get("project"), str)
            and isinstance(baseline.get("installed"), dict)):
        raise ValueError("invalid accepted stage")
    rollback = {name: GATE.read_private(directory / f"rollback-{name}", owner, MAX_FILE)
                for name in FILES}
    candidate = {name: GATE.read_private(directory / f"candidate-{name}", owner, MAX_FILE)
                 for name in FILES}
    old_pairs, new_pairs = pair_values(rollback), pair_values(candidate)
    other = next(name for name in PAIRS if name != pair)
    if not (pairs_match(rollback) and pairs_match(candidate)
            and old_pairs[other] == new_pairs[other]
            and old_pairs[pair][0] != new_pairs[pair][0]
            and replace_assignment(rollback["backend.env"], PAIRS[pair][0], new_pairs[pair][0])
                == candidate["backend.env"]
            and replace_assignment(rollback["mom.env"], PAIRS[pair][1], new_pairs[pair][1])
                == candidate["mom.env"]):
        raise ValueError("invalid staged pair")
    return pair, rollback, candidate, baseline


def stopped(project_name, baseline):
    for service in ("backend", "mom"):
        expected = baseline["installed"][service]["id"]
        current = PAUSE.container(project_name, service)
        if current is None or current["id"] != expected:
            return False
        if current["running"] is not False or current["status"] != "exited":
            return False
    return True


def isolated(project_name):
    """Both application writers must be stopped before restoring files."""
    return all((state := PAUSE.container(project_name, service)) is not None
               and state["running"] is False and state["status"] == "exited"
               for service in ("backend", "mom"))


def stop_pair(project_name):
    """Stop only selected project writers, without broad Compose operations."""
    success = True
    for service in ("mom", "backend"):
        identifier = PAUSE.identifiers(project_name, service)
        if identifier is None:
            success = False
            continue
        state = PAUSE.container(project_name, service)
        if state is None:
            success = False
            continue
        if state["running"] is True and PAUSE.command(
                ["docker", "stop", "--time", "30", identifier], timeout=50) != identifier:
            success = False
    return success


def replace_runtime(project, files, owner):
    """Replace only the two protected inputs after both writers are stopped."""
    runtime = project / "runtime"
    if not private(runtime, owner, directory=True):
        raise ValueError("unsafe runtime directory")
    for name in FILES:
        path = runtime / name
        if not private(path, owner):
            raise ValueError("unsafe runtime file")
        fd, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=runtime)
        try:
            with os.fdopen(fd, "wb") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(files[name])
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    descriptor = os.open(runtime, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def compose_up(project):
    command = ["docker", "compose", "--env-file", str(project / "stack.env"),
               "--project-directory", str(project), "-f", str(project / "compose.yml"),
               "-f", str(project / "compose.mom.yml"), "up", "--no-deps",
               "--no-build", "--pull", "never", "--force-recreate", "--wait",
               "--wait-timeout", "240", "backend", "mom"]
    try:
        result = subprocess.run(command, env=ENVIRONMENT, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def probe(container, url, token):
    """Send a bearer via stdin; emit only the HTTP status, never response data."""
    payload = json.dumps({"url": url, "token": token.decode("ascii")}).encode()
    try:
        result = subprocess.run(["docker", "exec", "-i", container,
                                 "/app/.venv/bin/python", "-c", PROBE],
                                input=payload, capture_output=True, env=ENVIRONMENT,
                                timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None
    if result.returncode or result.stdout not in (b"200\n", b"400\n", b"401\n"):
        return None
    return int(result.stdout)


def verified(project, pair, active, stale, baseline, owner):
    files, current = project_snapshot(project, owner)
    if files != active or current["project"] != baseline["project"]:
        return False
    old = baseline["installed"]
    new = current["installed"]
    if (set(new) != set(old) or any(new[name]["image"] != old[name]["image"] for name in old)
            or any(new[name]["id"] != old[name]["id"] for name in old if name not in ("backend", "mom"))
            or any(new[name]["id"] == old[name]["id"] for name in ("backend", "mom"))):
        return False
    active_pairs, stale_pairs = pair_values(active), pair_values(stale)
    backend = new["backend"]["id"]
    mom = new["mom"]["id"]
    for direction in PAIRS:
        if direction == "sidecar":
            container, url, expected = backend, "http://mom:8001/api/members", 200
        else:
            container, url, expected = mom, "http://backend:8000/api/members/me/preferences", 400
        if probe(container, url, active_pairs[direction][0]) != expected:
            return False
        if direction == pair and probe(container, url, stale_pairs[direction][0]) != 401:
            return False
    return True


def activate(project, directory, owner=0):
    """Lock, stop both writers, replace both inputs, recreate and verify them."""
    pair, rollback, candidate, baseline = staged(project, directory, owner)
    if (directory / "activation-incomplete.json").exists():
        raise ValueError("activation already attempted")
    result = {"phase": "bearer-activate", "status": "STOP", "run_id": directory.name,
              "pair": pair, "checks": {"baseline_current": False, "writers_stopped": False,
                                      "candidate_installed": False, "paired_verification": False,
                                      "path_isolated_after_failure": False}}
    with PAUSE.project_lock(project):
        current, actual_baseline = project_snapshot(project, owner)
        result["checks"]["baseline_current"] = current == rollback and actual_baseline == baseline
        if not result["checks"]["baseline_current"]:
            raise ValueError("activation baseline drift")
        write_private(directory / "activation-incomplete.json",
                      (json.dumps(result, sort_keys=True) + "\n").encode(), owner)
        try:
            if not stop_pair(baseline["project"]) or not stopped(baseline["project"], baseline):
                raise ValueError("writer stop failed")
            result["checks"]["writers_stopped"] = True
            replace_runtime(project, candidate, owner)
            result["checks"]["candidate_installed"] = read_runtime(project, owner) == candidate
            if not result["checks"]["candidate_installed"] or not compose_up(project):
                raise ValueError("candidate activation failed")
            result["checks"]["paired_verification"] = verified(
                project, pair, candidate, rollback, baseline, owner)
            if not result["checks"]["paired_verification"]:
                raise ValueError("paired verification failed")
            result["status"] = "PASS"
        except Exception:
            # Recovery has its own confirmation and phase. Leave both peers stopped.
            result["checks"]["path_isolated_after_failure"] = stop_pair(baseline["project"])
        write_private(directory / "activation-result.json",
                      (json.dumps(result, sort_keys=True) + "\n").encode(), owner)
    return result


def recover_routine(project, directory, old_bearer_safe=False, owner=0):
    """Separately confirmed paired rollback when the previous bearer remains safe."""
    if not old_bearer_safe:
        raise ValueError("old bearer safety not confirmed")
    pair, rollback, candidate, baseline = staged(project, directory, owner)
    marker = json.loads(GATE.read_private(directory / "activation-incomplete.json", owner),
                        object_pairs_hook=PREFLIGHT.REMOTE.no_duplicate_keys)
    if not isinstance(marker, dict) or marker.get("phase") != "bearer-activate":
        raise ValueError("activation marker missing")
    previous = directory / "activation-result.json"
    if previous.exists():
        outcome = json.loads(GATE.read_private(previous, owner),
                             object_pairs_hook=PREFLIGHT.REMOTE.no_duplicate_keys)
        if not isinstance(outcome, dict) or outcome.get("status") != "STOP":
            raise ValueError("successful activation cannot be rolled back here")
    if (directory / "recovery-incomplete.json").exists():
        raise ValueError("recovery already attempted")
    result = {"phase": "bearer-recover", "status": "STOP", "run_id": directory.name,
              "pair": pair, "checks": {"expected_project": False, "known_files": False,
                                      "writers_isolated": False, "rollback_installed": False,
                                      "paired_verification": False}}
    with PAUSE.project_lock(project):
        result["checks"]["expected_project"] = (
            GATE.project_name(project / "stack.env") == baseline["project"]
            and PAUSE.no_extra_writers(baseline["project"]))
        if not result["checks"]["expected_project"]:
            raise ValueError("project drift")
        current = read_runtime(project, owner)
        result["checks"]["known_files"] = all(
            current[name] in (rollback[name], candidate[name]) for name in FILES)
        if not result["checks"]["known_files"]:
            raise ValueError("runtime drift")
        write_private(directory / "recovery-incomplete.json",
                      (json.dumps(result, sort_keys=True) + "\n").encode(), owner)
        try:
            result["checks"]["writers_isolated"] = (
                stop_pair(baseline["project"]) and isolated(baseline["project"]))
            if not result["checks"]["writers_isolated"]:
                raise ValueError("writer isolation failed")
            replace_runtime(project, rollback, owner)
            result["checks"]["rollback_installed"] = read_runtime(project, owner) == rollback
            if not result["checks"]["rollback_installed"] or not compose_up(project):
                raise ValueError("routine rollback failed")
            result["checks"]["paired_verification"] = verified(
                project, pair, rollback, candidate, baseline, owner)
            if not result["checks"]["paired_verification"]:
                raise ValueError("routine rollback verification failed")
            result["status"] = "PASS"
        except Exception:
            stop_pair(baseline["project"])
        write_private(directory / "recovery-result.json",
                      (json.dumps(result, sort_keys=True) + "\n").encode(), owner)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("inspect", "stage", "activate", "recover-routine"))
    parser.add_argument("--project-directory", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--pair", choices=sorted(PAIRS))
    parser.add_argument("--evidence-directory", type=Path)
    parser.add_argument("--confirm-stage", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--confirm-activate")
    parser.add_argument("--confirm-recover-routine")
    parser.add_argument("--old-bearer-safe", action="store_true")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("STOP root_required")
        return 1
    try:
        if args.phase == "inspect":
            if (args.report is None or args.pair or args.evidence_directory or args.confirm_stage
                    or args.run_id or args.confirm_activate or args.confirm_recover_routine
                    or args.old_bearer_safe):
                raise ValueError("invalid inspect arguments")
            result = inspect(args.project_directory, args.report)
        elif args.phase == "stage":
            if (not args.pair or not args.evidence_directory or not args.confirm_stage or args.report
                    or args.run_id or args.confirm_activate or args.confirm_recover_routine
                    or args.old_bearer_safe):
                raise ValueError("invalid stage arguments")
            result = stage(args.project_directory, args.pair, args.evidence_directory)
        else:
            if (args.pair or args.report or args.confirm_stage or not args.evidence_directory
                    or not args.run_id or not str(uuid.UUID(args.run_id)) == args.run_id):
                raise ValueError("invalid operation selector")
            directory = args.evidence_directory / args.run_id
            if args.phase == "activate":
                if (args.confirm_activate != args.run_id or args.confirm_recover_routine
                        or args.old_bearer_safe):
                    raise ValueError("activation not confirmed")
                result = activate(args.project_directory, directory)
            else:
                if (args.confirm_recover_routine != args.run_id or args.confirm_activate
                        or not args.old_bearer_safe):
                    raise ValueError("routine recovery not confirmed")
                result = recover_routine(args.project_directory, directory,
                                         old_bearer_safe=args.old_bearer_safe)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        print("STOP")
        return 1
    print(result["status"], result.get("run_id", ""))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
