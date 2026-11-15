"""Safety boundaries for a staged, configuration-only day-role change."""

from contextlib import nullcontext
import importlib.util
import json
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

    result = RUNNER.activate(project, stage, tmp_path / "public.json")
    assert calls[:4] == ["marker", "stop", "replace", "compose"]
    assert result["status"] == ("PASS" if compose_ok else "STOP")
    assert calls.count("stop") == (1 if compose_ok else 2)


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

