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


DINGLER="$ROOT/upstream/Dingler-FrostRingArena"
CLIENT_RUNTIME="$ROOT/client-runtime"

if [[ -d "$DINGLER" && -d "$CLIENT_RUNTIME" ]]; then
  mkdir -p "$DINGLER/DLLs" "$DINGLER/Dingler.Terminal/bin/Release/net10.0"
  for name in Assembly-CSharp-firstpass.dll NCalc.dll ICSharpCode.SharpZipLib.dll; do
    cp "$CLIENT_RUNTIME/$name" "$DINGLER/DLLs/$name"
  done
  for name in Assembly-CSharp-firstpass.dll ICSharpCode.SharpZipLib.dll NCalc.dll SampleClassLibrary.dll System.EnterpriseServices.dll System.Web.Services.dll UnityEngine.dll; do
    cp "$CLIENT_RUNTIME/$name" "$DINGLER/Dingler.Terminal/bin/Release/net10.0/$name"
  done
  echo "Dingler client DLLs staged using the upstream documented layout."
else
  echo "Dingler submodule or client-runtime directory not present; skipping DLL staging."
fi

echo "Integration applied to $HEX"
