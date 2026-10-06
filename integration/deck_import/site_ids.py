"""Data-driven Hex Codex site-id and gem-type mapping."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class SiteId:
    guid: str
    kind: str
    name: str = ""


class SiteIds:
    def __init__(self, data_folder: str | Path) -> None:
        root = Path(data_folder)
        ids_path = root / "ids.json"
        gems_path = root / "gems.json"
        if not ids_path.is_file():
            raise FileNotFoundError(f"missing Hex Codex data file: {ids_path}")
        if not gems_path.is_file():
            raise FileNotFoundError(f"missing Hex Codex data file: {gems_path}")

        doc = json.loads(ids_path.read_text(encoding="utf-8"))
        self._ids: dict[int, SiteId] = {}
        for row in doc.get("entries", []):
            if not isinstance(row, list) or len(row) < 3:
                continue
            try:
                key = int(row[0])
            except (TypeError, ValueError):
                continue
            guid = str(row[1] or "").strip()
            kind = str(row[2] or "").strip().casefold()
            name = str(row[3] or "") if len(row) > 3 else ""
            if guid:
                self._ids[key] = SiteId(guid, kind, name)

        gems = json.loads(gems_path.read_text(encoding="utf-8"))
        self._gem_types: dict[str, str] = {}
        if isinstance(gems, dict):
            for value in gems.values():
                if not isinstance(value, list):
                    continue
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    guid = item.get("id")
                    gem_type = item.get("type")
                    if guid and gem_type:
                        self._gem_types[str(guid).casefold()] = str(gem_type).strip()

    @property
    def count(self) -> int:
        return len(self._ids)

    @property
    def gem_count(self) -> int:
        return len(self._gem_types)

    def find(self, site_id: int) -> SiteId | None:
        return self._ids.get(int(site_id))

    def gem_type(self, guid: str) -> str | None:
        return self._gem_types.get(str(guid).casefold())
