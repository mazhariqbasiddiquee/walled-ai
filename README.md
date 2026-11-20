# MCP Guard

**A security gateway for MCP.** Every time Claude tries to use an MCP tool
(write a file, delete something, call an API…), MCP Guard pauses the call and
asks you first. Nothing runs until you click **Allow**. Everything is logged.

---

## 1. The problem

MCP (Model Context Protocol) servers give Claude abilities: read and write files,
query databases, call APIs. Normally, when Claude decides to use one of those
tools, the action runs **immediately**. You can't see the exact parameters in
advance, and there's no central record of what was done.

## 2. What MCP Guard does

It puts a checkpoint between Claude and every MCP server:

- **Intercepts** each tool call before it reaches the real server
- **Shows** you exactly what Claude wants to do: server, tool, parameters
- **Waits** for your decision: Allow or Block
- **Forwards** the call only if you allow it
- **Logs** everything: who decided, when, the result, how long it took
- **Blocks by default**: if you don't answer in time, or the gateway is down,
  the call is refused

## 3. Architecture

```
┌────────────────┐  tool call   ┌────────────────┐  only if allowed   ┌─────────────────┐
│ Claude Desktop │ ───────────▶ │  MCP Guard     │ ─────────────────▶ │ Real MCP server │
│                │ ◀─────────── │  Proxy         │ ◀───────────────── │ (filesystem…)   │
└────────────────┘    result    └───────┬────────┘       result       └─────────────────┘
                                        │ "may I run this?"
                                        ▼
                               ┌────────────────────┐   WebSocket + REST   ┌──────────────────┐
                               │  MCP Guard Daemon  │ ◀──────────────────▶ │  Desktop App     │
                               │  127.0.0.1:8765    │                      │  (Electron)      │
                               │  • Policy rules    │                      │  • Approval popup│
                               │  • Approval queue  │                      │  • History       │
                               │  • Audit log (DB)  │                      │  • Rules/Settings│
                               └────────────────────┘                      └──────────────────┘
                                                                                  ▲
                                                                                  │ Allow / Block
                                                                                 You
```

There are **three programs**:

| Program | What it is | Who starts it |
|---|---|---|
| **Proxy** (`mcp-guard proxy`) | Stands in for each real MCP server. Claude thinks it *is* the server. | Claude Desktop, automatically |
| **Daemon** (`mcp-guard daemon`) | The decision-maker: rules, pending queue, audit log, API | You, in a terminal |
| **Desktop app** (Electron) | The user interface: popup, history, rules, settings | You (`npm run dev`) |

### Why split it this way?

- **Proxy per server:** Claude Desktop launches every MCP server as its own
  process. Wrapping each one is the least intrusive change: you prefix the
  server's command in Claude's config and nothing else changes.
- **One daemon:** many proxies (and many simultaneous calls) need one shared
  place for decisions, rules and the log, and the UI needs one place to connect to.
- **UI separate from the daemon:** the security logic keeps working without the UI
  (you can approve from the terminal), and the UI can be restarted or rebuilt
  without touching the protection.

## 4. The life of one tool call

Example: you ask Claude *"Save my notes to ~/Documents/notes.txt"*.

| # | What happens | Where |
|---|---|---|
| 1 | Claude decides to call `write_file` on the `filesystem` server | Claude |
| 2 | Claude shows its own "Allow once?" prompt (Claude's own check, not ours) | Claude |
| 3 | Claude sends the call to what it thinks is the filesystem server, which is really our **proxy** | Claude → Proxy |
| 4 | Proxy sends `{server, tool, arguments}` to the daemon (`POST /api/requests`) | Proxy → Daemon |
| 5 | Daemon checks the **rules**. A match → instant allow/block (skip to 9). No match → **PENDING** | Daemon |
| 6 | Daemon pushes `request.created` over WebSocket | Daemon → App |
| 7 | **Popup appears** with server, tool, risk hint and parameters, plus a countdown | App |
| 8 | You click **Allow** (or Block, or press Esc). The app sends the decision (`POST /api/requests/{id}/decision`) | You → App → Daemon |
| 9 | The proxy, which was waiting (`GET /api/requests/{id}/wait`), receives the decision | Daemon → Proxy |
| 10a | **Approved:** the proxy forwards the call to the real server, gets the result, reports it to the daemon (`POST .../result`) and returns it to Claude | Proxy ↔ Server |
| 10b | **Blocked / expired:** the real server is never contacted. Claude gets *"Tool call was not executed: REJECTED"* | Proxy → Claude |
| 11 | Everything is saved in the audit log | Daemon |

### Request states

```
             ┌──▶ APPROVED  (you clicked Allow, or an "allow" rule matched)
PENDING ─────┼──▶ REJECTED  (you clicked Block, or a "block" rule matched)
             ├──▶ EXPIRED   (no answer within the timeout → blocked)
             └──▶ CANCELLED (Claude gave up waiting)
```

Each pending request waits on its own, so several tool calls can be pending at
once without interfering with each other.

### Safety defaults (fail closed)

| Situation | Result |
|---|---|
| Daemon not running | Proxy blocks every call |
| No answer before the timeout | Request expires → blocked |
| Desktop app closed | Requests still wait; approve from the terminal (`mcp-guard watch`) or they expire |
| Token missing or wrong | API refuses the call |

## 5. Project structure

```
Project/
├── README.md                ← this file
├── .venv/                   ← Python environment (development)
├── backend/                 ← Python: proxy + daemon
│   ├── mcp_guard/
│   │   ├── proxy.py         ← MCP proxy for Claude Desktop (stdio)
│   │   ├── gateway.py       ← MCP gateway for claude.ai (HTTP, one URL for all servers)
│   │   ├── api.py           ← daemon HTTP + WebSocket API (FastAPI)
│   │   ├── approvals.py     ← approval manager: per-request lifecycle and timeouts
│   │   ├── policy.py        ← rules engine (allow / block / ask, glob patterns)
│   │   ├── db.py            ← SQLite audit log and rules
│   │   ├── events.py        ← live event broadcasting
│   │   ├── models.py        ← data models
│   │   ├── config.py        ← settings, paths, token
│   │   └── cli.py           ← `mcp-guard` command
│   ├── examples/demo_server.py   ← harmless test MCP server (echo, write_note)
│   └── tests/               ← 14 automated tests
└── desktop/                 ← Electron + React + TypeScript + Tailwind
    └── src/
        ├── main/guard.ts    ← daemon connection (holds the token)
        ├── main/index.ts    ← windows, popup, menu bar, notifications
        ├── preload/         ← safe bridge (`window.guard`)
        └── renderer/        ← React UI: popup, Pending, History, Rules, Settings
```

Files MCP Guard creates in your home folder (`~/.mcp-guard/`):

| File | Purpose |
|---|---|
| `config.json` | Port, approval timeout, default action |
| `token` | Secret shared by the daemon, proxies and app (readable only by you) |
| `audit.db` | SQLite database: every request plus your rules |
| `runtime/` | A copy of MCP Guard that Claude Desktop runs (see 6.2) |

## 6. Setup (one time)

### 6.1 Backend

```bash
cd /Users/mazhariqbal/Desktop/Project
python3 -m venv .venv
source .venv/bin/activate
pip install -e "backend[dev]"
cd backend && pytest          # 14 tests should pass
```

### 6.2 Runtime copy for Claude Desktop

macOS doesn't let apps read the **Desktop** folder without permission, so Claude
can't run MCP Guard from `~/Desktop/Project`. Install a copy outside it:

```bash
python3 -m venv ~/.mcp-guard/runtime
~/.mcp-guard/runtime/bin/pip install /Users/mazhariqbal/Desktop/Project/backend
cp /Users/mazhariqbal/Desktop/Project/backend/examples/demo_server.py ~/.mcp-guard/runtime/
```

After changing backend code, refresh this copy and restart Claude:

```bash
~/.mcp-guard/runtime/bin/pip install --force-reinstall --no-deps /Users/mazhariqbal/Desktop/Project/backend
```

### 6.3 Connect Claude Desktop

Quit Claude (**Cmd+Q**), open its config:

```bash
open -e ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

Add (or keep) an `mcpServers` section next to the existing `preferences`:

```json
"mcpServers": {
  "demo": {
    "command": "/Users/mazhariqbal/.mcp-guard/runtime/bin/mcp-guard",
    "args": ["proxy", "--name", "demo", "--",
             "/Users/mazhariqbal/.mcp-guard/runtime/bin/python",
             "/Users/mazhariqbal/.mcp-guard/runtime/demo_server.py"]
  },
  "filesystem": {
    "command": "/Users/mazhariqbal/.mcp-guard/runtime/bin/mcp-guard",
    "args": ["proxy", "--name", "filesystem", "--",
             "/opt/homebrew/bin/npx", "-y", "@modelcontextprotocol/server-filesystem",
             "/Users/mazhariqbal/Documents"]
  }
}
```

**To protect any MCP server:** keep its original command, put it after `--`, and
put `mcp-guard proxy --name <name>` in front. Always use full paths, because Claude
doesn't use your terminal's PATH.

Check the file is valid JSON, then reopen Claude:

```bash
python3 -m json.tool ~/Library/Application\ Support/Claude/claude_desktop_config.json > /dev/null && echo OK
```

### 6.4 Desktop app

```bash
cd /Users/mazhariqbal/Desktop/Project/desktop
npm install
```

## 7. Daily use

**Terminal 1: daemon** (must always be running):
```bash
cd /Users/mazhariqbal/Desktop/Project/backend
source ../.venv/bin/activate
mcp-guard daemon
```

**Terminal 2: desktop app:**
```bash
cd /Users/mazhariqbal/Desktop/Project/desktop
npm run dev
```

Bottom-left of the app should say **Protected** (green).

**Try it:** in Claude ask *"Use the demo echo tool to say hello"* → click
**Allow once** in Claude → the MCP Guard popup appears → **Allow** → Claude says
"hello". Ask it to use `write_note` and click **Block** to see a refusal.

### Without the app (terminal only)

```bash
mcp-guard watch                     # prompts y / n / a(lways) / b(lock always) per request
mcp-guard pending                   # list waiting requests
mcp-guard approve <id> [--remember]
mcp-guard block   <id> [--remember]
mcp-guard history                   # recent audit log
mcp-guard token                     # print the API token
```

### Rules

Rules decide calls automatically. Server and tool are patterns (`*`, `?`); the
**newest matching rule wins**; if none match, the default action applies (`ask`).

| Server | Tool | Action | Effect |
|---|---|---|---|
| `demo` | `echo` | allow | Never ask for demo/echo |
| `*` | `delete_*` | block | Every delete tool on any server is refused |
| `filesystem` | `read_*` | allow | File reads go through; writes still ask |

Add rules on the **Rules** page, or tick **Remember** in the popup.

## 7b. Claude on the web (claude.ai): the gateway

claude.ai runs in Anthropic's cloud, so it can't *start* our proxy the way Claude
Desktop does. It connects to a **URL** instead (a "custom connector"). The
**gateway** is the proxy as an HTTP server: one URL serves all your MCP servers,
and every call goes through the same daemon, rules, popup and audit log.

```
claude.ai ──HTTPS──▶ ngrok (public URL) ──▶ mcp-guard gateway (127.0.0.1:8766/mcp)
                                                 ├──▶ demo server
                                                 ├──▶ filesystem server
                                                 └──▶ daemon (stays private) ◀──▶ desktop app
```

| | Desktop (stdio proxy) | Web (gateway) |
|---|---|---|
| Started by | Claude Desktop | You (`mcp-guard gateway`) |
| Talks over | Pipe | HTTP at `/mcp`, made public by a tunnel |
| Servers per URL | One proxy per server | All servers behind one URL |
| Tool names in Claude | `echo` | `demo__echo` (server prefix) |
| Protection | Private pipe | **Gateway key** required on every request |

**Run it** (4 terminals; the daemon and app as in section 7):

```bash
# Terminal 3: gateway (server list in Claude Desktop format)
cd /Users/mazhariqbal/Desktop/Project/backend && source ../.venv/bin/activate
mcp-guard gateway --config examples/gateway.example.json

# Terminal 4: public HTTPS URL for the gateway (only port 8766, never 8765)
ngrok http 8766
```

**Connect claude.ai:** Settings → Connectors → **Add custom connector**

- **URL:** the `https://….ngrok-free.app` address from ngrok, plus `/mcp`
- **Authentication:** *No sign-in*, then under **Request headers** pick the
  standard **Authorization** header with value `Bearer <key>`, where `<key>` is
  the output of `mcp-guard gateway-key`. If the list offers **X-API-Key** instead,
  use that with the bare key. (Custom header names need Anthropic's approval, so
  don't type your own name.)

Then in a chat: *"Use demo__echo to say hello"* → popup → Allow.

**Rules for the web setup:**

- The **gateway key** (`~/.mcp-guard/gateway_key`) goes to claude.ai. The
  **daemon token** (`~/.mcp-guard/token`) never leaves your Mac.
- Only expose port **8766** (gateway). Never tunnel **8765** (daemon), or anyone
  could approve requests.
- claude.ai stops waiting after **5 minutes**, so keep the approval timeout below that.
- The free ngrok URL changes each time ngrok restarts; update the connector, or use a
  reserved ngrok domain.
- Free plan: one custom connector, which is why the gateway puts every server behind one URL.
- Stop exposure any time with Ctrl+C in the ngrok terminal.

**Why "No sign-in" instead of OAuth?** OAuth would mean building a full
authorization server into the gateway (login page, issuing and refreshing tokens,
client registration). That's a lot for a single-user tool on your own Mac. The
gateway key gives the protection that matters here: without it every request is
refused, the daemon token never leaves your Mac, and every tool call still waits
for your **Allow** in the popup. OAuth would be worth adding if several people
shared one gateway or you needed keys you can revoke per user.

## 7c. Installed app and auto-start at login

**Build the app** (creates `desktop/dist/mac-arm64/MCP Guard.app`, ad-hoc signed):

```bash
cd desktop && npm run package
```

**Install and auto-start everything** (stop any daemon/gateway/ngrok you started
in terminals first, then quit the dev app):

```bash
scripts/autostart.sh install                                   # daemon + gateway + app
scripts/autostart.sh install --with-ngrok https://YOUR-DOMAIN.ngrok-free.dev   # also expose for claude.ai
scripts/autostart.sh status
scripts/autostart.sh uninstall
```

| What | How it starts | Logs |
|---|---|---|
| Daemon | LaunchAgent `com.mcpguard.daemon` (restarts if it crashes) | `~/.mcp-guard/logs/daemon.log` |
| Gateway | LaunchAgent `com.mcpguard.gateway`, config `~/.mcp-guard/gateway.json` | `~/.mcp-guard/logs/gateway.log` |
| ngrok (optional) | LaunchAgent `com.mcpguard.ngrok`, fixed URL | `~/.mcp-guard/logs/ngrok.log` |
| Desktop app | `/Applications/MCP Guard.app`, login item (toggle "Open at login" in its menu-bar menu) | — |

Services run from `~/.mcp-guard/runtime`, because macOS stops background programs
from reading the Desktop folder. `install` refreshes that copy from `backend/`, so
run it again after changing backend code.

**When to use the services vs. terminals:**

| Situation | What to do |
|---|---|
| Changing code | Don't install. Run the daemon and `npm run dev` in terminals (section 7), plus the gateway and ngrok if you're testing claude.ai (7b). |
| Want MCP Guard always on | Build the app (`npm run package`), stop terminal processes, then run `scripts/autostart.sh install` once. It starts at every login. |
| Changed backend code | Run `scripts/autostart.sh install` again to refresh the runtime copy. |
| Back to working in terminals | Run `scripts/autostart.sh uninstall` first. The services hold ports 8765/8766. |

Don't run both ways at once: they need the same ports.

**Why claude.ai isn't auto-started by default:** `--with-ngrok` is opt-in because
an always-on tunnel makes the gateway reachable from the internet whenever you're
logged in (protected only by the gateway key). Claude Desktop doesn't need it, so
nothing is exposed unless you ask. It also needs your reserved ngrok domain: a
plain `ngrok http 8766` gets a new URL on each restart, which would break the
claude.ai connector after every login. And it needs ngrok installed and signed in.

**Fixed ngrok URL:** free ngrok accounts come with one static domain (shown in the
ngrok dashboard under *Domains*, ending in `.ngrok-free.dev`). Pass it with
`--with-ngrok` so claude.ai's connector URL never changes. Auto-starting ngrok
means the gateway is reachable from the internet whenever you're logged in. It
stays locked by the gateway key, but leave ngrok out if you don't need claude.ai.

## 8. Daemon API (for developers)

All endpoints need `Authorization: Bearer <token>` except `/api/health`.
Interactive docs: http://127.0.0.1:8765/docs

| Method | Path | Used by | Purpose |
|---|---|---|---|
| POST | `/api/requests` | proxy | Submit a tool call |
| GET | `/api/requests/{id}/wait` | proxy | Wait for the decision (long poll) |
| POST | `/api/requests/{id}/cancel` | proxy | Claude gave up |
| POST | `/api/requests/{id}/result` | proxy | Report execution result |
| GET | `/api/requests?status=&server=&limit=` | app | Pending list / history |
| POST | `/api/requests/{id}/decision` | app | `{"decision":"allow"\|"block","remember":bool}` |
| GET | `/api/stats` | app | Counts per status |
| GET/POST/DELETE | `/api/rules[/{id}]` | app | Manage rules |
| GET/PATCH | `/api/settings` | app | Timeout and default action |
| WS | `/ws?token=…` | app | Live events: `snapshot`, `request.created`, `request.decided`, `request.completed`, `rule.*`, `settings.updated` |

## 9. Security design

- Daemon listens on **127.0.0.1 only**; nothing on the network can reach it.
- **Token auth** on every API call; the token file is readable only by you.
- **Fail closed**: daemon down, timeout or error → blocked.
- The desktop app keeps the token in Electron's main process. Windows are
  sandboxed, with no Node access, a strict content-security policy, and
  navigation blocked.
- The popup has **no keyboard shortcut for Allow** (prevents accidental approval);
  **Esc = Block**.
- Remembered rules match the exact server/tool name only.

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `zsh: command not found: mcp-guard` | venv not activated | `source /Users/mazhariqbal/Desktop/Project/.venv/bin/activate` |
| App says **Daemon offline** | daemon not running | Start `mcp-guard daemon` |
| Claude says tool was blocked "fail-closed" | daemon not running | Start the daemon |
| Request shows **EXPIRED** | no answer in time | Answer faster, or raise the timeout in Settings |
| demo/filesystem missing in Claude | Claude not fully restarted | **Cmd+Q**, reopen |
| Log: `PermissionError … pyvenv.cfg` | macOS blocks Desktop access | Use the runtime copy (6.2) |
| `json.tool` error "Extra data" | extra `}` in Claude config | Remove it |
| Claude server logs | — | `~/Library/Logs/Claude/mcp-server-<name>.log` |

## 11. Known limitations / next steps

- The desktop app isn't packaged as a `.app` yet (run with `npm run dev`).
  Next: electron-builder packaging, and launching the daemon at login.
- Claude Desktop shows its own "Allow once" prompt before MCP Guard's popup;
  that's Claude's feature and MCP Guard can't remove it.
- Only **tool calls** are gated; resource reads and prompts pass through.
- The proxy doesn't yet forward "tool list changed" notifications or the original
  server's requests back to Claude (such as sampling).
