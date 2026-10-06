"""Validated Hex Codex deck importer."""
from __future__ import annotations
from dataclasses import dataclass
import json
from typing import Callable, Iterable, Sequence
from .codec import DeckEntry, DeckLinkError, decode, find_code
from .site_ids import SiteIds

@dataclass(frozen=True)
class ImportedDeck:
    name: str
    champion_guid: str
    cards: tuple[int, ...]
    reserves: tuple[int, ...]
    active_gems: dict[str, list[int]]

class DeckImportError(ValueError):
    pass

class DeckStorage:
    def __init__(self, *, owned_instances: Callable[[int, str], Sequence[int]],
                 save_deck: Callable[..., int],
                 existing_names: Callable[[int], Iterable[str]]) -> None:
        self.owned_instances = owned_instances
        self.save_deck = save_deck
        self.existing_names = existing_names

class DeckImporter:
    def __init__(self, site_ids: SiteIds, storage: DeckStorage, *,
                 gem_value_resolver: Callable[[str, object], int] | None = None) -> None:
        self.site_ids = site_ids
        self.storage = storage
        self.gem_value_resolver = gem_value_resolver

    @staticmethod
    def _kind(row, expected: str) -> bool:
        return bool(row) and str(row.kind or "").strip().casefold() == expected

    def build(self, user_id: int, link_or_code: str, *, name: str | None = None) -> ImportedDeck:
        code = find_code(link_or_code)
        if code is None:
            raise DeckImportError("no Hex Codex deck link found")
        try:
            deck = decode(code)
        except DeckLinkError as exc:
            raise DeckImportError(str(exc)) from exc

        champion = self.site_ids.find(deck.champion_site_id)
        if deck.champion_site_id == 0:
            raise DeckImportError("the deck has no champion")
        if not self._kind(champion, "champion"):
            raise DeckImportError(f"unknown champion (site id {deck.champion_site_id})")

        used: dict[str, int] = {}
        active_gems: dict[str, list[int]] = {}

        def materialize(entries: Sequence[DeckEntry]) -> list[int]:
            result: list[int] = []
            for entry in entries:
                card = self.site_ids.find(entry.site_id)
                if not self._kind(card, "card"):
                    raise DeckImportError(f"unknown card (site id {entry.site_id})")
                owned = list(self.storage.owned_instances(user_id, card.guid))
                offset = used.get(card.guid, 0)
                if offset + entry.copies > len(owned):
                    available = max(0, len(owned) - offset)
                    raise DeckImportError(
                        f"{card.name or card.guid} requires {entry.copies} copies; only {available} remain")
                gems: list[int] = []
                for gem_site_id in entry.gems:
                    gem = self.site_ids.find(gem_site_id)
                    if not self._kind(gem, "gem"):
                        raise DeckImportError(f"unknown gem (site id {gem_site_id})")
                    if self.gem_value_resolver is None:
                        raise DeckImportError(
                            "gem mapping is not configured; refusing to store a site id as EGemTypesNew")
                    try:
                        value = int(self.gem_value_resolver(str(gem.guid), gem))
                    except Exception as exc:
                        raise DeckImportError(
                            f"cannot map gem {gem.name or gem.guid} to EGemTypesNew") from exc
                    if value <= 0:
                        raise DeckImportError("invalid EGemTypesNew value")
                    gems.append(value)
                for instance_id in owned[offset:offset + entry.copies]:
                    iid = int(instance_id)
                    result.append(iid)
                    used[card.guid] = used.get(card.guid, 0) + 1
                    if gems:
                        active_gems[str(iid)] = list(gems)
            return result

        cards = materialize(deck.main)
        reserves = materialize(deck.reserves)
        if not cards:
            raise DeckImportError("the deck has no main-deck cards")

        existing = {str(x).strip().casefold()
                    for x in self.storage.existing_names(user_id) if str(x).strip()}
        base = (name or deck.name or "Imported deck").strip() or "Imported deck"
        final, suffix = base, 2
        while final.casefold() in existing:
            final = f"{base} ({suffix})"
            suffix += 1

        return ImportedDeck(final, champion.guid, tuple(cards), tuple(reserves), active_gems)

    def save(self, user_id: int, deck: ImportedDeck) -> int:
        kwargs = {
            "cards_json": json.dumps(list(deck.cards), separators=(",", ":")),
            "reserve_cards_json": json.dumps(list(deck.reserves), separators=(",", ":")),
            "pve_champion_id": None,
            "pvp_champion_guid": deck.champion_guid,
            "active_gems_json": json.dumps(deck.active_gems, separators=(",", ":")),
        }
        return int(self.storage.save_deck(user_id, deck.name, **kwargs))
