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

def _normalize_activation_data(value):
    if isinstance(value, (list, tuple)):
        return tuple(_normalize_activation_data(x) for x in value)
    if not isinstance(value, dict):
        return value
    aliases = {
        "abilityinstanceid": "ability_instance_id",
        "abilitytemplateid": "ability_template_id",
        "sourcecardid": "source_card_id",
        "targetmap": "target_map",
        "optionmap": "option_map",
        "variables": "variables",
        "xcostdata": "x_cost_data",
        "xcost": "x_cost",
        "disable": "disable",
        "optedin": "opted",
        "optedinset": "opted_in_set",
        "index": "index",
        "muid64": "uid64",
        "msessioncardids": "session_card_ids",
        "cardstosacrifice": "cards_to_sacrifice",
        "mplayerids": "player_ids",
        "resourcexcost": "resource_x_cost",
        "chargepointsxcost": "charge_points_x_cost",
        "lifexcost": "life_x_cost",
        "counterxcost": "counter_x_cost",
        "spellpointsxcost": "spell_points_x_cost",
        "setvalues": "set_values",
        "mresourcexcost": "resource_x_cost",
        "mchargepointsxcost": "charge_points_x_cost",
        "mlifexcost": "life_x_cost",
        "mcounterxcost": "counter_x_cost",
        "mspellpointsxcost": "spell_points_x_cost",
        "msetvalues": "set_values",
        "mcardstosacrifice": "cards_to_sacrifice",
        "mcardstodiscard": "cards_to_discard",
        "mcardstoexhaust": "cards_to_exhaust",
        "mcardstoputintodeck": "cards_to_put_into_deck",
        "mcardstoputintohand": "cards_to_put_into_hand",
        "mcardstoreveal": "cards_to_reveal",
        "mcardstoshuffleintodeck": "cards_to_shuffle_into_deck",
        "mcardstovoid": "cards_to_void",
        "mcardstomobilize": "cards_to_mobilize",
    }
    out = {}
    for key, item in value.items():
        normalized_key = aliases.get(str(key).replace("_", "").lower(), key)
        out[normalized_key] = _normalize_activation_data(item)

    if "x_cost" not in out and isinstance(out.get("x_cost_data"), dict):
        nested = out["x_cost_data"]
        for key in ("resource_x_cost", "x_cost", "value", "amount"):
            if key in nested:
                out["x_cost"] = nested[key]
                break

    target_map = out.get("target_map")
    if isinstance(target_map, dict):
        normalized_targets = {}
        for index, target in target_map.items():
            try:
                idx = int(index)
            except (TypeError, ValueError):
                continue
            if isinstance(target, dict):
                target = target.get("session_card_ids") or target.get(
                    "player_ids", target)
            if not isinstance(target, (list, tuple, set)):
                target = (target,)
            values = []
            for selected in target:
                if isinstance(selected, dict):
                    selected = selected.get(
                        "uid64", selected.get("UID", selected))
                    if isinstance(selected, dict):
                        selected = selected.get(
                            "value", selected.get("m_UID64", selected))
                try:
                    values.append(int(selected))
                except (TypeError, ValueError):
                    continue
            normalized_targets[idx] = values
        out["target_map"] = normalized_targets

    sacrifice = out.get("cards_to_sacrifice")
    if sacrifice is None and isinstance(out.get("x_cost_data"), dict):
        sacrifice = out["x_cost_data"].get("cards_to_sacrifice")
    if sacrifice is not None:
        if not isinstance(sacrifice, (list, tuple, set)):
            sacrifice = (sacrifice,)
        out.setdefault("cost_target_map", {})
        out["cost_target_map"].setdefault(
            0, [int(x) for x in sacrifice if isinstance(x, (int, str))])

    return out


def build_rules_transaction(player_id, decision, snapshot):
    from rules_port.session import RulesTransaction
    kind = str(getattr(decision, "kind", "") or "").strip().lower()
    payload = dict(getattr(decision, "payload", {}) or {})
    phase = _phase_from_snapshot(snapshot or {})

    if kind in {"pass", "pass_priority", "hold"}:
        if phase is None:
            raise AiDecisionError("pass decision has no phase")
        return RulesTransaction.pass_priority(player_id, phase)
    if kind == "choose_play_first":
        return RulesTransaction.choose_play_first(player_id, phase)
    if kind == "choose_draw_first":
        return RulesTransaction.choose_draw_first(player_id, phase)
    if kind == "accept_starting_hand":
        return RulesTransaction.accept_starting_hand(player_id)
    if kind == "mulligan":
        return RulesTransaction.mulligan(player_id)
    if kind == "play_champion":
        return RulesTransaction.play_champion(
            player_id, int(payload["card_id"]))
    if kind == "cancel_auto_pass":
        return RulesTransaction.cancel_auto_pass(player_id)
    if kind == "request_priority_sync":
        return RulesTransaction.request_priority_sync(player_id)
    if kind == "set_auto_pass":
        return RulesTransaction.set_auto_pass(
            player_id, bool(payload.get("as_active", False)),
            payload.get("passing_state", 0))
    if kind == "play_resource":
        return RulesTransaction.play_resource(player_id, int(payload["card_id"]))
    if kind in {"play_troop", "play_artifact", "play_spell"}:
        return getattr(RulesTransaction, kind)(
            player_id, int(payload["card_id"]),
            _normalize_activation_data(payload.get("ability_data") or ()),
            bool(payload.get("playing_for_free", False)), phase)
    if kind == "activate_ability":
        return RulesTransaction.activate_ability(
            player_id, int(payload["source_card_id"]),
            int(payload["ability_template_id"]),
            _normalize_activation_data(payload.get("activation_data") or {}),
            int(payload.get("ability_instance_id", 0) or 0))
    if kind == "discard":
        return RulesTransaction.discard(player_id, int(payload["card_id"]))
    if kind in {"attack", "commit_troops_to_attack"}:
        return RulesTransaction.commit_troops_to_attack(
            player_id, phase, payload.get("declarations") or ())
    if kind in {"defend", "commit_troops_to_defense"}:
        return RulesTransaction.commit_troops_to_defense(
            player_id, phase, payload.get("declarations") or ())
    if kind in {"activate_triggered_abilities", "activate_triggered"}:
        return RulesTransaction.activate_triggered_abilities(
            player_id,
            _normalize_activation_data(payload.get("activation_data") or ()))
    if kind == "set_ability_activation_data":
        return RulesTransaction.set_ability_activation_data(
            player_id, int(payload["ability_instance_id"]),
            _normalize_activation_data(payload.get("activation_data") or {}))
    if kind == "resolve_choice_continuation":
        return RulesTransaction.resolve_choice_continuation(
            player_id, _normalize_activation_data(payload.get("activation_data") or {}))
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
