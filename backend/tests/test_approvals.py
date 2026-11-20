"""API-level tests for the approval lifecycle (no MCP processes involved)."""

import asyncio

import httpx
import pytest

from mcp_guard.api import create_app
from mcp_guard.config import Settings
from mcp_guard.db import Database

TOKEN = "test-token"
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
async def client(tmp_path):
    settings = Settings(approval_timeout_seconds=5)
    app = create_app(settings, Database(tmp_path / "t.db"), TOKEN, persist_settings=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t", headers=H) as c:
            yield c, app


async def submit(c, tool="write_file", server="filesystem"):
    r = await c.post("/api/requests", json={"server": server, "tool": tool, "arguments": {"path": "/x"}})
    assert r.status_code == 201
    return r.json()


async def test_requires_token(client):
    c, _ = client
    r = await c.get("/api/requests", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


async def test_approve_flow(client):
    c, _ = client
    req = await submit(c)
    assert req["status"] == "PENDING"

    waiter = asyncio.create_task(c.get(f"/api/requests/{req['request_id']}/wait", params={"timeout": 5}))
    await asyncio.sleep(0.05)
    r = await c.post(f"/api/requests/{req['request_id']}/decision", json={"decision": "allow"})
    assert r.json()["status"] == "APPROVED"
    assert (await waiter).json()["status"] == "APPROVED"

    r = await c.post(
        f"/api/requests/{req['request_id']}/result",
        json={"success": True, "result_preview": "ok", "duration_ms": 3},
    )
    assert r.json()["execution_status"] == "success"


async def test_concurrent_requests_are_independent(client):
    c, _ = client
    a, b = await submit(c, "a"), await submit(c, "b")
    wa = asyncio.create_task(c.get(f"/api/requests/{a['request_id']}/wait", params={"timeout": 5}))
    wb = asyncio.create_task(c.get(f"/api/requests/{b['request_id']}/wait", params={"timeout": 5}))
    await asyncio.sleep(0.05)
    await c.post(f"/api/requests/{b['request_id']}/decision", json={"decision": "block"})
    await c.post(f"/api/requests/{a['request_id']}/decision", json={"decision": "allow"})
    assert (await wa).json()["status"] == "APPROVED"
    assert (await wb).json()["status"] == "REJECTED"


async def test_double_decision_conflicts(client):
    c, _ = client
    req = await submit(c)
    await c.post(f"/api/requests/{req['request_id']}/decision", json={"decision": "block"})
    r = await c.post(f"/api/requests/{req['request_id']}/decision", json={"decision": "allow"})
    assert r.status_code == 409


async def test_timeout_expires(client):
    c, app = client
    app.state.manager.settings.approval_timeout_seconds = 1
    req = await submit(c)
    r = await c.get(f"/api/requests/{req['request_id']}/wait", params={"timeout": 3})
    assert r.json()["status"] == "EXPIRED"
    assert r.json()["decided_by"] == "timeout"


async def test_cancel(client):
    c, _ = client
    req = await submit(c)
    r = await c.post(f"/api/requests/{req['request_id']}/cancel")
    assert r.json()["status"] == "CANCELLED"


async def test_remember_creates_rule(client):
    c, _ = client
    req = await submit(c, "read_file")
    await c.post(f"/api/requests/{req['request_id']}/decision", json={"decision": "allow", "remember": True})
    again = await submit(c, "read_file")
    assert again["status"] == "APPROVED" and again["decided_by"] == "policy"
    other = await submit(c, "write_file")
    assert other["status"] == "PENDING"


async def test_rules_and_default_action(client):
    c, _ = client
    await c.post("/api/rules", json={"server": "*", "tool": "delete_*", "action": "block"})
    assert (await submit(c, "delete_file"))["status"] == "REJECTED"
    await c.patch("/api/settings", json={"default_action": "allow"})
    assert (await submit(c, "list_dir"))["status"] == "APPROVED"
    stats = (await c.get("/api/stats")).json()
    assert stats["REJECTED"] == 1 and stats["APPROVED"] == 1
