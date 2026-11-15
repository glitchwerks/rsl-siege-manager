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
import re
import secrets
import subprocess
import tempfile
import uuid


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("rotation_release_preflight", HERE / "portable-vps-release-preflight.py")
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)
SPEC = importlib.util.spec_from_file_location("rotation_public_signal", HERE / "portable-vps-public-signal.py")
PUBLIC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUBLIC)
SPEC = importlib.util.spec_from_file_location("rotation_maintenance_control", HERE / "portable-vps-maintenance-control.py")
MAINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MAINT)
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
        canonical = key.strip().removeprefix(b"export ").strip()
        try:
            name = canonical.decode("ascii")
        except UnicodeDecodeError:
            continue
        if name not in required:
            continue
        if name in result or not value or key != name.encode():
            raise ValueError("ambiguous or empty bearer assignment")
        # Compose can strip quotes, interpolate $, or interpret inline comments.
        # Only literal bearer-token bytes may be probed from this raw file.
        if not re.fullmatch(rb"[A-Za-z0-9._~+/-]+={0,2}", value):
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


def reviewed_checkout():
    root = PREFLIGHT.PREFLIGHT.ROOT
    reviewed_git = ["git", "-c", f"safe.directory={root}", "-C", str(root)]
    head = PAUSE.command([*reviewed_git, "rev-parse", "HEAD"])
    changed = PAUSE.command([*reviewed_git, "status", "--porcelain"])
    if (head is None or not PREFLIGHT.REMOTE.SOURCE_SHA.fullmatch(head)
            or changed != ""):
        raise ValueError("unreviewed checkout")
    return head


def single_mom_container(project_name):
    """Require one running Compose Mom on this host and in the selected project."""
    output = PAUSE.command(["docker", "ps", "--no-trunc",
                            "--filter", "label=com.docker.compose.service=mom",
                            "--format", "{{.ID}}"])
    if output is None:
        return False
    identities = output.splitlines()
    return (len(identities) == 1 and identities[0] == PAUSE.identifiers(project_name, "mom"))


def project_snapshot(project, owner):
    """Capture protected inputs and exact healthy container identities."""
    files = read_runtime(project, owner)
    if not pairs_match(files):
        raise ValueError("bearer pair mismatch")
    stack = project / "stack.env"
    name = GATE.project_name(stack)
    if not name or not PAUSE.no_extra_writers(name) or not single_mom_container(name):
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
    return files, {"project": name, "installed": installed,
                   "source_sha": reviewed_checkout()}


def configuration_current(project, baseline):
    """Check reviewed Compose inputs and exact image references before recovery."""
    if not all(PREFLIGHT.reviewed_config(
            project / file, (PREFLIGHT.PREFLIGHT.PORTABLE / file).read_bytes())
            for file in ("compose.yml", "compose.mom.yml")):
        return False
    if not PREFLIGHT.PREFLIGHT.deployment_bind_sources_current(project):
        return False
    try:
        if reviewed_checkout() != baseline["source_sha"]:
            return False
        rendered = PREFLIGHT.PREFLIGHT.render("mom", project / "stack.env", project, {})
        installed = baseline["installed"]
        if not (GATE.project_name(project / "stack.env") == baseline["project"]
                and all(rendered["services"][name]["image"] == item["image"]
                        for name, item in installed.items())):
            return False
        for service in ("frontend", "postgres", "proxy"):
            expected = installed[service]
            if PAUSE.identifiers(baseline["project"], service) != expected["id"]:
                return False
            image = PAUSE.command(["docker", "inspect", "--format", "{{json .Config.Image}}",
                                   expected["id"]])
            state = PAUSE.container(baseline["project"], service)
            if (image is None or json.loads(image) != expected["image"] or state is None
                    or state["running"] is not True
                    or (service != "proxy" and state["healthy"] is not True)):
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def stack_current(project, directory, owner):
    """Compare the protected stage copy without emitting its contents or hash."""
    try:
        expected = GATE.read_private(directory / "baseline-stack.env", owner, MAX_FILE)
        actual = GATE.read_private(project / "stack.env", owner, MAX_FILE)
        return hmac.compare_digest(expected, actual)
    except (OSError, ValueError):
        return False


def public_healthy(project, config_path, owner):
    """Check reviewed public hostname, TLS, health and exact version identity."""
    if (config_path is None or not private(config_path.parent, owner, directory=True)
            or not private(config_path, owner)):
        return False
    try:
        host = parse_env(GATE.read_private(project / "stack.env", owner), {"PUBLIC_HOST"})[
            "PUBLIC_HOST"].decode("ascii")
        settings = PUBLIC.config(config_path)
        if settings["host"] != host:
            return False
        checks = PUBLIC.inspect(settings)
        return set(checks) == set(PUBLIC.CHECKS) and all(checks.values())
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, PUBLIC.DeadlineExpired):
        return False


def require_pause(project, *, drain=False):
    """Refuse bearer changes unless both application writers are paused."""
    history = MAINT.history(project)
    if (MAINT.state(project) != "paused" or history is None
            or history["pause_started_at"] is None):
        raise ValueError("maintenance pause missing")
    if drain and not MAINT.drain(project):
        raise ValueError("maintenance drain incomplete")


def write_private(path, raw, owner):
    if not private(path.parent, owner, directory=True):
        raise ValueError("unsafe evidence directory")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def write_result(path, raw, owner):
    """Publish complete private JSON without exposing a partial result path."""
    if not private(path.parent, owner, directory=True):
        raise ValueError("unsafe evidence directory")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


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
    stack_before = GATE.read_private(project / "stack.env", owner, MAX_FILE)
    before, baseline = project_snapshot(project, owner)
    run_id = str(uuid.uuid4())
    directory = evidence / run_id
    directory.mkdir(mode=0o700)
    if not private(directory, owner, directory=True):
        raise ValueError("unsafe stage directory")
    checks = {"initial_pair_equal": pairs_match(before), "baseline_stable": False,
              "stack_baseline_stable": False,
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
        checks["staged_pair_equal"] = (hmac.compare_digest(*proposed_pairs[pair])
                                        and not hmac.compare_digest(proposed_pairs[pair][0],
                                                                    original_pairs[pair][0]))
        other = next(name for name in PAIRS if name != pair)
        checks["unselected_pair_unchanged"] = proposed_pairs[other] == original_pairs[other]
        current, current_baseline = project_snapshot(project, owner)
        checks["baseline_stable"] = current == before and current_baseline == baseline
        checks["stack_baseline_stable"] = hmac.compare_digest(
            GATE.read_private(project / "stack.env", owner, MAX_FILE), stack_before)
        if not all(checks.values()):
            raise ValueError("staging validation failed")
        for name in FILES:
            write_private(directory / f"rollback-{name}", before[name], owner)
            write_private(directory / f"candidate-{name}", proposed[name], owner)
        write_private(directory / "baseline-stack.env", stack_before, owner)
        write_private(directory / "baseline.json", (json.dumps(baseline, sort_keys=True) + "\n").encode(), owner)
        if read_runtime(project, owner) != before:
            raise ValueError("runtime drift after staging")
        final = {"phase": "bearer-stage", "status": "PASS", "checks": checks,
                 "timestamp": now(), "run_id": run_id, "pair": pair}
        # The initial report remains as an interruption marker; final evidence is separate.
        write_result(directory / "accepted.json", (json.dumps(final, sort_keys=True) + "\n").encode(), owner)
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
    GATE.read_private(directory / "baseline-stack.env", owner, MAX_FILE)
    pair = accepted.get("pair") if isinstance(accepted, dict) else None
    if not (isinstance(accepted, dict)
            and accepted.get("phase") == "bearer-stage" and accepted.get("status") == "PASS"
            and accepted.get("run_id") == directory.name and pair in PAIRS
            and isinstance(baseline, dict) and isinstance(baseline.get("project"), str)
            and isinstance(baseline.get("installed"), dict)
            and isinstance(baseline.get("source_sha"), str)
            and PREFLIGHT.REMOTE.SOURCE_SHA.fullmatch(baseline["source_sha"])):
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
               and state["running"] is False and state["status"] in {"exited", "created"}
               for service in ("backend", "mom"))


def stop_pair(project_name, expected=None):
    """Stop only selected project writers, without broad Compose operations."""
    success = True
    for service in ("mom", "backend"):
        identifier = PAUSE.identifiers(project_name, service)
        if identifier is None:
            success = False
            continue
        if expected is not None and identifier != expected[service]["id"]:
            success = False
            continue
        state = PAUSE.container(project_name, service)
        if state is None:
            success = False
            continue
        if state["running"] is True and PAUSE.command(
                ["docker", "stop", "--time", "30", identifier], timeout=50) is None:
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


def verified(project, pair, active, stale, baseline, owner, details=None):
    """Check exact images and both HTTP directions, recording fixed booleans only."""
    if details is None:
        details = {}
    try:
        files, current = project_snapshot(project, owner)
    except (OSError, ValueError, TypeError, KeyError, AssertionError):
        details["snapshot"] = False
        return False
    details["snapshot"] = files == active and current["project"] == baseline["project"]
    if not details["snapshot"]:
        return False
    old = baseline["installed"]
    new = current["installed"]
    details["exact_images_and_targeted_recreation"] = not (
        set(new) != set(old) or any(new[name]["image"] != old[name]["image"] for name in old)
        or any(new[name]["id"] != old[name]["id"] for name in old if name not in ("backend", "mom"))
        or any(new[name]["id"] == old[name]["id"] for name in ("backend", "mom")))
    if not details["exact_images_and_targeted_recreation"]:
        return False
    active_pairs, stale_pairs = pair_values(active), pair_values(stale)
    backend = new["backend"]["id"]
    mom = new["mom"]["id"]
    for direction in PAIRS:
        if direction == "sidecar":
            container, url, expected = backend, "http://mom:8001/api/members", 200
        else:
            container, url, expected = mom, "http://backend:8000/api/members/me/preferences", 400
        details[f"{direction}_accepted"] = probe(container, url, active_pairs[direction][0]) == expected
        if not details[f"{direction}_accepted"]:
            return False
        if direction == pair:
            details[f"{direction}_stale_rejected"] = probe(
                container, url, stale_pairs[direction][0]) == 401
            if not details[f"{direction}_stale_rejected"]:
                return False
    return True


def activate(project, directory, public_config=None, owner=0):
    """Lock, stop both writers, replace both inputs, recreate and verify them."""
    pair, rollback, candidate, baseline = staged(project, directory, owner)
    if (directory / "activation-incomplete.json").exists():
        raise ValueError("activation already attempted")
    result = {"phase": "bearer-activate", "status": "STOP", "run_id": directory.name,
              "pair": pair, "checks": {"baseline_current": False, "writers_stopped": False,
                                      "candidate_installed": False, "paired_verification": False,
                                      "public_health": False,
                                      "path_isolated_after_failure": False}}
    with PAUSE.project_lock(project):
        current, actual_baseline = project_snapshot(project, owner)
        result["checks"]["baseline_current"] = (
            current == rollback and actual_baseline == baseline
            and stack_current(project, directory, owner))
        if not result["checks"]["baseline_current"]:
            raise ValueError("activation baseline drift")
        require_pause(project, drain=True)
        write_result(directory / "activation-incomplete.json",
                      (json.dumps(result, sort_keys=True) + "\n").encode(), owner)
        try:
            if not stop_pair(baseline["project"], baseline["installed"]) or not stopped(
                    baseline["project"], baseline):
                raise ValueError("writer stop failed")
            result["checks"]["writers_stopped"] = True
            replace_runtime(project, candidate, owner)
            result["checks"]["candidate_installed"] = read_runtime(project, owner) == candidate
            if not result["checks"]["candidate_installed"] or not compose_up(project):
                raise ValueError("candidate activation failed")
            detail = {}
            result["checks"]["paired_verification"] = verified(
                project, pair, candidate, rollback, baseline, owner, detail)
            result["verification"] = detail
            if not result["checks"]["paired_verification"]:
                raise ValueError("paired verification failed")
            result["checks"]["public_health"] = public_healthy(project, public_config, owner)
            if not result["checks"]["public_health"]:
                raise ValueError("public health failed")
            result["status"] = "PASS"
        except Exception:
            # Recovery has its own confirmation and phase. Leave both peers stopped.
            result["checks"]["path_isolated_after_failure"] = (
                stop_pair(baseline["project"]) and isolated(baseline["project"]))
        write_result(directory / "activation-result.json",
                      (json.dumps(result, sort_keys=True) + "\n").encode(), owner)
    return result


def fresh_path_selected(directory):
    """Treat even an interrupted fresh stage as an irreversible path choice."""
    names = [directory / f"fresh-{kind}-{name}"
             for kind in ("source", "candidate") for name in FILES]
    names.extend(directory / name for name in (
        "fresh-accepted.json", "fresh-activation-incomplete.json",
        "fresh-activation-result.json"))
    return any(path.exists() or path.is_symlink() for path in names) or any(
        directory.glob("fresh-interrupted-*"))


def recover_routine(project, directory, old_bearer_safe=False, public_config=None, owner=0):
    """Separately confirmed paired rollback when the previous bearer remains safe."""
    if not old_bearer_safe:
        raise ValueError("old bearer safety not confirmed")
    if fresh_path_selected(directory):
        raise ValueError("fresh recovery path already selected")
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
    result = {"phase": "bearer-recover", "status": "STOP", "run_id": directory.name,
              "pair": pair, "checks": {"expected_project": False, "known_files": False,
                                      "writers_isolated": False, "rollback_installed": False,
                                      "paired_verification": False,
                                      "public_health": False,
                                      "path_isolated_after_failure": False}}
    with PAUSE.project_lock(project):
        if fresh_path_selected(directory):
            raise ValueError("fresh recovery path already selected")
        prior_results = [directory / "recovery-result.json"]
        prior_results.extend((directory / "routine-attempts").glob("*/recovery-result.json"))
        for path in prior_results:
            if path.exists():
                prior = json.loads(GATE.read_private(path, owner),
                                   object_pairs_hook=PREFLIGHT.REMOTE.no_duplicate_keys)
                if not isinstance(prior, dict) or prior.get("status") not in {"PASS", "STOP"}:
                    raise ValueError("invalid recovery evidence")
                if prior["status"] == "PASS":
                    raise ValueError("recovery already succeeded")
        require_pause(project)
        result["checks"]["expected_project"] = (
            configuration_current(project, baseline) and stack_current(project, directory, owner)
            and PAUSE.no_extra_writers(baseline["project"]))
        if not result["checks"]["expected_project"]:
            raise ValueError("project drift")
        current = read_runtime(project, owner)
        result["checks"]["known_files"] = all(
            current[name] in (rollback[name], candidate[name]) for name in FILES)
        if not result["checks"]["known_files"]:
            raise ValueError("runtime drift")
        attempt_directory = directory
        if (directory / "recovery-incomplete.json").exists():
            attempts = directory / "routine-attempts"
            attempts.mkdir(mode=0o700, exist_ok=True)
            if not private(attempts, owner, directory=True):
                raise ValueError("unsafe recovery attempts directory")
            result["attempt_id"] = str(uuid.uuid4())
            attempt_directory = attempts / result["attempt_id"]
            attempt_directory.mkdir(mode=0o700)
        write_result(attempt_directory / "recovery-incomplete.json",
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
            detail = {}
            result["checks"]["paired_verification"] = verified(
                project, pair, rollback, candidate, baseline, owner, detail)
            result["verification"] = detail
            if not result["checks"]["paired_verification"]:
                raise ValueError("routine rollback verification failed")
            result["checks"]["public_health"] = public_healthy(project, public_config, owner)
            if not result["checks"]["public_health"]:
                raise ValueError("public health failed")
            result["status"] = "PASS"
        except Exception:
            result["checks"]["path_isolated_after_failure"] = (
                stop_pair(baseline["project"]) and isolated(baseline["project"]))
        write_result(attempt_directory / "recovery-result.json",
                      (json.dumps(result, sort_keys=True) + "\n").encode(), owner)
    return result


def failed_activation(directory, owner):
    marker = json.loads(GATE.read_private(directory / "activation-incomplete.json", owner),
                        object_pairs_hook=PREFLIGHT.REMOTE.no_duplicate_keys)
    if not isinstance(marker, dict) or marker.get("phase") != "bearer-activate":
        raise ValueError("activation marker missing")
    outcome_path = directory / "activation-result.json"
    if outcome_path.exists():
        outcome = json.loads(GATE.read_private(outcome_path, owner),
                             object_pairs_hook=PREFLIGHT.REMOTE.no_duplicate_keys)
        if not isinstance(outcome, dict) or outcome.get("status") != "STOP":
            raise ValueError("activation did not fail")


def prior_fresh_candidates(directory, owner, *, include_current=False):
    """Read protected earlier fresh candidates for a safely isolated retry."""
    result = {name: set() for name in FILES}
    for name in FILES:
        paths = list(directory.glob(f"fresh-interrupted-*/fresh-candidate-{name}"))
        if include_current:
            paths.append(directory / f"fresh-candidate-{name}")
        for path in paths:
            if path.exists():
                result[name].add(GATE.read_private(path, owner, MAX_FILE))
    return result


def stage_fresh(project, directory, owner=0):
    """Stage a different bearer after exposure, while both peers stay stopped."""
    pair, rollback, candidate, baseline = staged(project, directory, owner)
    failed_activation(directory, owner)
    if (directory / "recovery-incomplete.json").exists():
        raise ValueError("recovery path already selected")
    result = {"phase": "bearer-stage-fresh", "status": "STOP", "run_id": directory.name,
              "pair": pair, "checks": {"expected_project": False, "known_files": False,
                                      "writers_isolated": False, "fresh_pair_distinct": False}}
    with PAUSE.project_lock(project):
        if (directory / "recovery-incomplete.json").exists():
            raise ValueError("routine recovery path already selected")
        accepted = directory / "fresh-accepted.json"
        attempted = directory / "fresh-activation-incomplete.json"
        if accepted.exists() and not attempted.exists():
            raise ValueError("fresh stage already accepted")
        outcome_path = directory / "fresh-activation-result.json"
        if outcome_path.exists():
            outcome = json.loads(GATE.read_private(outcome_path, owner),
                                 object_pairs_hook=PREFLIGHT.REMOTE.no_duplicate_keys)
            if not isinstance(outcome, dict) or outcome.get("status") != "STOP":
                raise ValueError("fresh activation already succeeded")
        require_pause(project)
        result["checks"]["expected_project"] = (
            configuration_current(project, baseline) and stack_current(project, directory, owner)
            and PAUSE.no_extra_writers(baseline["project"]))
        if not result["checks"]["expected_project"]:
            raise ValueError("project drift")
        source = read_runtime(project, owner)
        previous = prior_fresh_candidates(directory, owner, include_current=True)
        result["checks"]["known_files"] = all(
            source[name] in {rollback[name], candidate[name], *previous[name]}
            for name in FILES)
        result["checks"]["writers_isolated"] = isolated(baseline["project"])
        if not result["checks"]["known_files"] or not result["checks"]["writers_isolated"]:
            raise ValueError("exposed path not isolated")
        incomplete = [directory / name for name in (
            *(f"fresh-{kind}-{file}" for kind in ("source", "candidate") for file in FILES),
            "fresh-accepted.json", "fresh-activation-incomplete.json",
            "fresh-activation-result.json") if (directory / name).exists()]
        if incomplete:
            archive = directory / f"fresh-interrupted-{uuid.uuid4()}"
            archive.mkdir(mode=0o700)
            if not private(archive, owner, directory=True):
                raise ValueError("unsafe interrupted stage archive")
            for path in incomplete:
                if not private(path, owner):
                    raise ValueError("unsafe interrupted stage file")
                os.replace(path, archive / path.name)
        fresh_value = secrets.token_urlsafe(48).encode("ascii")
        keys = PAIRS[pair]
        fresh = {"backend.env": replace_assignment(source["backend.env"], keys[0], fresh_value),
                 "mom.env": replace_assignment(source["mom.env"], keys[1], fresh_value)}
        other = next(name for name in PAIRS if name != pair)
        source_pairs = pair_values(source)
        fresh_pairs = pair_values(fresh)
        old_values = {pair_values(rollback)[pair][0], pair_values(candidate)[pair][0]}
        for name in FILES:
            key = PAIRS[pair][FILES.index(name)]
            old_values.update(parse_env(raw, {key})[key] for raw in previous[name])
        result["checks"]["fresh_pair_distinct"] = (
            pairs_match(fresh) and hmac.compare_digest(*source_pairs[other])
            and fresh_pairs[other] == source_pairs[other]
            and fresh_value not in old_values)
        if not result["checks"]["fresh_pair_distinct"] or read_runtime(project, owner) != source:
            raise ValueError("fresh stage drift")
        for name in FILES:
            write_private(directory / f"fresh-source-{name}", source[name], owner)
            write_private(directory / f"fresh-candidate-{name}", fresh[name], owner)
        result["status"] = "PASS"
        write_result(directory / "fresh-accepted.json",
                      (json.dumps(result, sort_keys=True) + "\n").encode(), owner)
    return result


def activate_fresh(project, directory, public_config=None, owner=0):
    """Use only a fresh pair after exposure; failure leaves both peers stopped."""
    pair, rollback, candidate, baseline = staged(project, directory, owner)
    failed_activation(directory, owner)
    accepted = json.loads(GATE.read_private(directory / "fresh-accepted.json", owner),
                          object_pairs_hook=PREFLIGHT.REMOTE.no_duplicate_keys)
    if (not isinstance(accepted, dict) or accepted.get("phase") != "bearer-stage-fresh"
            or accepted.get("status") != "PASS" or accepted.get("run_id") != directory.name
            or accepted.get("pair") != pair):
        raise ValueError("fresh stage not accepted")
    if (directory / "fresh-activation-incomplete.json").exists():
        raise ValueError("fresh activation already attempted")
    source = {name: GATE.read_private(directory / f"fresh-source-{name}", owner, MAX_FILE)
              for name in FILES}
    fresh = {name: GATE.read_private(directory / f"fresh-candidate-{name}", owner, MAX_FILE)
             for name in FILES}
    previous = prior_fresh_candidates(directory, owner)
    if not all(source[name] in {rollback[name], candidate[name], *previous[name]}
               for name in FILES):
        raise ValueError("unknown fresh source")
    fresh_pairs = pair_values(fresh)
    other = next(name for name in PAIRS if name != pair)
    if not (pairs_match(fresh) and fresh_pairs[other] == pair_values(source)[other]
            and fresh_pairs[pair][0] not in (pair_values(rollback)[pair][0],
                                             pair_values(candidate)[pair][0])
            and all(replace_assignment(source[name], PAIRS[pair][index], fresh_pairs[pair][0])
                    == fresh[name] for index, name in enumerate(FILES))):
        raise ValueError("invalid fresh pair")
    result = {"phase": "bearer-activate-fresh", "status": "STOP", "run_id": directory.name,
              "pair": pair, "checks": {"baseline_current": False, "writers_isolated": False,
                                      "fresh_installed": False, "paired_verification": False,
                                      "both_prior_bearers_rejected": False,
                                      "public_health": False,
                                      "path_isolated_after_failure": False}}
    with PAUSE.project_lock(project):
        require_pause(project)
        result["checks"]["baseline_current"] = (
            configuration_current(project, baseline)
            and stack_current(project, directory, owner)
            and PAUSE.no_extra_writers(baseline["project"])
            and read_runtime(project, owner) == source)
        result["checks"]["writers_isolated"] = isolated(baseline["project"])
        if not result["checks"]["baseline_current"] or not result["checks"]["writers_isolated"]:
            raise ValueError("fresh activation drift")
        write_result(directory / "fresh-activation-incomplete.json",
                      (json.dumps(result, sort_keys=True) + "\n").encode(), owner)
        try:
            replace_runtime(project, fresh, owner)
            result["checks"]["fresh_installed"] = read_runtime(project, owner) == fresh
            if not result["checks"]["fresh_installed"] or not compose_up(project):
                raise ValueError("fresh activation failed")
            detail = {}
            result["checks"]["paired_verification"] = verified(
                project, pair, fresh, rollback, baseline, owner, detail)
            result["verification"] = detail
            if not result["checks"]["paired_verification"]:
                raise ValueError("fresh pair verification failed")
            active, installed = project_snapshot(project, owner)
            if active != fresh:
                raise ValueError("fresh drift")
            direction = pair
            if direction == "sidecar":
                container, url = installed["installed"]["backend"]["id"], "http://mom:8001/api/members"
            else:
                container, url = installed["installed"]["mom"]["id"], "http://backend:8000/api/members/me/preferences"
            prior_values = {pair_values(candidate)[pair][0]}
            for name in FILES:
                key = PAIRS[pair][FILES.index(name)]
                prior_values.update(parse_env(raw, {key})[key] for raw in previous[name])
            result["checks"]["both_prior_bearers_rejected"] = all(
                probe(container, url, value) == 401 for value in prior_values)
            if not result["checks"]["both_prior_bearers_rejected"]:
                raise ValueError("prior candidate still accepted")
            result["checks"]["public_health"] = public_healthy(project, public_config, owner)
            if not result["checks"]["public_health"]:
                raise ValueError("public health failed")
            result["status"] = "PASS"
        except Exception:
            result["checks"]["path_isolated_after_failure"] = (
                stop_pair(baseline["project"]) and isolated(baseline["project"]))
        write_result(directory / "fresh-activation-result.json",
                      (json.dumps(result, sort_keys=True) + "\n").encode(), owner)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("inspect", "stage", "activate", "recover-routine",
                                          "stage-fresh", "activate-fresh"))
    parser.add_argument("--project-directory", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--pair", choices=sorted(PAIRS))
    parser.add_argument("--evidence-directory", type=Path)
    parser.add_argument("--confirm-stage", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--confirm-activate")
    parser.add_argument("--confirm-recover-routine")
    parser.add_argument("--confirm-stage-fresh")
    parser.add_argument("--confirm-activate-fresh")
    parser.add_argument("--old-bearer-safe", action="store_true")
    parser.add_argument("--public-config", type=Path)
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("STOP root_required")
        return 1
    try:
        if args.phase == "inspect":
            if (args.report is None or args.pair or args.evidence_directory or args.confirm_stage
                    or args.run_id or args.confirm_activate or args.confirm_recover_routine
                    or args.old_bearer_safe or args.confirm_stage_fresh
                    or args.confirm_activate_fresh or args.public_config):
                raise ValueError("invalid inspect arguments")
            result = inspect(args.project_directory, args.report)
        elif args.phase == "stage":
            if (not args.pair or not args.evidence_directory or not args.confirm_stage or args.report
                    or args.run_id or args.confirm_activate or args.confirm_recover_routine
                    or args.old_bearer_safe or args.confirm_stage_fresh
                    or args.confirm_activate_fresh or args.public_config):
                raise ValueError("invalid stage arguments")
            result = stage(args.project_directory, args.pair, args.evidence_directory)
        else:
            if (args.pair or args.report or args.confirm_stage or not args.evidence_directory
                    or not args.run_id or not str(uuid.UUID(args.run_id)) == args.run_id):
                raise ValueError("invalid operation selector")
            directory = args.evidence_directory / args.run_id
            if args.phase == "activate":
                if (args.confirm_activate != args.run_id or not args.public_config
                        or args.confirm_recover_routine
                        or args.old_bearer_safe or args.confirm_stage_fresh
                        or args.confirm_activate_fresh):
                    raise ValueError("activation not confirmed")
                result = activate(args.project_directory, directory,
                                  public_config=args.public_config)
            elif args.phase == "recover-routine":
                if (args.confirm_recover_routine != args.run_id or args.confirm_activate
                        or not args.old_bearer_safe or not args.public_config
                        or args.confirm_stage_fresh
                        or args.confirm_activate_fresh):
                    raise ValueError("routine recovery not confirmed")
                result = recover_routine(args.project_directory, directory,
                                         old_bearer_safe=args.old_bearer_safe,
                                         public_config=args.public_config)
            elif args.phase == "stage-fresh":
                if (args.confirm_stage_fresh != args.run_id or args.confirm_activate
                        or args.confirm_recover_routine or args.confirm_activate_fresh
                        or args.old_bearer_safe or args.public_config):
                    raise ValueError("fresh staging not confirmed")
                result = stage_fresh(args.project_directory, directory)
            else:
                if (args.confirm_activate_fresh != args.run_id or not args.public_config
                        or args.confirm_activate
                        or args.confirm_recover_routine or args.confirm_stage_fresh
                        or args.old_bearer_safe):
                    raise ValueError("fresh activation not confirmed")
                result = activate_fresh(args.project_directory, directory,
                                        public_config=args.public_config)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        print("STOP")
        return 1
    print(result["status"], result.get("run_id", ""), result.get("attempt_id", ""))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
