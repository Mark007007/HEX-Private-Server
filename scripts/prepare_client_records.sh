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

# gamedata_seed.load_records_text skips exactly one leading line of each
# Records file, and scripts/validate_records.py requires that same line to be
# the format marker, so the marker has to take line 0.
#
# extract_records.py also emits a synthetic pseudo-record as line 0 of every
# section: it splits the section body on '$$--$$', and the remainder of the
# section header itself ("$$$---$$$ <SectionName>") is the first piece. That
# pseudo-record contains a literal '$$$---$$$', so if it survives into the
# section stream it terminates the section immediately and every record in it
# parses to zero -- which silently yields an empty client data seed while the
# validator still passes. Drop it when present.
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
    case "$(head -n 1 "$path")" in
      '"$$$---$$$'*) tail -n +2 "$path" ;;
      *) cat "$path" ;;
    esac
  } > "$tmp"
  mv "$tmp" "$path"
done

python3 "$ROOT/scripts/validate_records.py" "$RECORDS_DIR"