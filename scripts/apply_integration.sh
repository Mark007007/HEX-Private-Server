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

# Whole-file overlay.  hconnect_server.py is upstream's largest source file and
# carries our patches (deck-inbox tick, ActiveGems 64-bit parse, GetDeckInfo gem
# forwarding), so it is overlaid too -- which also means an upstream change to
# that file has to be merged into overlay/hex-server/hconnect_server.py by hand.
for name in hconnect_server.py deck_inbox.py static.py db.py profile_db.py \
            encoded_decks.py commands.py ai.py; do
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

# The pinned upstream restart.sh contains the original developer absolute checkout path.
# Replace it with the current submodule directory so the project is portable.
python3 - "$HEX/restart.sh" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
old = 'BASE_DIR="/home/ianutley/Hex"'
dollar = chr(36)
new = 'BASE_DIR="' + dollar + '(cd -- "' + dollar + '(dirname -- "' + dollar + '{BASH_SOURCE[0]}")" && pwd -P)"'
if old in text:
    path.write_text(text.replace(old, new, 1))
PY

echo "Integration applied to $HEX"
