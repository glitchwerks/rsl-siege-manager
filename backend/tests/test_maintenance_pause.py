"""Disposable request-admission and drain checks for portable maintenance."""

import asyncio
import os

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.maintenance import MaintenanceGate, MaintenancePauseMiddleware


@pytest.fixture
def gate(tmp_path):
    state = tmp_path / "state"
    state.write_bytes(b"active\n")
    state.chmod(0o600)
    return MaintenanceGate(enabled=True, state_file=str(state), owner_uid=os.geteuid())


@pytest.mark.asyncio
async def test_paused_requests_reject_writes_and_keep_read_only_probes(gate):
    app = FastAPI()
    app.add_middleware(MaintenancePauseMiddleware, gate=gate)

    @app.get("/api/health")
    async def health():
        return {"status": "healthy"}

    @app.get("/api/members/me/preferences")
    async def preferences():
        return {"probe": True}

    @app.post("/api/write")
    async def write():
        return {"changed": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/write")).status_code == 200
        gate.state_file.write_bytes(b"paused\n")
        blocked = await client.post("/api/write")
        assert blocked.status_code == 503
        assert blocked.headers["Retry-After"] == "30"
        assert (await client.get("/api/health")).status_code == 200
        assert (
            await client.get(
                "/api/members/me/preferences",
                headers={"Authorization": "Bearer synthetic", "X-Acting-Discord-Id": "invalid"},
            )
        ).status_code == 200
        assert (await client.get("/api/members/me/preferences")).status_code == 503
        assert gate.snapshot() == {"paused": True, "inflight": 0}


@pytest.mark.asyncio
async def test_pause_waits_for_admitted_request_to_drain(gate):
    app = FastAPI()
    app.add_middleware(MaintenancePauseMiddleware, gate=gate)
    entered = asyncio.Event()
    release = asyncio.Event()

    @app.post("/api/write")
    async def write():
        entered.set()
        await release.wait()
        return {"changed": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        active = asyncio.create_task(client.post("/api/write"))
        await asyncio.wait_for(entered.wait(), timeout=2)
        gate.state_file.write_bytes(b"paused\n")
        assert gate.snapshot() == {"paused": True, "inflight": 1}
        assert (await client.post("/api/write")).status_code == 503
        release.set()
        assert (await active).status_code == 200
        assert gate.snapshot() == {"paused": True, "inflight": 0}


def test_missing_invalid_or_symlink_state_fails_closed(gate, tmp_path):
    state = gate.state_file
    state.write_bytes(b"typo\n")
    assert gate.state() == "paused"
    state.unlink()
    assert gate.state() == "paused"
    target = tmp_path / "target"
    target.write_bytes(b"active\n")
    state.symlink_to(target)
    assert gate.state() == "paused"
