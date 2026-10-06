#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GAMEDATA="${HEX_GAMEDATA:-${GAMEDATA:-}}"
RECORDS_DIR="${HEX_RECORDS:-$ROOT/hex-server/Records}"

if [[ -z "$GAMEDATA" ]]; then
  cat >&2 <<'EOF'
Usage:
  HEX_GAMEDATA=/path/to/Data/gamedata bash scripts/prepare_client_records.sh

Optional:
  HEX_RECORDS=/path/to/Records
EOF
  exit 2
fi

if [[ ! -f "$GAMEDATA" ]]; then
  echo "gamedata file not found: $GAMEDATA" >&2
  exit 1
fi

if [[ "$RECORDS_DIR" != /* ]]; then
  RECORDS_DIR="$ROOT/$RECORDS_DIR"
fi
mkdir -p "$RECORDS_DIR"

echo "Extracting client-derived Records from:"
echo "  $GAMEDATA"
echo "into:"
echo "  $RECORDS_DIR"

(
  cd "$ROOT/hex-server"
  GAMEDATA="$GAMEDATA" RECORDS_DIR="$RECORDS_DIR" \
    python3 AssetExtraction/extract_records.py
)

# The pinned server seed loader intentionally skips the first physical line
# of each Records file. Add the format marker it expects after extraction so
# the first real record is not silently discarded.
for section in \
  AbilityEffectConditionTemplate \
  AbilityEffectTemplate \
  AbilityTargetTemplate \
  AbilityTemplate \
  CardCounterTemplate \
  CardTemplate \
  ChampionClassData \
  ChampionTalentData \
  ChampionTemplate \
  ConversationTemplate \
  DeckTemplate \
  EncounterDeck \
  InventoryItemData \
  QuestTemplate \
  SceneData
do
  path="$RECORDS_DIR/$section.jsonl"
  tmp="$path.tmp"
  {
    printf '%s\n' '# HEX-PRIVATE-SERVER Records v1'
    cat "$path"
  } > "$tmp"
  mv "$tmp" "$path"
done

python3 "$ROOT/scripts/validate_records.py" "$RECORDS_DIR"