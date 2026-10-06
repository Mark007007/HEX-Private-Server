import unittest
from types import SimpleNamespace
from unittest import mock

from integration.ai_bridge.snapshot import _collect_live_events


class GrowingEventHistoryTests(unittest.TestCase):
    def test_same_game_captures_events_appended_later(self):
        game = SimpleNamespace(events=["event-1"])
        sink = SimpleNamespace(_unpublished=[], game=game)
        port = SimpleNamespace(event_sink=sink)
        session = SimpleNamespace(_hex_original_ai_db_loaded=True)
        calls = []

        def project(cloned, viewer_uid):
            calls.append(list(cloned.events))
            return [
                {
                    "class_id": 1,
                    "data_base64": str(event),
                    "embedded_class_id": 1,
                }
                for event in cloned.events
            ]

        with mock.patch(
            "integration.ai_bridge.snapshot._event_envelopes_from_game",
            side_effect=project,
        ):
            first = _collect_live_events(session, port, 9001)
            game.events.append("event-2")
            second = _collect_live_events(session, port, 9001)

        self.assertEqual([item["data_base64"] for item in first], ["event-1"])
        self.assertEqual(
            [item["data_base64"] for item in second],
            ["event-1", "event-2"],
        )
        self.assertEqual(calls, [["event-1"], ["event-2"]])


if __name__ == "__main__":
    unittest.main()
