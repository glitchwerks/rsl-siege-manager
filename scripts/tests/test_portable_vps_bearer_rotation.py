"""Disposable file tests for portable bearer staging; no Docker or VPS access."""

import importlib.util
from contextlib import contextmanager
import os
from pathlib import Path
from unittest.mock import patch

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-bearer-rotation.py"
SPEC = importlib.util.spec_from_file_location("bearer_rotation", SCRIPT)
ROTATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ROTATE)


@pytest.fixture(autouse=True)
def paused_peer_contract():
    with patch.object(ROTATE.MAINT, "state", return_value="paused"), patch.object(
        ROTATE.MAINT, "history", return_value={"schema": 1, "pause_started_at": "2026-11-15T00:00:00+00:00"}
    ), patch.object(ROTATE.MAINT, "drain", return_value=True):
        yield


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir(mode=0o700)
    runtime = root / "runtime"
    runtime.mkdir(mode=0o700)
    evidence = root / "evidence"
    evidence.mkdir(mode=0o700)
    (root / "stack.env").write_text("COMPOSE_PROJECT_NAME=isolated\n")
    (root / "stack.env").chmod(0o600)
    backend = (b"# preserved\r\nDISCORD_BOT_API_KEY=sidecar-old\r\n"
               b"BOT_SERVICE_TOKEN=reverse-old\nUNRELATED=keep exactly\n")
    mom = (b"MOM_BOT_SECRET_DISCORD_BOT_API_KEY=sidecar-old\n"
           b"MOM_BOT_SECRET_SIEGE_WEB_BOT_TOKEN=reverse-old\nOTHER=value\n")
    (runtime / "backend.env").write_bytes(backend)
    (runtime / "mom.env").write_bytes(mom)
    for name in ROTATE.FILES:
        (runtime / name).chmod(0o600)
    return root, evidence, {"backend.env": backend, "mom.env": mom}


@pytest.mark.parametrize("pair", sorted(ROTATE.PAIRS))
def test_stage_preserves_runtime_and_other_pair(project, pair):
    root, evidence, before = project
    owner = os.geteuid()
    baseline = {"project": "isolated", "installed": {"backend": "exact", "mom": "exact"}}
    with patch.object(ROTATE, "project_snapshot", return_value=(before, baseline)):
        result = ROTATE.stage(root, pair, evidence, owner=owner)

    assert result["status"] == "PASS"
    assert ROTATE.read_runtime(root, owner) == before
    stage = evidence / result["run_id"]
    proposed = {name: (stage / f"candidate-{name}").read_bytes() for name in ROTATE.FILES}
    assert ROTATE.pairs_match(proposed)
    selected = ROTATE.pair_values(proposed)[pair]
    assert selected[0] == selected[1]
    assert selected[0] != ROTATE.pair_values(before)[pair][0]
    other = next(name for name in ROTATE.PAIRS if name != pair)
    assert ROTATE.pair_values(proposed)[other] == ROTATE.pair_values(before)[other]
    for name in ROTATE.FILES:
        assert (stage / f"rollback-{name}").read_bytes() == before[name]
        assert (stage / f"candidate-{name}").stat().st_mode & 0o077 == 0
    assert b"UNRELATED=keep exactly\n" in proposed["backend.env"]
    report = (stage / "accepted.json").read_bytes()
    assert b"sidecar-old" not in report and b"reverse-old" not in report
    assert selected[0] not in report


def test_inspect_writes_only_sanitized_checks(project):
    root, evidence, before = project
    baseline = {"project": "isolated", "installed": {"backend": "exact", "mom": "exact"}}
    path = evidence / "inspection.json"
    with patch.object(ROTATE, "project_snapshot", return_value=(before, baseline)):
        result = ROTATE.inspect(root, path, owner=os.geteuid())
    assert result["status"] == "PASS"
    assert all(result["checks"].values())
    raw = path.read_bytes()
    assert b"sidecar-old" not in raw and b"reverse-old" not in raw


def test_stage_refuses_drift_without_accepted_candidate(project):
    root, evidence, before = project
    changed = dict(before)
    changed["mom.env"] += b"DRIFT=yes\n"
    baseline = {"project": "isolated", "installed": {"backend": "exact", "mom": "exact"}}
    with patch.object(ROTATE, "project_snapshot", side_effect=[(before, baseline), (changed, baseline)]):
        with pytest.raises(ValueError, match="staging validation failed"):
            ROTATE.stage(root, "sidecar", evidence, owner=os.geteuid())
    assert ROTATE.read_runtime(root, os.geteuid()) == before
    stage = next(evidence.iterdir())
    assert (stage / "stopped.json").is_file()
    assert not (stage / "accepted.json").exists()
    assert not (stage / "candidate-backend.env").exists()


@pytest.mark.parametrize("invalid", [
    b"DISCORD_BOT_API_KEY=one\nDISCORD_BOT_API_KEY=two\nBOT_SERVICE_TOKEN=reverse-old\n",
    b"DISCORD_BOT_API_KEY=\nBOT_SERVICE_TOKEN=reverse-old\n",
    b"export DISCORD_BOT_API_KEY=one\nBOT_SERVICE_TOKEN=reverse-old\n",
    b"DISCORD_BOT_API_KEY=one\nexport DISCORD_BOT_API_KEY=two\nBOT_SERVICE_TOKEN=reverse-old\n",
    b"DISCORD_BOT_API_KEY='quoted'\nBOT_SERVICE_TOKEN=reverse-old\n",
    b"DISCORD_BOT_API_KEY=raw # comment\nBOT_SERVICE_TOKEN=reverse-old\n",
    b"DISCORD_BOT_API_KEY=${TOKEN}\nBOT_SERVICE_TOKEN=reverse-old\n",
])
def test_ambiguous_bearer_assignment_stops(invalid):
    with pytest.raises(ValueError):
        ROTATE.parse_env(invalid, {"DISCORD_BOT_API_KEY", "BOT_SERVICE_TOKEN"})


def test_dirty_source_checkout_is_rejected():
    with patch.object(ROTATE.PAUSE, "command", side_effect=["a" * 40, " M scripts/runner.py"]):
        with pytest.raises(ValueError, match="unreviewed checkout"):
            ROTATE.reviewed_checkout()


def test_checkout_trust_is_scoped_to_exact_reviewed_repository():
    with patch.object(ROTATE.PAUSE, "command", side_effect=["a" * 40, ""]) as command:
        assert ROTATE.reviewed_checkout() == "a" * 40
    for call in command.call_args_list:
        args = call.args[0]
        assert args[:5] == ["git", "-c", f"safe.directory={ROTATE.PREFLIGHT.PREFLIGHT.ROOT}",
                            "-C", str(ROTATE.PREFLIGHT.PREFLIGHT.ROOT)]


def test_result_publication_does_not_leave_partial_target(project):
    _root, evidence, _before = project
    target = evidence / "result.json"
    with patch.object(ROTATE.os, "link", side_effect=OSError("synthetic interruption")):
        with pytest.raises(OSError):
            ROTATE.write_result(target, b'{"status":"PASS"}\n', os.geteuid())
    assert not target.exists()
    assert not list(evidence.glob(".result.json.*"))
    ROTATE.write_result(target, b'{"status":"STOP"}\n', os.geteuid())
    with pytest.raises(FileExistsError):
        ROTATE.write_result(target, b'{"status":"PASS"}\n', os.geteuid())
    assert target.read_bytes() == b'{"status":"STOP"}\n'


def test_second_running_mom_refuses_single_bot_topology():
    with (patch.object(ROTATE.PAUSE, "command", return_value="a" * 64 + "\n" + "b" * 64),
          patch.object(ROTATE.PAUSE, "identifiers", return_value="a" * 64)):
        assert not ROTATE.single_mom_container("isolated")


def test_public_health_is_bound_to_project_hostname(project):
    root, evidence, _files = project
    stack = root / "stack.env"
    stack.write_text("PUBLIC_HOST=siege.example.net\n")
    stack.chmod(0o600)
    config = evidence / "public.json"
    config.write_text("{}")
    config.chmod(0o600)
    checks = dict.fromkeys(ROTATE.PUBLIC.CHECKS, True)
    with (patch.object(ROTATE.PUBLIC, "config", return_value={"host": "other.example.net"}),
          patch.object(ROTATE.PUBLIC, "inspect") as inspect):
        assert not ROTATE.public_healthy(root, config, os.geteuid())
    inspect.assert_not_called()
    with (patch.object(ROTATE.PUBLIC, "config", return_value={"host": "siege.example.net"}),
          patch.object(ROTATE.PUBLIC, "inspect", return_value=checks)):
        assert ROTATE.public_healthy(root, config, os.geteuid())


@contextmanager
def _lock(_project):
    yield


def _activation_fixture(project):
    root, evidence, rollback = project
    directory = evidence / "00000000-0000-4000-8000-000000000001"
    directory.mkdir(mode=0o700)
    (directory / "baseline-stack.env").write_bytes((root / "stack.env").read_bytes())
    (directory / "baseline-stack.env").chmod(0o600)
    candidate = {
        "backend.env": ROTATE.replace_assignment(
            rollback["backend.env"], "DISCORD_BOT_API_KEY", b"sidecar-new"),
        "mom.env": ROTATE.replace_assignment(
            rollback["mom.env"], "MOM_BOT_SECRET_DISCORD_BOT_API_KEY", b"sidecar-new"),
    }
    baseline = {"project": "isolated", "source_sha": "a" * 40,
                "installed": {"backend": {"id": "old-backend", "image": "exact"},
                              "mom": {"id": "old-mom", "image": "exact"}}}
    return root, directory, rollback, candidate, baseline


def test_activate_recreates_both_under_project_lock(project):
    root, directory, rollback, candidate, baseline = _activation_fixture(project)
    current = dict(rollback)
    actions = []

    def replace(_project, files, _owner):
        actions.append("replace")
        current.update(files)

    with (patch.object(ROTATE, "staged", return_value=("sidecar", rollback, candidate, baseline)),
          patch.object(ROTATE.PAUSE, "project_lock", side_effect=_lock),
          patch.object(ROTATE, "project_snapshot", return_value=(rollback, baseline)),
          patch.object(ROTATE, "stop_pair", side_effect=lambda _name, *_args: actions.append("stop") or True),
          patch.object(ROTATE, "stopped", return_value=True),
          patch.object(ROTATE, "replace_runtime", side_effect=replace),
          patch.object(ROTATE, "read_runtime", side_effect=lambda *_args: dict(current)),
          patch.object(ROTATE, "compose_up", side_effect=lambda _project: actions.append("up") or True),
          patch.object(ROTATE, "verified", return_value=True),
          patch.object(ROTATE, "public_healthy", return_value=True)):
        result = ROTATE.activate(root, directory, owner=os.geteuid())
    assert result["status"] == "PASS"
    assert actions == ["stop", "replace", "up"]
    assert current == candidate


def test_activate_refuses_drift_before_mutation(project):
    root, directory, rollback, candidate, baseline = _activation_fixture(project)
    with (patch.object(ROTATE, "staged", return_value=("sidecar", rollback, candidate, baseline)),
          patch.object(ROTATE.PAUSE, "project_lock", side_effect=_lock),
          patch.object(ROTATE, "project_snapshot", return_value=(candidate, baseline)),
          patch.object(ROTATE, "stop_pair") as stop):
        with pytest.raises(ValueError, match="baseline drift"):
            ROTATE.activate(root, directory, owner=os.geteuid())
    stop.assert_not_called()
    assert not (directory / "activation-incomplete.json").exists()


@pytest.mark.parametrize("state,drained", [("active", True), ("paused", False)])
def test_activate_requires_paused_and_drained_peers(project, state, drained):
    root, directory, rollback, candidate, baseline = _activation_fixture(project)
    with (patch.object(ROTATE, "staged", return_value=("sidecar", rollback, candidate, baseline)),
          patch.object(ROTATE.PAUSE, "project_lock", side_effect=_lock),
          patch.object(ROTATE, "project_snapshot", return_value=(rollback, baseline)),
          patch.object(ROTATE.MAINT, "state", return_value=state),
          patch.object(ROTATE.MAINT, "drain", return_value=drained),
          patch.object(ROTATE, "stop_pair") as stop):
        with pytest.raises(ValueError, match="maintenance"):
            ROTATE.activate(root, directory, owner=os.geteuid())
    stop.assert_not_called()
    assert not (directory / "activation-incomplete.json").exists()


def test_activate_rejects_stack_configuration_drift(project):
    root, directory, rollback, candidate, baseline = _activation_fixture(project)
    (root / "stack.env").write_text("COMPOSE_PROJECT_NAME=isolated\nPROXY_IP=10.0.0.4\n")
    (root / "stack.env").chmod(0o600)
    with (patch.object(ROTATE, "staged", return_value=("sidecar", rollback, candidate, baseline)),
          patch.object(ROTATE.PAUSE, "project_lock", side_effect=_lock),
          patch.object(ROTATE, "project_snapshot", return_value=(rollback, baseline)),
          patch.object(ROTATE, "stop_pair") as stop):
        with pytest.raises(ValueError, match="baseline drift"):
            ROTATE.activate(root, directory, owner=os.geteuid())
    stop.assert_not_called()
    assert not (directory / "activation-incomplete.json").exists()


def test_pause_without_durable_boundary_is_rejected(project):
    root, _evidence, _before = project
    with patch.object(ROTATE.MAINT, "history", return_value={"pause_started_at": None}):
        with pytest.raises(ValueError, match="maintenance pause missing"):
            ROTATE.require_pause(root, drain=True)


def test_interrupted_activation_cannot_be_replayed(project):
    root, directory, rollback, candidate, baseline = _activation_fixture(project)
    ROTATE.write_private(directory / "activation-incomplete.json",
                         b'{"phase":"bearer-activate","status":"STOP"}\n', os.geteuid())
    with patch.object(ROTATE, "staged", return_value=("sidecar", rollback, candidate, baseline)):
        with pytest.raises(ValueError, match="already attempted"):
            ROTATE.activate(root, directory, owner=os.geteuid())


def test_partial_activation_keeps_path_isolated_for_separate_recovery(project):
    root, directory, rollback, candidate, baseline = _activation_fixture(project)
    current = dict(rollback)
    writes = []

    def replace(_project, files, _owner):
        writes.append(files)
        current.update(files)
        if files is candidate:
            raise ValueError("synthetic partial failure")

    with (patch.object(ROTATE, "staged", return_value=("sidecar", rollback, candidate, baseline)),
          patch.object(ROTATE.PAUSE, "project_lock", side_effect=_lock),
          patch.object(ROTATE, "project_snapshot", return_value=(rollback, baseline)),
          patch.object(ROTATE, "stop_pair", return_value=True),
          patch.object(ROTATE, "isolated", return_value=True),
          patch.object(ROTATE, "stopped", return_value=True),
          patch.object(ROTATE, "replace_runtime", side_effect=replace),
          patch.object(ROTATE, "read_runtime", side_effect=lambda *_args: dict(current)),
          patch.object(ROTATE, "compose_up", return_value=True),
          patch.object(ROTATE, "verified", return_value=True),
          patch.object(ROTATE, "public_healthy", return_value=True)):
        result = ROTATE.activate(root, directory, owner=os.geteuid())
    assert result["status"] == "STOP"
    assert result["checks"]["path_isolated_after_failure"] is True
    assert writes == [candidate]
    assert current == candidate


def test_routine_recovery_restores_both_files_after_failed_activation(project):
    root, directory, rollback, candidate, baseline = _activation_fixture(project)
    ROTATE.write_private(directory / "activation-incomplete.json",
                         b'{"phase":"bearer-activate","status":"STOP"}\n', os.geteuid())
    current = dict(candidate)
    replacements = []

    def replace(_project, files, _owner):
        replacements.append(files)
        current.update(files)

    with (patch.object(ROTATE, "staged", return_value=("sidecar", rollback, candidate, baseline)),
          patch.object(ROTATE.PAUSE, "project_lock", side_effect=_lock),
          patch.object(ROTATE, "configuration_current", return_value=True),
          patch.object(ROTATE.PAUSE, "no_extra_writers", return_value=True),
          patch.object(ROTATE, "read_runtime", side_effect=lambda *_args: dict(current)),
          patch.object(ROTATE, "stop_pair", return_value=True),
          patch.object(ROTATE, "isolated", return_value=True),
          patch.object(ROTATE, "replace_runtime", side_effect=replace),
          patch.object(ROTATE, "compose_up", return_value=True),
          patch.object(ROTATE, "verified", return_value=True),
          patch.object(ROTATE, "public_healthy", return_value=True)):
        result = ROTATE.recover_routine(root, directory, old_bearer_safe=True,
                                        owner=os.geteuid())
    assert result["status"] == "PASS"
    assert replacements == [rollback]
    assert current == rollback


def test_stopped_routine_recovery_can_retry_with_separate_evidence(project):
    root, directory, rollback, candidate, baseline = _activation_fixture(project)
    ROTATE.write_private(directory / "activation-incomplete.json",
                         b'{"phase":"bearer-activate","status":"STOP"}\n', os.geteuid())
    ROTATE.write_private(directory / "recovery-incomplete.json", b'{"status":"STOP"}\n', os.geteuid())
    ROTATE.write_private(directory / "recovery-result.json", b'{"status":"STOP"}\n', os.geteuid())
    current = dict(candidate)
    with (patch.object(ROTATE, "staged", return_value=("sidecar", rollback, candidate, baseline)),
          patch.object(ROTATE.PAUSE, "project_lock", side_effect=_lock),
          patch.object(ROTATE, "configuration_current", return_value=True),
          patch.object(ROTATE.PAUSE, "no_extra_writers", return_value=True),
          patch.object(ROTATE, "read_runtime", side_effect=lambda *_args: dict(current)),
          patch.object(ROTATE, "stop_pair", return_value=True),
          patch.object(ROTATE, "isolated", return_value=True),
          patch.object(ROTATE, "replace_runtime", side_effect=lambda _p, files, _o: current.update(files)),
          patch.object(ROTATE, "compose_up", return_value=True),
          patch.object(ROTATE, "verified", return_value=True),
          patch.object(ROTATE, "public_healthy", return_value=True)):
        result = ROTATE.recover_routine(root, directory, old_bearer_safe=True,
                                        owner=os.geteuid())
    assert result["status"] == "PASS"
    retry = directory / "routine-attempts" / result["attempt_id"]
    assert (retry / "recovery-incomplete.json").exists()
    assert (retry / "recovery-result.json").exists()
    assert (directory / "recovery-result.json").read_bytes() == b'{"status":"STOP"}\n'


def test_exposed_old_bearer_cannot_enter_routine_recovery(project):
    root, directory, _rollback, _candidate, _baseline = _activation_fixture(project)
    with patch.object(ROTATE, "staged") as staged:
        with pytest.raises(ValueError, match="safety not confirmed"):
            ROTATE.recover_routine(root, directory, old_bearer_safe=False,
                                   owner=os.geteuid())
    staged.assert_not_called()


def test_partial_fresh_stage_prevents_routine_recovery(project):
    root, directory, rollback, candidate, baseline = _activation_fixture(project)
    ROTATE.write_private(directory / "fresh-source-backend.env", candidate["backend.env"],
                         os.geteuid())
    with patch.object(ROTATE, "staged") as staged:
        with pytest.raises(ValueError, match="fresh recovery path already selected"):
            ROTATE.recover_routine(root, directory, old_bearer_safe=True,
                                   owner=os.geteuid())
    staged.assert_not_called()


def test_created_but_nonrunning_writer_is_safe_for_recovery_isolation():
    states = {
        "backend": {"running": False, "status": "created"},
        "mom": {"running": False, "status": "exited"},
    }
    with patch.object(ROTATE.PAUSE, "container", side_effect=lambda _p, s: states[s]):
        assert ROTATE.isolated("isolated")
        states["backend"]["running"] = True
        assert not ROTATE.isolated("isolated")


@pytest.mark.parametrize("pair", sorted(ROTATE.PAIRS))
def test_verified_probes_both_directions_and_rejects_selected_old_token(project, pair):
    root, _evidence, rollback = project
    selected = ROTATE.PAIRS[pair]
    candidate = {
        "backend.env": ROTATE.replace_assignment(rollback["backend.env"], selected[0], b"fresh"),
        "mom.env": ROTATE.replace_assignment(rollback["mom.env"], selected[1], b"fresh"),
    }
    baseline = {"project": "isolated", "installed": {
        "backend": {"id": "old-backend", "image": "backend-image"},
        "mom": {"id": "old-mom", "image": "mom-image"},
        "postgres": {"id": "same-postgres", "image": "postgres-image"},
    }}
    current = {"project": "isolated", "installed": {
        "backend": {"id": "new-backend", "image": "backend-image"},
        "mom": {"id": "new-mom", "image": "mom-image"},
        "postgres": {"id": "same-postgres", "image": "postgres-image"},
    }}
    calls = []

    def probe(container, url, token):
        calls.append((container, url, token))
        expected_token = ROTATE.pair_values(candidate)["sidecar" if "mom:8001" in url else "reverse-api"][0]
        if token != expected_token:
            return 401
        return 200 if "mom:8001" in url else 400

    with (patch.object(ROTATE, "project_snapshot", return_value=(candidate, current)),
          patch.object(ROTATE, "probe", side_effect=probe)):
        assert ROTATE.verified(root, pair, candidate, rollback, baseline, os.geteuid())
    assert any("mom:8001" in url for _, url, _ in calls)
    assert any("backend:8000" in url for _, url, _ in calls)
    assert len(calls) == 3


def test_exposed_path_stages_and_activates_only_a_fresh_pair(project):
    root, directory, rollback, candidate, baseline = _activation_fixture(project)
    ROTATE.write_private(directory / "activation-incomplete.json",
                         b'{"phase":"bearer-activate","status":"STOP"}\n', os.geteuid())
    current = dict(candidate)
    snapshots = []

    def replace(_project, files, _owner):
        snapshots.append(dict(files))
        current.update(files)

    with (patch.object(ROTATE, "staged", return_value=("sidecar", rollback, candidate, baseline)),
          patch.object(ROTATE.PAUSE, "project_lock", side_effect=_lock),
          patch.object(ROTATE, "configuration_current", return_value=True),
          patch.object(ROTATE.PAUSE, "no_extra_writers", return_value=True),
          patch.object(ROTATE, "isolated", return_value=True),
          patch.object(ROTATE, "read_runtime", side_effect=lambda *_args: dict(current)),
          patch.object(ROTATE.secrets, "token_urlsafe", return_value="sidecar-fresh")):
        staged = ROTATE.stage_fresh(root, directory, owner=os.geteuid())
    assert staged["status"] == "PASS"
    assert current == candidate
    fresh = {name: (directory / f"fresh-candidate-{name}").read_bytes() for name in ROTATE.FILES}
    assert ROTATE.pairs_match(fresh)
    assert ROTATE.pair_values(fresh)["sidecar"][0] == b"sidecar-fresh"
    with pytest.raises(ValueError, match="fresh recovery path"):
        ROTATE.recover_routine(root, directory, old_bearer_safe=True,
                               owner=os.geteuid())

    with (patch.object(ROTATE, "staged", return_value=("sidecar", rollback, candidate, baseline)),
          patch.object(ROTATE.PAUSE, "project_lock", side_effect=_lock),
          patch.object(ROTATE, "configuration_current", return_value=True),
          patch.object(ROTATE.PAUSE, "no_extra_writers", return_value=True),
          patch.object(ROTATE, "isolated", return_value=True),
          patch.object(ROTATE, "read_runtime", side_effect=lambda *_args: dict(current)),
          patch.object(ROTATE, "replace_runtime", side_effect=replace),
          patch.object(ROTATE, "compose_up", return_value=True),
          patch.object(ROTATE, "verified", return_value=True),
          patch.object(ROTATE, "project_snapshot", side_effect=lambda *_args:
                       (dict(current), {"installed": {"backend": {"id": "fresh-backend"}}})),
          patch.object(ROTATE, "probe", return_value=401),
          patch.object(ROTATE, "public_healthy", return_value=True)):
        activated = ROTATE.activate_fresh(root, directory, owner=os.geteuid())
    assert activated["status"] == "PASS"
    assert activated["checks"]["both_prior_bearers_rejected"]
    assert snapshots == [fresh]
    assert current == fresh


def test_fresh_stage_refuses_running_writer(project):
    root, directory, rollback, candidate, baseline = _activation_fixture(project)
    ROTATE.write_private(directory / "activation-incomplete.json",
                         b'{"phase":"bearer-activate","status":"STOP"}\n', os.geteuid())
    with (patch.object(ROTATE, "staged", return_value=("sidecar", rollback, candidate, baseline)),
          patch.object(ROTATE.PAUSE, "project_lock", side_effect=_lock),
          patch.object(ROTATE, "configuration_current", return_value=True),
          patch.object(ROTATE.PAUSE, "no_extra_writers", return_value=True),
          patch.object(ROTATE, "read_runtime", return_value=candidate),
          patch.object(ROTATE, "isolated", return_value=False)):
        with pytest.raises(ValueError, match="not isolated"):
            ROTATE.stage_fresh(root, directory, owner=os.geteuid())
    assert not (directory / "fresh-accepted.json").exists()


def test_interrupted_fresh_stage_preserves_partial_files_and_retries(project):
    root, directory, rollback, candidate, baseline = _activation_fixture(project)
    ROTATE.write_private(directory / "activation-incomplete.json",
                         b'{"phase":"bearer-activate","status":"STOP"}\n', os.geteuid())
    partial = directory / "fresh-source-backend.env"
    ROTATE.write_private(partial, candidate["backend.env"], os.geteuid())
    with (patch.object(ROTATE, "staged", return_value=("sidecar", rollback, candidate, baseline)),
          patch.object(ROTATE.PAUSE, "project_lock", side_effect=_lock),
          patch.object(ROTATE, "configuration_current", return_value=True),
          patch.object(ROTATE.PAUSE, "no_extra_writers", return_value=True),
          patch.object(ROTATE, "isolated", return_value=True),
          patch.object(ROTATE, "read_runtime", return_value=candidate)):
        result = ROTATE.stage_fresh(root, directory, owner=os.geteuid())
    assert result["status"] == "PASS"
    archived = list(directory.glob("fresh-interrupted-*/fresh-source-backend.env"))
    assert len(archived) == 1 and archived[0].read_bytes() == candidate["backend.env"]
    assert (directory / "fresh-accepted.json").exists()


def test_failed_fresh_activation_can_stage_new_token_and_preserve_evidence(project):
    root, directory, rollback, candidate, baseline = _activation_fixture(project)
    ROTATE.write_private(directory / "activation-incomplete.json",
                         b'{"phase":"bearer-activate","status":"STOP"}\n', os.geteuid())
    current = dict(candidate)
    with (patch.object(ROTATE, "staged", return_value=("sidecar", rollback, candidate, baseline)),
          patch.object(ROTATE.PAUSE, "project_lock", side_effect=_lock),
          patch.object(ROTATE, "configuration_current", return_value=True),
          patch.object(ROTATE.PAUSE, "no_extra_writers", return_value=True),
          patch.object(ROTATE, "isolated", return_value=True),
          patch.object(ROTATE, "read_runtime", side_effect=lambda *_args: dict(current)),
          patch.object(ROTATE.secrets, "token_urlsafe", side_effect=["fresh-one", "fresh-two"])):
        ROTATE.stage_fresh(root, directory, owner=os.geteuid())
        first = {name: (directory / f"fresh-candidate-{name}").read_bytes()
                 for name in ROTATE.FILES}
        current.update(first)
        ROTATE.write_private(directory / "fresh-activation-incomplete.json",
                             b'{"status":"STOP"}\n', os.geteuid())
        ROTATE.write_result(directory / "fresh-activation-result.json",
                            b'{"status":"STOP"}\n', os.geteuid())
        second = ROTATE.stage_fresh(root, directory, owner=os.geteuid())
    assert second["status"] == "PASS"
    assert ROTATE.pair_values(current)["sidecar"][0] == b"fresh-one"
    latest = {name: (directory / f"fresh-candidate-{name}").read_bytes()
              for name in ROTATE.FILES}
    assert ROTATE.pair_values(latest)["sidecar"][0] == b"fresh-two"
    archives = list(directory.glob("fresh-interrupted-*"))
    assert len(archives) == 1
    assert (archives[0] / "fresh-activation-result.json").read_bytes() == b'{"status":"STOP"}\n'
    assert (archives[0] / "fresh-candidate-backend.env").read_bytes() == first["backend.env"]
