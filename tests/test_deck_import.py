import base64
import binascii
import json
import tempfile
import unittest
from pathlib import Path

from integration.deck_import.importer import (
    GEM_FORMAT_BIT, DeckImportError, DeckImporter, DeckStorage)
from integration.deck_import.site_ids import SiteIds


def uv(value):
    out = bytearray()
    value = int(value)
    while True:
        b = value & 0x7F
        value >>= 7
        out.append(b | 0x80 if value else b)
        if not value:
            return bytes(out)


def make_code():
    body = bytearray()
    body += uv(1) + uv(1)
    body += uv(1) + uv(2) + uv(5) + uv(1) + uv(4)
    body += uv(1) + uv(3) + uv(2)
    body += uv(1) + uv(4) + b"Test"
    checksum = (binascii.crc32(body) & 0xFFFFFFFF).to_bytes(4, "big")
    return "v1" + base64.urlsafe_b64encode(body + checksum).decode().rstrip("=")


class FakeStorage(DeckStorage):
    def __init__(self):
        self.saved = None
        super().__init__(
            owned_instances=lambda _uid, guid: {
                "card-main": [101, 102],
                "card-reserve": [201],
            }.get(guid, []),
            save_deck=self._save,
            existing_names=lambda _uid: ["Test"],
        )

    def _save(self, user_id, name, **kwargs):
        self.saved = (user_id, name, kwargs)
        return 77


class DeckImportTests(unittest.TestCase):
    def test_import_preserves_reserve_and_maps_gem_enum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ids.json").write_text(json.dumps({
                "entries": [
                    [1, "champion-guid", "champion", "Champion"],
                    [2, "card-main", "card", "Main"],
                    [3, "card-reserve", "card", "Reserve"],
                    [4, "gem-guid", "gem", "Gem"],
                ]
            }), encoding="utf-8")
            (root / "gems.json").write_text(json.dumps({
                "minor": [{"id": "gem-guid", "type": "Blood_Minor_1"}]
            }), encoding="utf-8")

            storage = FakeStorage()
            importer = DeckImporter(
                SiteIds(root), storage,
                gem_value_resolver=lambda guid, _row: 5 if guid == "gem-guid" else 0,
            )
            deck = importer.build(9, make_code())
            self.assertEqual(deck.name, "Test (2)")
            self.assertEqual(deck.cards, (101, 102))
            self.assertEqual(deck.reserves, (201,))
            # EGemTypesNew is a packed bitfield: bit 62 marks the packed form
            # and every socket owns ten bits, so a lone gem 5 is
            # ``GemFormatBit | 5``.
            single = GEM_FORMAT_BIT | 5
            self.assertEqual(deck.active_gems, {"101": single, "102": single})
            self.assertEqual(importer.save(9, deck), 77)
            self.assertEqual(json.loads(storage.saved[2]["cards_json"]), [101, 102])
            self.assertEqual(json.loads(storage.saved[2]["reserve_cards_json"]), [201])
            self.assertEqual(
                json.loads(storage.saved[2]["active_gems_json"]),
                {"101": single, "102": single},
            )

    def test_missing_gem_resolver_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ids.json").write_text(json.dumps({
                "entries": [
                    [1, "champion-guid", "champion"],
                    [2, "card-main", "card"],
                    [4, "gem-guid", "gem"],
                ]
            }), encoding="utf-8")
            (root / "gems.json").write_text(json.dumps({
                "x": [{"id": "gem-guid", "type": "Blood_Minor_1"}]
            }), encoding="utf-8")
            importer = DeckImporter(SiteIds(root), FakeStorage())
            with self.assertRaises(DeckImportError):
                importer.build(1, make_code())


    def test_shortfall_is_reported_instead_of_failing_the_import(self):
        storage = FakeStorage()  # owns card-main x2 only
        names = {"Princess Victoria": "champion-guid", "Chill": "card-main"}
        importer = DeckImporter(None, storage,
                                name_resolver=lambda name, kind: names.get(name))
        deck = importer.build_from_text(
            9, "Champion: Princess Victoria\n3x Chill")
        self.assertEqual(deck.cards, (101, 102))
        self.assertEqual([(s.name, s.wanted, s.taken) for s in deck.shortfalls],
                         [("Chill", 3, 2)])

    def test_text_import_maps_gem_names_through_the_gem_resolver(self):
        storage = FakeStorage()
        names = {"Princess Victoria": "champion-guid", "Chill": "card-main"}
        importer = DeckImporter(
            None, storage,
            name_resolver=lambda name, kind: names.get(name),
            gem_value_resolver=lambda gem, _row: 5 if gem.startswith("Major") else 0)
        deck = importer.build_from_text(9, "\n".join([
            "Champion: Princess Victoria",
            "Troops · 2",
            "",
            "2",
            "Chill",
            "Major Diamond of Battalion",
            "1",
        ]))
        self.assertEqual(deck.cards, (101, 102))
        self.assertEqual(deck.active_gems,
                         {"101": GEM_FORMAT_BIT | 5, "102": GEM_FORMAT_BIT | 5})
        self.assertEqual(deck.warnings, ())

    def test_two_socketed_gems_are_packed_into_one_value(self):
        # A two-socket card must arrive as a single packed value: socket 1 in
        # the low ten bits, socket 2 ten bits up, and the format bit set.  The
        # client's own save path builds exactly ``GemFormatBit | g1 | (g2 <<
        # 10)`` (GemHelper.AddGemToGem with a GemFormatBit seed).
        storage = FakeStorage()
        names = {"Princess Victoria": "champion-guid", "Chill": "card-main"}
        gems = {"Major Diamond of Battalion": 55, "Minor Diamond of Duty": 63}
        importer = DeckImporter(
            None, storage,
            name_resolver=lambda name, kind: names.get(name),
            gem_value_resolver=lambda gem, _row: gems.get(gem, 0))
        deck = importer.build_from_text(9, "\n".join([
            "Champion: Princess Victoria",
            "Troops · 2",
            "",
            "2",
            "Chill",
            "Major Diamond of Battalion, Minor Diamond of Duty",
            "1",
        ]))
        self.assertEqual(deck.cards, (101, 102))
        packed = GEM_FORMAT_BIT | 55 | (63 << 10)
        self.assertEqual(deck.active_gems, {"101": packed, "102": packed})

    def test_unmapped_gem_warns_but_keeps_the_deck(self):
        storage = FakeStorage()
        names = {"Princess Victoria": "champion-guid", "Chill": "card-main"}
        importer = DeckImporter(
            None, storage,
            name_resolver=lambda name, kind: names.get(name),
            gem_value_resolver=lambda _gem, _row: 0)
        deck = importer.build_from_text(9, "\n".join([
            "Champion: Princess Victoria",
            "Troops · 2",
            "",
            "2",
            "Chill",
            "Unknown Gem",
            "1",
        ]))
        self.assertEqual(deck.cards, (101, 102))
        self.assertEqual(len(deck.warnings), 1)
        self.assertEqual(deck.active_gems, {})

    def test_text_import_without_a_name_resolver_is_rejected(self):
        importer = DeckImporter(None, FakeStorage())
        with self.assertRaises(DeckImportError):
            importer.build_from_text(1, "Champion: X\n2x Chill")

    def test_share_link_without_a_data_folder_is_rejected(self):
        importer = DeckImporter(None, FakeStorage())
        with self.assertRaises(DeckImportError):
            importer.build(1, make_code())


if __name__ == "__main__":
    unittest.main()
