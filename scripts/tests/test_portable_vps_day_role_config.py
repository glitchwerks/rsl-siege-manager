"""Safety boundaries for a staged, configuration-only day-role change."""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "portable-vps-day-role-config.py"
SPEC = importlib.util.spec_from_file_location("day_role_config_under_test", SOURCE)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)

DISABLED = (b"SESSION_SECRET=unchanged-secret\n"
            b"DAY_ROLE_SYNC_ENABLED=false\n"
            b"# DAY_ROLE_SYNC_URL=example\n")
VALUES = {"DAY_ROLE_SYNC_ENABLED": "true",
          "DAY_ROLE_SYNC_URL": "https://siege.example.net/api/internal/role-sync",
          "DISCORD_DAY_1_ROLE_ID": "12345678901234567",
          "DISCORD_DAY_2_ROLE_ID": "12345678901234568"}


def test_candidate_only_changes_role_settings_and_preserves_other_bytes():
    candidate = RUNNER.changed(DISABLED, VALUES)
    assert candidate.startswith(b"SESSION_SECRET=unchanged-secret\n")
    assert RUNNER.role_delta(DISABLED, candidate, "true") == VALUES
    assert RUNNER.changed(candidate, {"DAY_ROLE_SYNC_ENABLED": "false"}) != DISABLED


@pytest.mark.parametrize("tamper", [
    lambda raw: raw.replace(b"unchanged-secret", b"different-secret"),
    lambda raw: raw + b"DAY_ROLE_SYNC_ENABLED=true\n",
    lambda raw: raw.replace(b"12345678901234568", b"12345678901234567"),
    lambda raw: raw.replace(b"DAY_ROLE_SYNC_URL=", b"export DAY_ROLE_SYNC_URL="),
])
def test_stage_rejects_tampered_candidate(tamper):
    candidate = RUNNER.changed(DISABLED, VALUES)
    with pytest.raises(ValueError):
        RUNNER.role_delta(DISABLED, tamper(candidate), "true")


def test_disable_keeps_role_mapping_and_other_runtime_values():
    enabled = RUNNER.changed(DISABLED, VALUES)
    disabled = RUNNER.changed(enabled, {"DAY_ROLE_SYNC_ENABLED": "false"})
    assert RUNNER.role_delta(enabled, disabled, "false") == {"DAY_ROLE_SYNC_ENABLED": "false"}
    with pytest.raises(ValueError):
        RUNNER.role_delta(enabled, disabled.replace(b"12345678901234568", b"12345678901234569"), "false")


def test_candidate_model_updates_backend_and_migration_service(monkeypatch):
    current = {"services": {"backend": {"environment": {"DAY_ROLE_SYNC_ENABLED": "false"}},
                            "migrate-siege": {"environment": {"DAY_ROLE_SYNC_ENABLED": "false"}},
                            "mom": {"environment": {"OTHER": "unchanged"}}}}
    monkeypatch.setattr(RUNNER.PREFLIGHT, "validate", lambda *_: {"valid": True})
    proposed = RUNNER.proposed_model(current, VALUES, {}, Path("/unused"))
    assert all(proposed["services"][name]["environment"]["DAY_ROLE_SYNC_ENABLED"] == "true"
               for name in ("backend", "migrate-siege"))
    assert current["services"]["backend"]["environment"]["DAY_ROLE_SYNC_ENABLED"] == "false"
    assert proposed["services"]["mom"] == current["services"]["mom"]


def test_recovery_cannot_reenable_prior_flag_without_separate_safety_decision(monkeypatch, tmp_path):
    baseline = {"project": "test", "installed": {}}
    monkeypatch.setattr(RUNNER, "load", lambda *_: (
        {"old_enabled": "true"}, baseline, b"old", b"candidate", b"mom", {}))
    monkeypatch.setattr(RUNNER.PAUSE, "project_lock", lambda *_: pytest.fail("lock acquired"))
    with pytest.raises(ValueError, match="separate safety decision"):
        RUNNER.recover(tmp_path, tmp_path / "stage", tmp_path / "public.json")


def test_cli_rejects_missing_phase_confirmation_before_stage(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(RUNNER.os, "geteuid", lambda: 0)
    monkeypatch.setattr(RUNNER, "stage", lambda *args: pytest.fail("stage called"))
    code = RUNNER.main(["stage", "--project-directory", str(tmp_path),
                        "--decision-file", str(tmp_path / "decision.json"),
                        "--evidence-directory", str(tmp_path)])
    assert code == 1
    assert '"status": "STOP"' in capsys.readouterr().out


def test_input_drift_guard_excludes_only_the_changed_backend_file(monkeypatch, tmp_path):
    expected = {"runtime": {"backend.env": "old", "mom.env": "same", "database.env": "db"},
                "config": {"compose.yml": "same"}}
    monkeypatch.setattr(RUNNER.GATE, "read_private", lambda *_: json.dumps(expected).encode())
    monkeypatch.setattr(RUNNER, "input_hashes", lambda *_: {
        "runtime": {"backend.env": "candidate", "mom.env": "same", "database.env": "db"},
        "config": {"compose.yml": "same"}})
    assert RUNNER.inputs_current(tmp_path, tmp_path, 0, include_backend=False)
    assert not RUNNER.inputs_current(tmp_path, tmp_path, 0, include_backend=True)
    monkeypatch.setattr(RUNNER, "input_hashes", lambda *_: {
        "runtime": {"backend.env": "candidate", "mom.env": "changed", "database.env": "db"},
        "config": {"compose.yml": "same"}})
    assert not RUNNER.inputs_current(tmp_path, tmp_path, 0, include_backend=False)


def test_recovery_drain_accepts_verified_stopped_backend_and_paused_mom(monkeypatch, tmp_path):
    baseline = {"project": "test", "installed": {"backend": {"image": "sha256:reviewed"}}}
    monkeypatch.setattr(RUNNER.PAUSE, "container", lambda *args: {
        "id": "backend-id", "running": False, "status": "exited"})
    monkeypatch.setattr(RUNNER.PAUSE, "identifiers", lambda *args: "backend-id")
    monkeypatch.setattr(RUNNER.PAUSE, "command", lambda *args: b'"sha256:reviewed"')
    monkeypatch.setattr(RUNNER.ROTATION.MAINT, "state", lambda *_: "paused")
    calls = []
    monkeypatch.setattr(RUNNER.ROTATION.MAINT, "service_status",
                        lambda *args, **kwargs: calls.append(args[1]) or
                        {"paused": True, "inflight": 0})
    monkeypatch.setattr(RUNNER.time, "sleep", lambda *_: None)
    assert RUNNER.recovery_drain(tmp_path, baseline)
    assert calls == ["mom", "mom", "mom"]


def test_recovery_drain_rejects_wrong_backend_image(monkeypatch, tmp_path):
    baseline = {"project": "test", "installed": {"backend": {"image": "sha256:reviewed"}}}
    monkeypatch.setattr(RUNNER.PAUSE, "container", lambda *args: {
        "id": "backend-id", "running": False, "status": "exited"})
    monkeypatch.setattr(RUNNER.PAUSE, "identifiers", lambda *args: "backend-id")
    monkeypatch.setattr(RUNNER.PAUSE, "command", lambda *args: b'"sha256:other"')
    monkeypatch.setattr(RUNNER.ROTATION.MAINT, "service_status",
                        lambda *args, **kwargs: pytest.fail("queried unexpected Mom"))
    assert not RUNNER.recovery_drain(tmp_path, baseline)


@pytest.mark.parametrize("compose_ok", [False, True])
def test_activation_recreates_only_backend_and_isolates_failed_path(monkeypatch, tmp_path, compose_ok):
    project = tmp_path / "project"
    stage = tmp_path / "stage"
    baseline = {"project": "test", "installed": {"backend": {"id": "old-id"}}}
    files = {"backend.env": DISABLED, "mom.env": b"mom"}
    current = {"services": {"backend": {"environment": {"DAY_ROLE_SYNC_ENABLED": "false"}}}}
    accepted = {"old_enabled": "false", "new_enabled": "true"}
    calls = []
    monkeypatch.setattr(RUNNER, "load", lambda *_: (accepted, baseline, DISABLED,
                                                      b"candidate", b"mom", VALUES))
    monkeypatch.setattr(RUNNER.PAUSE, "project_lock", lambda *_: nullcontext())
    monkeypatch.setattr(RUNNER.ROTATION, "project_snapshot", lambda *_: (files, baseline))
    monkeypatch.setattr(RUNNER, "model", lambda *_: (current, {}))
    monkeypatch.setattr(RUNNER.ROTATION, "stack_current", lambda *_: True)
    monkeypatch.setattr(RUNNER, "inputs_current", lambda *_, **__: True)
    monkeypatch.setattr(RUNNER.EFFECTIVE, "inspect", lambda *_: {"valid": True})
    monkeypatch.setattr(RUNNER.ROTATION, "require_pause", lambda *_, **__: None)
    monkeypatch.setattr(RUNNER.ROTATION.MAINT, "history", lambda *_: {"pause_started_at": "fixed"})
    monkeypatch.setattr(RUNNER.ROTATION, "write_result", lambda *args: calls.append("marker"))
    monkeypatch.setattr(RUNNER, "stop_backend", lambda *args: calls.append("stop") or True)
    monkeypatch.setattr(RUNNER, "replace_backend", lambda *args: calls.append("replace"))
    monkeypatch.setattr(RUNNER, "proposed_model", lambda *args: {"candidate": True})
    monkeypatch.setattr(RUNNER.PREFLIGHT, "render", lambda *args: {"candidate": True})
    monkeypatch.setattr(RUNNER.GATE, "read_private", lambda *args: b"candidate")
    monkeypatch.setattr(RUNNER, "compose_backend", lambda *_: calls.append("compose") or compose_ok)
    monkeypatch.setattr(RUNNER, "verify", lambda *args: True)
    monkeypatch.setattr(RUNNER.ROTATION, "public_healthy", lambda *args: True)
    monkeypatch.setattr(RUNNER, "proof_current", lambda *args: True)

    result = RUNNER.activate(project, stage, tmp_path / "public.json", tmp_path / "backup-profile.json")
    assert calls[:4] == ["marker", "stop", "replace", "compose"]
    assert result["status"] == ("PASS" if compose_ok else "STOP")
    assert calls.count("stop") == (1 if compose_ok else 2)


def test_activation_refuses_changed_backup_proof_before_stopping_backend(monkeypatch, tmp_path):
    project, stage = tmp_path / "project", tmp_path / "stage"
    baseline = {"project": "test", "installed": {"backend": {"id": "old-id"}}}
    current = {"services": {"backend": {"environment": {"DAY_ROLE_SYNC_ENABLED": "false"}}}}
    monkeypatch.setattr(RUNNER, "load", lambda *_: (
        {"old_enabled": "false", "new_enabled": "true"}, baseline, DISABLED,
        b"candidate", b"mom", VALUES))
    monkeypatch.setattr(RUNNER.PAUSE, "project_lock", lambda *_: nullcontext())
    monkeypatch.setattr(RUNNER.ROTATION, "project_snapshot", lambda *_: (
        {"backend.env": DISABLED, "mom.env": b"mom"}, baseline))
    monkeypatch.setattr(RUNNER, "model", lambda *_: (current, {}))
    monkeypatch.setattr(RUNNER, "proposed_model", lambda *args: current)
    monkeypatch.setattr(RUNNER.ROTATION, "stack_current", lambda *_: True)
    monkeypatch.setattr(RUNNER, "inputs_current", lambda *_, **__: True)
    monkeypatch.setattr(RUNNER.EFFECTIVE, "inspect", lambda *_: {"valid": True})
    monkeypatch.setattr(RUNNER.ROTATION, "require_pause", lambda *_, **__: None)
    monkeypatch.setattr(RUNNER, "proof_current", lambda *_: False)
    monkeypatch.setattr(RUNNER, "stop_backend", lambda *_: pytest.fail("backend was stopped"))
    with pytest.raises(ValueError, match="backup proof changed"):
        RUNNER.activate(project, stage, tmp_path / "public.json", tmp_path / "profile.json")


def test_proof_requires_backup_after_current_completed_drain(monkeypatch, tmp_path):
    project, stage = tmp_path / "project", tmp_path / "stage"
    (project / "control").mkdir(parents=True)
    stage.mkdir()
    backup_id = "11111111-1111-4111-8111-111111111111"
    backup_root = tmp_path / "backups"
    backup = backup_root / "runs" / backup_id / "job.json"
    recover, restore = tmp_path / "recover.json", tmp_path / "restore.json"
    pause = datetime.now(timezone.utc) - timedelta(minutes=30)
    history = {"schema": 1, "pause_started_at": (pause - timedelta(days=1)).isoformat(),
               "last_resumed_at": (pause - timedelta(hours=1)).isoformat()}
    state_file = project / "control" / "state"
    state_file.write_text("paused\n")
    maintenance_report = stage / "pause-report.json"
    maintenance_report.write_text("private test report\n")
    os.utime(state_file, (pause.timestamp(), pause.timestamp()))
    drained_at = pause + timedelta(minutes=2)
    os.utime(maintenance_report, (drained_at.timestamp(), drained_at.timestamp()))
    reports = {"pause-report.json": {"phase": "maintenance-pause", "status": "PASS",
                                      "checks": {"paused": True, "drained": True}},
               "job.json": {"timestamp": (pause + timedelta(minutes=1)).isoformat(),
                            "run_id": backup_id},
               "recover.json": {}, "restore.json": {}}
    baseline = {"project": "test", "installed": {}}
    candidate_bytes = {"value": b"candidate-role-1"}
    monkeypatch.setattr(RUNNER, "load", lambda *_: (
        {"old_enabled": "false"}, baseline, DISABLED, candidate_bytes["value"], b"mom", {}))
    monkeypatch.setattr(RUNNER.ROTATION, "require_pause", lambda *_, **__: None)
    monkeypatch.setattr(RUNNER.ROTATION.MAINT, "history", lambda *_: history)
    monkeypatch.setattr(RUNNER.ROTATION.MAINT, "state", lambda *_: "paused")
    monkeypatch.setattr(RUNNER.BACKUP_PROFILE, "profile", lambda *_: (
        {"project_directory": project, "backup_owner": "root", "backup_root": backup_root}, "profile-hash"))
    monkeypatch.setattr(RUNNER.ROTATION, "project_snapshot", lambda *_: (
        {"backend.env": DISABLED, "mom.env": b"mom"}, baseline))
    monkeypatch.setattr(RUNNER, "model", lambda *_: (
        {"services": {"backend": {"environment": {"DAY_ROLE_SYNC_ENABLED": "false"}}}}, {}))
    monkeypatch.setattr(RUNNER.ROTATION, "stack_current", lambda *_: True)
    monkeypatch.setattr(RUNNER, "inputs_current", lambda *_, **__: True)
    monkeypatch.setattr(RUNNER.EFFECTIVE, "inspect", lambda *_: {"valid": True})
    monkeypatch.setattr(RUNNER.GATE, "read_private", lambda path, *_: json.dumps(reports[path.name]).encode())
    monkeypatch.setattr(RUNNER.BACKUP_EVIDENCE, "inspect", lambda *_, **__: {"valid": True})
    with pytest.raises(ValueError, match="backup proof or pause changed"):
        RUNNER.proof_snapshot(project, stage, tmp_path / "profile.json", maintenance_report,
                              backup, recover, restore)
    with pytest.raises(ValueError, match="backup outside managed run"):
        RUNNER.proof_snapshot(project, stage, tmp_path / "profile.json", maintenance_report,
                              backup.parent / "other.json", recover, restore)
    reports["job.json"]["timestamp"] = (pause + timedelta(minutes=3)).isoformat()
    accepted = RUNNER.proof_snapshot(project, stage, tmp_path / "profile.json", maintenance_report,
                                     backup, recover, restore)
    assert accepted["stage_id"] == stage.name
    assert accepted["pause_history"] == history
    assert set(accepted["reports"]) == {"backup", "recover", "restore"}
    candidate_bytes["value"] = b"candidate-role-2"
    changed_candidate = RUNNER.proof_snapshot(project, stage, tmp_path / "profile.json",
                                              maintenance_report, backup, recover, restore)
    assert changed_candidate["candidate_sha256"] != accepted["candidate_sha256"]
    candidate_bytes["value"] = b"candidate-role-1"
    later = drained_at + timedelta(minutes=1)
    os.utime(state_file, (later.timestamp(), later.timestamp()))
    with pytest.raises(ValueError, match="predates current pause"):
        RUNNER.proof_snapshot(project, stage, tmp_path / "profile.json", maintenance_report,
                              backup, recover, restore)
    os.utime(state_file, (pause.timestamp(), pause.timestamp()))
    reports["pause-report.json"]["checks"]["drained"] = False
    with pytest.raises(ValueError, match="drain was not accepted"):
        RUNNER.proof_snapshot(project, stage, tmp_path / "profile.json", maintenance_report,
                              backup, recover, restore)
    reports["pause-report.json"]["checks"]["drained"] = True
    reads = {"job.json": 0}
    def changed_report(path, *_):
        if path.name == "job.json":
            reads["job.json"] += 1
            if reads["job.json"] > 1:
                return b'{"changed":true}'
        return json.dumps(reports[path.name]).encode()
    monkeypatch.setattr(RUNNER.GATE, "read_private", changed_report)
    with pytest.raises(ValueError, match="backup proof or pause changed"):
        RUNNER.proof_snapshot(project, stage, tmp_path / "profile.json", maintenance_report,
                              backup, recover, restore)


def test_accepted_proof_rejects_changed_record(monkeypatch, tmp_path):
    stage = tmp_path / "11111111-1111-4111-8111-111111111111"
    backup = "/protected/backup/runs/22222222-2222-4222-8222-222222222222/job.json"
    record = {"phase": "day-role-proof", "status": "PASS", "proof": {
        "schema": 1, "stage_id": stage.name, "pause_history": {},
        "profile_sha256": "a" * 64,
        "candidate_sha256": "e" * 64,
        "maintenance": {"path": str(stage / "pause-report.json"), "sha256": "d" * 64},
        "reports": {name: {"path": path, "sha256": "b" * 64} for name, path in (
            ("backup", backup), ("recover", "/protected/recover.json"),
            ("restore", "/protected/restore.json"))}}}
    monkeypatch.setattr(RUNNER.GATE, "read_private", lambda *args: json.dumps(record).encode())
    monkeypatch.setattr(RUNNER, "proof_snapshot", lambda *args: record["proof"])
    assert RUNNER.proof_current(tmp_path, stage, tmp_path / "profile.json")
    monkeypatch.setattr(RUNNER, "proof_snapshot", lambda *args: {**record["proof"],
        "candidate_sha256": "f" * 64})
    assert not RUNNER.proof_current(tmp_path, stage, tmp_path / "profile.json")
    record["proof"]["profile_sha256"] = "c" * 64
    monkeypatch.setattr(RUNNER, "proof_snapshot", lambda *args: {**record["proof"],
        "profile_sha256": "a" * 64})
    assert not RUNNER.proof_current(tmp_path, stage, tmp_path / "profile.json")
    record["proof"]["reports"]["backup"]["sha256"] = "invalid"
    with pytest.raises(ValueError, match="invalid proof selector"):
        RUNNER.proof_current(tmp_path, stage, tmp_path / "profile.json")


def test_failed_recovery_verification_stops_backend_again(monkeypatch, tmp_path):
    project, stage = tmp_path / "project", tmp_path / "stage"
    baseline = {"project": "test", "installed": {"backend": {"id": "old-id"},
                                                 "mom": {"id": "mom-id"}}}
    history = {"pause_started_at": "fixed", "last_resumed_at": None}
    calls = []
    active = {"backend": b"candidate"}
    monkeypatch.setattr(RUNNER, "load", lambda *_: (
        {"old_enabled": "false"}, baseline, b"old", b"candidate", b"mom", {}))
    monkeypatch.setattr(RUNNER.GATE, "read_private", lambda path, *_: (
        json.dumps({"phase": "day-role-activate", "status": "INCOMPLETE",
                    "pause_history": history}).encode()
        if path.name == "activation-incomplete.json" else active["backend"]
        if path.name == "backend.env" else b"mom"))
    monkeypatch.setattr(RUNNER.PAUSE, "project_lock", lambda *_: nullcontext())
    monkeypatch.setattr(RUNNER.ROTATION, "require_pause", lambda *_, **__: None)
    monkeypatch.setattr(RUNNER.ROTATION.MAINT, "history", lambda *_: history)
    monkeypatch.setattr(RUNNER, "inputs_current", lambda *_, **__: True)
    monkeypatch.setattr(RUNNER, "recovery_drain", lambda *args: True)
    monkeypatch.setattr(RUNNER.ROTATION, "configuration_current", lambda *_: True)
    monkeypatch.setattr(RUNNER.PAUSE, "identifiers", lambda *args: "mom-id")
    monkeypatch.setattr(RUNNER.PAUSE, "container", lambda *args: {"running": True, "healthy": True})
    monkeypatch.setattr(RUNNER.ROTATION, "write_result", lambda *args: calls.append("marker"))
    monkeypatch.setattr(RUNNER, "stop_backend", lambda *args: calls.append("stop") or True)
    def replace_backend(*args):
        calls.append("replace")
        active["backend"] = b"old"
    monkeypatch.setattr(RUNNER, "replace_backend", replace_backend)
    monkeypatch.setattr(RUNNER, "compose_backend", lambda *_: calls.append("compose") or True)
    monkeypatch.setattr(RUNNER, "verify", lambda *args: False)
    monkeypatch.setattr(RUNNER.ROTATION, "public_healthy", lambda *args: True)

    result = RUNNER.recover(project, stage, tmp_path / "public.json")
    assert result["status"] == "STOP"
    assert calls.count("stop") == 2
    assert calls.index("stop") < calls.index("replace") < calls.index("compose")

