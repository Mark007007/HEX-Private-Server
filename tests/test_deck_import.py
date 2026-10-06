import base64
import binascii
import json
import tempfile
import unittest
from pathlib import Path

from integration.deck_import.importer import DeckImportError, DeckImporter, DeckStorage
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
    # format=1, champion=1
    # main: site 2 x2 with gem site 4
    # reserve: site 3 x1
    body = bytearray()
    body += uv(1) + uv(1)
    body += uv(1) + uv(2) + uv(2 * 2 + 1) + uv(1) + uv(4)
    body += uv(1) + uv(3) + uv(1 * 2)
    body += uv(1) + uv(5) + b"Test"
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
                    [1, "champion", "champion", "Champion"],
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
            self.assertEqual(deck.active_gems, {"101": [5], "102": [5]})
            self.assertEqual(importer.save(9, deck), 77)
            self.assertEqual(json.loads(storage.saved[2]["cards_json"]), [101, 102])
            self.assertEqual(json.loads(storage.saved[2]["reserve_cards_json"]), [201])
            self.assertEqual(json.loads(storage.saved[2]["active_gems_json"]), {"101": [5], "102": [5]})

    def test_missing_gem_resolver_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ids.json").write_text(json.dumps({
                "entries": [[1, "champion", "champion"], [2, "card-main", "card"], [4, "gem", "gem"]]
            }), encoding="utf-8")
            (root / "gems.json").write_text(json.dumps({
                "x": [{"id": "gem", "type": "Blood_Minor_1"}]
            }), encoding="utf-8")
            importer = DeckImporter(SiteIds(root), FakeStorage())
            with self.assertRaises(DeckImportError):
                importer.build(1, make_code())


if __name__ == "__main__":
    unittest.main()
