#!/usr/bin/env bash
# Stop everything start-game.sh started.
#
# The launcher detaches its services on purpose (setsid/nohup plus `start` for
# the game), so closing its console window does not stop them -- this is the
# counterpart that does.
#
#   bash scripts/stop-game.sh          # services + watcher
#   bash scripts/stop-game.sh --all    # also close the game client
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
KILL_CLIENT=0
[[ "${1:-}" == "--all" ]] && KILL_CLIENT=1

log() { echo "[stop-game] $*"; }

log "stopping the Hex services ..."
bash "$ROOT/hex-server/restart.sh" stop || true

log "stopping the deck clipboard watcher ..."
# The watcher holds an exclusive lock on build/deck-watch.lock; killing by
# image path is the reliable way on Windows, where a pid written by one shell
# is not always resolvable by another.  Match both interpreter names: the
# launcher may reach either ``python.exe`` or ``python3.exe``.
PS="/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
[ -x "$PS" ] || PS="powershell.exe"
"$PS" -NoProfile -NonInteractive -Command \
  "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='python3.exe'\" | Where-Object { \$_.CommandLine -match 'deck_clipboard_watch' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }" \
  >/dev/null 2>&1 || true
rm -f "$ROOT/build/deck-watch.lock"

if [[ "$KILL_CLIENT" == "1" ]]; then
  log "closing the game client ..."
  "$PS" -NoProfile -NonInteractive -Command \
    "Get-Process -Name Hex -ErrorAction SilentlyContinue | Stop-Process -Force" \
    >/dev/null 2>&1 || true
fi

for port in "${HEX_SERVER_PORT:-9933}" "${HEX_PROXY_PORT:-8081}"; do
  if (echo > "/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    log "WARNING: port $port is still listening"
  else
    log "port $port released"
  fi
done
log "done${KILL_CLIENT:+ (game client included)}"