"""
Encode decks in the game's EncodedDecks binary format.
This format is used in the profile stream to send deck data to the client.
"""
import io
import struct
import json

from profile_db import db_card_instance_for_encoded_deck

def write_varint(buf, val):
    while val >= 0x80:
        buf.write(bytes([(val & 0x7F) | 0x80]))
        val >>= 7
    buf.write(bytes([val]))

def write_csharp_string(buf, s):
    b = s.encode('utf-8') if isinstance(s, str) else s
    write_varint(buf, len(b))
    buf.write(b)

def write_guid(buf, guid_str):
    """Write GUID in .NET Guid.ToByteArray() format (mixed-endian)."""
    parts = guid_str.replace('-', '')
    b = bytes.fromhex(parts)
    # .NET Guid format: int32 LE, int16 LE, int16 LE, last 8 bytes BE
    guid_le = struct.pack('<IHH', 
        int.from_bytes(b[0:4], 'big'), 
        int.from_bytes(b[4:6], 'big'), 
        int.from_bytes(b[6:8], 'big'))
    buf.write(guid_le)
    buf.write(b[8:16])

def encode_profile_deck_template(buf, name, champ_guid, sleeve_guid, cards, extended_data=None, card_gems=None):
    """Encode ProfileDeckTemplate.ToBytes with per-instance gem and reserve data."""
    write_csharp_string(buf, name)
    write_guid(buf, champ_guid)
    write_guid(buf, sleeve_guid)
    write_varint(buf, 0)
    write_varint(buf, len(cards))
    for card in cards:
        if len(card) == 6:
            tguid, count, is_ext, is_foil, is_reserve, instance_id = card
        else:
            tguid, count, is_ext, is_foil, is_reserve = card
            instance_id = None
        write_guid(buf, tguid)
        write_varint(buf, count)
        buf.write(b'\x00' if not is_reserve else b'\x01')
        buf.write(b'\x00' if not is_ext else b'\x01')
        buf.write(b'\x00' if not is_foil else b'\x01')
        raw_gems = [] if instance_id is None else (card_gems or {}).get(int(instance_id), [])
        if isinstance(raw_gems, int):
            raw_gems = [raw_gems]
        gem_types = [int(v) for v in (raw_gems or ()) if int(v) > 0]
        write_varint(buf, len(gem_types))
        for g in gem_types:
            write_varint(buf, g)
    edata = extended_data or {}
    write_varint(buf, len(edata))
    for k, v in edata.items():
        write_csharp_string(buf, k)
        write_csharp_string(buf, v)
def encode_card_group_id(buf, template_guid, is_extended, card_ids):
    """Encode a single CardGroupId entry."""
    write_guid(buf, template_guid)
    buf.write(b'\x01' if is_extended else b'\x00')  # Extended
    escrow_bytes = b'NONE'
    buf.write(struct.pack('<i', len(escrow_bytes)))   # Escrow length (int32)
    buf.write(escrow_bytes)                            # Escrow bytes
    buf.write(b'\x00')                                  # NoTrade
    buf.write(struct.pack('<i', len(card_ids)))        # Card count (int32)
    sorted_ids = sorted(card_ids)
    prev = 0
    for cid in sorted_ids:
        delta = cid - prev
        write_varint(buf, delta)
        prev = cid

def encode_encoded_decks(db_decks, user_id, conn=None):
    """Create the full EncodedDecks binary payload, preserving reserves."""
    buf = io.BytesIO()
    all_cards_by_group = {}
    deck_entries = []

    def load_ids(raw):
        try:
            value = json.loads(raw or "[]") if isinstance(raw, str) else raw
            return [int(v) for v in (value or ())]
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    def load_gems(raw):
        try:
            value = json.loads(raw or "{}") if isinstance(raw, str) else raw
            if not isinstance(value, dict):
                return {}
            out = {}
            for key, value in value.items():
                if isinstance(value, (list, tuple)):
                    out[int(key)] = [int(v) for v in value if int(v) > 0]
                elif value:
                    out[int(key)] = [int(value)]
            return out
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    for dk in db_decks:
        card_ids = load_ids(dk.get("cards", "[]"))
        reserve_ids = load_ids(dk.get("reserves", "[]"))
        active_gems = load_gems(dk.get("active_gems", "{}"))
        cards = []
        for cid in card_ids:
            row = db_card_instance_for_encoded_deck(user_id, cid, conn=conn)
            if not row:
                continue
            tguid, is_ext = row[1], bool(row[2])
            cards.append((tguid, 1, is_ext, False, False, int(cid)))
            all_cards_by_group.setdefault((tguid, is_ext), []).append(int(cid))
        for cid in reserve_ids:
            row = db_card_instance_for_encoded_deck(user_id, cid, conn=conn)
            if not row:
                continue
            tguid, is_ext = row[1], bool(row[2])
            cards.append((tguid, 1, is_ext, False, True, int(cid)))
            all_cards_by_group.setdefault((tguid, is_ext), []).append(int(cid))
        deck_entries.append({
            "name": dk.get("name", ""),
            "champ": dk.get("pvp_champion_guid") or "00000000-0000-0000-0000-000000000000",
            "sleeve": dk.get("deck_sleeve_guid") or "00000000-0000-0000-0000-000000000000",
            "gameboard": dk.get("gameboard_guid") or "00000000-0000-0000-0000-000000000000",
            "coin": dk.get("coin_guid") or "00000000-0000-0000-0000-000000000000",
            "id": dk.get("id", 0),
            "cards": cards,
            "active_gems": active_gems,
        })

    buf.write(struct.pack('<i', 1))
    buf.write(struct.pack('<i', len(deck_entries)))
    for dk in deck_entries:
        pt_buf = io.BytesIO()
        encode_profile_deck_template(pt_buf, dk["name"], dk["champ"], dk["sleeve"],
                                     dk["cards"], card_gems=dk["active_gems"])
        pt_bytes = pt_buf.getvalue()
        buf.write(struct.pack('<i', len(pt_bytes)))
        buf.write(pt_bytes)
        write_guid(buf, dk["gameboard"])
        buf.write(struct.pack("<Q", dk["id"]))
        buf.write(struct.pack('<i', 0))
        write_varint(buf, 0)
        write_varint(buf, 0)
        write_varint(buf, 0)
        write_csharp_string(buf, dk["coin"])

    buf.write(struct.pack('<i', len(all_cards_by_group)))
    for (tguid, is_ext), card_ids in sorted(all_cards_by_group.items()):
        if card_ids:
            encode_card_group_id(buf, tguid, is_ext, card_ids)
    return buf.getvalue()
