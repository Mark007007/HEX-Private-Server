#!/usr/bin/env python3
"""Grant a player every collectible card and every piece of equipment.

Built for a local test account that should be able to build any deck.  Both
halves are written the way the server itself writes them, so nothing here is a
side channel the client would refuse to display:

* cards go to ``collections`` (template + quantity, which ``GetPlayerCardIDList``
  reads) AND to ``card_instances`` (one row per physical card, which is what the
  client's collection is actually populated from at login).
* equipment goes to ``player_inventory``, where each row is an
  ``InventoryEquipmentData`` template and ``client_item_uid`` is the ``Id`` the
  client de-duplicates on.

The grant is a **top-up**: a template already at or above the requested count is
left alone, and one below it is raised to it.  Running the script twice changes
nothing, and lowering the target never removes cards.

The equipment list comes from the client's own
``Records/InventoryItemData.jsonl``, so the GUIDs are exactly the ones the
client can resolve.

Stop the server before running this: it writes the database directly and a
running server would not see the change without a restart anyway.

``--collapse-printings`` tackles collection-screen lag.  The client lists one
deck-builder row per *template*, so owning every printing of every card costs
rows and full-scan filter/sort work, while extra copies of a single printing
cost only the one pass that builds the list.  This mode keeps one printing per
card name, deletes the other printings' instances, and spares any instance a
saved deck still points at.

Usage::

    python scripts/grant_full_collection.py --dry-run
    python scripts/grant_full_collection.py
    python scripts/grant_full_collection.py --cards 4 --equipment 1
    python scripts/grant_full_collection.py --collapse-printings --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

REPO = Path(__file__).resolve().parents[1]
HEX = REPO / "hex-server"
LIVE_DB = HEX / "hconnect.db"
INVENTORY_RECORDS = HEX / "Records" / "InventoryItemData.jsonl"

sys.path.insert(0, str(HEX))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Reuse the launcher-config loader so --player defaults the same way the other
# scripts and start-game.sh do.
from validate_deck_import import LOCAL_ENV, load_local_env  # noqa: E402

# Game pieces that are not collectible: campaign banes and the arena
# incantation.  They live in card_templates but are not part of a collection,
# so granting them would only add noise to the collection view.
NON_COLLECTIBLE_TYPES = ("Bane", "Mod")

EQUIPMENT_TYPE = "Reckoning.Game.InventoryEquipmentData"


def parse_record(line: str):
    """Records lines are a JSON string literal wrapping the record JSON."""
    text = line.strip().rstrip(",")
    if not text:
        return None
    try:
        inner = json.loads(text)
    except Exception:
        return None
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except Exception:
            return None
    return inner if isinstance(inner, dict) else None


def pick_canonical_printings(conn, where: str, params: list) -> set[str]:
    """One printing per unique card name.

    The deck builder lists one row per TEMPLATE, not per copy: its
    ``StackedCardEntryList.Add`` (Assembly-CSharp 202666) keys a row on
    ``template.m_Id`` and merely appends the instance id when the template is
    already present.  Extra printings therefore cost rows and filter/sort work,
    while extra copies of one printing cost only the single pass that builds the
    list.  Keeping one printing per name halves both and, unlike keeping every
    printing once, still leaves a full playset to build with.
    """
    best: dict[str, tuple[str, bool]] = {}
    for guid, name, no_pvp in conn.execute(
            f"SELECT guid, name, COALESCE(no_pvp,0) FROM card_templates "
            f"WHERE {where} ORDER BY guid", params):
        key = str(name or "").strip().casefold()
        current = best.get(key)
        # Prefer a PvP-legal printing so the kept copy is usable everywhere;
        # ties go to the lowest guid, which keeps repeated runs stable.
        if current is None or (not no_pvp and current[1]):
            best[key] = (guid, bool(no_pvp))
    return {guid for guid, _ in best.values()}


def plan_collapse(conn, player: int, canonical: set[str]):
    """Decide which card instances to delete, sparing any a deck points at.

    Returns ``(drop, spared, surviving_templates)``.
    """
    referenced: set[int] = set()
    for (raw_cards,) in conn.execute(
            "SELECT cards FROM decks WHERE user_id=?", (player,)):
        try:
            for value in json.loads(raw_cards or "[]"):
                if isinstance(value, int):
                    referenced.add(value)
        except Exception:
            continue

    drop: list[int] = []
    spared_by_template: dict[str, int] = {}
    instances_by_template: dict[str, int] = {}
    for instance_id, guid in conn.execute(
            "SELECT instance_id, template_guid FROM card_instances "
            "WHERE user_id=? ORDER BY instance_id", (player,)):
        instances_by_template[guid] = instances_by_template.get(guid, 0) + 1
        if guid in canonical:
            continue
        if instance_id in referenced:
            spared_by_template[guid] = spared_by_template.get(guid, 0) + 1
        else:
            drop.append(instance_id)

    # A template survives if it is canonical, or if a deck-referenced instance
    # of it was spared.
    surviving = sum(
        1 for guid in instances_by_template
        if guid in canonical or guid in spared_by_template)
    return drop, sum(spared_by_template.values()), surviving


def load_equipment_guids(path: Path) -> list[str]:
    if not path.is_file():
        raise SystemExit(f"missing Records file: {path}\n"
                         f"  run: HEX_GAMEDATA=<client>/Data/gamedata "
                         f"bash scripts/prepare_client_records.sh")
    guids: list[str] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            record = parse_record(line)
            if not record or str(record.get("_t")) != EQUIPMENT_TYPE:
                continue
            template = record.get("m_Id")
            guid = str(template.get("m_Guid", "")) if isinstance(template, dict) else ""
            if guid and guid not in seen:
                seen.add(guid)
                guids.append(guid)
    return guids


def pick_player(conn, wanted: str | None) -> int:
    if wanted:
        row = None
        if str(wanted).isdigit():
            row = conn.execute("SELECT id, name FROM users WHERE id=?",
                               (int(wanted),)).fetchone()
        if not row:
            row = conn.execute("SELECT id, name FROM users WHERE LOWER(name)=LOWER(?)",
                               (str(wanted),)).fetchone()
        if not row:
            raise SystemExit(f"player {wanted!r} not found")
        return int(row[0])
    rows = conn.execute("SELECT id, name FROM users ORDER BY id").fetchall()
    if len(rows) == 1:
        return int(rows[0][0])
    raise SystemExit(f"{len(rows)} players exist; pass --player <name or id>")


def main() -> int:
    load_local_env(LOCAL_ENV)

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--player", default=os.environ.get("HEX_DECK_USER"),
                        help="player name or id (default: HEX_DECK_USER, else the "
                             "only player in the database)")
    parser.add_argument("--cards", type=int, default=4,
                        help="copies of every card to ensure (default 4, the deck limit)")
    parser.add_argument("--equipment", type=int, default=1,
                        help="copies of every equipment template to ensure "
                             "(default 1: only one can occupy a slot)")
    parser.add_argument("--pvp-legal-only", action="store_true",
                        help="skip cards flagged no_pvp, which are PvE-only "
                             "(default: grant every collectible card)")
    parser.add_argument("--collapse-printings", action="store_true",
                        help="keep only one printing per unique card name and "
                             "delete the rest (see the performance note below)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing")
    args = parser.parse_args()

    if args.cards < 0 or args.equipment < 0:
        raise SystemExit("--cards and --equipment must not be negative")
    if not LIVE_DB.is_file():
        raise SystemExit(f"database not found: {LIVE_DB}\n"
                         f"  run: HEX_GAMEDATA=<client>/Data/gamedata "
                         f"bash scripts/prepare_client_records.sh")

    from profile_db import (
        db_grant_collection_cards,
        db_grant_inventory_item,
        db_insert_collection_card_instances,
        db_next_card_instance_id,
        db_next_inventory_client_uid,
        db_set_inventory_client_uid,
    )

    conn = sqlite3.connect(str(LIVE_DB), timeout=15)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        player = pick_player(conn, args.player)
        name = conn.execute("SELECT name FROM users WHERE id=?",
                            (player,)).fetchone()[0]
        print(f"player {player} ({name!r})")
        print(f"target: {args.cards} copies per card, "
              f"{args.equipment} copies per equipment")
        print("=" * 62)

        # ---- cards ---------------------------------------------------------
        placeholders = ",".join("?" * len(NON_COLLECTIBLE_TYPES))
        where = [f"card_type NOT IN ({placeholders})"]
        params: list = list(NON_COLLECTIBLE_TYPES)
        if args.pvp_legal_only:
            where.append("no_pvp=0")
        sql = (f"SELECT guid, name FROM card_templates WHERE {' AND '.join(where)} "
               f"ORDER BY guid")
        templates = conn.execute(sql, params).fetchall()
        print(f"card templates in scope : {len(templates)}")

        owned = dict(conn.execute(
            "SELECT card_template_id, quantity FROM collections WHERE user_id=?",
            (player,)).fetchall())

        need = [(guid, args.cards - int(owned.get(guid, 0)))
                for guid, _ in templates]
        need = [(guid, missing) for guid, missing in need if missing > 0]
        total_new = sum(missing for _, missing in need)
        print(f"templates below target  : {len(need)}")
        print(f"new card instances      : {total_new}")

        # Also report the drift this script is about to heal.
        drift = conn.execute("""
            SELECT COUNT(*) FROM collections col
            WHERE col.user_id=?
              AND col.quantity > (SELECT COUNT(*) FROM card_instances ci
                                  WHERE ci.user_id=col.user_id
                                    AND ci.template_guid=col.card_template_id)
        """, (player,)).fetchone()[0]
        if drift:
            print(f"drifted templates to heal: {drift} "
                  f"(collections ahead of card_instances)")

        # ---- equipment -----------------------------------------------------
        equipment_guids = load_equipment_guids(INVENTORY_RECORDS)
        inv_owned = dict(conn.execute(
            "SELECT template_guid, quantity FROM player_inventory WHERE user_id=?",
            (player,)).fetchall())
        equip_need = [g for g in equipment_guids
                      if int(inv_owned.get(g, 0)) < args.equipment]
        print(f"equipment templates     : {len(equipment_guids)}")
        print(f"equipment below target  : {len(equip_need)}")

        # ---- preview the printing collapse ---------------------------------
        canonical: set[str] | None = None
        if args.collapse_printings:
            canonical = pick_canonical_printings(conn, " AND ".join(where), params)
            drop, spared, surviving = plan_collapse(conn, player, canonical)
            print(f"printings kept          : {len(canonical)} (one per card name)")
            print(f"card instances to delete: {len(drop)}")
            print(f"instances kept because a deck uses them: {spared}")
            print(f"templates after collapse: {surviving}")

        if args.dry_run:
            print()
            print("dry run: nothing written")
            return 0

        # ---- write cards ---------------------------------------------------
        cid = db_next_card_instance_id(player, conn=conn)
        for guid, missing in need:
            db_grant_collection_cards(player, guid, missing, conn=conn)
            cid = db_insert_collection_card_instances(
                player, cid, guid, missing, conn=conn)

        # ---- heal collections/card_instances drift -------------------------
        # ``collections`` and ``card_instances`` are written by separate
        # helpers, and the instance insert is an INSERT OR IGNORE: a repeated
        # grant raises the quantity while the duplicate instance row is
        # dropped, so the two drift apart.  The client builds its collection
        # view from card_instances and its deck limits from collections, so a
        # drifted template looks like it has a copy the deck builder will
        # accept but nothing can actually fill.  Reconcile rather than trust.
        drifted = conn.execute("""
            SELECT col.card_template_id,
                   col.quantity - (SELECT COUNT(*) FROM card_instances ci
                                    WHERE ci.user_id=col.user_id
                                      AND ci.template_guid=col.card_template_id)
            FROM collections col
            WHERE col.user_id=?
              AND col.quantity > (SELECT COUNT(*) FROM card_instances ci
                                  WHERE ci.user_id=col.user_id
                                    AND ci.template_guid=col.card_template_id)
        """, (player,)).fetchall()
        for guid, gap in drifted:
            cid = db_insert_collection_card_instances(
                player, cid, guid, int(gap), conn=conn)

        # ---- apply the printing collapse -----------------------------------
        if canonical is not None:
            # Re-plan here rather than reusing the preview: the grant above may
            # have inserted instances for non-canonical templates that were
            # below target, and those ids were not in the earlier list.
            drop, _spared, _surviving = plan_collapse(conn, player, canonical)
            for start in range(0, len(drop), 500):
                chunk = drop[start:start + 500]
                conn.execute(
                    f"DELETE FROM card_instances WHERE user_id=? AND instance_id "
                    f"IN ({','.join('?' * len(chunk))})",
                    (player, *chunk))
            # ``collections.quantity`` is the deck-limit counter and must match
            # the surviving instances exactly, so re-derive it rather than
            # guessing at the arithmetic.
            conn.execute("""
                UPDATE collections SET quantity = (
                    SELECT COUNT(*) FROM card_instances ci
                    WHERE ci.user_id=collections.user_id
                      AND ci.template_guid=collections.card_template_id)
                WHERE user_id=?
            """, (player,))
            conn.execute("DELETE FROM collections WHERE user_id=? AND quantity<=0",
                         (player,))

        # ---- write equipment ----------------------------------------------
        uid = db_next_inventory_client_uid(player, conn=conn)
        for guid in equip_need:
            db_grant_inventory_item(player, guid, args.equipment, conn=conn)
            db_set_inventory_client_uid(player, guid, uid, conn=conn)
            uid += 1

        conn.commit()
        print()
        print("written.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # ---- report ------------------------------------------------------------
    conn = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
    try:
        cards = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(quantity),0) FROM collections "
            "WHERE user_id=?", (player,)).fetchone()
        instances = conn.execute(
            "SELECT COUNT(*) FROM card_instances WHERE user_id=?",
            (player,)).fetchone()[0]
        inv = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(quantity),0) FROM player_inventory "
            "WHERE user_id=?", (player,)).fetchone()
        no_uid = conn.execute(
            "SELECT COUNT(*) FROM player_inventory WHERE user_id=? AND "
            "(client_item_uid IS NULL OR client_item_uid=0)", (player,)).fetchone()[0]
        dup_uid = conn.execute(
            "SELECT COUNT(*) FROM (SELECT client_item_uid FROM player_inventory "
            "WHERE user_id=? GROUP BY client_item_uid HAVING COUNT(*)>1)",
            (player,)).fetchone()[0]
    finally:
        conn.close()

    print("=" * 62)
    print(f"  collections       : {cards[0]} templates, {cards[1]} total copies")
    print(f"  card_instances    : {instances} physical cards")
    print(f"  player_inventory  : {inv[0]} templates, {inv[1]} total copies")
    print(f"  inventory rows without a client UID : {no_uid}")
    print(f"  duplicated client UIDs              : {dup_uid}")
    print()
    print("  next: start the server and log in -- the collection arrives with the")
    print("        login profile stream, so no re-login is needed after this.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())