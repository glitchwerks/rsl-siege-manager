#!/usr/bin/env python3
"""Stage, activate, or recover one reviewed portable day-role configuration change.

No phase runs without an explicit operator invocation. Reports contain fixed
checks only; candidate and rollback environment files remain private on host.
"""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import tempfile
import time
import uuid


HERE = Path(__file__).resolve().parent


def sibling(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROTATION = sibling("day_role_rotation_guards", "portable-vps-bearer-rotation.py")
EFFECTIVE = sibling("day_role_effective_guard", "portable-vps-day-role-effective.py")
PREFLIGHT = ROTATION.PREFLIGHT.PREFLIGHT
PAUSE = ROTATION.PAUSE
GATE = ROTATION.GATE
BACKUP_EVIDENCE = sibling("day_role_backup_evidence", "portable-vps-backup-evidence.py")
BACKUP_PROFILE = sibling("day_role_backup_profile", "portable-vps-release-pause-backup.py")
KEYS = ("DAY_ROLE_SYNC_ENABLED", "DAY_ROLE_SYNC_URL",
        "DISCORD_DAY_1_ROLE_ID", "DISCORD_DAY_2_ROLE_ID")
SNOWFLAKE = re.compile(r"[0-9]{17,20}\Z")
MAX_FILE = ROTATION.MAX_FILE


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def assignments(raw):
    """Reject ambiguous role settings; preserve unrelated bytes on replacement."""
    seen = {}
    for line in raw.splitlines(keepends=True):
        body = line.rstrip(b"\r\n")
        if body.lstrip().startswith(b"#") or b"=" not in body:
            continue
        left, value = body.split(b"=", 1)
        name = left.strip().removeprefix(b"export ").strip()
        if name not in {key.encode() for key in KEYS}:
            continue
        if left != name or name in seen or b"\x00" in value:
            raise ValueError("ambiguous role setting")
        seen[name] = value
    if b"DAY_ROLE_SYNC_ENABLED" not in seen:
        raise ValueError("missing role flag")
    return seen


def changed(raw, values):
    """Change only unique literal assignments and append absent role settings."""
    assignments(raw)
    replacements = {key.encode(): value.encode("ascii") for key, value in values.items()}
    found = set()
    lines = []
    for line in raw.splitlines(keepends=True):
        ending = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
        body = line[:-len(ending)] if ending else line
        left = body.split(b"=", 1)[0] if b"=" in body else b""
        if left in replacements and not body.lstrip().startswith(b"#"):
            lines.append(left + b"=" + replacements[left] + ending)
            found.add(left)
        else:
            lines.append(line)
    result = b"".join(lines)
    for key in KEYS:
        encoded = key.encode()
        if encoded in replacements and encoded not in found:
            if result and not result.endswith(b"\n"):
                result += b"\n"
            result += encoded + b"=" + replacements[encoded] + b"\n"
    if result == raw:
        raise ValueError("unchanged role configuration")
    return result


def role_delta(old, candidate, enabled):
    """Prove the candidate differs only in the reviewed day-role assignments."""
    before, after = assignments(old), assignments(candidate)
    if after[b"DAY_ROLE_SYNC_ENABLED"] != enabled.encode("ascii"):
        raise ValueError("candidate flag mismatch")
    if enabled == "true":
        if not all(key.encode() in after for key in KEYS):
            raise ValueError("candidate role setting missing")
        first = after[b"DISCORD_DAY_1_ROLE_ID"].decode("ascii")
        second = after[b"DISCORD_DAY_2_ROLE_ID"].decode("ascii")
        if (not SNOWFLAKE.fullmatch(first) or not SNOWFLAKE.fullmatch(second)
                or int(first) == int(second) or int(first) >= 2**64
                or int(second) >= 2**64):
            raise ValueError("candidate role IDs invalid")
        values = {key: after[key.encode()].decode("ascii") for key in KEYS}
    else:
        if any(after.get(key.encode()) != before.get(key.encode()) for key in KEYS[1:]):
            raise ValueError("disable candidate changed role mapping")
        values = {"DAY_ROLE_SYNC_ENABLED": "false"}
    if changed(old, values) != candidate:
        raise ValueError("candidate changed unrelated runtime values")
    return values


def decision(path, owner):
    if not ROTATION.private(path.parent, owner, directory=True):
        raise ValueError("unsafe decision directory")
    data = json.loads(GATE.read_private(path, owner, 4096),
                      object_pairs_hook=ROTATION.PREFLIGHT.REMOTE.no_duplicate_keys)
    if not isinstance(data, dict) or data.get("schema") != 1 or type(data.get("enabled")) is not bool:
        raise ValueError("invalid decision")
    if data["enabled"]:
        if set(data) != {"schema", "enabled", "day_1_role_id", "day_2_role_id", "receiver_url"}:
            raise ValueError("invalid enable decision")
        first, second = data["day_1_role_id"], data["day_2_role_id"]
        if (not isinstance(first, str) or not isinstance(second, str)
                or not SNOWFLAKE.fullmatch(first) or not SNOWFLAKE.fullmatch(second)
                or int(first) == int(second) or int(first) >= 2**64 or int(second) >= 2**64
                or not isinstance(data["receiver_url"], str)
                or len(data["receiver_url"]) > 512):
            raise ValueError("invalid role decision")
        return {"DAY_ROLE_SYNC_ENABLED": "true", "DAY_ROLE_SYNC_URL": data["receiver_url"],
                "DISCORD_DAY_1_ROLE_ID": first, "DISCORD_DAY_2_ROLE_ID": second}
    if set(data) != {"schema", "enabled"}:
        raise ValueError("invalid disable decision")
    return {"DAY_ROLE_SYNC_ENABLED": "false"}


def model(project):
    stack = project / "stack.env"
    if (not PREFLIGHT.private_runtime_files("mom", stack, project)
            or not PREFLIGHT.deployment_bind_sources_current(project)):
        raise ValueError("unsafe project inputs")
    rendered = PREFLIGHT.render("mom", stack, project, {})
    approval = PREFLIGHT.read_mom_approval(project / "runtime" / "mom-image.json")
    if approval is None or not all(PREFLIGHT.validate(rendered, "mom", approval, project).values()):
        raise ValueError("invalid current configuration")
    return rendered, approval


def proposed_model(current, values, approval, project):
    proposed = copy.deepcopy(current)
    for service in ("backend", "migrate-siege"):
        proposed["services"][service]["environment"].update(values)
    if not all(PREFLIGHT.validate(proposed, "mom", approval, project).values()):
        raise ValueError("invalid proposed configuration")
    return proposed


def private_json(path, value, owner):
    ROTATION.write_private(path, (json.dumps(value, sort_keys=True) + "\n").encode(), owner)


def input_hashes(project):
    return {"runtime": ROTATION.PREFLIGHT.runtime_hashes(project),
            "config": ROTATION.PREFLIGHT.config_hashes(project)}


def inputs_current(project, directory, owner, *, include_backend):
    expected = json.loads(GATE.read_private(directory / "input-hashes.json", owner, 4096),
                          object_pairs_hook=ROTATION.PREFLIGHT.REMOTE.no_duplicate_keys)
    current = input_hashes(project)
    if not include_backend:
        expected["runtime"].pop("backend.env", None)
        current["runtime"].pop("backend.env", None)
    return current == expected


def stage(project, decision_path, evidence, owner=0):
    if not ROTATION.private(evidence, owner, directory=True):
        raise ValueError("unsafe evidence directory")
    values = decision(decision_path, owner)
    stack_before = GATE.read_private(project / "stack.env", owner, MAX_FILE)
    hashes_before = input_hashes(project)
    current, approval = model(project)
    before, baseline = ROTATION.project_snapshot(project, owner)
    flag = current["services"]["backend"]["environment"].get("DAY_ROLE_SYNC_ENABLED", "false")
    if (flag == values["DAY_ROLE_SYNC_ENABLED"]
            or not all(EFFECTIVE.inspect(current, flag).values())):
        raise ValueError("baseline flag or running state mismatch")
    proposed_model(current, values, approval, project)
    candidate = changed(before["backend.env"], values)
    run_id = str(uuid.uuid4())
    directory = evidence / run_id
    directory.mkdir(mode=0o700)
    if not ROTATION.private(directory, owner, directory=True):
        raise ValueError("unsafe stage directory")
    private_json(directory / "stage-incomplete.json", {"phase": "day-role-stage", "status": "INCOMPLETE"}, owner)
    try:
        after, again = ROTATION.project_snapshot(project, owner)
        if (after != before or again != baseline or model(project)[0] != current
                or GATE.read_private(project / "stack.env", owner, MAX_FILE) != stack_before
                or input_hashes(project) != hashes_before
                or not all(EFFECTIVE.inspect(current, flag).values())):
            raise ValueError("baseline drift")
        ROTATION.write_private(directory / "rollback-backend.env", before["backend.env"], owner)
        ROTATION.write_private(directory / "candidate-backend.env", candidate, owner)
        ROTATION.write_private(directory / "baseline-mom.env", before["mom.env"], owner)
        ROTATION.write_private(directory / "baseline-stack.env", stack_before, owner)
        private_json(directory / "input-hashes.json", hashes_before, owner)
        private_json(directory / "baseline.json", baseline, owner)
        private_json(directory / "accepted.json", {
            "phase": "day-role-stage", "status": "PASS", "run_id": run_id,
            "old_enabled": flag, "new_enabled": values["DAY_ROLE_SYNC_ENABLED"],
            "timestamp": now(), "checks": {"candidate_valid": True, "baseline_stable": True}}, owner)
        return directory
    except Exception:
        private_json(directory / "stage-stopped.json", {"phase": "day-role-stage", "status": "STOP"}, owner)
        raise


def load(project, directory, owner):
    if (not directory.is_absolute() or ".." in directory.parts
            or not ROTATION.private(directory.parent, owner, directory=True)
            or not ROTATION.private(directory, owner, directory=True)
            or str(uuid.UUID(directory.name)) != directory.name):
        raise ValueError("unsafe stage")
    accepted = json.loads(GATE.read_private(directory / "accepted.json", owner),
                          object_pairs_hook=ROTATION.PREFLIGHT.REMOTE.no_duplicate_keys)
    baseline = json.loads(GATE.read_private(directory / "baseline.json", owner),
                          object_pairs_hook=ROTATION.PREFLIGHT.REMOTE.no_duplicate_keys)
    if (not isinstance(accepted, dict) or accepted.get("phase") != "day-role-stage"
            or accepted.get("status") != "PASS" or accepted.get("run_id") != directory.name
            or accepted.get("old_enabled") not in ("true", "false")
            or accepted.get("new_enabled") not in ("true", "false")
            or accepted["old_enabled"] == accepted["new_enabled"]
            or not isinstance(baseline, dict) or baseline.get("project") is None):
        raise ValueError("invalid stage")
    old = GATE.read_private(directory / "rollback-backend.env", owner, MAX_FILE)
    candidate = GATE.read_private(directory / "candidate-backend.env", owner, MAX_FILE)
    mom = GATE.read_private(directory / "baseline-mom.env", owner, MAX_FILE)
    stack = GATE.read_private(directory / "baseline-stack.env", owner, MAX_FILE)
    if (assignments(old)[b"DAY_ROLE_SYNC_ENABLED"].decode() != accepted["old_enabled"]
            or assignments(candidate)[b"DAY_ROLE_SYNC_ENABLED"].decode() != accepted["new_enabled"]
            or not ROTATION.pairs_match({"backend.env": old, "mom.env": mom})
            or not ROTATION.pairs_match({"backend.env": candidate, "mom.env": mom})
            or GATE.read_private(project / "stack.env", owner, MAX_FILE) != stack):
        raise ValueError("stage binding changed")
    values = role_delta(old, candidate, accepted["new_enabled"])
    return accepted, baseline, old, candidate, mom, values


def proof_snapshot(project, directory, profile_path, maintenance_report,
                   backup_path, recover_path, restore_path, owner=0):
    """Bind same-copy evidence to this unchanged stage and maintenance pause."""
    accepted, baseline, old, candidate, mom, _ = load(project, directory, owner)
    ROTATION.require_pause(project, drain=True)
    pause_history = ROTATION.MAINT.history(project)
    if pause_history is None:
        raise ValueError("missing pause history")
    resumed_at = BACKUP_EVIDENCE.moment(pause_history["last_resumed_at"])
    if pause_history["pause_started_at"] is None:
        raise ValueError("missing maintenance pause boundary")
    expected_phase = {"pause-report.json": "maintenance-pause",
                      "drain-report.json": "maintenance-drain"}.get(maintenance_report.name)
    if (maintenance_report.parent != directory or expected_phase is None
            or not maintenance_report.is_absolute() or ".." in maintenance_report.parts):
        raise ValueError("invalid maintenance report selector")
    maintenance_raw = GATE.read_private(maintenance_report, owner)
    maintenance = json.loads(maintenance_raw,
                             object_pairs_hook=ROTATION.PREFLIGHT.REMOTE.no_duplicate_keys)
    if (not isinstance(maintenance, dict) or maintenance.get("phase") != expected_phase
            or maintenance.get("status") != "PASS"
            or not isinstance(maintenance.get("checks"), dict)
            or maintenance["checks"].get("drained") is not True
            or maintenance["checks"].get("paused") is not True
            or not all(value is True for value in maintenance["checks"].values())):
        raise ValueError("maintenance drain was not accepted")
    state_path = project / "control" / "state"
    state_mtime_ns = state_path.lstat().st_mtime_ns
    drain_mtime_ns = maintenance_report.lstat().st_mtime_ns
    drain_mtime = datetime.fromtimestamp(drain_mtime_ns / 1e9, timezone.utc)
    if (drain_mtime_ns < state_mtime_ns
            or (resumed_at is not None and drain_mtime <= resumed_at)):
        raise ValueError("maintenance report predates current pause")
    profile, profile_sha = BACKUP_PROFILE.profile(profile_path)
    if profile["project_directory"] != project:
        raise ValueError("backup profile project mismatch")
    backup_owner = pwd.getpwnam(profile["backup_owner"]).pw_uid
    if any(not path.is_absolute() or ".." in path.parts
           for path in (backup_path, recover_path, restore_path)):
        raise ValueError("invalid evidence selector")
    if len({backup_path, recover_path, restore_path}) != 3:
        raise ValueError("duplicate evidence selector")
    run_id = backup_path.parent.name
    if (not BACKUP_EVIDENCE.valid_uuid(run_id)
            or backup_path != profile["backup_root"] / "runs" / run_id / "job.json"):
        raise ValueError("backup outside managed run")
    before, current_baseline = ROTATION.project_snapshot(project, owner)
    current, _ = model(project)
    if (before["backend.env"] != old or before["mom.env"] != mom
            or current_baseline != baseline
            or not ROTATION.stack_current(project, directory, owner)
            or not inputs_current(project, directory, owner, include_backend=True)
            or not all(EFFECTIVE.inspect(current, accepted["old_enabled"]).values())):
        raise ValueError("stage baseline drift")
    paths = {"backup": backup_path, "recover": recover_path, "restore": restore_path}
    raw = {name: GATE.read_private(path, backup_owner) for name, path in paths.items()}
    parsed = {name: json.loads(value, object_pairs_hook=ROTATION.PREFLIGHT.REMOTE.no_duplicate_keys)
              for name, value in raw.items()}
    if not all(isinstance(value, dict) for value in parsed.values()):
        raise ValueError("invalid backup report shape")
    checks = BACKUP_EVIDENCE.inspect(backup_path, recover_path, restore_path,
        owner_uid=backup_owner,
        reader=lambda path: parsed[next(name for name, item in paths.items() if item == path)])
    backup_started = BACKUP_EVIDENCE.moment(parsed["backup"].get("timestamp"))
    if (not checks or not all(value is True for value in checks.values())
            or backup_started is None or backup_started < drain_mtime
            or parsed["backup"].get("run_id") != run_id
            or any(digest(GATE.read_private(path, backup_owner)) != digest(raw[name])
                   for name, path in paths.items())
            or GATE.read_private(maintenance_report, owner) != maintenance_raw
            or maintenance_report.lstat().st_mtime_ns != drain_mtime_ns
            or state_path.lstat().st_mtime_ns != state_mtime_ns
            or ROTATION.MAINT.history(project) != pause_history
            or ROTATION.MAINT.state(project) != "paused"):
        raise ValueError("backup proof or pause changed")
    return {"schema": 1, "stage_id": directory.name,
            "candidate_sha256": digest(candidate),
            "pause_history": pause_history, "profile_sha256": profile_sha,
            "maintenance": {"path": str(maintenance_report),
                            "sha256": digest(maintenance_raw)},
            "reports": {name: {"path": str(path), "sha256": digest(raw[name])}
                        for name, path in paths.items()}}


def accept_proof(project, directory, profile_path, maintenance_report,
                 backup_path, recover_path, restore_path, owner=0):
    with PAUSE.project_lock(project):
        load(project, directory, owner)
        if (directory / "proof-attempt.json").exists() or (directory / "proof-accepted.json").exists():
            raise ValueError("proof already attempted")
        private_json(directory / "proof-attempt.json",
                     {"phase": "day-role-proof", "status": "INCOMPLETE"}, owner)
        proof = proof_snapshot(project, directory, profile_path, maintenance_report, backup_path,
                               recover_path, restore_path, owner)
        private_json(directory / "proof-accepted.json",
                     {"phase": "day-role-proof", "status": "PASS", "proof": proof}, owner)
    return {"phase": "day-role-proof", "status": "PASS", "stage_id": directory.name}


def proof_current(project, directory, profile_path, owner=0):
    record = json.loads(GATE.read_private(directory / "proof-accepted.json", owner),
                        object_pairs_hook=ROTATION.PREFLIGHT.REMOTE.no_duplicate_keys)
    if (not isinstance(record, dict) or set(record) != {"phase", "status", "proof"}
            or record["phase"] != "day-role-proof" or record["status"] != "PASS"
            or not isinstance(record["proof"], dict)
            or set(record["proof"]) != {"schema", "stage_id", "candidate_sha256",
                                         "pause_history", "profile_sha256", "maintenance", "reports"}
            or record["proof"]["schema"] != 1
            or record["proof"]["stage_id"] != directory.name
            or not isinstance(record["proof"]["candidate_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", record["proof"]["candidate_sha256"])):
        raise ValueError("invalid accepted proof")
    maintenance = record["proof"]["maintenance"]
    if (not isinstance(maintenance, dict) or set(maintenance) != {"path", "sha256"}
            or not isinstance(maintenance["path"], str)
            or not isinstance(maintenance["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", maintenance["sha256"])):
        raise ValueError("invalid maintenance selector")
    reports = record["proof"]["reports"]
    if not isinstance(reports, dict) or set(reports) != {"backup", "recover", "restore"}:
        raise ValueError("invalid proof selectors")
    paths = []
    for name in ("backup", "recover", "restore"):
        item = reports[name]
        if (not isinstance(item, dict) or set(item) != {"path", "sha256"}
                or not isinstance(item["path"], str)
                or not isinstance(item["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise ValueError("invalid proof selector")
        paths.append(Path(item["path"]))
    return record["proof"] == proof_snapshot(project, directory, profile_path,
                                             Path(maintenance["path"]), *paths, owner)


def replace_backend(project, raw, owner):
    path = project / "runtime" / "backend.env"
    if not ROTATION.private(path, owner) or not ROTATION.private(path.parent, owner, directory=True):
        raise ValueError("unsafe backend env")
    descriptor, temporary = tempfile.mkstemp(prefix=".backend.env.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def stop_backend(project_name, expected=None):
    identifier = PAUSE.identifiers(project_name, "backend")
    if identifier is None or (expected is not None and identifier != expected):
        return False
    state = PAUSE.container(project_name, "backend")
    if state is None:
        return False
    if state["running"] is True and PAUSE.command(
            ["docker", "stop", "--time", "30", identifier], timeout=50) is None:
        return False
    stopped = PAUSE.container(project_name, "backend")
    return stopped is not None and stopped["running"] is False


def recovery_drain(project, baseline):
    """Accept a stopped backend only after checking Mom's paused drain alone."""
    name = baseline["project"]
    state = PAUSE.container(name, "backend")
    if state is None or state["id"] != PAUSE.identifiers(name, "backend"):
        return False
    image_raw = PAUSE.command(["docker", "inspect", "--format", "{{json .Config.Image}}", state["id"]])
    try:
        if json.loads(image_raw) != baseline["installed"]["backend"]["image"]:
            return False
    except (TypeError, ValueError, KeyError):
        return False
    if state["running"] is True:
        return ROTATION.MAINT.drain(project)
    if state["running"] is not False or state["status"] not in {"exited", "created"}:
        return False
    deadline = time.monotonic() + 20
    for attempt in range(3):
        if (ROTATION.MAINT.state(project) != "paused"
                or ROTATION.MAINT.service_status(project, "mom", deadline=deadline)
                != {"paused": True, "inflight": 0}):
            return False
        if attempt < 2:
            time.sleep(0.1)
    return ROTATION.MAINT.state(project) == "paused"


def compose_backend(project):
    command = ["docker", "compose", "--env-file", str(project / "stack.env"),
               "--project-directory", str(project), "-f", str(project / "compose.yml"),
               "-f", str(project / "compose.mom.yml"), "up", "--no-deps", "--no-build",
               "--pull", "never", "--force-recreate", "--wait", "--wait-timeout", "240", "backend"]
    try:
        result = subprocess.run(command, env=ROTATION.ENVIRONMENT, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=300, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def verify(project, baseline, expected_enabled, old_backend_id):
    current, _ = model(project)
    _, after = ROTATION.project_snapshot(project, 0)
    installed, original = after["installed"], baseline["installed"]
    unchanged = (after["source_sha"] == baseline["source_sha"]
                 and set(installed) == set(original)
                 and all(installed[name] == original[name] for name in original if name != "backend")
                 and installed["backend"]["image"] == original["backend"]["image"]
                 and installed["backend"]["id"] != old_backend_id)
    return unchanged and all(EFFECTIVE.inspect(current, expected_enabled).values())


def activate(project, directory, public_config, backup_profile, owner=0):
    accepted, baseline, old, candidate, mom, values = load(project, directory, owner)
    if (directory / "activation-incomplete.json").exists():
        raise ValueError("activation already attempted")
    result = {"phase": "day-role-activate", "status": "STOP", "timestamp": now(),
              "checks": {"baseline_current": False, "writers_paused": False,
                         "backup_proof_current": False,
                         "public_baseline_healthy": False,
                         "backend_stopped": False, "candidate_valid": False,
                         "backend_recreated": False, "effective_state": False,
                         "public_health": False}}
    with PAUSE.project_lock(project):
        before, current_baseline = ROTATION.project_snapshot(project, owner)
        current, approval = model(project)
        result["checks"]["baseline_current"] = (
            before["backend.env"] == old and before["mom.env"] == mom
            and current_baseline == baseline and ROTATION.stack_current(project, directory, owner)
            and inputs_current(project, directory, owner, include_backend=True)
            and all(EFFECTIVE.inspect(current, accepted["old_enabled"]).values()))
        if not result["checks"]["baseline_current"]:
            raise ValueError("baseline drift")
        proposed = proposed_model(current, values, approval, project)
        ROTATION.require_pause(project, drain=True)
        result["checks"]["writers_paused"] = True
        result["checks"]["backup_proof_current"] = proof_current(
            project, directory, backup_profile, owner)
        if not result["checks"]["backup_proof_current"]:
            raise ValueError("backup proof changed")
        result["checks"]["public_baseline_healthy"] = ROTATION.public_healthy(
            project, public_config, owner)
        if not result["checks"]["public_baseline_healthy"]:
            raise ValueError("public baseline unhealthy")
        pause_history = ROTATION.MAINT.history(project)
        ROTATION.write_result(directory / "activation-incomplete.json",
            (json.dumps({"phase": "day-role-activate", "status": "INCOMPLETE",
                         "pause_history": pause_history}, sort_keys=True) + "\n").encode(), owner)
        try:
            result["checks"]["backend_stopped"] = stop_backend(
                baseline["project"], baseline["installed"]["backend"]["id"])
            if not result["checks"]["backend_stopped"]:
                raise ValueError("backend stop failed")
            replace_backend(project, candidate, owner)
            rendered = PREFLIGHT.render("mom", project / "stack.env", project, {})
            result["checks"]["candidate_valid"] = (
                GATE.read_private(project / "runtime" / "backend.env", owner, MAX_FILE) == candidate
                and rendered == proposed
                and inputs_current(project, directory, owner, include_backend=False))
            if not result["checks"]["candidate_valid"] or not compose_backend(project):
                raise ValueError("candidate activation failed")
            result["checks"]["backend_recreated"] = True
            result["checks"]["effective_state"] = verify(
                project, baseline, accepted["new_enabled"], baseline["installed"]["backend"]["id"])
            if not result["checks"]["effective_state"]:
                raise ValueError("effective state mismatch")
            result["checks"]["public_health"] = ROTATION.public_healthy(project, public_config, owner)
            if not result["checks"]["public_health"]:
                raise ValueError("public health failed")
            result["status"] = "PASS"
        except Exception:
            result["checks"]["backend_isolated_after_failure"] = stop_backend(baseline["project"])
        ROTATION.write_result(directory / "activation-result.json",
                              (json.dumps(result, sort_keys=True) + "\n").encode(), owner)
    return result


def recover(project, directory, public_config, restore_enabled_safe=False, owner=0):
    accepted, baseline, old, candidate, mom, _ = load(project, directory, owner)
    if accepted["old_enabled"] == "true" and not restore_enabled_safe:
        raise ValueError("re-enabling old feature requires separate safety decision")
    marker = json.loads(GATE.read_private(directory / "activation-incomplete.json", owner),
                        object_pairs_hook=ROTATION.PREFLIGHT.REMOTE.no_duplicate_keys)
    if (not isinstance(marker, dict) or marker.get("phase") != "day-role-activate"
            or marker.get("status") != "INCOMPLETE"):
        raise ValueError("missing activation marker")
    if (directory / "recovery-incomplete.json").exists():
        raise ValueError("recovery already attempted")
    prior = directory / "activation-result.json"
    if prior.exists():
        outcome = json.loads(GATE.read_private(prior, owner),
                             object_pairs_hook=ROTATION.PREFLIGHT.REMOTE.no_duplicate_keys)
        if not isinstance(outcome, dict) or outcome.get("status") != "STOP":
            raise ValueError("successful activation cannot be rolled back here")
    result = {"phase": "day-role-recover", "status": "STOP", "timestamp": now(),
              "checks": {"pause_unchanged": False, "known_files": False,
                         "writers_drained": False,
                         "backend_stopped": False, "rollback_installed": False,
                         "effective_state": False, "public_health": False}}
    with PAUSE.project_lock(project):
        ROTATION.require_pause(project)
        result["checks"]["pause_unchanged"] = ROTATION.MAINT.history(project) == marker.get("pause_history")
        current = GATE.read_private(project / "runtime" / "backend.env", owner, MAX_FILE)
        result["checks"]["known_files"] = (
            current in (old, candidate)
            and GATE.read_private(project / "runtime" / "mom.env", owner, MAX_FILE) == mom
            and inputs_current(project, directory, owner, include_backend=False)
            and ROTATION.configuration_current(project, baseline)
            and PAUSE.identifiers(baseline["project"], "mom") == baseline["installed"]["mom"]["id"]
            and (mom_state := PAUSE.container(baseline["project"], "mom")) is not None
            and mom_state["running"] is True and mom_state["healthy"] is True)
        if not result["checks"]["pause_unchanged"] or not result["checks"]["known_files"]:
            raise ValueError("recovery baseline changed")
        result["checks"]["writers_drained"] = recovery_drain(project, baseline)
        if not result["checks"]["writers_drained"]:
            raise ValueError("recovery drain incomplete")
        ROTATION.write_result(directory / "recovery-incomplete.json",
            (json.dumps({"phase": "day-role-recover", "status": "INCOMPLETE"}) + "\n").encode(), owner)
        try:
            result["checks"]["backend_stopped"] = stop_backend(baseline["project"])
            if not result["checks"]["backend_stopped"]:
                raise ValueError("backend stop failed")
            replace_backend(project, old, owner)
            result["checks"]["rollback_installed"] = (
                GATE.read_private(project / "runtime" / "backend.env", owner, MAX_FILE) == old
                and compose_backend(project))
            if not result["checks"]["rollback_installed"]:
                raise ValueError("rollback activation failed")
            result["checks"]["effective_state"] = verify(
                project, baseline, accepted["old_enabled"], baseline["installed"]["backend"]["id"])
            result["checks"]["public_health"] = ROTATION.public_healthy(project, public_config, owner)
            if not result["checks"]["effective_state"] or not result["checks"]["public_health"]:
                raise ValueError("recovery verification failed")
            result["status"] = "PASS"
        except Exception:
            result["checks"]["backend_isolated_after_failure"] = stop_backend(baseline["project"])
        ROTATION.write_result(directory / "recovery-result.json",
                              (json.dumps(result, sort_keys=True) + "\n").encode(), owner)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("stage", "proof", "activate", "recover"))
    parser.add_argument("--project-directory", type=Path, required=True)
    parser.add_argument("--decision-file", type=Path)
    parser.add_argument("--evidence-directory", type=Path)
    parser.add_argument("--stage-directory", type=Path)
    parser.add_argument("--public-config", type=Path)
    parser.add_argument("--backup-profile", type=Path)
    parser.add_argument("--maintenance-report", type=Path)
    parser.add_argument("--backup-report", type=Path)
    parser.add_argument("--recover-report", type=Path)
    parser.add_argument("--restore-report", type=Path)
    parser.add_argument("--confirm", choices=("stage", "proof", "activate", "recover"))
    parser.add_argument("--confirm-restore-enabled-safe", action="store_true")
    args = parser.parse_args(argv)
    try:
        if (os.geteuid() != 0 or args.confirm != args.phase
                or (args.phase != "recover" and args.confirm_restore_enabled_safe)
                or not args.project_directory.is_absolute()
                or ".." in args.project_directory.parts
                or args.project_directory.resolve() != args.project_directory):
            raise ValueError("invalid invocation")
        if args.phase == "stage":
            if args.decision_file is None or args.evidence_directory is None:
                raise ValueError("missing stage inputs")
            directory = stage(args.project_directory, args.decision_file,
                              args.evidence_directory)
            print(json.dumps({"phase": "day-role-stage", "status": "PASS", "run_id": directory.name}))
            return 0
        if args.stage_directory is None:
            raise ValueError("missing stage selector")
        if args.phase == "proof":
            if any(value is None for value in (args.backup_profile, args.maintenance_report,
                                               args.backup_report, args.recover_report,
                                               args.restore_report)):
                raise ValueError("missing proof inputs")
            result = accept_proof(args.project_directory, args.stage_directory,
                                  args.backup_profile, args.maintenance_report, args.backup_report,
                                  args.recover_report, args.restore_report)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.public_config is None:
            raise ValueError("missing public config")
        if args.phase == "activate":
            if args.backup_profile is None:
                raise ValueError("missing backup profile")
            result = activate(args.project_directory, args.stage_directory,
                              args.public_config, args.backup_profile)
        else:
            result = recover(args.project_directory, args.stage_directory, args.public_config,
                             restore_enabled_safe=args.confirm_restore_enabled_safe)
        print(json.dumps({"phase": result["phase"], "status": result["status"]}))
        return 0 if result["status"] == "PASS" else 1
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError,
            subprocess.TimeoutExpired):
        print(json.dumps({"phase": "day-role-" + args.phase, "status": "STOP"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
