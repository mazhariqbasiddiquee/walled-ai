# MCP Guard — desktop app

Electron + React + TypeScript + Tailwind. Shows an always-on-top approval popup
whenever Claude calls a protected MCP tool, plus a window for history, rules and
settings. It talks to the Python daemon in `../backend`.

```
Claude ─▶ mcp-guard proxy ─▶ mcp-guard daemon ◀── WebSocket + REST ──▶ this app
```

## Run

```bash
# 1. the daemon must be running (separate terminal)
source ../.venv/bin/activate && mcp-guard daemon

# 2. the app
cd desktop
npm install            # first time only
npm run dev            # development, with hot reload
# or
npm run build && npm start
```

The app reads the daemon address from `~/.mcp-guard/config.json` and the token
from `~/.mcp-guard/token` (both created by the daemon). `MCP_GUARD_HOME`
overrides that folder, the same as for the backend.

## What it does

- **Approval popup**: opens and comes to the front for each pending request, with
  the server, tool, a risk hint and the parameters. Allow / Block, "Remember"
  (saves a rule), countdown to auto-block. **Esc = Block**; Allow has no
  keyboard shortcut on purpose. Several requests queue up ("1 of 3").
- **Menu bar item + Dock badge**: pending count; the app keeps running when
  windows are closed, so popups still appear. Quit from the menu bar item.
- **Notification** for each new request.
- **Main window**: Pending (with stats), History (audit log, filter, click a row
  for details/result), Rules (add/delete allow/block/ask patterns), Settings
  (timeout, default action).
- **Offline handling**: shows "Daemon offline" and reconnects automatically.
  While the daemon is down, the proxy blocks every call (fail-closed).

## Structure

| Path | Role |
|---|---|
| `src/main/guard.ts` | Daemon client: WebSocket with reconnect, REST calls. Holds the token. |
| `src/main/index.ts` | Windows, popup, tray, notifications, IPC handlers, renderer crash recovery |
| `src/preload/index.ts` | Exposes a typed `window.guard` API (no token, no raw IPC) |
| `src/shared/types.ts` | Types shared with the renderer; mirror `backend/mcp_guard/models.py` |
| `src/renderer/src/components/ApprovalPopup.tsx` | The popup |
| `src/renderer/src/components/pages/*` | Pending, History, Rules, Settings |

Security: `contextIsolation`, `sandbox`, no `nodeIntegration`, strict CSP,
navigation and new windows blocked. The renderer can only call the methods in
`window.guard`.
