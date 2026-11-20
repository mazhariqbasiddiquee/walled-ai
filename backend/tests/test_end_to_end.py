"""End to end: MCP client -> mcp-guard proxy -> demo server, with the daemon deciding.

Spawns the real proxy as a subprocess (exactly like Claude Desktop would) and
runs the daemon in a background thread on a free port.
"""

import asyncio
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import mcp_types as types
import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from mcp_guard.api import create_app
from mcp_guard.config import Settings, save_settings
from mcp_guard.db import Database

TOKEN = "e2e-token"
DEMO = Path(__file__).parent.parent / "examples" / "demo_server.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_GUARD_HOME", str(tmp_path))
    monkeypatch.setenv("MCP_GUARD_TOKEN", TOKEN)
    settings = Settings(port=_free_port(), approval_timeout_seconds=10)
    save_settings(settings)  # the proxy subprocess reads this to find the daemon
    app = create_app(settings, Database(tmp_path / "audit.db"), TOKEN, persist_settings=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=settings.port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield settings, tmp_path
    server.should_exit = True
    thread.join(5)


def _proxy_params(home: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_guard.cli", "proxy", "--name", "demo", "--", sys.executable, str(DEMO)],
        env={"MCP_GUARD_HOME": str(home), "MCP_GUARD_TOKEN": TOKEN},
    )


async def _decide_next_pending(base_url: str, decision: str) -> dict:
    async with httpx.AsyncClient(base_url=base_url, headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        for _ in range(100):
            pending = (await c.get("/api/requests", params={"status": "PENDING"})).json()
            if pending:
                req = pending[0]
                await c.post(f"/api/requests/{req['request_id']}/decision", json={"decision": decision})
                return req
            await asyncio.sleep(0.1)
    raise AssertionError("no pending request appeared")


async def test_allow_and_block_through_proxy(daemon):
    settings, home = daemon
    async with stdio_client(_proxy_params(home)) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {"echo", "write_note"}

            # Allowed call reaches the real server.
            call = asyncio.create_task(session.call_tool("echo", {"text": "hello"}))
            seen = await _decide_next_pending(settings.base_url, "allow")
            assert seen["server"] == "demo" and seen["arguments"] == {"text": "hello"}
            result = await call
            assert not result.is_error
            assert isinstance(result.content[0], types.TextContent)
            assert result.content[0].text == "hello"

            # Blocked call never reaches the server.
            call = asyncio.create_task(session.call_tool("write_note", {"path": "/x", "content": "hi"}))
            await _decide_next_pending(settings.base_url, "block")
            result = await call
            assert result.is_error
            assert "REJECTED" in result.content[0].text

    async with httpx.AsyncClient(base_url=settings.base_url, headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        history = (await c.get("/api/requests")).json()
    by_tool = {h["tool"]: h for h in history}
    assert by_tool["echo"]["status"] == "APPROVED"
    assert by_tool["echo"]["execution_status"] == "success"
    assert by_tool["echo"]["result_preview"] == "hello"
    assert by_tool["write_note"]["status"] == "REJECTED"
    assert by_tool["write_note"]["execution_status"] is None


async def test_fail_closed_when_daemon_down(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_GUARD_HOME", str(tmp_path))
    save_settings(Settings(port=_free_port()))  # nothing listening there
    async with stdio_client(_proxy_params(tmp_path)) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool("echo", {"text": "hello"})
            assert result.is_error
            assert "fail-closed" in result.content[0].text
