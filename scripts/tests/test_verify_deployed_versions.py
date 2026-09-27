"""Tests for exact deployed-version verification."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "verify-deployed-versions.py"
SPEC = importlib.util.spec_from_file_location("verify_deployed_versions", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def _version_files(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "bot").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "backend/VERSION").write_text("1.4.2\n", encoding="utf-8")
    (tmp_path / "bot/VERSION").write_text("1.4.3\n", encoding="utf-8")
    (tmp_path / "frontend/package.json").write_text(
        json.dumps({"version": "1.5.0"}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)


def test_exact_versions_pass(tmp_path, monkeypatch) -> None:
    _version_files(tmp_path, monkeypatch)
    sha = "abcdef0123456789"
    version = {
        "backend_version": "1.4.2+42.abcdef0",
        "bot_version": "1.4.3+42.abcdef0",
        "frontend_version": "1.5.0+42.abcdef0",
        "git_sha": sha,
    }

    marker = {"frontend_version": "1.5.0+42.abcdef0", "git_sha": sha}

    assert module.validate({"status": "healthy"}, version, marker, sha) == []


def test_null_or_stale_frontend_fails(tmp_path, monkeypatch) -> None:
    _version_files(tmp_path, monkeypatch)
    sha = "abcdef0123456789"
    version = {
        "backend_version": "1.4.2+42.abcdef0",
        "bot_version": "1.4.3+42.abcdef0",
        "frontend_version": None,
        "git_sha": sha,
    }

    marker = {"frontend_version": "1.5.0+42.abcdef0", "git_sha": sha}
    errors = module.validate({"status": "healthy"}, version, marker, sha)

    assert any("frontend_version mismatch" in error for error in errors)


def test_wrong_sha_metadata_fails(tmp_path, monkeypatch) -> None:
    _version_files(tmp_path, monkeypatch)
    version = {
        "backend_version": "1.4.2+42.1234567",
        "bot_version": "1.4.3+42.1234567",
        "frontend_version": "1.5.0+42.1234567",
        "git_sha": "abcdef0123456789",
    }

    marker = {
        "frontend_version": "1.5.0+42.1234567",
        "git_sha": "abcdef0123456789",
    }
    assert "expected build metadata" in module.validate(
        {"status": "healthy"}, version, marker, "abcdef0123456789"
    )[0]


def test_stale_frontend_artifact_fails(tmp_path, monkeypatch) -> None:
    _version_files(tmp_path, monkeypatch)
    sha = "abcdef0123456789"
    version = {
        "backend_version": "1.4.2+42.abcdef0",
        "bot_version": "1.4.3+42.abcdef0",
        "frontend_version": "1.5.0+42.abcdef0",
        "git_sha": sha,
    }
    marker = {"frontend_version": "1.4.2+41.1234567", "git_sha": "1234567890"}

    errors = module.validate({"status": "healthy"}, version, marker, sha)

    assert any("frontend marker frontend_version mismatch" in error for error in errors)
    assert any("frontend marker git_sha mismatch" in error for error in errors)


def test_newer_selected_sha_supersedes_verification(monkeypatch) -> None:
    monkeypatch.setattr(module, "_get_json", lambda _url: {"sha": "newer-sha"})

    assert module.superseding_sha("https://example.test/main", "older-sha") == "newer-sha"
    assert module.superseding_sha(None, "older-sha") is None
