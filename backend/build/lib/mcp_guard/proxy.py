"""MCP Proxy: sits between an MCP client (e.g. Claude Desktop) and one real MCP server.

Claude launches *this* process instead of the original server. The proxy:

1. spawns the original server as a child process and connects to it as an MCP client;
2. serves the same tools / resources / prompts back to Claude over stdio;
3. on every ``tools/call`` asks the MCP Guard daemon for a decision and only
   forwards the call to the original server if it was approved.

It fails closed: if the daemon is unreachable, or no decision arrives, the
tool call is blocked.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import anyio
import httpx
import mcp_types as types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

from .config import Settings, load_or_create_token
from .models import TERMINAL_DECISIONS, RequestStatus, ToolRequest

log = logging.getLogger(__name__)

LONG_POLL_SECONDS = 25.0


class GuardUnavailable(RuntimeError):
    pass


class GuardClient:
    """Talks to the MCP Guard daemon on behalf of one proxied server."""

    def __init__(self, base_url: str, token: str, server_name: str):
        self.server_name = server_name
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(10.0, read=LONG_POLL_SECONDS + 10.0),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, url: str, **kwargs: Any) -> ToolRequest:
        try:
            resp = await self._http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise GuardUnavailable(f"cannot reach MCP Guard daemon: {exc}") from exc
        if resp.status_code >= 400:
            raise GuardUnavailable(f"MCP Guard daemon returned {resp.status_code}: {resp.text}")
        return ToolRequest.model_validate(resp.json())

    async def request_approval(self, tool: str, arguments: dict[str, Any]) -> ToolRequest:
        """Submit a tool call and block until it reaches a terminal decision."""
        req = await self._call(
            "POST", "/api/requests", json={"server": self.server_name, "tool": tool, "arguments": arguments}
        )
        try:
            while req.status not in TERMINAL_DECISIONS:
                # Safety net in case the daemon never resolves the request.
                if req.expires_at and datetime.now(timezone.utc) > req.expires_at + timedelta(seconds=15):
                    raise GuardUnavailable("approval did not resolve before its deadline")
                req = await self._call(
                    "GET", f"/api/requests/{req.request_id}/wait", params={"timeout": LONG_POLL_SECONDS}
                )
            return req
        except BaseException:
            # Claude cancelled the call, or we gave up: withdraw the pending popup.
            with anyio.CancelScope(shield=True):
                try:
                    await self._http.post(f"/api/requests/{req.request_id}/cancel", timeout=3.0)
                except httpx.HTTPError:
                    pass
            raise

    async def report_result(
        self, request_id: str, *, success: bool, preview: str | None, error: str | None, duration_ms: int
    ) -> None:
        try:
            await self._http.post(
                f"/api/requests/{request_id}/result",
                json={"success": success, "result_preview": preview, "error": error, "duration_ms": duration_ms},
            )
        except httpx.HTTPError as exc:  # auditing failure must not break the tool result
            log.warning("failed to report result for %s: %s", request_id, exc)


def _blocked(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


def _preview(result: types.CallToolResult) -> str:
    parts: list[str] = []
    for item in result.content:
        if isinstance(item, types.TextContent):
            parts.append(item.text)
        else:
            parts.append(f"[{getattr(item, 'type', 'content')}]")
    return "\n".join(parts)


async def guarded_call(
    guard: GuardClient, upstream: ClientSession, tool: str, arguments: dict[str, Any]
) -> types.CallToolResult:
    """Ask the daemon for approval; run the tool on ``upstream`` only if approved.

    Shared by the stdio proxy (Claude Desktop) and the HTTP gateway (Claude web).
    """
    try:
        req = await guard.request_approval(tool, arguments)
    except GuardUnavailable as exc:
        log.error("blocking %s/%s: %s", guard.server_name, tool, exc)
        return _blocked(f"Blocked by MCP Guard (fail-closed): {exc}")

    if req.status != RequestStatus.APPROVED:
        reason = req.decision_reason or req.status.value.lower()
        return _blocked(
            f"Tool call '{tool}' on server '{guard.server_name}' was not executed: "
            f"{req.status.value} by {req.decided_by} ({reason})."
        )

    started = time.monotonic()
    try:
        result = await upstream.call_tool(tool, arguments)
    except Exception as exc:
        ms = int((time.monotonic() - started) * 1000)
        await guard.report_result(req.request_id, success=False, preview=None, error=str(exc), duration_ms=ms)
        raise
    ms = int((time.monotonic() - started) * 1000)
    if isinstance(result, types.CallToolResult):
        failed = bool(result.is_error)
        preview = _preview(result)
        await guard.report_result(
            req.request_id, success=not failed, preview=preview, error=preview if failed else None, duration_ms=ms
        )
    return result


def build_proxy_server(
    name: str, upstream: ClientSession, init: types.InitializeResult, guard: GuardClient
) -> Server:
    caps = init.capabilities
    handlers: dict[str, Any] = {}

    if caps.tools is not None:

        async def list_tools(ctx, params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
            return await upstream.list_tools(params=params)

        async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
            return await guarded_call(guard, upstream, params.name, params.arguments or {})

        handlers.update(on_list_tools=list_tools, on_call_tool=call_tool)

    if caps.resources is not None:

        async def list_resources(ctx, params):
            return await upstream.list_resources(params=params)

        async def list_resource_templates(ctx, params):
            return await upstream.list_resource_templates(params=params)

        async def read_resource(ctx, params: types.ReadResourceRequestParams):
            return await upstream.read_resource(params.uri)

        handlers.update(
            on_list_resources=list_resources,
            on_list_resource_templates=list_resource_templates,
            on_read_resource=read_resource,
        )

    if caps.prompts is not None:

        async def list_prompts(ctx, params):
            return await upstream.list_prompts(params=params)

        async def get_prompt(ctx, params: types.GetPromptRequestParams):
            return await upstream.get_prompt(params.name, params.arguments)

        handlers.update(on_list_prompts=list_prompts, on_get_prompt=get_prompt)

    info = init.server_info
    return Server(
        info.name or name,
        version=info.version or "",
        instructions=init.instructions,
        **handlers,
    )


async def run_proxy(
    name: str,
    command: str,
    args: list[str],
    env: dict[str, str] | None,
    settings: Settings,
) -> None:
    guard = GuardClient(settings.base_url, load_or_create_token(), name)
    params = StdioServerParameters(command=command, args=args, env=env)
    try:
        async with stdio_client(params) as (up_read, up_write):
            async with ClientSession(up_read, up_write) as upstream:
                init = await upstream.initialize()
                log.info("connected to upstream '%s' (%s)", name, init.server_info.name)
                server = build_proxy_server(name, upstream, init, guard)
                async with stdio_server() as (read, write):
                    await server.run(read, write, server.create_initialization_options())
    finally:
        await guard.aclose()


def main(name: str, command: str, args: list[str], env: dict[str, str] | None, settings: Settings) -> None:
    asyncio.run(run_proxy(name, command, args, env, settings))
