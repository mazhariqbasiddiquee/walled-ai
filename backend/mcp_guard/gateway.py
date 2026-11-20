"""MCP Gateway: the proxy as an HTTP server, for Claude on the web (claude.ai).

Claude Desktop can *start* our stdio proxy. claude.ai runs in Anthropic's cloud
and cannot, so instead it connects to a URL. This module:

1. starts every configured upstream MCP server (same format as Claude Desktop's
   ``mcpServers``) and connects to each one as a client;
2. serves all their tools behind ONE Streamable-HTTP endpoint (``/mcp``), with
   names prefixed by server: ``filesystem__write_file``, ``demo__echo``;
3. sends every tool call through the same approval flow as the stdio proxy
   (``guarded_call``): ask the daemon, forward only if approved, fail closed.

Every HTTP request must carry the gateway key (``~/.mcp-guard/gateway_key``)
as ``Authorization: Bearer <key>``, ``X-API-Key: <key>`` or ``X-MCP-Guard-Key: <key>``. This key is
separate from the daemon token: the daemon API must never be exposed publicly.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mcp_types as types
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.lowlevel.server import Server
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__
from .config import Settings, guard_home, load_or_create_token
from .proxy import GuardClient, guarded_call

log = logging.getLogger(__name__)

SEPARATOR = "__"
SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9-]+$")  # no "_" so SEPARATOR stays unambiguous
MAX_TOOL_NAME = 64  # Claude's tool-name limit
# Headers that may carry the gateway key as a bare value (besides "Authorization: Bearer <key>").
KEY_HEADERS = (b"x-mcp-guard-key", b"x-api-key", b"x-api-token", b"api-key", b"x-auth-token")


# ---- configuration ----------------------------------------------------------


@dataclass
class UpstreamConfig:
    name: str
    command: str
    args: list[str]
    env: dict[str, str] | None = None


def load_upstreams(path: Path) -> list[UpstreamConfig]:
    """Read ``{"mcpServers": {...}}`` (Claude Desktop format) or ``{"servers": {...}}``."""
    data = json.loads(path.read_text())
    servers = data.get("mcpServers") or data.get("servers")
    if not isinstance(servers, dict) or not servers:
        raise ValueError(f"{path}: expected a non-empty 'mcpServers' object")
    upstreams = []
    for name, spec in servers.items():
        if not SERVER_NAME_RE.match(name):
            raise ValueError(f"server name {name!r}: use only letters, digits and '-'")
        upstreams.append(
            UpstreamConfig(name=name, command=spec["command"], args=spec.get("args", []), env=spec.get("env"))
        )
    return upstreams


def load_or_create_gateway_key() -> str:
    """Secret that claude.ai sends with every request. Separate from the daemon token."""
    env = os.environ.get("MCP_GUARD_GATEWAY_KEY")
    if env:
        return env
    path = guard_home() / "gateway_key"
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    return key


# ---- authentication ---------------------------------------------------------


class RequireGatewayKey:
    """ASGI middleware: reject any HTTP request without the gateway key."""

    def __init__(self, app: Any, key: str):
        self.app = app
        self.key = key.encode()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":  # lifespan etc. pass through
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        # claude.ai only allows pre-approved header names, so accept the common API-key ones.
        supplied = next((headers[h] for h in KEY_HEADERS if headers.get(h)), b"")
        auth = headers.get(b"authorization", b"")
        if auth.lower().startswith(b"bearer "):
            supplied = supplied or auth[7:].strip()
        if not supplied or not secrets.compare_digest(supplied, self.key):
            client = scope.get("client") or ("?", 0)
            log.warning("rejected request without valid gateway key from %s", client[0])
            body = b'{"error":"unauthorized"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        if scope.get("path") in ("", "/"):
            # Accept the bare URL too, in case the connector was saved without /mcp.
            scope = {**scope, "path": "/mcp", "raw_path": b"/mcp"}
        await self.app(scope, receive, send)


# ---- MCP server -------------------------------------------------------------


@dataclass
class Upstream:
    name: str
    session: ClientSession
    guard: GuardClient


def build_gateway_server(upstreams: dict[str, Upstream]) -> Server:
    async def list_tools(ctx, params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        tools: list[types.Tool] = []
        for up in upstreams.values():
            cursor: str | None = None
            while True:
                page = await up.session.list_tools(
                    params=types.PaginatedRequestParams(cursor=cursor) if cursor else None
                )
                for tool in page.tools:
                    public_name = f"{up.name}{SEPARATOR}{tool.name}"
                    if len(public_name) > MAX_TOOL_NAME:
                        log.warning("skipping %s: name longer than %d characters", public_name, MAX_TOOL_NAME)
                        continue
                    description = f"[{up.name}] {tool.description or ''}".strip()
                    tools.append(tool.model_copy(update={"name": public_name, "description": description}))
                cursor = page.next_cursor
                if not cursor:
                    break
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        server, sep, tool = params.name.partition(SEPARATOR)
        up = upstreams.get(server)
        if not sep or up is None:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Unknown tool '{params.name}'")], is_error=True
            )
        # The daemon sees the real server and tool names, so popups and rules stay the same.
        return await guarded_call(up.guard, up.session, tool, params.arguments or {})

    return Server(
        "mcp-guard-gateway",
        version=__version__,
        instructions="Tools from several MCP servers, each call approved by the user via MCP Guard. "
        "Tool names are '<server>__<tool>'.",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def run_gateway(config_path: Path, host: str, port: int, settings: Settings) -> None:
    configs = load_upstreams(config_path)
    key = load_or_create_gateway_key()
    token = load_or_create_token()

    async with AsyncExitStack() as stack:
        upstreams: dict[str, Upstream] = {}
        for cfg in configs:
            try:
                params = StdioServerParameters(command=cfg.command, args=cfg.args, env=cfg.env)
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                init = await session.initialize()
            except Exception as exc:  # one broken server should not take the others down
                log.error("upstream '%s' failed to start: %s", cfg.name, exc)
                continue
            guard = GuardClient(settings.base_url, token, cfg.name)
            stack.push_async_callback(guard.aclose)
            upstreams[cfg.name] = Upstream(cfg.name, session, guard)
            log.info("upstream '%s' connected (%s)", cfg.name, init.server_info.name)

        if not upstreams:
            raise RuntimeError("no upstream MCP server could be started")

        server = build_gateway_server(upstreams)
        app = server.streamable_http_app(
            streamable_http_path="/mcp",
            stateless_http=True,
            # Requests arrive through a tunnel with a public Host header; the
            # gateway key (not the Host header) is what authenticates them.
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
            host=host,
        )
        log.info("MCP Guard gateway on http://%s:%d/mcp serving: %s", host, port, ", ".join(upstreams))
        config = uvicorn.Config(RequireGatewayKey(app, key), host=host, port=port, log_level="info", lifespan="on")
        await uvicorn.Server(config).serve()
