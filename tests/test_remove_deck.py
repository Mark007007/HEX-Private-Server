"""RemoveDeck (dt=2093): the deck editor's delete must reach the database.

The client only drops a deck from its own list when the response says Ok, and
the server used to answer nothing at all, so the request vanished silently.
These tests drive the real handler against a throwaway copy of the database.

Placed in the parent repository rather than ``hex-server/tests`` on purpose:
CI checks submodules out at the pinned commit, so a new file inside the
submodule would not exist there and could not guard this fix.
"""
import os
from binascii import hexlify
from pathlib import Path
import shutil
import sqlite3
import struct
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
HEX = REPO / "hex-server"
LIVE_DB = HEX / "hconnect.db"
USER_ID = 2318431741638412123

# The handler is server code that needs a seeded database at import time, and
# CI runs this suite even when the client-derived Records are absent.  Skip
# rather than fail in that case; the rest of the suite still covers the repo.
HAVE_DB = LIVE_DB.is_file()


def _deck_request_bytes(deck_db_id: int) -> bytes:
    """ObjFmt bytes shaped like the real request.

    The parser looks for ``DeckID``, then the nested ``m_UID64``, and reads the
    fifth ``;``-separated token as a little-endian u64 -- the same layout
    GetDeckInfo (2083) parses.
    """
    uid64 = (deck_db_id << 8) | 17  # UID.Type.Deck
    return (b';0;0;1;DeckID;1;1;1;m_UID64;2;0;1;'
            + hexlify(struct.pack("<Q", uid64)) + b';')


@unittest.skipUnless(HAVE_DB, "hex-server/hconnect.db not built yet")
class RemoveDeckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workdir = Path(tempfile.mkdtemp(prefix="hex-remove-deck-"))
        cls.copy = cls.workdir / "hconnect.db"
        # Back up through SQLite so a live WAL is included.
        source = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
        try:
            destination = sqlite3.connect(cls.copy)
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()

        # Must be set before the server module opens its connection.
        os.environ["HEX_DB_PATH"] = str(cls.copy)
        sys.path.insert(0, str(HEX))
        sys.path.insert(0, str(REPO))
        import hconnect_server
        cls.server = hconnect_server

        cls.db = hconnect_server._db
        active = cls.db.execute("PRAGMA database_list").fetchone()[2]
        assert Path(active).resolve() == cls.copy.resolve(), active

    @classmethod
    def tearDownClass(cls):
        import gc
        gc.collect()
        for obj in list(gc.get_objects()):
            if isinstance(obj, sqlite3.Connection):
                try:
                    obj.close()
                except Exception:
                    pass
        shutil.rmtree(cls.workdir, ignore_errors=True)

    def setUp(self):
        # A deck the player owns, referenced by both a champion and the Arena
        # run so every cleanup path is exercised.
        row = self.db.execute(
            "SELECT id FROM decks WHERE user_id=? ORDER BY id LIMIT 1",
            (USER_ID,)).fetchone()
        if not row:
            self.skipTest("player has no deck to delete")
        self.deck_id = int(row[0])
        self.db.execute("UPDATE champions SET last_deck_id=? WHERE user_id=?",
                        (self.deck_id, USER_ID))
        self.db.execute("UPDATE arena_state SET deck_id=? WHERE user_id=?",
                        (self.deck_id, USER_ID))
        self.db.commit()
        self.sent = []

    def _invoke(self, inner_bytes):
        sent = self.sent

        class Stub:
            user_profile = {"id": USER_ID}
            client_uid = 4242
            scnt = 0
            sid = "test-sid"

            def send(self, headers, body):
                sent.append((headers, body))

        self.server.HCPHandler._handle_service_request_legacy(
            Stub(), "ServiceProfile", "Shared", 2093, 2093, 0, "sid", 0,
            {}, inner_bytes)
        return sent

    def test_delete_removes_the_deck_and_answers_ok(self):
        sent = self._invoke(_deck_request_bytes(self.deck_id))

        self.assertIsNone(
            self.db.execute("SELECT id FROM decks WHERE id=?",
                            (self.deck_id,)).fetchone(),
            "the deck row should be gone")
        self.assertEqual(len(sent), 1, "the client needs exactly one response")

        headers, body = sent[0]
        self.assertEqual(headers.get("reqid"), 2093 | 1)
        text = body.decode("latin-1", "replace")
        self.assertIn("RemoveDeckResponse", text)
        self.assertIn("ERemoveDeckError", text)

    def test_delete_clears_rows_that_only_point_at_the_deck(self):
        self._invoke(_deck_request_bytes(self.deck_id))

        # Nothing declares a foreign key to decks, so a stale reference here
        # would leave a champion or an Arena run aiming at a missing deck.
        champions = self.db.execute(
            "SELECT last_deck_id FROM champions WHERE user_id=?",
            (USER_ID,)).fetchall()
        self.assertTrue(all(c[0] != self.deck_id for c in champions), champions)

        arena = self.db.execute(
            "SELECT deck_id FROM arena_state WHERE user_id=?",
            (USER_ID,)).fetchall()
        self.assertTrue(all(a[0] != self.deck_id for a in arena), arena)

    def test_deleting_a_missing_deck_still_answers(self):
        # A silent failure is what made the original bug so hard to see, so the
        # error path must respond too -- the client's callback depends on it.
        self._invoke(_deck_request_bytes(self.deck_id))
        self._invoke(_deck_request_bytes(self.deck_id))
        self.assertEqual(len(self.sent), 2)
        second = self.sent[1][1].decode("latin-1", "replace")
        self.assertIn("RemoveDeckResponse", second)

    def test_refuses_to_delete_a_deck_the_profile_does_not_own(self):
        other = self.db.execute("SELECT id FROM decks WHERE user_id!=? LIMIT 1",
                                (USER_ID,)).fetchone()
        if not other:
            self.skipTest("no foreign deck in the database")
        other_id = int(other[0])
        self._invoke(_deck_request_bytes(other_id))
        self.assertIsNotNone(
            self.db.execute("SELECT id FROM decks WHERE id=?",
                            (other_id,)).fetchone(),
            "another profile's deck must survive")
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()