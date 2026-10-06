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
    row = conn.execute("SELECT quantity FROM collections WHERE user_id=? AND card_template_id=?",
                        (int(user_id), template_guid)).fetchone()
    wanted = max(0, int(row[0] if row else 0))
    rows = conn.execute(
        "SELECT instance_id FROM card_instances WHERE user_id=? AND template_guid=? ORDER BY instance_id",
        (int(user_id), template_guid)).fetchall()
    ids = [int(r[0]) for r in rows]
    if len(ids) >= wanted:
        return ids
    from profile_db import db_insert_card_instance, db_next_card_instance_for_user
    next_id = int(db_next_card_instance_for_user(int(user_id), conn=conn))
    while len(ids) < wanted:
        while conn.execute(
            "SELECT 1 FROM card_instances WHERE user_id=? AND instance_id=?",
            (int(user_id), next_id)).fetchone():
            next_id += 1
        db_insert_card_instance(int(user_id), next_id, template_guid, conn=conn)
        ids.append(next_id)
        next_id += 1
    return ids

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

def build_hex_server_importer(handler, data_root: str | Path) -> DeckImporter:
    conn = _db_connection()
    site_ids = SiteIds(data_root)
    return DeckImporter(
        site_ids,
        HexServerDeckStorage(conn),
        gem_value_resolver=lambda guid, row: _resolve_gem_value(site_ids, guid, row, conn),
    )

def import_into_hex_server(handler, link_or_code: str, *, name: str | None = None) -> int:
    import os
    data_root = os.environ.get("HEX_CODEX_DATA") or os.environ.get("HEX_CODEX_DATA_PATH")
    if not data_root:
        raise RuntimeError("HEX_CODEX_DATA is not configured")
    importer = build_hex_server_importer(handler, data_root)
    deck = importer.build(int(handler.user_profile["id"]), link_or_code, name=name)
    return importer.save(int(handler.user_profile["id"]), deck)
