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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("inspect", "stage"))
    parser.add_argument("--project-directory", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--pair", choices=sorted(PAIRS))
    parser.add_argument("--evidence-directory", type=Path)
    parser.add_argument("--confirm-stage", action="store_true")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("STOP root_required")
        return 1
    try:
        if args.phase == "inspect":
            if args.report is None or args.pair or args.evidence_directory or args.confirm_stage:
                raise ValueError("invalid inspect arguments")
            result = inspect(args.project_directory, args.report)
        else:
            if not args.pair or not args.evidence_directory or not args.confirm_stage or args.report:
                raise ValueError("invalid stage arguments")
            result = stage(args.project_directory, args.pair, args.evidence_directory)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        print("STOP")
        return 1
    print(result["status"], result.get("run_id", ""))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
