"""Tests for GET /api/version endpoint and _read_backend_version helper."""

import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_version_module():
    """Force re-import of version module so env-var reads pick up monkeypatches."""
    import app.api.version as mod

    importlib.reload(mod)
    return mod


# ---------------------------------------------------------------------------
# Unit tests for _read_backend_version()
# ---------------------------------------------------------------------------


def test_read_backend_version_semver_only(monkeypatch):
    """When BUILD_NUMBER / GIT_SHA are absent, return bare semver."""
    monkeypatch.delenv("BUILD_NUMBER", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)

    import app.api.version as mod

    result = mod._read_backend_version()
    # Should be a plain semver — no "+" character
    assert "+" not in result
    assert result != "unknown"  # VERSION file exists


def test_read_backend_version_semver_only_when_unknown(monkeypatch):
    """When env vars are explicitly 'unknown', return bare semver."""
    monkeypatch.setenv("BUILD_NUMBER", "unknown")
    monkeypatch.setenv("GIT_SHA", "unknown")

    import app.api.version as mod

    result = mod._read_backend_version()
    assert "+" not in result


def test_read_backend_version_with_build_info(monkeypatch):
    """When both BUILD_NUMBER and GIT_SHA are set, return combined string."""
    monkeypatch.setenv("BUILD_NUMBER", "42")
    monkeypatch.setenv("GIT_SHA", "abc1234567890")

    import app.api.version as mod

    # Read the canonical VERSION file dynamically so this test doesn't break on every release bump.
    expected_semver = mod._VERSION_FILE.read_text().strip()

    result = mod._read_backend_version()
    # e.g. "<semver>+42.abc1234"
    assert "+" in result
    parts = result.split("+")
    assert len(parts) == 2
    semver_part, build_part = parts
    assert semver_part == expected_semver
    assert build_part == "42.abc1234"  # GIT_SHA truncated to 7 chars


def test_read_backend_version_missing_version_file(monkeypatch, tmp_path):
    """When the VERSION file is missing, semver falls back to 'unknown'."""
    monkeypatch.setenv("BUILD_NUMBER", "unknown")
    monkeypatch.setenv("GIT_SHA", "unknown")

    import app.api.version as mod

    # Point the module at a non-existent file for the duration of this test
    original = mod._VERSION_FILE
    mod._VERSION_FILE = tmp_path / "NONEXISTENT"
    try:
        result = mod._read_backend_version()
        assert result == "unknown"
    finally:
        mod._VERSION_FILE = original


def test_read_backend_version_build_info_with_missing_file(monkeypatch, tmp_path):
    """Even with a missing VERSION file, build suffix is appended if env vars are set."""
    monkeypatch.setenv("BUILD_NUMBER", "7")
    monkeypatch.setenv("GIT_SHA", "deadbeef12345")

    import app.api.version as mod

    original = mod._VERSION_FILE
    mod._VERSION_FILE = tmp_path / "NONEXISTENT"
    try:
        result = mod._read_backend_version()
        assert result == "unknown+7.deadbee"
    finally:
        mod._VERSION_FILE = original


# ---------------------------------------------------------------------------
# Integration tests for GET /api/version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_version_returns_200_with_expected_keys(monkeypatch):
    """GET /api/version responds 200 with all required fields."""
    monkeypatch.delenv("BUILD_NUMBER", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.setenv("FRONTEND_VERSION", "1.0.1")

    with patch("app.api.version._fetch_bot_version", new=AsyncMock(return_value="1.0.1")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/version")

    assert response.status_code == 200
    data = response.json()
    assert "backend_version" in data
    assert "bot_version" in data
    assert "frontend_version" in data
    assert "git_sha" in data


@pytest.mark.asyncio
async def test_get_version_backend_version_has_build_suffix(monkeypatch):
    """backend_version includes build metadata when env vars are present."""
    monkeypatch.setenv("BUILD_NUMBER", "99")
    monkeypatch.setenv("GIT_SHA", "ffff000111222")

    with patch("app.api.version._fetch_bot_version", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/version")

    assert response.status_code == 200
    backend_version = response.json()["backend_version"]
    assert "+" in backend_version
    assert backend_version.endswith("+99.ffff000")


@pytest.mark.asyncio
async def test_get_version_backend_version_clean_in_local_dev(monkeypatch):
    """backend_version is bare semver when BUILD_NUMBER/GIT_SHA are absent."""
    monkeypatch.delenv("BUILD_NUMBER", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)

    with patch("app.api.version._fetch_bot_version", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/version")

    assert response.status_code == 200
    backend_version = response.json()["backend_version"]
    assert "+" not in backend_version


@pytest.mark.asyncio
async def test_get_version_git_sha_field_preserved(monkeypatch):
    """git_sha top-level field is still returned for backward compatibility."""
    monkeypatch.setenv("GIT_SHA", "abc123def456")

    with patch("app.api.version._fetch_bot_version", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/version")

    assert response.status_code == 200
    data = response.json()
    # git_sha field must still be present (backward compat)
    assert "git_sha" in data
    assert data["git_sha"] == "abc123def456"


@pytest.mark.asyncio
async def test_get_version_bot_unreachable_returns_none(monkeypatch):
    """bot_version is null when the bot sidecar is unreachable."""
    monkeypatch.delenv("BUILD_NUMBER", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)

    with patch("app.api.version._fetch_bot_version", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/version")

    assert response.status_code == 200
    assert response.json()["bot_version"] is None


@pytest.mark.asyncio
async def test_fetch_bot_version_calls_api_version_path(monkeypatch):
    """_fetch_bot_version must call /api/version on the sidecar, not /version."""
    import app.api.version as mod

    captured_urls = []

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            captured_urls.append(url)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value={"version": "1.0.1"})
            return resp

    with patch("app.api.version.httpx.AsyncClient", return_value=_FakeClient()):
        result = await mod._fetch_bot_version()

    assert result == "1.0.1"
    assert len(captured_urls) == 1
    assert captured_urls[0].endswith(
        "/api/version"
    ), f"expected URL ending with /api/version, got: {captured_urls[0]!r}"
