"""Gateway end to end: MCP over HTTP -> gateway -> demo server, daemon deciding.

This is the path claude.ai uses (minus the public tunnel).
"""

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import mcp_types as types
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from test_end_to_end import TOKEN, _decide_next_pending, _free_port, daemon  # noqa: F401  (fixture)

DEMO = Path(__file__).parent.parent / "examples" / "demo_server.py"
KEY = "test-gateway-key"


@pytest.fixture
def gateway(daemon, tmp_path):  # noqa: F811
    settings, home = daemon
    cfg = tmp_path / "gateway.json"
    cfg.write_text(json.dumps({"mcpServers": {"demo": {"command": sys.executable, "args": [str(DEMO)]}}}))
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_guard.cli", "gateway", "--config", str(cfg), "--port", str(port)],
        env={"MCP_GUARD_HOME": str(home), "MCP_GUARD_TOKEN": TOKEN, "MCP_GUARD_GATEWAY_KEY": KEY, "PATH": "/usr/bin:/bin"},
    )
    url = f"http://127.0.0.1:{port}/mcp"
    for _ in range(100):
        try:
            httpx.get(url, timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield settings, url
    proc.terminate()
    proc.wait(5)


def test_rejects_requests_without_key(gateway):
    _, url = gateway
    assert httpx.post(url, json={}).status_code == 401
    assert httpx.post(url, json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert httpx.post(url, json={}, headers={"X-MCP-Guard-Key": "wrong"}).status_code == 401
    assert httpx.post(url, json={}, headers={"X-API-Key": "wrong"}).status_code == 401
    # The right key in X-API-Key gets past auth (MCP then rejects the empty body, not 401).
    assert httpx.post(url, json={}, headers={"X-API-Key": KEY}).status_code != 401
    assert httpx.post(url, json={}, headers={"x-api-token": KEY}).status_code != 401
    assert httpx.post(url, json={}, headers={"x-api-token": "wrong"}).status_code == 401


async def test_tools_are_namespaced_and_gated(gateway):
    settings, url = gateway
    async with create_mcp_http_client(headers={"Authorization": f"Bearer {KEY}"}) as http:
        async with streamable_http_client(url, http_client=http) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                assert names == {"demo__echo", "demo__write_note"}

                call = asyncio.create_task(session.call_tool("demo__echo", {"text": "from the web"}))
                seen = await _decide_next_pending(settings.base_url, "allow")
                # The daemon (and the popup) see the real server/tool names.
                assert (seen["server"], seen["tool"]) == ("demo", "echo")
                result = await call
                assert not result.is_error
                assert isinstance(result.content[0], types.TextContent)
                assert result.content[0].text == "from the web"

                call = asyncio.create_task(session.call_tool("demo__write_note", {"path": "/x", "content": "y"}))
                await _decide_next_pending(settings.base_url, "block")
                result = await call
                assert result.is_error and "REJECTED" in result.content[0].text

                result = await session.call_tool("nope__tool", {})
                assert result.is_error and "Unknown tool" in result.content[0].text

    # The bare URL (no /mcp) works too.
    async with create_mcp_http_client(headers={"Authorization": f"Bearer {KEY}"}) as http:
        async with streamable_http_client(url.removesuffix("/mcp") + "/", http_client=http) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 2
                result = await session.call_tool("nope__tool", {})
                assert result.is_error and "Unknown tool" in result.content[0].text
