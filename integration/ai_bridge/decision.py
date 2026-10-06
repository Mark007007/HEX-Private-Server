"""Convert worker intents into authoritative hex-server RulesTransactions."""
from __future__ import annotations
from typing import Any, Mapping

class AiDecisionError(ValueError):
    pass

def _phase_from_snapshot(snapshot: Mapping[str, Any]):
    phase = snapshot.get("phase")
    if phase is None:
        return None
    try:
        import game_engine
        if isinstance(phase, int):
            return phase
        return getattr(game_engine.ETurnPhases, str(phase), phase)
    except Exception:
        return phase

def build_rules_transaction(player_id, decision, snapshot):
    from rules_port.session import RulesTransaction
    kind = str(getattr(decision, "kind", "") or "").strip().lower()
    payload = dict(getattr(decision, "payload", {}) or {})
    phase = _phase_from_snapshot(snapshot or {})

    if kind in {"pass", "pass_priority", "hold"}:
        if phase is None:
            raise AiDecisionError("pass decision has no phase")
        return RulesTransaction.pass_priority(player_id, phase)
    if kind == "play_resource":
        return RulesTransaction.play_resource(player_id, int(payload["card_id"]))
    if kind in {"play_troop", "play_artifact", "play_spell"}:
        return getattr(RulesTransaction, kind)(
            player_id, int(payload["card_id"]),
            payload.get("ability_data") or (),
            bool(payload.get("playing_for_free", False)), phase)
    if kind == "activate_ability":
        return RulesTransaction.activate_ability(
            player_id, int(payload["source_card_id"]),
            int(payload["ability_template_id"]),
            payload.get("activation_data") or {},
            int(payload.get("ability_instance_id", 0) or 0))
    if kind == "discard":
        return RulesTransaction.discard(player_id, int(payload["card_id"]))
    if kind in {"attack", "commit_troops_to_attack"}:
        return RulesTransaction.commit_troops_to_attack(
            player_id, phase, payload.get("declarations") or ())
    if kind in {"defend", "commit_troops_to_defense"}:
        return RulesTransaction.commit_troops_to_defense(
            player_id, phase, payload.get("declarations") or ())
    if kind == "set_ability_activation_data":
        return RulesTransaction.set_ability_activation_data(
            player_id, int(payload["ability_instance_id"]),
            payload.get("activation_data") or {})
    if kind == "resolve_choice_continuation":
        return RulesTransaction.resolve_choice_continuation(
            player_id, payload.get("activation_data") or {})
    if kind == "resolve_triggered_continuation":
        return RulesTransaction.resolve_triggered_continuation(
            player_id, payload.get("activation_data") or {})
    if kind == "resolve_discard_continuation":
        return RulesTransaction.resolve_discard_continuation(
            player_id, payload.get("activation_data") or {})
    raise AiDecisionError(f"unsupported AI decision kind: {kind}")

def submit_ai_decision(port, player_id, decision, snapshot) -> bool:
    tx = build_rules_transaction(player_id, decision, snapshot)
    if not port.submit_transaction(tx):
        return False
    return bool(port.handle_transaction())
