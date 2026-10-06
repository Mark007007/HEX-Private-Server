"""Tolerant parser for pasted HEX deck lists.

Two input shapes are supported, and they can be mixed:

1. The Hex Codex deck page as rendered, where an entry spans several lines::

       Bring Your Daughter To The Slaughter   <- deck name
       Uzzu the Bonewalker                    <- champion (bare name)
       Diamond                                <- shard
       60 cards

       Troops · 16                            <- section header
       4                                      <- copies
       Daughter of the Poet                   <- card name
       2                                      <- cost

       4
       Brilliant Annihilix
       Major Diamond of Solidarity, Minor Diamond of Duty   <- gems
       3                                      <- cost

2. A hand-written single-line list::

       Champion: Ozawa
       4x Chill
       3 Cloudwalk
       Blessing of Unicorns x2

   Reserves:  /  Reserves · 15  switch the following entries to the reserve.

The copies/cost ambiguity is resolved by position: inside a section an entry
always starts with a bare copy count, then the name, then optional gem lines,
then exactly one cost line (a number, or the em dash the site uses for
resources).  Anything unrecognised is collected rather than rejected, because
this text arrives from web pages and OCR.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

@dataclass(frozen=True)
class TextEntry:
    name: str
    copies: int
    gems: tuple[str, ...] = ()

@dataclass(frozen=True)
class TextDeckSpec:
    champion: str | None
    main: tuple[TextEntry, ...]
    reserves: tuple[TextEntry, ...]
    name: str | None = None
    unparsed: tuple[str, ...] = field(default_factory=tuple)

_CHAMPION = re.compile(r"^champions?\s*:?\s*(.+)$", re.IGNORECASE)
_DECK_NAME = re.compile(r"^(?:deck\s*)?name\s*:?\s*(.+)$", re.IGNORECASE)
# "Troops · 16", "Reserves", "Reserves: 15", "Equipment"
_SECTION = re.compile(r"^([A-Za-z][A-Za-z ]{0,20}?)\s*(?:[:·]\s*\d+)?\s*:?$")
_SECTION_KINDS = {
    "deck": "main", "main": "main", "maindeck": "main", "main deck": "main",
    "troops": "main", "actions": "main", "artifacts": "main",
    "resources": "main", "spells": "main", "cards": "main",
    "reserve": "reserve", "reserves": "reserve", "sideboard": "reserve",
    "side board": "reserve", "side deck": "reserve",
    "champion": "champion",
    "equipment": "ignore", "gear": "ignore",
}
_COPIES_FIRST = re.compile(r"^(\d+)\s*[x×]\s*(.+)$", re.IGNORECASE)
_NAME_FIRST = re.compile(r"^(.+?)\s*[x×]\s*(\d+)$", re.IGNORECASE)
_COPIES_SPACE = re.compile(r"^(\d+)\s+(.+)$")
_SUMMARY = re.compile(r"^\d+\s+(cards?|total)\b.*$", re.IGNORECASE)
# The site writes an em dash for resources, which cost nothing.
_NO_COST = {"—", "–", "-", "n/a", ""}

_SHARDS = {"blood", "diamond", "ruby", "sapphire", "wild", "shardless"}
_BULLET = re.compile(r"^[\-\*\u2022\u00b7]+\s*")
_COMMENT_PREFIXES = ("#", "//", ";", "--")
_PREAMBLE_NOISE = re.compile(
    r"^(built by|open in the deck builder|shareable link|deck\s*·|about |"
    r"the card list|loading|sorted by|created by)\b", re.IGNORECASE)


def _clean(line: str) -> str:
    return _BULLET.sub("", line or "").strip()


def _is_cost(line: str) -> bool:
    return line.strip().casefold() in _NO_COST or line.strip().isdigit()


def _split_gems(line: str) -> list[str]:
    return [g.strip() for g in line.split(",") if g.strip()]


def _single_line_entry(line: str) -> TextEntry | None:
    """Handle the compact '4x Name' / 'Name x4' / '4 Name' shapes."""
    for pattern, copies_group in ((_COPIES_FIRST, 1), (_NAME_FIRST, 2), (_COPIES_SPACE, 1)):
        match = pattern.match(line)
        if not match:
            continue
        if copies_group == 1:
            copies_raw, name_raw = match.group(1), match.group(2)
        else:
            name_raw, copies_raw = match.group(1), match.group(2)
        name = name_raw.strip(" .:-\t")
        if not name or name.casefold() in {"card", "cards", "total"}:
            return None
        try:
            copies = int(copies_raw)
        except ValueError:
            return None
        return TextEntry(name, copies) if copies > 0 else None
    return None


def _section_kind(line: str) -> str | None:
    if _SUMMARY.match(line):
        return None
    match = _SECTION.match(line)
    if not match:
        return None
    return _SECTION_KINDS.get(match.group(1).strip().casefold())


def parse_deck_text(text: str) -> TextDeckSpec:
    champion: str | None = None
    name: str | None = None
    main: list[TextEntry] = []
    reserves: list[TextEntry] = []
    champion_section: list[TextEntry] = []
    unparsed: list[str] = []
    preamble: list[str] = []

    target: list[TextEntry] = main
    in_entries = False
    # Sections we deliberately drop (equipment, gear) must not add to the
    # "unparsed" report; their lines are not deck entries and never were.
    ignoring = False
    pending_copies: int | None = None
    pending_name: str | None = None
    pending_gems: list[str] = []

    def flush() -> None:
        nonlocal pending_copies, pending_name, pending_gems
        if pending_copies is not None and pending_name:
            target.append(TextEntry(pending_name, pending_copies, tuple(pending_gems)))
        pending_copies, pending_name, pending_gems = None, None, []

    for raw_line in (text or "").splitlines():
        line = _clean(raw_line)
        if not line or line.startswith(_COMMENT_PREFIXES):
            continue

        # Explicit prefixes win anywhere in the text.
        champion_match = _CHAMPION.match(line)
        if champion_match and not _single_line_entry(line):
            champion = champion_match.group(1).strip(" .:-\t")
            continue
        deck_name_match = _DECK_NAME.match(line)
        if deck_name_match and not _single_line_entry(line):
            name = deck_name_match.group(1).strip(" .:-\t") or None
            continue

        kind = _section_kind(line)
        if kind:
            flush()
            in_entries = True
            ignoring = kind == "ignore"
            if kind == "reserve":
                target = reserves
            elif kind == "main":
                target = main
            elif kind == "champion":
                target = champion_section
            else:  # "ignore" — equipment and friends
                target = []
            continue

        if _SUMMARY.match(line) or _PREAMBLE_NOISE.match(line):
            continue

        if not in_entries:
            # A list with no section headers at all starts its main deck at the
            # first entry-shaped line; everything before that is name/champion.
            compact = _single_line_entry(line)
            if compact:
                in_entries = True
                target = main
                target.append(compact)
                continue
            if line.isdigit():
                in_entries = True
                target = main
                pending_copies = int(line)
                continue
            # Before any section header: deck name, then champion, then shard.
            preamble.append(line)
            continue

        if pending_copies is None:
            compact = _single_line_entry(line)
            if compact:
                target.append(compact)
                continue
            if line.isdigit():
                pending_copies = int(line)
                continue
            if not ignoring:
                unparsed.append(raw_line.strip())
            continue

        if pending_name is None:
            pending_name = line
            continue

        # After the name: gem lines, then the cost line that closes the entry.
        if _is_cost(line):
            flush()
            continue
        pending_gems.extend(_split_gems(line))

    flush()

    if champion is None and champion_section:
        champion = champion_section[0].name

    # Preamble: first line is the deck name, second is the champion unless it
    # is really the shard or a summary.
    for index, line in enumerate(preamble):
        if _is_cost(line):
            continue
        if line.casefold() in _SHARDS:
            continue
        if index == 0:
            name = name or line
        elif champion is None and not line.isdigit():
            champion = line
            break
        else:
            unparsed.append(line)

    return TextDeckSpec(champion, tuple(main), tuple(reserves), name,
                        tuple(unparsed))