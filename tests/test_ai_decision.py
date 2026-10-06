import sys
import types
import unittest

from integration.ai_bridge.adapter import AiDecision
from integration.ai_bridge.decision import build_rules_transaction


class FakeRulesTransaction:
    def __init__(self, kind, args):
        self.kind = kind
        self.args = args

    @classmethod
    def pass_priority(cls, player, phase):
        return cls("pass_priority", (player, phase))

    @classmethod
    def play_resource(cls, player, card):
        return cls("play_resource", (player, card))

    @classmethod
    def play_troop(cls, player, card, ability_data, free, phase):
        return cls("play_troop", (player, card, ability_data, free, phase))


class AiDecisionContractTests(unittest.TestCase):
    def setUp(self):
        self.old = sys.modules.get("rules_port.session")
        pkg = types.ModuleType("rules_port")
        mod = types.ModuleType("rules_port.session")
        mod.RulesTransaction = FakeRulesTransaction
        sys.modules["rules_port"] = pkg
        sys.modules["rules_port.session"] = mod

    def tearDown(self):
        if self.old is None:
            sys.modules.pop("rules_port.session", None)
        else:
            sys.modules["rules_port.session"] = self.old

    def test_pass_is_converted_to_typed_transaction(self):
        tx = build_rules_transaction(
            "ai", AiDecision("pass", {}),
            {"phase": "FirstMainPhase"},
        )
        self.assertEqual(tx.kind, "pass_priority")
        self.assertEqual(tx.args, ("ai", "FirstMainPhase"))

    def test_play_resource_is_converted(self):
        tx = build_rules_transaction(
            "ai", AiDecision("play_resource", {"card_id": 42}),
            {"phase": "FirstMainPhase"},
        )
        self.assertEqual(tx.kind, "play_resource")
        self.assertEqual(tx.args, ("ai", 42))


if __name__ == "__main__":
    unittest.main()
