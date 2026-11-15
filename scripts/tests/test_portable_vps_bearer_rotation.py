"""Disposable file tests for portable bearer staging; no Docker or VPS access."""

import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-bearer-rotation.py"
SPEC = importlib.util.spec_from_file_location("bearer_rotation", SCRIPT)
ROTATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ROTATE)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir(mode=0o700)
    runtime = root / "runtime"
    runtime.mkdir(mode=0o700)
    evidence = root / "evidence"
    evidence.mkdir(mode=0o700)
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
])
def test_ambiguous_bearer_assignment_stops(invalid):
    with pytest.raises(ValueError):
        ROTATE.parse_env(invalid, {"DISCORD_BOT_API_KEY", "BOT_SERVICE_TOKEN"})
