#!/bin/bash
# MCP Guard auto-start for macOS.
#
#   scripts/autostart.sh install [--with-ngrok URL]   start daemon + gateway (+ ngrok) at login, install the app
#   scripts/autostart.sh status                      show what is running
#   scripts/autostart.sh uninstall                   stop everything and remove auto-start
#
# Background services run from ~/.mcp-guard/runtime, because macOS blocks
# background programs from reading the Desktop folder (where the project lives).
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="$HOME/.mcp-guard"
RUNTIME="$HOME_DIR/runtime"
LOGS="$HOME_DIR/logs"
AGENTS="$HOME/Library/LaunchAgents"
APP_SRC="$PROJECT/desktop/dist/mac-arm64/MCP Guard.app"
APP_DST="/Applications/MCP Guard.app"
UID_DOMAIN="gui/$(id -u)"
LABELS=(com.mcpguard.daemon com.mcpguard.gateway com.mcpguard.ngrok)
PATH_ENV="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
port_owner() { lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1" (pid "$2")"}'; }

write_plist() { # label, then program arguments
  local label="$1"; shift
  local args=""
  for a in "$@"; do args+="    <string>$a</string>"$'\n'; done
  cat > "$AGENTS/$label.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
$args  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$PATH_ENV</string></dict>
  <key>WorkingDirectory</key><string>$HOME_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOGS/${label#com.mcpguard.}.log</string>
  <key>StandardErrorPath</key><string>$LOGS/${label#com.mcpguard.}.log</string>
</dict>
</plist>
EOF
}

load() { launchctl bootout "$UID_DOMAIN/$1" 2>/dev/null || true; launchctl bootstrap "$UID_DOMAIN" "$AGENTS/$1.plist"; }

install() {
  local ngrok_url=""
  if [[ "${1:-}" == "--with-ngrok" ]]; then
    ngrok_url="${2:?usage: install --with-ngrok https://your-domain.ngrok-free.dev}"
  fi

  # Our own services from a previous install would hold the ports; stop them first.
  for l in "${LABELS[@]}"; do launchctl bootout "$UID_DOMAIN/$l" 2>/dev/null || true; done
  sleep 1
  for port in 8765 8766; do
    owner="$(port_owner $port)"
    if [[ -n "$owner" ]]; then
      echo "Port $port is in use by $owner."
      echo "Stop the mcp-guard daemon/gateway you started in a terminal (Ctrl+C), then run this again."
      exit 1
    fi
  done
  if [[ -n "$ngrok_url" ]] && pgrep -x ngrok >/dev/null; then
    echo "ngrok is already running in a terminal. Stop it (Ctrl+C), then run this again."
    exit 1
  fi

  say "1/4 Updating the runtime copy in $RUNTIME"
  [[ -x "$RUNTIME/bin/python" ]] || python3 -m venv "$RUNTIME"
  "$RUNTIME/bin/pip" install -q --upgrade "$PROJECT/backend"
  cp "$PROJECT/backend/examples/demo_server.py" "$RUNTIME/demo_server.py"
  mkdir -p "$LOGS" "$AGENTS"
  if [[ ! -f "$HOME_DIR/gateway.json" ]]; then
    cp "$PROJECT/backend/examples/gateway.example.json" "$HOME_DIR/gateway.json"
    echo "   created $HOME_DIR/gateway.json (edit it to change which servers the gateway serves)"
  fi

  say "2/4 Registering login services"
  write_plist com.mcpguard.daemon "$RUNTIME/bin/mcp-guard" daemon
  write_plist com.mcpguard.gateway "$RUNTIME/bin/mcp-guard" gateway --config "$HOME_DIR/gateway.json"
  load com.mcpguard.daemon
  sleep 2
  load com.mcpguard.gateway
  if [[ -n "$ngrok_url" ]]; then
    write_plist com.mcpguard.ngrok "$(command -v ngrok)" http 8766 --url "$ngrok_url" --log stdout
    load com.mcpguard.ngrok
    echo "$ngrok_url" > "$HOME_DIR/ngrok_url"
  else
    rm -f "$AGENTS/com.mcpguard.ngrok.plist"
    echo "   ngrok not auto-started (add --with-ngrok URL to expose the gateway at login)"
  fi

  say "3/4 Installing the app to $APP_DST"
  if [[ -d "$APP_SRC" ]]; then
    osascript -e 'quit app "MCP Guard"' 2>/dev/null || true
    rm -rf "$APP_DST"
    ditto "$APP_SRC" "$APP_DST"
    open "$APP_DST"   # first launch turns on "Open at login"
  else
    echo "   app not built yet: cd desktop && npm run package   (then run install again)"
  fi

  say "4/4 Checking"
  sleep 4
  status
}

uninstall() {
  for l in "${LABELS[@]}"; do
    launchctl bootout "$UID_DOMAIN/$l" 2>/dev/null && echo "stopped $l" || true
    rm -f "$AGENTS/$l.plist"
  done
  echo "Auto-start removed. The app stays in /Applications; untick 'Open at login' in its"
  echo "menu-bar menu, or delete it. Your data in $HOME_DIR is kept."
}

status() {
  for l in "${LABELS[@]}"; do
    if [[ -f "$AGENTS/$l.plist" ]]; then
      pid="$(launchctl print "$UID_DOMAIN/$l" 2>/dev/null | awk '/^\tpid =/ {print $3}')"
      printf '  %-22s %s\n' "$l" "${pid:+running (pid $pid)}${pid:-NOT running, see $LOGS/${l#com.mcpguard.}.log}"
    else
      printf '  %-22s %s\n' "$l" "not installed"
    fi
  done
  printf '  %-22s %s\n' "daemon health" "$(curl -s -m 2 localhost:8765/api/health || echo unreachable)"
  code="$(curl -s -m 2 -o /dev/null -w '%{http_code}' localhost:8766/mcp || true)"
  printf '  %-22s %s\n' "gateway" "$([[ $code == 401 ]] && echo 'up (locked, as expected)' || echo "unreachable ($code)")"
  if [[ -f "$HOME_DIR/ngrok_url" && -f "$AGENTS/com.mcpguard.ngrok.plist" ]]; then
    url="$(cat "$HOME_DIR/ngrok_url")"
    code="$(curl -s -m 10 -o /dev/null -w '%{http_code}' -X POST "$url/mcp" || true)"
    printf '  %-22s %s\n' "public URL" "$url/mcp -> $([[ $code == 401 ]] && echo 'reachable and locked' || echo "problem ($code)")"
  fi
  printf '  %-22s %s\n' "app" "$(pgrep -f 'MCP Guard.app/Contents/MacOS' >/dev/null && echo running || echo 'not running')"
}

case "${1:-}" in
  install) shift; install "$@" ;;
  uninstall) uninstall ;;
  status) status ;;
  *) sed -n '2,8p' "$0"; exit 1 ;;
esac
