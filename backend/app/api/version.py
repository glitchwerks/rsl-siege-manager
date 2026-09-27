import json
import logging
import os
from pathlib import Path

import httpx
from fastapi import APIRouter

from app.config import settings
from app.schemas.version import VersionResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# VERSION file sits two levels above this file: backend/VERSION
_VERSION_FILE = Path(__file__).parent.parent.parent / "VERSION"
_FRONTEND_PACKAGE_FILE = Path("/component-metadata/frontend-package.json")


def _read_backend_version() -> str:
    """Return a version string for the backend.

    When both BUILD_NUMBER and GIT_SHA are injected at image build time the
    string has the form ``1.0.1+42.abc1234`` (semver + build metadata per
    PEP 440 / SemVer 2 convention).  In local development, where those env
    vars are absent or set to their ``unknown`` defaults, only the bare semver
    is returned so the output stays clean.
    """
    try:
        semver = _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("backend/VERSION not found; defaulting to 'unknown'")
        semver = "unknown"

    build_number = os.environ.get("BUILD_NUMBER", "unknown")
    git_sha = os.environ.get("GIT_SHA", "unknown")

    if build_number != "unknown" and git_sha != "unknown":
        return f"{semver}+{build_number}.{git_sha[:7]}"
    return semver


async def _fetch_bot_version() -> str | None:
    """Call the bot sidecar's /version endpoint. Returns None if unreachable."""
    url = f"{settings.discord_bot_api_url.rstrip('/')}/api/version"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json().get("version")
    except Exception as exc:
        logger.warning("Could not reach bot version endpoint (%s): %s", url, exc)
        return None


def _read_frontend_version() -> str | None:
    """Return the injected build version or the local Compose package version."""
    injected = os.environ.get("FRONTEND_VERSION")
    if injected and injected != "unknown":
        return injected
    try:
        package = json.loads(_FRONTEND_PACKAGE_FILE.read_text(encoding="utf-8"))
        return str(package["version"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        logger.warning("Frontend version metadata is unavailable")
        return None


@router.get("/version", response_model=VersionResponse)
async def get_version() -> VersionResponse:
    """Return version information for all components."""
    backend_version = _read_backend_version()
    bot_version = await _fetch_bot_version()
    frontend_version = _read_frontend_version()
    git_sha = os.environ.get("GIT_SHA") or None

    return VersionResponse(
        backend_version=backend_version,
        bot_version=bot_version,
        frontend_version=frontend_version,
        git_sha=git_sha,
    )
