#!/usr/bin/env bash
# One-click launcher: bring the server up (if it is not already) and start the
# game, then report the ports that must be reachable.
#
# Local paths live in ``local-env.sh`` next to the repo root (untracked), e.g.
#
#     HEX_CLIENT_DIR="/d/game/HEX SHARDS OF FATE"
#     HEX_CODEX_DATA="$PWD/build/codex-data"
#     HEX_DECK_USER="123"
#     HEX_DECK_WATCH=1        # also run the clipboard deck watcher
#
# Environment overrides are honoured, so nothing here is machine-specific.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
SERVER_PORT="${HEX_SERVER_PORT:-9933}"
PROXY_PORT="${HEX_PROXY_PORT:-8081}"

if [[ -f "$ROOT/local-env.sh" ]]; then
  # shellcheck disable=SC1090
  source "$ROOT/local-env.sh"
fi

log() { echo "[start-game] $*"; }
die() { echo "[start-game] ERROR: $*" >&2; exit 1; }

port_open() { (echo > "/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

wait_port() {
  local port="$1" tries="${2:-120}" i
  for ((i = 1; i <= tries; i++)); do
    port_open "$port" && return 0
    sleep 0.5
  done
  return 1
}

# ---------------------------------------------------------------------------
# 1. Server.  restart.sh must run with the repository root as the working
#    directory: the Original-AI worker command is a relative path.
# ---------------------------------------------------------------------------
if port_open "$SERVER_PORT" && port_open "$PROXY_PORT"; then
  log "server already listening; reusing it (run hex-server/restart.sh stop to reset)"
else
  log "starting server ..."
  cd "$ROOT"
  HEX_USE_SUPERVISOR=0 \
  HEX_ORIGINAL_AI="${HEX_ORIGINAL_AI:-1}" \
  HEX_CLIENT_DLL="${HEX_CLIENT_DLL:-$ROOT/client-runtime/Assembly-CSharp-firstpass.dll}" \
  HEX_CODEX_DATA="${HEX_CODEX_DATA:-$ROOT/build/codex-data}" \
    bash "$ROOT/hex-server/restart.sh"
fi

wait_port "$SERVER_PORT" || die "nothing is listening on $SERVER_PORT"
wait_port "$PROXY_PORT" || die "nothing is listening on $PROXY_PORT"

# ---------------------------------------------------------------------------
# 2. Optional clipboard deck watcher.
# ---------------------------------------------------------------------------
if [[ "${HEX_DECK_WATCH:-0}" == "1" ]]; then
  # The watcher takes its own exclusive lock, so launching it unconditionally
  # is safe: a second copy prints a notice and exits.  Do not gate this on a
  # pidfile -- a pid recorded by PowerShell is not resolvable by kill -0 here,
  # so the check would either miss a live watcher or double-start one.
  mkdir -p "$ROOT/build"
  log "starting deck clipboard watcher ..."
  HEX_CODEX_DATA="${HEX_CODEX_DATA:-}" \
    nohup python -u "$ROOT/scripts/deck_clipboard_watch.py" \
    ${HEX_DECK_USER:+--player "$HEX_DECK_USER"} \
    >> "$ROOT/build/deck-watch.log" 2>&1 &
  log "watcher launched; log: build/deck-watch.log"
fi

# ---------------------------------------------------------------------------
# 3. Game client.
# ---------------------------------------------------------------------------
CLIENT_DIR="${HEX_CLIENT_DIR:-}"
[[ -n "$CLIENT_DIR" ]] || die "HEX_CLIENT_DIR is not set; add it to local-env.sh"
[[ -f "$CLIENT_DIR/Hex.exe" ]] || die "Hex.exe not found under: $CLIENT_DIR"

if [[ "${HEX_SKIP_CLIENT:-0}" == "1" ]]; then
  log "HEX_SKIP_CLIENT=1; not launching the game"
else
  WIN_DIR="$(cygpath -w "$CLIENT_DIR" 2>/dev/null || echo "$CLIENT_DIR")"
  log "launching HEX ..."
  # cmd.exe by absolute path: this environment's PATH can carry unexpanded
  # %SystemRoot% entries, so bare `cmd` does not always resolve.
  /c/Windows/System32/cmd.exe //c start "" "$WIN_DIR\\Hex.exe" >/dev/null 2>&1 \
    || die "failed to launch Hex.exe from $WIN_DIR"
  log "game started; it reads the deck list at login, so re-log after importing"
fi

# ---------------------------------------------------------------------------
# 4. Report.
# ---------------------------------------------------------------------------
echo
echo "============================================================"
echo " HEX private server is up"
echo "============================================================"
printf '  HConnect (game protocol)  127.0.0.1:%s\n' "$SERVER_PORT"
printf '  Auth proxy (HTTP)         127.0.0.1:%s\n' "$PROXY_PORT"
echo
echo "  client config.ini points at both already:"
grep -hE '^(GameServerIP|CZEAuthUrl)=' "$CLIENT_DIR/config.ini" 2>/dev/null | sed 's/^/    /' || true
echo
echo "  logs:  tail -f /tmp/hconnect_log.txt"
echo "         tail -f /tmp/proxy_log.txt"
echo "  stop:  bash hex-server/restart.sh stop"