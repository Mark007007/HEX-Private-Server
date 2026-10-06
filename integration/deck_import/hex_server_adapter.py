"""Concrete persistence adapter for hex-server deck imports."""
from __future__ import annotations
from pathlib import Path
from typing import Sequence
from .importer import DeckImporter, DeckStorage
from .site_ids import SiteIds

def _db_connection():
    import hconnect_server
    return hconnect_server._db

def _ensure_instance_rows(user_id: int, template_guid: str, conn) -> Sequence[int]:
    """Return only already-owned card instances for this template.

    The importer must never manufacture instances as a side effect of a deck
    import.  Collection quantity and instance rows are distinct persistence
    concepts; if an installation has fewer instance rows than collection
    quantity, the import keeps the copies that exist and reports the rest.
    """
    rows = conn.execute(
        "SELECT instance_id FROM card_instances "
        "WHERE user_id=? AND template_guid=? ORDER BY instance_id",
        (int(user_id), template_guid),
    ).fetchall()
    return [int(row[0]) for row in rows]

class HexServerDeckStorage(DeckStorage):
    def __init__(self, conn=None) -> None:
        self.conn = conn or _db_connection()
        super().__init__(owned_instances=self._owned_instances,
                         save_deck=self._save_deck,
                         existing_names=self._existing_names)

    def _owned_instances(self, user_id: int, template_guid: str) -> Sequence[int]:
        return _ensure_instance_rows(user_id, template_guid, self.conn)

    def _existing_names(self, user_id: int):
        rows = self.conn.execute("SELECT deck_name FROM decks WHERE user_id=? ORDER BY id",
                                 (int(user_id),)).fetchall()
        return [str(row[0] or "") for row in rows]

    def _save_deck(self, user_id: int, deck_name: str, **kwargs) -> int:
        from profile_db import db_save_deck
        return int(db_save_deck(int(user_id), deck_name, conn=self.conn, **kwargs))

def _resolve_gem_value(site_ids: SiteIds, gem_guid: str, gem_row, conn) -> int:
    type_name = site_ids.gem_type(gem_guid)
    if not type_name:
        raise ValueError(f"Hex Codex gem {gem_guid} has no gems.json type mapping")
    row = conn.execute(
        "SELECT gem_type FROM gem_templates "
        "WHERE LOWER(gem_type_name)=LOWER(?) OR LOWER(name)=LOWER(?) "
        "ORDER BY gem_type LIMIT 1", (type_name, type_name)).fetchone()
    if not row or int(row[0] or 0) <= 0:
        raise ValueError(f"server has no EGemTypesNew mapping for {type_name}")
    return int(row[0])

def build_name_resolver(conn, user_id: int):
    """Resolve a displayed card/champion name to a template guid.

    Champions and cards live in different tables: ``card_templates`` carries no
    Champion type at all, while ``champion_templates_extended`` holds the
    champions.  Names repeat heavily in ``card_templates`` (the same card is
    repeated once per printing), so an ambiguous name prefers a printing the
    player actually owns and otherwise falls back to the lowest guid, which
    keeps repeated imports stable.
    """

    def resolve(name: str, kind: str = "card") -> str | None:
        wanted = str(name or "").strip()
        if not wanted:
            return None

        # Several seeded names carry a trailing space, so both sides are
        # trimmed instead of relying on exact equality.
        if kind == "champion":
            rows = conn.execute(
                "SELECT guid FROM champion_templates_extended "
                "WHERE TRIM(LOWER(name))=TRIM(LOWER(?)) "
                "ORDER BY (CASE WHEN champion_class IS NULL OR champion_class='None' "
                "THEN 1 ELSE 0 END), guid",
                (wanted,)).fetchall()
            guids = [str(r[0]) for r in rows]
            return guids[0] if guids else None

        rows = conn.execute(
            "SELECT guid FROM card_templates "
            "WHERE TRIM(LOWER(name))=TRIM(LOWER(?)) ORDER BY guid",
            (wanted,)).fetchall()
        guids = [str(r[0]) for r in rows]
        if not guids:
            return None
        if len(guids) == 1:
            return guids[0]

        placeholders = ",".join("?" * len(guids))
        owned = conn.execute(
            f"SELECT template_guid FROM card_instances "
            f"WHERE user_id=? AND template_guid IN ({placeholders}) "
            f"GROUP BY template_guid ORDER BY template_guid",
            (int(user_id), *guids)).fetchall()
        if owned:
            return str(owned[0][0])
        return guids[0]

    return resolve

def build_gem_name_resolver(conn):
    """Map a displayed gem name to the server's EGemTypesNew value.

    Pasted deck text names gems ("Major Diamond of Solidarity"), so the text
    path resolves them by name.  Zero means "no mapping", which the importer
    reports as a warning instead of failing the import.
    """

    def resolve(gem_name: str, _row=None) -> int:
        row = conn.execute(
            "SELECT gem_type FROM gem_templates WHERE LOWER(name)=LOWER(?) "
            "ORDER BY gem_type LIMIT 1", (str(gem_name or "").strip(),)).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    return resolve

def build_hex_server_importer(handler, data_root: str | Path) -> DeckImporter:
    conn = _db_connection()
    site_ids = SiteIds(data_root)
    user_id = int(handler.user_profile["id"])
    return DeckImporter(
        site_ids,
        HexServerDeckStorage(conn),
        gem_value_resolver=lambda guid, row: _resolve_gem_value(site_ids, guid, row, conn),
        name_resolver=build_name_resolver(conn, user_id),
    )

def build_text_importer(handler, conn=None) -> DeckImporter:
    """Name-based importer.  Needs no Hex Codex data files at all."""
    conn = conn or _db_connection()
    user_id = int(handler.user_profile["id"])
    return DeckImporter(
        None,
        HexServerDeckStorage(conn),
        gem_value_resolver=build_gem_name_resolver(conn),
        name_resolver=build_name_resolver(conn, user_id),
    )

def import_into_hex_server(handler, link_or_code: str, *, name: str | None = None) -> int:
    import os
    data_root = os.environ.get("HEX_CODEX_DATA") or os.environ.get("HEX_CODEX_DATA_PATH")
    if not data_root:
        raise RuntimeError("HEX_CODEX_DATA is not configured")
    importer = build_hex_server_importer(handler, data_root)
    deck = importer.build(int(handler.user_profile["id"]), link_or_code, name=name)
    return importer.save(int(handler.user_profile["id"]), deck)

def import_into_hex_server_text(handler, text: str, *, name: str | None = None) -> int:
    importer = build_text_importer(handler)
    deck = importer.build_from_text(int(handler.user_profile["id"]), text, name=name)
    return importer.save(int(handler.user_profile["id"]), deck)