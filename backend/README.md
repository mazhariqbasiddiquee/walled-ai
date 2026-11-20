# MCP Guard — backend

A security gateway for MCP. Every tool call Claude makes is held until a user
approves it; blocked or unanswered calls never reach the real MCP server.

```
Claude Desktop ──stdio──▶ mcp-guard proxy ──stdio──▶ original MCP server
                               │  ▲
                   submit/wait │  │ decision
                               ▼  │
                         mcp-guard daemon  ◀──HTTP + WebSocket──▶  desktop UI (later)
                     (approvals · policy · SQLite audit)
```

- **Proxy** (`mcp_guard/proxy.py`) — one per protected server. Claude launches it
  in place of the original server. It passes through tools, resources and prompts,
  and gates every `tools/call` on the daemon. **Fails closed:** if the daemon is
  down, the call is blocked.
- **Daemon** (`mcp_guard/api.py`) — one per machine on `127.0.0.1:8765`. Owns the
  Approval Manager, Policy Engine, audit log and the API the UI will use.
- **Approval Manager** (`mcp_guard/approvals.py`) — one future per request, so
  concurrent calls are independent. States: `PENDING → APPROVED | REJECTED | EXPIRED | CANCELLED`.
- **Policy Engine** (`mcp_guard/policy.py`) — glob rules `server/tool → allow | block | ask`,
  newest first, then `default_action` (default `ask`). "Remember this decision" adds a rule.
- **Audit** (`mcp_guard/db.py`) — SQLite at `~/.mcp-guard/audit.db`: arguments, decision,
  who decided, result preview, error, duration.

## Setup

See the project-level `../README.md` for the full setup, including the runtime
copy Claude Desktop needs on macOS. Short version (the venv lives in the project root):

```bash
cd /Users/mazhariqbal/Desktop/Project
python3 -m venv .venv && source .venv/bin/activate
pip install -e "backend[dev]"
cd backend && pytest        # 12 tests, including a real proxy end-to-end test
```

## Run

1. Start the daemon (keep it running):

   ```bash
   mcp-guard daemon
   ```

2. Wrap servers in `~/Library/Application Support/Claude/claude_desktop_config.json`.
   Put `mcp-guard proxy --name <name> --` in front of the original command:

   ```json
   {
     "mcpServers": {
       "filesystem": {
         "command": "/ABSOLUTE/PATH/backend/.venv/bin/mcp-guard",
         "args": ["proxy", "--name", "filesystem", "--",
                  "npx", "-y", "@modelcontextprotocol/server-filesystem", "/Users/you/Documents"]
       }
     }
   }
   ```

   Restart Claude Desktop. Use absolute paths; Claude does not use your shell's PATH.

3. Until the UI exists, decide from the terminal:

   ```bash
   mcp-guard pending
   mcp-guard approve req_ab12cd34 [--remember]
   mcp-guard block   req_ab12cd34 [--remember]
   mcp-guard history
   ```

Settings live in `~/.mcp-guard/config.json` (`host`, `port`, `approval_timeout_seconds`
— default 60, `default_action` — default `ask`). Keep the timeout below your MCP
client's own tool-call timeout, or the client may give up first (the request is then
recorded as `CANCELLED`).

## API (for the desktop UI)

All endpoints except `/api/health` need `Authorization: Bearer <token>`. The token is
in `~/.mcp-guard/token` (`mcp-guard token`). Interactive docs: `http://127.0.0.1:8765/docs`.

| Method | Path | Used by | Purpose |
|---|---|---|---|
| GET | `/api/health` | any | liveness |
| POST | `/api/requests` | proxy | submit `{server, tool, arguments}`; policy may decide instantly |
| GET | `/api/requests/{id}/wait?timeout=25` | proxy | long-poll for a decision |
| POST | `/api/requests/{id}/cancel` | proxy | client gave up |
| POST | `/api/requests/{id}/result` | proxy | `{success, result_preview, error, duration_ms}` |
| GET | `/api/requests?status=&server=&tool=&limit=&offset=` | UI | pending list / history |
| GET | `/api/requests/{id}` | UI | details |
| POST | `/api/requests/{id}/decision` | UI | `{decision: "allow"\|"block", remember?, reason?}` (409 if already decided) |
| GET | `/api/stats` | UI | counts per status |
| GET / POST / DELETE | `/api/rules`, `/api/rules/{id}` | UI | policy rules `{server, tool, action, note}` |
| GET / PATCH | `/api/settings` | UI | `approval_timeout_seconds`, `default_action` |
| WS | `/ws?token=<token>` | UI | live events |

WebSocket messages are `{"event": ..., "data": ...}`:

- `snapshot` — on connect: `{pending: [...], stats: {...}}`
- `request.created` — show the Allow / Block popup
- `request.decided` — close the popup (user, policy, timeout or client cancel)
- `request.completed` — execution result recorded
- `rule.created`, `rule.deleted`, `settings.updated`

## Known limits (MVP)

- Upstream `list_changed` notifications and server→client requests (sampling,
  elicitation) are not forwarded through the proxy yet.
- Only `tools/call` is gated; resource reads and prompts pass through.
- Anyone who can read `~/.mcp-guard/token` as your user can approve requests.
