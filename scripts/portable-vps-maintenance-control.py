#!/usr/bin/env python3
"""Phase-scoped host control for the portable backend maintenance state.

No phase touches production unless an operator invokes it on the approved host.
The pause phase leaves the state paused on interruption. Resume requires fresh
paired bearer and independent-monitor evidence; it never infers success from a
healthy container alone.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "portable_pause", HERE / "portable-vps-release-pause-backup.py"
)
PAUSE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PAUSE)
PROBE = """import json, os, urllib.request
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/maintenance/status",
    headers={"Authorization": "Bearer " + os.environ["BOT_SERVICE_TOKEN"]},
)
with urllib.request.urlopen(request, timeout=4) as response:
    data = json.load(response)
if set(data) != {"paused", "inflight"} or type(data["paused"]) is not bool \\
        or type(data["inflight"]) is not int or data["inflight"] < 0:
    raise SystemExit(1)
print(json.dumps(data, sort_keys=True))"""
MOM_PROBE = PROBE.replace("127.0.0.1:8000", "127.0.0.1:8001").replace(
    "BOT_SERVICE_TOKEN", "MOM_BOT_SECRET_DISCORD_BOT_API_KEY"
)


def root_owned(path: Path, *, directory: bool = False, readable: bool = False) -> bool:
    try:
        if (
            not path.is_absolute()
            or ".." in path.parts
            or any(item.is_symlink() for item in (path, *path.parents))
        ):
            return False
        info = path.lstat()
        kind = stat.S_ISDIR if directory else stat.S_ISREG
        return (
            kind(info.st_mode)
            and info.st_uid == 0
            and not info.st_mode & 0o022
            and (
                not readable
                or (info.st_mode & (0o005 if directory else 0o004))
                == (0o005 if directory else 0o004)
            )
        )
    except OSError:
        return False


def state(project: Path) -> str | None:
    directory = project / "control"
    path = directory / "state"
    if (
        not root_owned(project, directory=True)
        or not root_owned(directory, directory=True, readable=True)
        or not root_owned(path, readable=True)
    ):
        return None
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or info.st_mode & 0o022
            ):
                return None
            value = source.read(17)
        return {b"active\n": "active", b"paused\n": "paused"}.get(value)
    except OSError:
        return None


def history(project: Path) -> dict | None:
    """Read the nonsecret pause interval used to quarantine uncertain events."""
    path = project / "control" / "history.json"
    if not root_owned(path.parent, directory=True, readable=True) or not root_owned(
        path, readable=True
    ):
        return None
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size > 256
                or info.st_uid != 0
                or info.st_mode & 0o022
            ):
                return None
            value = json.load(source)
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "pause_started_at", "last_resumed_at"}
            or value["schema"] != 1
        ):
            return None
        for field in ("pause_started_at", "last_resumed_at"):
            if value[field] is not None:
                if not isinstance(value[field], str):
                    return None
                parsed = datetime.fromisoformat(value[field])
                if parsed.tzinfo != timezone.utc:
                    return None
        if value["last_resumed_at"] is not None and value["pause_started_at"] is None:
            return None
        return value
    except (OSError, ValueError, TypeError):
        return None


def replace_history(project: Path, value: dict) -> bool:
    """Atomically persist the pause boundary before any resume is allowed."""
    if (
        history(project) is None
        or not isinstance(value, dict)
        or set(value) != {"schema", "pause_started_at", "last_resumed_at"}
        or value["schema"] != 1
    ):
        return False
    for field in ("pause_started_at", "last_resumed_at"):
        if value[field] is not None:
            if not isinstance(value[field], str):
                return False
            try:
                if datetime.fromisoformat(value[field]).tzinfo != timezone.utc:
                    return False
            except ValueError:
                return False
    if value["last_resumed_at"] is not None and value["pause_started_at"] is None:
        return False
    directory = project / "control"
    descriptor, temporary = tempfile.mkstemp(prefix=".history.", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o644)
            output.write((json.dumps(value, sort_keys=True) + "\n").encode())
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, directory / "history.json")
        control = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(control)
        finally:
            os.close(control)
        return history(project) == value
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def replace_state(project: Path, value: str) -> bool:
    """Atomically update the nonsecret state through the mounted directory."""
    if value not in {"active", "paused"} or state(project) is None:
        return False
    directory = project / "control"
    descriptor, temporary = tempfile.mkstemp(prefix=".state.", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o644)
            output.write((value + "\n").encode())
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, directory / "state")
        control = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(control)
        finally:
            os.close(control)
        return state(project) == value
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def service_status(project: Path, service: str) -> dict | None:
    """Query the exact Compose peer using its own secret, without printing it."""
    if service not in {"backend", "mom"}:
        return None
    try:
        name = PAUSE.GATE.project_name(project / "stack.env")
        container = PAUSE.container(name, service) if name else None
        if not container or container["running"] is not True:
            return None
        result = subprocess.run(
            [
                "docker",
                "exec",
                container["id"],
                "python" if service == "backend" else "/app/.venv/bin/python",
                "-c",
                PROBE if service == "backend" else MOM_PROBE,
            ],
            env=PAUSE.ENVIRONMENT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode or len(result.stdout) > 128:
            return None
        data = json.loads(result.stdout)
        if (
            set(data) != {"paused", "inflight"}
            or type(data["paused"]) is not bool
            or type(data["inflight"]) is not int
            or data["inflight"] < 0
        ):
            return None
        return data
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        return None


def drain(project: Path, *, attempts: int = 20, interval: float = 1) -> bool:
    """Require repeated zero in-flight observations from both paused peers."""
    consecutive = 0
    for _ in range(attempts):
        if state(project) != "paused":
            return False
        backend = service_status(project, "backend")
        mom = service_status(project, "mom")
        consecutive = (
            consecutive + 1 if backend == mom == {"paused": True, "inflight": 0} else 0
        )
        if consecutive >= 3 and state(project) == "paused":
            return True
        time.sleep(interval)
    return False


def private_report(path: Path) -> dict | None:
    if not root_owned(path.parent, directory=True) or not PAUSE.GATE.protected(path, 0):
        return None
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
            info = os.fstat(source.fileno())
            if info.st_size > 16384 or info.st_mtime < time.time() - 3600:
                return None
            return json.load(source)
    except (OSError, ValueError, TypeError):
        return None


def resume_evidence(
    reports: list[Path], monitor: Path | None, paused_at: float
) -> bool:
    """Require one successful fresh activation per direction plus alert delivery."""
    if monitor is None or len(reports) != 2 or len(set(reports)) != 2:
        return False
    try:
        pairs = set()
        latest = paused_at
        for path in reports:
            value = private_report(path)
            if (
                path.stat().st_mtime <= paused_at
                or not isinstance(value, dict)
                or value.get("phase")
                not in {"bearer-activate", "bearer-activate-fresh"}
                or value.get("status") != "PASS"
            ):
                return False
            pair = value.get("pair")
            checks = value.get("checks")
            verified = value.get("verification")
            if (
                pair not in {"sidecar", "reverse-api"}
                or not isinstance(checks, dict)
                or not isinstance(verified, dict)
            ):
                return False
            installed = (
                "fresh_installed"
                if value["phase"] == "bearer-activate-fresh"
                else "candidate_installed"
            )
            isolated = (
                "writers_isolated"
                if value["phase"] == "bearer-activate-fresh"
                else "writers_stopped"
            )
            if not all(
                checks.get(key) is True
                for key in (
                    "baseline_current",
                    isolated,
                    installed,
                    "paired_verification",
                    "public_health",
                )
            ) or not all(
                verified.get(key) is True
                for key in (
                    "snapshot",
                    "exact_images_and_targeted_recreation",
                    "sidecar_accepted",
                    "reverse-api_accepted",
                    f"{pair}_stale_rejected",
                )
            ):
                return False
            if (
                value["phase"] == "bearer-activate-fresh"
                and checks.get("both_prior_bearers_rejected") is not True
            ):
                return False
            latest = max(latest, path.stat().st_mtime)
            pairs.add(pair)
        if monitor.stat().st_mtime <= latest:
            return False
        observed = private_report(monitor)
        return (
            pairs == {"sidecar", "reverse-api"}
            and isinstance(observed, dict)
            and observed.get("phase") == "public-monitor"
            and observed.get("status") == "PASS"
            and observed.get("observation_passed") is True
            and observed.get("delivery_acknowledged") is True
        )
    except OSError:
        return False


def run(args: argparse.Namespace) -> dict:
    checks = {
        "root_execution": os.geteuid() == 0,
        "project_private": PAUSE.GATE.protected(
            args.project_directory, 0, directory=True
        ),
        "control_state_valid": state(args.project_directory) is not None,
        "pause_history_valid": history(args.project_directory) is not None,
    }
    report = {
        "phase": f"maintenance-{args.phase}",
        "status": "STOP",
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if not all(checks.values()):
        return report
    with PAUSE.project_lock(args.project_directory):
        if args.phase == "inspect":
            report["state"] = state(args.project_directory)
            checks["state_known"] = report["state"] in {"active", "paused"}
            report["status"] = "PASS"
        elif args.phase == "pause":
            checks["explicit_confirmation"] = args.confirm == "pause"
            checks["currently_active"] = state(args.project_directory) == "active"
            if all(checks.values()):
                checks["paused"] = replace_state(args.project_directory, "paused")
                prior = history(args.project_directory)
                checks["pause_boundary_recorded"] = (
                    checks["paused"]
                    and prior is not None
                    and replace_history(
                        args.project_directory,
                        {
                            "schema": 1,
                            "pause_started_at": prior["pause_started_at"]
                            or datetime.now(timezone.utc).isoformat(),
                            "last_resumed_at": prior["last_resumed_at"],
                        },
                    )
                )
                checks["drained"] = checks["pause_boundary_recorded"] and drain(
                    args.project_directory
                )
            report["status"] = "PASS" if all(checks.values()) else "STOP"
        elif args.phase == "drain":
            checks["paused"] = state(args.project_directory) == "paused"
            checks["drained"] = checks["paused"] and drain(args.project_directory)
            report["status"] = "PASS" if all(checks.values()) else "STOP"
        elif args.phase == "resume":
            checks["explicit_confirmation"] = args.confirm == "resume"
            checks["paused"] = state(args.project_directory) == "paused"
            checks["backend_drained"] = checks["paused"] and drain(
                args.project_directory
            )
            checks["paired_and_monitor_evidence"] = resume_evidence(
                args.bearer_report,
                args.monitor_report,
                (args.project_directory / "control/state").stat().st_mtime,
            )
            if all(checks.values()):
                prior = history(args.project_directory)
                checks["resume_boundary_recorded"] = (
                    prior is not None
                    and prior["pause_started_at"] is not None
                    and replace_history(
                        args.project_directory,
                        {
                            "schema": 1,
                            "pause_started_at": prior["pause_started_at"],
                            "last_resumed_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                )
                checks["resumed"] = checks[
                    "resume_boundary_recorded"
                ] and replace_state(args.project_directory, "active")
            report["status"] = "PASS" if all(checks.values()) else "STOP"
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("inspect", "pause", "drain", "resume"))
    parser.add_argument("--project-directory", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--confirm", choices=("pause", "resume"))
    parser.add_argument("--bearer-report", type=Path, action="append", default=[])
    parser.add_argument("--monitor-report", type=Path)
    args = parser.parse_args(argv)
    try:
        if (
            not PAUSE.GATE.protected(args.report.parent, 0, directory=True)
            or args.report.exists()
            or args.report.is_symlink()
        ):
            return 1
        PAUSE.write_private(
            args.report,
            {
                "phase": f"maintenance-{args.phase}",
                "status": "INCOMPLETE",
                "checks": {},
            },
        )
    except (OSError, ValueError, TypeError, KeyError):
        return 1
    try:
        result = run(args)
    except (Exception, KeyboardInterrupt):
        result = {
            "phase": f"maintenance-{args.phase}",
            "status": "STOP",
            "checks": {"interrupted_or_failed": False},
        }
    try:
        PAUSE.replace_private(args.report, result)
    except OSError:
        return 1
    print(result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
