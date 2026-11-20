"""Command line entry point.

    mcp-guard daemon                                  run the approval service
    mcp-guard proxy --name fs -- npx -y @modelcontextprotocol/server-filesystem ~/Documents
    mcp-guard pending | approve <id> | block <id>     decide from the terminal (no UI needed)
    mcp-guard history                                 recent audit log
    mcp-guard token                                   print the API token for the desktop UI
    mcp-guard gateway --config servers.json           HTTP gateway for Claude on the web
    mcp-guard gateway-key                             print the key to paste into claude.ai
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import httpx

from .config import db_path, load_or_create_token, load_settings


def _setup_logging(verbose: bool) -> None:
    # Always log to stderr: in proxy mode stdout carries the MCP protocol.
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s [mcp-guard] %(name)s: %(message)s",
    )
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)


def _cmd_daemon(args: argparse.Namespace) -> None:
    import uvicorn

    from .api import create_app
    from .db import Database

    settings = load_settings()
    if args.port:
        settings.port = args.port
    app = create_app(settings, Database(db_path()), load_or_create_token())
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


def _cmd_proxy(args: argparse.Namespace) -> None:
    from .proxy import main as proxy_main

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        sys.exit("mcp-guard proxy: missing upstream server command after '--'")
    env = dict(e.split("=", 1) for e in args.env) if args.env else None
    proxy_main(args.name, command[0], command[1:], env, load_settings())


def _cmd_gateway(args: argparse.Namespace) -> None:
    import asyncio
    from pathlib import Path

    from .gateway import run_gateway

    asyncio.run(run_gateway(Path(args.config).expanduser(), args.host, args.port, load_settings()))


def _cmd_gateway_key(_: argparse.Namespace) -> None:
    from .gateway import load_or_create_gateway_key

    print(load_or_create_gateway_key())


def _client() -> httpx.Client:
    settings = load_settings()
    return httpx.Client(
        base_url=settings.base_url,
        headers={"Authorization": f"Bearer {load_or_create_token()}"},
        timeout=10.0,
    )


def _print_requests(rows: list[dict]) -> None:
    if not rows:
        print("(none)")
    for r in rows:
        print(f"{r['request_id']}  {r['status']:<9}  {r['server']}/{r['tool']}  {r['created_at']}")
        print(f"    args: {json.dumps(r['arguments'])[:200]}")


def _cmd_pending(_: argparse.Namespace) -> None:
    with _client() as c:
        resp = c.get("/api/requests", params={"status": "PENDING"})
        resp.raise_for_status()
        _print_requests(resp.json())


def _cmd_history(args: argparse.Namespace) -> None:
    with _client() as c:
        resp = c.get("/api/requests", params={"limit": args.limit})
        resp.raise_for_status()
        _print_requests(resp.json())


def _decide(request_id: str, decision: str, remember: bool) -> None:
    with _client() as c:
        resp = c.post(
            f"/api/requests/{request_id}/decision", json={"decision": decision, "remember": remember}
        )
        if resp.status_code >= 400:
            sys.exit(f"error {resp.status_code}: {resp.text}")
        r = resp.json()
        print(f"{r['request_id']} -> {r['status']}")


def _cmd_watch(_: argparse.Namespace) -> None:
    """Terminal stand-in for the desktop popup: prompt for each new pending request."""
    import time

    print("Watching for tool calls (Ctrl+C to stop)...")
    seen: set[str] = set()
    with _client() as c:
        while True:
            try:
                resp = c.get("/api/requests", params={"status": "PENDING"})
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                print(f"daemon not reachable ({exc}); retrying...")
                time.sleep(2)
                continue
            for r in reversed(resp.json()):
                if r["request_id"] in seen:
                    continue
                seen.add(r["request_id"])
                print("\n=== Approval required ===")
                print(f"  server : {r['server']}")
                print(f"  tool   : {r['tool']}")
                print(f"  args   : {json.dumps(r['arguments'], indent=2)}")
                print(f"  expires: {r['expires_at']}")
                answer = input("Allow? [y]es / [n]o / [a]lways / [b]lock always: ").strip().lower()
                decision = "allow" if answer in ("y", "yes", "a") else "block"
                remember = answer in ("a", "b")
                d = c.post(
                    f"/api/requests/{r['request_id']}/decision",
                    json={"decision": decision, "remember": remember},
                )
                status_text = d.json().get("status") if d.status_code < 400 else d.text
                print(f"-> {status_text}")
            time.sleep(0.5)


def _run_quietly(fn, args: argparse.Namespace) -> None:
    try:
        fn(args)
    except (KeyboardInterrupt, EOFError):
        print("\nstopped")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mcp-guard", description="MCP Security Gateway")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("daemon", help="run the approval daemon (HTTP + WebSocket API)")
    p.add_argument("--port", type=int)
    p.set_defaults(func=_cmd_daemon)

    p = sub.add_parser("proxy", help="wrap an MCP server; use as the command in Claude's config")
    p.add_argument("--name", required=True, help="server name shown in approvals, e.g. filesystem")
    p.add_argument("--env", action="append", metavar="KEY=VALUE", help="extra env for the upstream server")
    p.add_argument("command", nargs=argparse.REMAINDER, help="-- <upstream command> [args...]")
    p.set_defaults(func=_cmd_proxy)

    p = sub.add_parser("gateway", help="serve protected MCP servers over HTTP (for claude.ai connectors)")
    p.add_argument("--config", required=True, help='JSON file with {"mcpServers": {...}} (Claude Desktop format)')
    p.add_argument("--host", default="127.0.0.1", help="keep 127.0.0.1 and expose it through a tunnel")
    p.add_argument("--port", type=int, default=8766)
    p.set_defaults(func=_cmd_gateway)

    p = sub.add_parser("gateway-key", help="print the secret claude.ai must send to the gateway")
    p.set_defaults(func=_cmd_gateway_key)

    p = sub.add_parser("pending", help="list requests waiting for a decision")
    p.set_defaults(func=_cmd_pending)

    p = sub.add_parser("watch", help="interactively approve/block requests as they arrive")
    p.set_defaults(func=lambda a: _run_quietly(_cmd_watch, a))

    p = sub.add_parser("history", help="show recent audit log entries")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=_cmd_history)

    for name, decision in (("approve", "allow"), ("block", "block")):
        p = sub.add_parser(name, help=f"{name} a pending request")
        p.add_argument("request_id")
        p.add_argument("--remember", action="store_true", help="auto-apply to this server+tool from now on")
        p.set_defaults(func=lambda a, d=decision: _decide(a.request_id, d, a.remember))

    p = sub.add_parser("token", help="print the API token (for the desktop UI)")
    p.set_defaults(func=lambda _: print(load_or_create_token()))

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()
