"""Hex Codex v1 deck-link decoder."""
from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii


UV_MAX = (1 << 28) - 1


class DeckLinkError(ValueError):
    pass


@dataclass(frozen=True)
class DeckEntry:
    site_id: int
    copies: int
    gems: tuple[int, ...] = ()


@dataclass(frozen=True)
class DeckLink:
    format_version: int
    champion_site_id: int
    main: tuple[DeckEntry, ...]
    reserves: tuple[DeckEntry, ...]
    name: str | None = None


def find_code(value: str) -> str | None:
    s = "".join(str(value or "").split())
    d = s.find("d=")
    if "/deck" in s and d >= 0:
        s = s[d + 2:]
    for sep in ("&", "#"):
        p = s.find(sep)
        if p >= 0:
            s = s[:p]
            break
    return s if len(s) >= 2 and s.startswith("v") else None


def _decode_b64(value: str) -> bytes:
    if len(value) % 4 == 1:
        raise DeckLinkError("the code is damaged (length)")
    padded = value + "=" * ((4 - len(value) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except (ValueError, binascii.Error) as exc:
        raise DeckLinkError("invalid base64url data") from exc


def decode(code: str) -> DeckLink:
    if len(code) < 2 or code[0] != "v" or not code[1].isdigit():
        raise DeckLinkError("not a deck link code")
    if code[1] != "1":
        raise DeckLinkError(
            f"made by a newer version of Hex Codex (version {code[1]})")

    raw = _decode_b64(code[2:])
    if len(raw) < 8:
        raise DeckLinkError("the code is too short")
    end = len(raw) - 4
    expected = int.from_bytes(raw[end:], "big")
    actual = binascii.crc32(raw[:end]) & 0xFFFFFFFF
    if actual != expected:
        raise DeckLinkError("the code is damaged (checksum)")

    pos = 0

    def uv() -> int:
        nonlocal pos
        value = 0
        mul = 1
        for _ in range(4):
            if pos >= end:
                raise DeckLinkError("the code is damaged (cut off)")
            b = raw[pos]
            pos += 1
            value += (b & 0x7F) * mul
            if not b & 0x80:
                return value
            mul *= 128
        raise DeckLinkError("the code is damaged (number too long)")

    def take(n: int) -> bytes:
        nonlocal pos
        if n < 0 or pos + n > end:
            raise DeckLinkError("the code is damaged (section)")
        out = raw[pos:pos + n]
        pos += n
        return out

    format_version = uv()
    champion_site_id = uv()
    lists = [[], []]
    for target in lists:
        count = uv()
        previous = 0
        for _ in range(count):
            site_id = previous + uv()
            if not 0 < site_id <= UV_MAX:
                raise DeckLinkError("the code is damaged (card id)")
            head = uv()
            copies = head // 2
            if copies < 1:
                raise DeckLinkError("the code is damaged (0 copies)")
            gems = []
            if head & 1:
                for _ in range(uv()):
                    gems.append(uv())
            target.append(DeckEntry(site_id, copies, tuple(gems)))
            previous = site_id

    name = None
    while pos < end:
        section_type = uv()
        length = uv()
        payload = take(length)
        if section_type == 1:
            try:
                name = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise DeckLinkError("the deck name is not valid UTF-8") from exc
        elif section_type % 2 == 0:
            raise DeckLinkError(
                f"needs a newer server (section {section_type})")

    return DeckLink(format_version, champion_site_id,
                    tuple(lists[0]), tuple(lists[1]), name)
