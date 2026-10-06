#!/usr/bin/env python3
"""Generate the Hex Codex data folder (ids.json + gems.json) for deck import.

The Hex Codex deck builder inlines its complete catalogs in the page it serves,
so the data folder can be rebuilt locally instead of fetched from the site's
repository.  Three disjoint site-id ranges are involved:

    cards      2 - 3373   -> card_templates
    champions  3374 - 3493 -> champion_templates_extended
    gems       3494 - 3566 -> gem_templates

Every site name resolves against the local database by name (all three match
100%).  Card names repeat once per printing, so a name is resolved through a
deduplicated catalog built from the Records snapshot: one row per
``m_DesignerCardId`` (``DELETE*`` entries are dropped), plus designer-id-less
rows collapsed by (name, cost, type, subtype).  When a name still has several
candidates a printing the player owns is preferred, otherwise the lowest guid
wins, which keeps repeated runs stable.

Usage::

    python scripts/build_codex_ids.py --cards-html path/to/deck-builder.html
    python scripts/build_codex_ids.py --cards-html https://.../deck-builder/ --player 123

Then point the server at the output folder::

    HEX_CODEX_DATA=<out>/codex-data
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import urllib.request

REPO = Path(__file__).resolve().parents[1]
DEFAULT_HTML = Path(os.environ.get("TEMP", "/tmp")) / "hexcodex_db.html"

_CARD_ROW = re.compile(r"^(\d+)\|")
_GEM = re.compile(
    r'\{"id":(\d+),"name":"((?:[^"\\]|\\.)*)","socket":"(?:[^"\\]|\\.)*",'
    r'"kind":"(?:[^"\\]|\\.)*"')
_CHAMPION = re.compile(
    r'\{"id":(\d+),"name":"((?:[^"\\]|\\.)*)","href":"/champions/')


def load_page(source: str) -> str:
    if source.startswith(("http://", "https://")):
        print(f"fetching {source} ...")
        with urllib.request.urlopen(source, timeout=60) as response:
            return response.read().decode("utf-8", "replace")
    return Path(source).read_text(encoding="utf-8", errors="replace")


def extract_site_catalogs(page: str) -> tuple[dict[int, str], dict[int, str], dict[int, str]]:
    """Return (cards, champions, gems), each mapping site id -> name."""
    cards: dict[int, str] = {}
    for line in page.replace("\\n", "\n").split("\n"):
        if not _CARD_ROW.match(line):
            continue
        fields = line.split("|")
        if len(fields) < 3:
            continue
        cards[int(fields[0])] = html_lib.unescape((fields[2] or "").strip())

    # The JSON catalogs are inlined inside a JS string, so they are escaped
    # twice before they are readable.
    norm = html_lib.unescape(page).replace('\\"', '"').replace("\\\\", "\\")
    champions = {int(i): n.strip() for i, n in _CHAMPION.findall(norm)}
    gems = {int(i): n.strip() for i, n in _GEM.findall(norm)}
    return cards, champions, gems


def _record(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(re.sub(r",\s*([}\]])", r"\1", value))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def build_card_index(records_dir: Path, owned: set[str]) -> dict[str, list[str]]:
    """name(lower) -> candidate guids, best candidate first.

    Every row is indexed so that no name can go missing.  ``m_DesignerCardId``
    is an *art* id rather than a card identity -- several of them are shared by
    two or more different cards (S1_C235 is both "Cavern Commando" and "Master
    Theorycrafter"), so collapsing to one row per designer id silently drops
    those names.  The id is only used to rank candidates, never to filter.
    """
    path = records_dir / "CardTemplate.jsonl"
    rows = [r for r in (_record(l) for l in
                        path.read_text(encoding="utf-8", errors="replace").splitlines()[1:])
            if r]

    collected: dict[str, dict[tuple, tuple[str, str]]] = {}
    for row in rows:
        mid = row.get("m_Id")
        guid = str(mid.get("m_Guid") or "").strip() if isinstance(mid, dict) else ""
        name = str(row.get("m_Name") or "").strip()
        if not guid or not name:
            continue
        value = row.get("m_DesignerCardId")
        art_id = value.strip() if isinstance(value, str) else ""
        # Printings of one card differ only by set/guid; fold them together so
        # "several candidates" reports real card-level ambiguity rather than
        # counting reprints.
        signature = (row.get("m_ResourceCost"), row.get("m_CardType"),
                     row.get("m_CardSubtype"), row.get("m_BaseAttackValue"),
                     row.get("m_BaseDefenseValue"))
        collected.setdefault(name.casefold(), {}).setdefault(signature, (guid, art_id))

    index: dict[str, list[str]] = {}
    for name, by_signature in collected.items():
        index[name] = [
            guid for guid, _ in sorted(
                by_signature.values(),
                key=lambda kv: (kv[1].upper().startswith("DELETE"),
                                kv[0] not in owned, kv[0]))
        ]
    return index


def _lookup(conn, table: str, name: str, columns: str, order: str = "guid"):
    # Some seeded names carry a trailing space ("Bunoshi the Ruthless "), so the
    # comparison trims both sides rather than relying on exact equality.
    row = conn.execute(
        f"SELECT {columns} FROM {table} "
        f"WHERE TRIM(LOWER(name))=TRIM(LOWER(?)) ORDER BY {order} LIMIT 1",
        (name,)).fetchone()
    return row[0] if row else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cards-html", default=str(DEFAULT_HTML),
                        help="saved deck-builder page, or its URL")
    parser.add_argument("--out", default=str(REPO / "build" / "codex-data"))
    parser.add_argument("--records", default=str(REPO / "hex-server" / "Records"))
    parser.add_argument("--db", default=str(REPO / "hex-server" / "hconnect.db"))
    parser.add_argument("--player", default=None,
                        help="player name or id whose owned printings are preferred")
    args = parser.parse_args()

    source = args.cards_html
    if not source.startswith(("http://", "https://")) and not Path(source).is_file():
        print(f"card page not found: {source}\n"
              f"Save the deck-builder page, or pass a URL with --cards-html",
              file=sys.stderr)
        return 2

    page = load_page(source)
    cards, champions, gems = extract_site_catalogs(page)
    print(f"site catalogs: {len(cards)} cards, {len(champions)} champions, {len(gems)} gems")
    if not cards:
        print("no card rows found in the page; is it the deck builder?", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    owned: set[str] = set()
    if args.player:
        # Accept either the numeric id or the display name; a bare "123" is far
        # more likely to be the name than a 19-digit id.
        row = None
        try:
            row = conn.execute("SELECT id FROM users WHERE id=?",
                               (int(args.player),)).fetchone()
        except ValueError:
            pass
        if not row:
            row = conn.execute("SELECT id FROM users WHERE LOWER(name)=LOWER(?)",
                               (args.player,)).fetchone()
        if not row:
            print(f"player {args.player!r} not found", file=sys.stderr)
            return 2
        owned = {str(r[0]) for r in conn.execute(
            "SELECT DISTINCT template_guid FROM card_instances WHERE user_id=?", (row[0],))}
        print(f"preferring printings owned by player {args.player!r}: {len(owned)} templates")

    card_index = build_card_index(Path(args.records), owned)

    entries = []
    unresolved: list[str] = []
    ambiguous = 0

    for site_id, name in sorted(cards.items()):
        guids = card_index.get(name.casefold(), [])
        if len(guids) > 1:
            ambiguous += 1
        if not guids:
            unresolved.append(f"card {site_id} {name!r}")
            continue
        entries.append([site_id, guids[0], "card", name])

    for site_id, name in sorted(champions.items()):
        guid = _lookup(conn, "champion_templates_extended", name, "guid",
                       "CASE WHEN champion_class IS NULL OR champion_class='None' "
                       "THEN 1 ELSE 0 END, guid")
        if not guid:
            unresolved.append(f"champion {site_id} {name!r}")
            continue
        entries.append([site_id, str(guid), "champion", name])

    gem_rows = []
    for site_id, name in sorted(gems.items()):
        key = _lookup(conn, "gem_templates", name, "gem_type_name", "gem_type")
        if not key:
            unresolved.append(f"gem {site_id} {name!r}")
            continue
        entries.append([site_id, str(key), "gem", name])
        gem_rows.append({"id": str(key), "type": str(key)})

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ids.json").write_text(
        json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")
    (out / "gems.json").write_text(
        json.dumps({"gems": gem_rows}, ensure_ascii=False), encoding="utf-8")

    print(f"\nwrote {out / 'ids.json'}  ({len(entries)} entries)")
    print(f"wrote {out / 'gems.json'} ({len(gem_rows)} gems)")
    print(f"cards with several candidate printings: {ambiguous} "
          f"({ambiguous / max(1, len(cards)) * 100:.1f}%)")
    if unresolved:
        print(f"unresolved: {len(unresolved)}")
        for item in unresolved[:20]:
            print("   ", item)
    print(f"\nPoint the server at it with:  HEX_CODEX_DATA={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())