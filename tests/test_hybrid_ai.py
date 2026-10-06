import unittest

from integration.ai_bridge.adapter import AiDecision
from integration.ai_bridge.hybrid import HybridAi


class HybridAiTests(unittest.TestCase):
    def test_repeated_signature_is_suppressed(self):
        class Original:
            def decide(self, **kwargs):
                return AiDecision("play_resource", {"card_id": 7})

        calls = []

        def fallback(**kwargs):
            calls.append(True)
            return AiDecision("pass", {})

        hybrid = HybridAi(Original(), fallback)
        self.assertFalse(
            hybrid.note_submission("s", "same", phase_key="p"))
        self.assertFalse(
            hybrid.note_submission("s", "same", phase_key="p"))
        self.assertTrue(
            hybrid.note_submission("s", "same", phase_key="p"))

    def test_new_phase_clears_suppression_state(self):
        class Original:
            def decide(self, **kwargs):
                return AiDecision("pass", {})

        hybrid = HybridAi(Original(), lambda **kwargs: AiDecision("pass", {}))
        hybrid.note_submission("s", "same", phase_key="p1")
        hybrid.note_submission("s", "same", phase_key="p1")
        hybrid.note_submission("s", "same", phase_key="p1")
        self.assertIsNotNone(hybrid.state_by_session["s"].suppressed)
        hybrid.note_submission("s", "other", phase_key="p2")
        self.assertEqual(hybrid.state_by_session["s"].repeats, 0)
        self.assertEqual(
            hybrid.state_by_session["s"].suppressed, set())


if __name__ == "__main__":
    unittest.main()
