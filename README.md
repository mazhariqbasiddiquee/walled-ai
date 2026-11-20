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

Claude Desktop and Claude on the web (claude.ai) use **the same path**: both talk
to one **gateway** URL, and the gateway is the only thing that starts and talks to
the real MCP servers.

```
Claude Desktop ──stdio──▶ mcp-remote ──HTTP (local)──┐
                                                     ▼
claude.ai ──HTTPS──▶ ngrok (public URL) ─────▶ ┌──────────────────────┐  only if allowed  ┌─────────────────┐
                                               │  MCP Guard Gateway   │ ────────────────▶ │ Real MCP servers│
                                               │  127.0.0.1:8766/mcp  │ ◀──────────────── │ demo, filesystem│
                                               └──────────┬───────────┘      result       └─────────────────┘
                                                          │ "may I run this?"
                                                          ▼
                                               ┌──────────────────────┐  WebSocket + REST  ┌──────────────────┐
                                               │  MCP Guard Daemon    │ ◀────────────────▶ │  Desktop App     │
                                               │  127.0.0.1:8765      │                    │  (Electron)      │
                                               │  • Policy rules      │                    │  • Approval popup│
                                               │  • Approval queue    │                    │  • History       │
                                               │  • Audit log (DB)    │                    │  • Rules/Settings│
                                               └──────────────────────┘                    └──────────────────┘
                                                                                                   ▲
                                                                                                   │ Allow / Block
                                                                                                  You
```

There are **three programs**:

| Program | What it is | Who starts it |
|---|---|---|
| **Gateway** (`mcp-guard gateway`) | One MCP endpoint (`/mcp`) in front of all your real MCP servers. Every tool call is held until the daemon says yes. | Auto-start service, or you in a terminal |
| **Daemon** (`mcp-guard daemon`) | The decision-maker: rules, pending queue, audit log, API | Auto-start service, or you in a terminal |
| **Desktop app** (Electron) | The user interface: popup, history, rules, settings | Login item, or you (`npm run dev`) |

Two small helpers connect Claude to the gateway:

- **Claude Desktop → `mcp-remote`** (an npm package). Claude Desktop's config only
  starts local programs, so it starts `mcp-remote`, which forwards to
  `http://127.0.0.1:8766/mcp`. This stays on your Mac; no internet needed.
- **claude.ai → ngrok.** claude.ai runs in Anthropic's cloud, so it needs a public
  HTTPS URL. ngrok forwards that URL to the gateway (see 7b).

### Why it's built this way

- **One gateway for both Claudes:** the list of protected servers lives in one
  file (`~/.mcp-guard/gateway.json`). Add a server there and it shows up, guarded,
  in Desktop and on the web.
- **One daemon:** many simultaneous calls need one shared place for decisions,
  rules and the log, and the UI needs one place to connect to.
- **UI separate from the daemon:** the security logic keeps working without the UI
  (you can approve from the terminal), and the UI can be restarted or rebuilt
  without touching the protection.
- **Daemon never exposed:** only the gateway (port 8766) is ever tunnelled. The
  daemon (port 8765), which can approve requests, stays on `127.0.0.1`.

## 4. The life of one tool call

Example: you ask Claude *"Save my notes to ~/Documents/notes.txt"*. Tool names
in Claude carry the server as a prefix: `filesystem__write_file`.

| # | What happens | Where |
|---|---|---|
| 1 | Claude decides to call `write_file` on the `filesystem` server | Claude |
| 2 | Claude shows its own "Allow once?" prompt (Claude's own check, not ours) | Claude |
| 3 | Claude sends `filesystem__write_file` to the **gateway** (Desktop via `mcp-remote`, web via ngrok) | Claude → Gateway |
| 4 | Gateway sends `{server, tool, arguments}` to the daemon (`POST /api/requests`) | Gateway → Daemon |
| 5 | Daemon checks the **rules**. A match → instant allow/block (skip to 9). No match → **PENDING** | Daemon |
| 6 | Daemon pushes `request.created` over WebSocket | Daemon → App |
| 7 | **Popup appears** with server, tool, risk hint and parameters, plus a countdown | App |
| 8 | You click **Allow** (or Block, or press Esc). The app sends the decision (`POST /api/requests/{id}/decision`) | You → App → Daemon |
| 9 | The gateway, which was waiting (`GET /api/requests/{id}/wait`), receives the decision | Daemon → Gateway |
| 10a | **Approved:** the gateway forwards the call to the real server, gets the result, reports it to the daemon (`POST .../result`) and returns it to Claude | Gateway ↔ Server |
| 10b | **Blocked / expired:** the real server is never contacted. Claude gets *"Tool call was not executed: REJECTED"* | Gateway → Claude |
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
| Daemon not running | Gateway blocks every call |
| Gateway not running | Claude can't reach any protected tool (nothing runs unguarded) |
| No answer before the timeout | Request expires → blocked |
| Desktop app closed | Requests still wait; approve from the terminal (`mcp-guard watch`) or they expire |
| Token or gateway key missing or wrong | Request refused |

## 5. Project structure

```
walled-ai/
├── README.md                ← this file
├── .venv/                   ← Python environment (create it; not in git)
├── scripts/autostart.sh     ← install/uninstall LaunchAgents + app (see 7c)
├── backend/                 ← Python: gateway + daemon
│   ├── mcp_guard/
│   │   ├── gateway.py       ← MCP gateway: one HTTP URL for all servers (Desktop + web)
│   │   ├── proxy.py         ← shared approval logic + optional per-server stdio proxy
│   │   ├── api.py           ← daemon HTTP + WebSocket API (FastAPI)
│   │   ├── approvals.py     ← approval manager: per-request lifecycle and timeouts
│   │   ├── policy.py        ← rules engine (allow / block / ask, glob patterns)
│   │   ├── db.py            ← SQLite audit log and rules
│   │   ├── events.py        ← live event broadcasting
│   │   ├── models.py        ← data models
│   │   ├── config.py        ← settings, paths, token
│   │   └── cli.py           ← `mcp-guard` command
│   ├── examples/
│   │   ├── demo_server.py         ← harmless test MCP server (echo, write_note)
│   │   └── gateway.example.json   ← server list for `mcp-guard gateway`
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
| `gateway_key` | Secret Claude Desktop and claude.ai send to the gateway |
| `gateway.json` | The MCP servers the gateway protects (see 6.3) |
| `audit.db` | SQLite database: every request plus your rules |
| `runtime/` | The installed copy the background services run (see 6.2) |
| `logs/` | Service logs, created by `scripts/autostart.sh` (see 7c) |

Set `MCP_GUARD_HOME` to use a different folder (see 6.0).

## 6. Setup (one time)

**Requirements:** macOS (the auto-start script and paths assume it), Python 3.11+,
Node.js + npm, Claude Desktop, and optionally [ngrok](https://ngrok.com) for claude.ai.

### 6.0 Paths: set them once

Every command in this guide uses these variables instead of hard-coded folders.
If you move or rename the repo, change **one line** (`MCP_GUARD_REPO`) and
everything else follows.

```bash
export MCP_GUARD_REPO="$HOME/Desktop/walled-ai"      # where this repo lives: edit if you move it
export MCP_GUARD_HOME="$HOME/.mcp-guard"             # MCP Guard's data folder (the default)
export MCP_GUARD_RUNTIME="$MCP_GUARD_HOME/runtime"   # the copy the services run (see 6.2)
```

To keep them in every new terminal, add the same three lines to `~/.zshrc` and
run `source ~/.zshrc`. Check them with:

```bash
echo "$MCP_GUARD_REPO" "$MCP_GUARD_RUNTIME" && ls "$MCP_GUARD_REPO/backend"
```

| Variable | Default | Used for |
|---|---|---|
| `MCP_GUARD_REPO` | `~/Desktop/walled-ai` | Source code: `backend/`, `desktop/`, `scripts/` |
| `MCP_GUARD_HOME` | `~/.mcp-guard` | Token, audit DB, config, logs. MCP Guard itself reads this variable. |
| `MCP_GUARD_RUNTIME` | `~/.mcp-guard/runtime` | The installed copy that the background services run |

> `scripts/autostart.sh` always uses `~/.mcp-guard`, so keep `MCP_GUARD_HOME`
> at its default if you use auto-start (7c).

### 6.1 Backend

```bash
cd "$MCP_GUARD_REPO"
python3 -m venv .venv
source .venv/bin/activate
pip install -e "backend[dev]"
cd backend && pytest          # 14 tests should pass
```

### 6.2 Runtime copy

macOS doesn't let background programs read the **Desktop** folder without
permission, so the services can't run MCP Guard from a repo under `~/Desktop`.
Install a copy outside it (`scripts/autostart.sh install` also does this for you):

```bash
python3 -m venv "$MCP_GUARD_RUNTIME"
"$MCP_GUARD_RUNTIME/bin/pip" install "$MCP_GUARD_REPO/backend"
cp "$MCP_GUARD_REPO/backend/examples/demo_server.py" "$MCP_GUARD_RUNTIME/"
```

After changing backend code, refresh it (or re-run `scripts/autostart.sh install`):

```bash
"$MCP_GUARD_RUNTIME/bin/pip" install --force-reinstall --no-deps "$MCP_GUARD_REPO/backend"
```

### 6.3 Choose the servers to protect (`gateway.json`)

This one file is the list of MCP servers, for Desktop **and** web. It uses Claude
Desktop's `mcpServers` format. Create it with your paths filled in (once):

```bash
cat > "$MCP_GUARD_HOME/gateway.json" <<EOF
{
  "mcpServers": {
    "demo": {
      "command": "$MCP_GUARD_RUNTIME/bin/python",
      "args": ["$MCP_GUARD_RUNTIME/demo_server.py"]
    },
    "filesystem": {
      "command": "$(which npx)",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "$HOME/Documents"]
    }
  }
}
EOF
```

**To protect another MCP server:** add its entry here, exactly as you would have
put it in Claude Desktop's config (full paths, since services don't use your
terminal's PATH), then restart the gateway (`scripts/autostart.sh install`, or
Ctrl+C and start it again). Its tools appear in both Claudes as `<server>__<tool>`.

### 6.4 Connect Claude Desktop

Claude Desktop starts `mcp-remote`, which connects to the local gateway with the
gateway key. Quit Claude (**Cmd+Q**), back up its config, then put a single
`mcp-guard` entry in `mcpServers` (keep `preferences` and any other keys).
This prints the entry with your real paths and key filled in:

```bash
CFG=~/Library/Application\ Support/Claude/claude_desktop_config.json
cp "$CFG" "$CFG.backup"
cat <<EOF
"mcpServers": {
  "mcp-guard": {
    "command": "$(which npx)",
    "args": ["-y", "mcp-remote@0.16.7", "http://127.0.0.1:8766/mcp",
             "--header", "X-Auth-Token:\${MCP_GUARD_KEY}"],
    "env": {
      "MCP_GUARD_KEY": "$("$MCP_GUARD_RUNTIME/bin/mcp-guard" gateway-key)",
      "PATH": "$(dirname "$(which npx)"):/usr/local/bin:/usr/bin:/bin"
    }
  }
}
EOF
open -e "$CFG"
```

Paste it in place of the old `mcpServers`, save, check the JSON, and reopen Claude:

```bash
python3 -m json.tool "$CFG" > /dev/null && echo OK
```

Notes:
- `${MCP_GUARD_KEY}` is filled in by `mcp-remote` from `env`, so the key sits in
  one place in the file. The config file is only readable by you.
- `PATH` in `env` is needed because Claude doesn't use your terminal's PATH, and
  `npx` needs to find `node`.
- `mcp-remote` is pinned to a tested version. Bump it deliberately.
- If you create a new gateway key, update `MCP_GUARD_KEY` here **and** the
  claude.ai connector.

### 6.5 Desktop app

```bash
cd "$MCP_GUARD_REPO/desktop"
npm install
```

## 7. Daily use

**Terminal 1: daemon** (must always be running):
```bash
cd "$MCP_GUARD_REPO/backend"
source ../.venv/bin/activate
mcp-guard daemon
```

**Terminal 2: gateway** (Claude Desktop and claude.ai both connect to it):
```bash
cd "$MCP_GUARD_REPO/backend"
source ../.venv/bin/activate
mcp-guard gateway --config "$MCP_GUARD_HOME/gateway.json"
```

**Terminal 3: desktop app:**
```bash
cd "$MCP_GUARD_REPO/desktop"
npm run dev
```

Or skip the terminals and let them start at login: `scripts/autostart.sh install` (7c).

Bottom-left of the app should say **Protected** (green).

**Try it:** in Claude ask *"Use demo__echo to say hello"* → click **Allow once**
in Claude → the MCP Guard popup appears → **Allow** → Claude says "hello". Ask it
to use `demo__write_note` and click **Block** to see a refusal. The same works on
claude.ai once 7b is set up.

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

## 7b. Claude on the web (claude.ai)

claude.ai uses the **same gateway** as Claude Desktop, with the same servers,
rules, popup and audit log. The only difference is how it reaches the gateway:
claude.ai runs in Anthropic's cloud, so it needs a public HTTPS URL, which ngrok
provides.

```
claude.ai ──HTTPS──▶ ngrok (public URL) ──▶ mcp-guard gateway (127.0.0.1:8766/mcp)
```

| | Claude Desktop | claude.ai |
|---|---|---|
| Reaches the gateway via | `mcp-remote`, locally (`http://127.0.0.1:8766/mcp`) | ngrok tunnel (`https://….ngrok-free.dev/mcp`) |
| Needs internet / ngrok | No | Yes |
| Key sent as | `X-Auth-Token` header (set in Claude's config) | Header set in the connector |
| Servers, tool names, rules, popup, audit log | Same | Same |

**Expose the gateway** (gateway, daemon and app running as in section 7):

```bash
# public HTTPS URL for the gateway (only port 8766, never 8765)
ngrok http 8766
```

Or let it start at login with your fixed domain:
`scripts/autostart.sh install --with-ngrok https://YOUR-DOMAIN.ngrok-free.dev` (7c).

**Connect claude.ai:** Settings → Connectors → **Add custom connector**

- **URL:** your ngrok address plus `/mcp`, e.g. `https://YOUR-DOMAIN.ngrok-free.dev/mcp`
- **Authentication:** *No sign-in*, then under **Request headers** pick the
  standard **Authorization** header with value `Bearer <key>`, where `<key>` is
  the output of `mcp-guard gateway-key`. **X-Auth-Token**, **X-API-Key**,
  **X-API-Token** and **API-Key** also work: use them with the bare key, no
  `Bearer`. (Custom header names need Anthropic's approval, so pick one from the
  list rather than typing your own.)

Then in a chat: *"Use demo__echo to say hello"* → popup → Allow.

**Rules for the web setup:**

- The **gateway key** (`~/.mcp-guard/gateway_key`) goes to claude.ai and into
  Claude Desktop's config. The **daemon token** (`~/.mcp-guard/token`) never
  leaves your Mac.
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
cd "$MCP_GUARD_REPO/desktop" && npm run package
```

**Install and auto-start everything** (stop any daemon/gateway/ngrok you started
in terminals first, then quit the dev app):

```bash
cd "$MCP_GUARD_REPO"
scripts/autostart.sh install                                   # daemon + gateway + app
scripts/autostart.sh install --with-ngrok https://YOUR-DOMAIN.ngrok-free.dev   # also expose for claude.ai
scripts/autostart.sh status
scripts/autostart.sh uninstall
```

| What | How it starts | Logs |
|---|---|---|
| Daemon | LaunchAgent `com.mcpguard.daemon` (restarts if it crashes) | `~/.mcp-guard/logs/daemon.log` |
| Gateway | LaunchAgent `com.mcpguard.gateway`, config `~/.mcp-guard/gateway.json` (Desktop + web) | `~/.mcp-guard/logs/gateway.log` |
| ngrok (optional) | LaunchAgent `com.mcpguard.ngrok`, fixed URL | `~/.mcp-guard/logs/ngrok.log` |
| Desktop app | `/Applications/MCP Guard.app`, login item (toggle "Open at login" in its menu-bar menu) | — |

Services run from `~/.mcp-guard/runtime`, because macOS stops background programs
from reading the Desktop folder. `install` refreshes that copy from `backend/`, so
run it again after changing backend code.

**When to use the services vs. terminals:**

| Situation | What to do |
|---|---|
| Changing code | Don't install. Run the daemon, gateway and `npm run dev` in terminals (section 7), plus ngrok if you're testing claude.ai (7b). |
| Want MCP Guard always on | Build the app (`npm run package`), stop terminal processes, then run `scripts/autostart.sh install` once. It starts at every login. |
| Changed backend code | Run `scripts/autostart.sh install` again to refresh the runtime copy. |
| Back to working in terminals | Run `scripts/autostart.sh uninstall` first. The services hold ports 8765/8766. |

Don't run both ways at once: they need the same ports.

**Why claude.ai isn't auto-started by default:** `--with-ngrok` is opt-in because
an always-on tunnel makes the gateway reachable from the internet whenever you're
logged in (protected only by the gateway key). Claude Desktop reaches the gateway
locally and doesn't need it, so nothing is exposed unless you ask. It also needs your reserved ngrok domain: a
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
| POST | `/api/requests` | gateway | Submit a tool call |
| GET | `/api/requests/{id}/wait` | gateway | Wait for the decision (long poll) |
| POST | `/api/requests/{id}/cancel` | gateway | Claude gave up |
| POST | `/api/requests/{id}/result` | gateway | Report execution result |
| GET | `/api/requests?status=&server=&limit=` | app | Pending list / history |
| GET | `/api/requests/{id}` | app | One request |
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
| `zsh: command not found: mcp-guard` | venv not activated | `source "$MCP_GUARD_REPO/.venv/bin/activate"` |
| App says **Daemon offline** | daemon not running | Start `mcp-guard daemon` |
| Claude says tool was blocked "fail-closed" | daemon not running | Start the daemon |
| Request shows **EXPIRED** | no answer in time | Answer faster, or raise the timeout in Settings |
| `demo__*` / `filesystem__*` tools missing in Claude Desktop | Claude not fully restarted, or gateway not running | **Cmd+Q** and reopen; `scripts/autostart.sh status` |
| Claude Desktop log shows `401` | Wrong/old key in `MCP_GUARD_KEY` | Put the output of `mcp-guard gateway-key` in Claude's config (6.4) |
| Claude Desktop log: `env: node: No such file or directory` | `PATH` missing in the config's `env` | Add it (6.4) |
| New server not showing up | Gateway not restarted after editing `gateway.json` | `scripts/autostart.sh install`, then restart Claude |
| Log: `PermissionError … pyvenv.cfg` | macOS blocks Desktop access | Use the runtime copy (6.2) |
| Gateway log | — | `~/.mcp-guard/logs/gateway.log` |
| `json.tool` error "Extra data" | extra `}` in Claude config | Remove it |
| Claude Desktop's side | — | `~/Library/Logs/Claude/mcp-server-mcp-guard.log` |

## 11. Known limitations / next steps

- The packaged app is built for Apple Silicon only (`mac-arm64`) and is ad-hoc
  signed, not notarized.
- Claude Desktop shows its own "Allow once" prompt before MCP Guard's popup;
  that's Claude's feature and MCP Guard can't remove it.
- Only **tool calls** are gated; resource reads and prompts pass through.
- The gateway doesn't yet forward "tool list changed" notifications or the original
  server's requests back to Claude (such as sampling).
- Tool names carry a server prefix (`filesystem__write_file`), because one gateway
  serves several servers.

### Alternative: per-server stdio proxy (no gateway)

`mcp-guard proxy` still exists for Claude Desktop setups without the gateway. You
wrap each server in Claude's config: `"command": "$MCP_GUARD_RUNTIME/bin/mcp-guard"`,
`"args": ["proxy", "--name", "<name>", "--", <original command and args>]`. Tool
names then have no prefix, but each server is configured separately and claude.ai
can't use it. Don't use both for the same server, or you'll be asked twice.
