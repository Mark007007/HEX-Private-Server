#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"

die() {
  echo
  echo "[HEX] ERROR: $*" >&2
  exit 1
}

echo "============================================================"
echo " HEX Private Server - one-click setup / validation / start"
echo "============================================================"
echo

command -v git >/dev/null 2>&1 || die "Git is not installed."
command -v python3 >/dev/null 2>&1 || die "Python 3 is not installed."
command -v dotnet >/dev/null 2>&1 || die ".NET 10 SDK is not installed."

echo "[1/5] Syncing pinned upstreams ..."
bash "$ROOT/scripts/pull_upstreams.sh"

echo "[2/5] Applying integration ..."
bash "$ROOT/scripts/apply_integration.sh"

PYTHON="$ROOT/.venv/bin/python"
PIP="$ROOT/.venv/bin/pip"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$ROOT/.venv/Scripts/python.exe"
  PIP="$ROOT/.venv/Scripts/pip.exe"
fi
if [[ ! -x "$PYTHON" ]]; then
  python3 -m venv "$ROOT/.venv"
  PYTHON="$ROOT/.venv/bin/python"
  PIP="$ROOT/.venv/bin/pip"
  if [[ ! -x "$PYTHON" ]]; then
    PYTHON="$ROOT/.venv/Scripts/python.exe"
    PIP="$ROOT/.venv/Scripts/pip.exe"
  fi
fi
[[ -x "$PYTHON" ]] || die "Could not create the Python virtual environment."

"$PIP" install -q -r "$ROOT/hex-server/requirements.txt"

echo "[3/5] Running integration tests ..."
"$PYTHON" -m unittest discover -s "$ROOT/tests" -v

echo "[4/5] Building Original-AI worker ..."
dotnet build "$ROOT/legacy-ai-worker/LegacyAiWorker.csproj" -c Release --nologo

echo "[5/5] Running Original-AI self-check ..."
HEX_CLIENT_DLL="${HEX_CLIENT_DLL:-$ROOT/client-runtime/Assembly-CSharp-firstpass.dll}"
export HEX_CLIENT_DLL
[[ -f "$HEX_CLIENT_DLL" ]] || die "Original-AI DLL not found: $HEX_CLIENT_DLL"
AI_DLL="$ROOT/legacy-ai-worker/bin/Release/net10.0/LegacyAiWorker.dll"
AI_OUTPUT="$(
  printf "%s\n%s\n" \
    "{\"protocol\":1,\"request_id\":\"selftest-health\",\"action\":\"health\",\"payload\":{}}" \
    "{\"protocol\":1,\"request_id\":\"selftest-probe\",\"action\":\"probe\",\"payload\":{\"session_uid64\":10001,\"ai_player_uid64\":20001,\"human_player_uid64\":20002,\"ai_position\":1,\"session_name\":\"HEX AI SelfTest\",\"session_flags\":128}}" \
  | HEX_CLIENT_DLL="$HEX_CLIENT_DLL" dotnet "$AI_DLL"
)"
printf "%s\n" "$AI_OUTPUT"
grep -q ""status":"ready"" <<<"$AI_OUTPUT" || die "Original-AI health check failed."
grep -q "selftest-probe" <<<"$AI_OUTPUT" || die "Original-AI session probe did not return."

RECORDS_DIR="${HEX_RECORDS:-$ROOT/hex-server/Records}"
if [[ -n "${HEX_GAMEDATA:-}" ]]; then
  echo
echo "[HEX] HEX_GAMEDATA supplied; generating Records ..."
  HEX_GAMEDATA="$HEX_GAMEDATA" bash "$ROOT/scripts/prepare_client_records.sh"
fi

if [[ -d "$RECORDS_DIR" ]] && "$PYTHON" "$ROOT/scripts/validate_records.py" "$RECORDS_DIR" >/dev/null 2>&1; then
  echo
echo "============================================================"
echo " All checks passed. Starting HEX server."
echo "============================================================"
  export HEX_USE_SUPERVISOR="${HEX_USE_SUPERVISOR:-0}"
  exec bash "$ROOT/hex-server/restart.sh"
fi

echo
echo "============================================================"
echo " BASE INSTALLATION COMPLETE"
echo "============================================================"
echo
echo "Python integration tests: PASS"
echo "Original-AI build:        PASS"
echo "Original-AI self-check:   PASS"
echo
echo "Full server was not started because client-derived Records"
echo "are not available."
echo
echo "The only external game-data dependency is your own:"
echo "  Data/gamedata"
echo
echo "Run again with:"
echo "  HEX_GAMEDATA=/path/to/Data/gamedata ./start.sh"
echo
echo "The script will generate Records and start the server automatically."
