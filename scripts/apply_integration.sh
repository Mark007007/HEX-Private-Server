#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
HEX="$ROOT/hex-server"

if [[ ! -e "$HEX/.git" ]]; then
  echo "hex-server submodule is not initialized. Run: git submodule update --init --recursive" >&2
  exit 1
fi

mkdir -p "$HEX/integration/deck_import" "$HEX/integration/ai_bridge"
cp "$ROOT/integration/__init__.py" "$HEX/integration/__init__.py"
cp "$ROOT/integration/deck_import/"*.py "$HEX/integration/deck_import/"
cp "$ROOT/integration/ai_bridge/"*.py "$HEX/integration/ai_bridge/"

for name in static.py db.py profile_db.py encoded_decks.py commands.py ai.py; do
  cp "$ROOT/overlay/hex-server/$name" "$HEX/$name"
done

echo "Integration applied to $HEX"
