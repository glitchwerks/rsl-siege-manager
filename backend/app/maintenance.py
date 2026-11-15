"""Portable maintenance gate shared by request admission and drain reporting.

Azure deployments leave this gate disabled. Portable deployments opt in with a
read-only directory mount whose state file can change without container
recreation. Missing or invalid state fails closed when enforcement is enabled.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
from threading import Lock
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings


class MaintenanceGate:
    """Admit work only while active and count in-flight requests for drain."""

    def __init__(self, *, enabled: bool, state_file: str, owner_uid: int = 0) -> None:
        self.enabled = enabled
        self.state_file = Path(state_file) if state_file else None
        self.owner_uid = owner_uid
        self._lock = Lock()
        self._inflight = 0

    def state(self) -> str:
        """Return active or paused; malformed control state always pauses."""
        if not self.enabled:
            return "active"
        path = self.state_file
        if path is None or not path.is_absolute():
            return "paused"
        try:
            if any(part.is_symlink() for part in (path, *path.parents)):
                return "paused"
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != self.owner_uid
                    or info.st_mode & 0o022
                    or info.st_size > 16
                ):
                    return "paused"
                raw = source.read(17)
            return "active" if raw == b"active\n" else "paused"
        except OSError:
            return "paused"

    def begin(self) -> bool:
        """Count a request only if the current state still permits it."""
        if self.state() != "active":
            return False
        with self._lock:
            if self.state() != "active":
                return False
            self._inflight += 1
        return True

    def end(self) -> None:
        """Release one admitted request after its ASGI response completes."""
        with self._lock:
            self._inflight -= 1

    def snapshot(self) -> dict[str, Any]:
        """Expose only pause and in-flight count, never control file content."""
        with self._lock:
            count = self._inflight
        return {"paused": self.state() != "active", "inflight": count}


def _probe_allowed(scope: Scope) -> bool:
    method = scope.get("method")
    path = scope.get("path")
    if method == "OPTIONS":
        return True
    if method != "GET":
        return False
    if path in {"/api/health", "/api/version", "/api/maintenance/status"}:
        return True
    if path != "/api/members/me/preferences":
        return False
    headers = dict(scope.get("headers", []))
    return headers.get(b"x-acting-discord-id") == b"invalid" and headers.get(
        b"authorization", b""
    ).lower().startswith(b"bearer ")


class MaintenancePauseMiddleware:
    """Reject affected requests while preserving narrow read-only probes."""

    def __init__(self, app: ASGIApp, gate: MaintenanceGate) -> None:
        self.app = app
        self.gate = gate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _probe_allowed(scope):
            await self.app(scope, receive, send)
            return
        if not self.gate.begin():
            response = JSONResponse(
                {"detail": "Maintenance pause active"},
                status_code=503,
                headers={"Retry-After": "30"},
            )
            await response(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self.gate.end()


gate = MaintenanceGate(
    enabled=settings.maintenance_enforced, state_file=settings.maintenance_state_file
)
