"""Validated Hex Codex deck importer."""
from __future__ import annotations
from dataclasses import dataclass, field
import json
from typing import Callable, Iterable, Sequence

from .codec import DeckEntry, DeckLinkError, decode, find_code
from .site_ids import SiteIds

# ``EGemTypesNew.GemFormatBit``: the client sets bit 62 on every packed gem
# value it produces (GemHelper.AddGemToGem seeds its aggregate with it).
GEM_FORMAT_BIT = 1 << 62

@dataclass(frozen=True)
class ImportedDeck:
    name: str
    champion_guid: str
    cards: tuple[int, ...]
    reserves: tuple[int, ...]
    active_gems: dict[str, int]
    # Cards the deck asked for but the player does not own enough of.  A
    # shortfall is reported, never raised: the player keeps the copies they
    # do own.
    shortfalls: tuple["ImportShortfall", ...] = field(default_factory=tuple)
    # Non-fatal problems, e.g. a gem the site's card table does not cover.
    # The deck still imports, just without that gem.
    warnings: tuple[str, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class ImportShortfall:
    name: str
    guid: str
    wanted: int
    taken: int

class DeckImportError(ValueError):
    pass

class DeckStorage:
    def __init__(self, *, owned_instances: Callable[[int, str], Sequence[int]],
                 save_deck: Callable[..., int],
                 existing_names: Callable[[int], Iterable[str]]) -> None:
        self.owned_instances = owned_instances
        self.save_deck = save_deck
        self.existing_names = existing_names

# A normalized deck entry: everything resolved down to game identities.
@dataclass(frozen=True)
class _Resolved:
    guid: str
    display: str
    copies: int
    gem_guids: tuple[str, ...] = ()

class DeckImporter:
    """Imports a deck from a Hex Codex link or from a pasted card list.

    Both entry points normalize into ``_Resolved`` rows and then share one
    assembler, so ownership, naming and gem handling cannot drift apart.
    """

    def __init__(self, site_ids: SiteIds | None, storage: DeckStorage, *,
                 gem_value_resolver: Callable[[str, object], int] | None = None,
                 name_resolver: Callable[[str, str], str | None] | None = None) -> None:
        self.site_ids = site_ids
        self.storage = storage
        self.gem_value_resolver = gem_value_resolver
        # ``name_resolver`` maps a card name to a template guid.  It is only
        # used by ``build_from_text``; the link path resolves through site ids.
        self.name_resolver = name_resolver

    @staticmethod
    def _kind(row, expected: str) -> bool:
        return bool(row) and str(row.kind or "").strip().casefold() == expected

    # -- link path ---------------------------------------------------------

    def _from_link(self, link_or_code: str) -> tuple[str, list[_Resolved], list[_Resolved], str | None, tuple[str, ...]]:
        if self.site_ids is None:
            raise DeckImportError(
                "deck links need the Hex Codex data folder (ids.json + gems.json); "
                "paste a card list instead")
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

        warnings: list[str] = []

        def resolve(entries: Sequence[DeckEntry]) -> list[_Resolved]:
            out: list[_Resolved] = []
            for entry in entries:
                card = self.site_ids.find(entry.site_id)
                if not self._kind(card, "card"):
                    raise DeckImportError(f"unknown card (site id {entry.site_id})")
                gems: list[str] = []
                for gem_site_id in entry.gems:
                    gem = self.site_ids.find(gem_site_id)
                    # A gem the data folder cannot resolve must not sink the
                    # whole deck; import the card without it and report.
                    if not self._kind(gem, "gem"):
                        warnings.append(
                            f"gem site id {gem_site_id} is not in the data folder")
                        continue
                    gems.append(str(gem.guid))
                out.append(_Resolved(str(card.guid), card.name or str(card.guid),
                                     int(entry.copies), tuple(gems)))
            return out

        main = resolve(deck.main)
        reserves = resolve(deck.reserves)
        return (str(champion.guid), main, reserves, deck.name, tuple(warnings))

    # -- text path ---------------------------------------------------------

    def _from_text(self, text: str) -> tuple[str, list[_Resolved], list[_Resolved], str | None, tuple[str, ...]]:
        from .text_deck import parse_deck_text

        if self.name_resolver is None:
            raise DeckImportError(
                "text import is not configured (no card name resolver)")
        spec = parse_deck_text(text)
        if not spec.champion:
            raise DeckImportError("the deck list names no champion")

        champion_guid = self.name_resolver(spec.champion, "champion")
        if not champion_guid:
            raise DeckImportError(f"unknown champion {spec.champion!r}")

        def resolve(entries) -> list[_Resolved]:
            out: list[_Resolved] = []
            for entry in entries:
                guid = self.name_resolver(entry.name, "card")
                if not guid:
                    raise DeckImportError(f"unknown card {entry.name!r}")
                out.append(_Resolved(str(guid), entry.name, int(entry.copies),
                                     tuple(entry.gems)))
            return out

        return (str(champion_guid), resolve(spec.main), resolve(spec.reserves),
                spec.name, ())

    # -- shared assembler --------------------------------------------------

    def _assemble(self, user_id: int, champion_guid: str,
                  main: list[_Resolved], reserves: list[_Resolved],
                  name: str | None, warnings: tuple[str, ...] = ()) -> ImportedDeck:
        used: dict[str, int] = {}
        active_gems: dict[str, int] = {}
        shortfalls: list[ImportShortfall] = []
        notes: list[str] = list(warnings)

        def materialize(entries: Sequence[_Resolved]) -> list[int]:
            result: list[int] = []
            for entry in entries:
                owned = list(self.storage.owned_instances(user_id, entry.guid))
                offset = used.get(entry.guid, 0)
                available = max(0, len(owned) - offset)
                # Take what the player owns; report the rest instead of
                # failing the whole import.
                take = min(entry.copies, available)
                if take < entry.copies:
                    shortfalls.append(ImportShortfall(
                        entry.display, entry.guid, int(entry.copies), int(take)))
                gems: list[int] = []
                # No copies kept means the card is not in the deck, so its gems
                # are moot; resolving them would only add warning noise.
                for gem_guid in (entry.gem_guids if take else ()):
                    if self.gem_value_resolver is None:
                        raise DeckImportError(
                            "gem mapping is not configured; refusing to store a site id as EGemTypesNew")
                    try:
                        value = int(self.gem_value_resolver(gem_guid, gem_guid))
                    except Exception:
                        notes.append(f"unmapped gem {gem_guid}")
                        continue
                    if value <= 0:
                        notes.append(f"unmapped gem {gem_guid}")
                        continue
                    gems.append(value)
                for instance_id in owned[offset:offset + take]:
                    iid = int(instance_id)
                    result.append(iid)
                    used[entry.guid] = used.get(entry.guid, 0) + 1
                    if gems:
                        # The client's EGemTypesNew is a ulong bitfield: bit 62
                        # (GemFormatBit) marks the packed form and each of the
                        # six sockets owns ten bits, so sockets 1 and 2 of a
                        # two-gem card are exactly ``GemFormatBit | g1 |
                        # (g2 << 10)``.  GemHelper.AddGemToGem seeds its
                        # aggregate with that bit, so the client always sets it
                        # when it saves; leaving it out makes the value read
                        # back as a single bogus gem id.  The gem numbers are
                        # shared with the server: the client enum is
                        # Wild_Minor_1=1, Wild_Minor_2=2, and so on, matching
                        # gem_templates.gem_type.
                        packed = GEM_FORMAT_BIT
                        for slot, gem in enumerate(gems):
                            packed |= int(gem) << (10 * slot)
                        active_gems[str(iid)] = packed
            return result

        cards = materialize(main)
        reserves = materialize(reserves)
        if not cards:
            raise DeckImportError("the deck has no main-deck cards")

        existing = {str(x).strip().casefold()
                    for x in self.storage.existing_names(user_id) if str(x).strip()}
        base = (name or "Imported deck").strip() or "Imported deck"
        final, suffix = base, 2
        while final.casefold() in existing:
            final = f"{base} ({suffix})"
            suffix += 1

        return ImportedDeck(final, champion_guid, tuple(cards), tuple(reserves),
                            active_gems, tuple(shortfalls), tuple(notes))

    # -- public API --------------------------------------------------------

    def build(self, user_id: int, link_or_code: str, *, name: str | None = None) -> ImportedDeck:
        champion_guid, main, reserves, deck_name, warnings = self._from_link(link_or_code)
        return self._assemble(user_id, champion_guid, main, reserves,
                              name or deck_name, warnings)

    def build_from_text(self, user_id: int, text: str, *, name: str | None = None) -> ImportedDeck:
        champion_guid, main, reserves, deck_name, warnings = self._from_text(text)
        return self._assemble(user_id, champion_guid, main, reserves,
                              name or deck_name, warnings)

    def save(self, user_id: int, deck: ImportedDeck) -> int:
        kwargs = {
            "cards_json": json.dumps(list(deck.cards), separators=(",", ":")),
            "reserve_cards_json": json.dumps(list(deck.reserves), separators=(",", ":")),
            "pve_champion_id": None,
            "pvp_champion_guid": deck.champion_guid,
            "active_gems_json": json.dumps(deck.active_gems, separators=(",", ":")),
        }
        return int(self.storage.save_deck(user_id, deck.name, **kwargs))