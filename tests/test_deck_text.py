"""Regression tests for the pasted-deck parser.

The deck page renders an entry across several lines (copies, name, gems,
cost), which the earlier single-line-only parser could not read at all.
"""
import unittest

from integration.deck_import.text_deck import parse_deck_text

SITE_PAGE = """Bring Your Daughter To The Slaughter
Uzzu the Bonewalker
Diamond
60 cards
Built by RomoSJR
Open in the deck builder

Deck · 60
Troops · 16

4
Daughter of the Poet
2

4
Brilliant Annihilix
Major Diamond of Solidarity, Minor Diamond of Duty
3

Actions · 12

4
Light the Votives
1

Resources · 20

4
Brewed Ambrosia
—

8
Diamond Shard
—

Equipment

Chest
Reaver Ringmail
for Slaughtergear's Reaver
"""


class DeckTextTests(unittest.TestCase):
    def test_rendered_page_shape_is_parsed(self):
        spec = parse_deck_text(SITE_PAGE)
        self.assertEqual(spec.name, "Bring Your Daughter To The Slaughter")
        self.assertEqual(spec.champion, "Uzzu the Bonewalker")
        self.assertEqual([(e.copies, e.name) for e in spec.main], [
            (4, "Daughter of the Poet"),
            (4, "Brilliant Annihilix"),
            (4, "Light the Votives"),
            (4, "Brewed Ambrosia"),
            (8, "Diamond Shard"),
        ])
        self.assertEqual(sum(e.copies for e in spec.main), 24)

    def test_gems_stay_attached_to_their_card(self):
        spec = parse_deck_text(SITE_PAGE)
        by_name = {e.name: e for e in spec.main}
        self.assertEqual(list(by_name["Brilliant Annihilix"].gems),
                         ["Major Diamond of Solidarity", "Minor Diamond of Duty"])
        self.assertEqual(list(by_name["Daughter of the Poet"].gems), [])

    def test_equipment_section_is_ignored_silently(self):
        spec = parse_deck_text(SITE_PAGE)
        self.assertEqual(spec.unparsed, ())
        self.assertNotIn("Reaver Ringmail", [e.name for e in spec.main])

    def test_compact_single_line_shape_still_parses(self):
        spec = parse_deck_text(
            "Champion: Ozawa\n4x Chill\n3 Cloudwalk\nBlessing of Unicorns x2\n"
            "\nReserves:\n2x Extinction")
        self.assertEqual(spec.champion, "Ozawa")
        self.assertEqual([(e.copies, e.name) for e in spec.main],
                         [(4, "Chill"), (3, "Cloudwalk"), (2, "Blessing of Unicorns")])
        self.assertEqual([(e.copies, e.name) for e in spec.reserves],
                         [(2, "Extinction")])

    def test_reserves_header_with_count(self):
        spec = parse_deck_text("Reserves · 15\n\n2x Extinction\n1x Chill")
        self.assertEqual(len(spec.main), 0)
        self.assertEqual([(e.copies, e.name) for e in spec.reserves],
                         [(2, "Extinction"), (1, "Chill")])

    def test_summary_and_author_lines_are_not_cards(self):
        spec = parse_deck_text(
            "My Deck\nChampion: Ozawa\n60 cards\nBuilt by Someone\n\n2x Chill")
        self.assertEqual(spec.name, "My Deck")
        self.assertEqual([(e.copies, e.name) for e in spec.main], [(2, "Chill")])
        self.assertEqual(spec.unparsed, ())

    def test_unknown_lines_are_collected_not_fatal(self):
        spec = parse_deck_text("Troops · 4\n4\nChill\n2\n??? mystery line ???")
        self.assertEqual([(e.copies, e.name) for e in spec.main], [(4, "Chill")])
        self.assertEqual(spec.unparsed, ("??? mystery line ???",))


if __name__ == "__main__":
    unittest.main()