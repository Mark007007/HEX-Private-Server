"""Build the authoritative state + client-wire event replay used by Original AI."""
from __future__ import annotations

import base64
import copy
import struct
from typing import Any


def _jsonable(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(getattr(value, "name", value))


def _uid64(value) -> int:
    if isinstance(value, int):
        return int(value)
    try:
        return int(value.uid64)
    except (AttributeError, TypeError, ValueError):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0


def _event_envelopes_from_game(game, viewer_uid) -> list[dict[str, Any]]:
    """Project a pending Game event stream through the server's real visibility rules."""
    if game is None or not getattr(game, "events", None):
        return []
    try:
        clone = copy.copy(game)
        clone.events = copy.deepcopy(list(game.events))
        if hasattr(game, "_visibility_by_uid"):
            clone._visibility_by_uid = copy.deepcopy(
                getattr(game, "_visibility_by_uid")
            )
        packet = clone.make_network_packet(viewer_uid)
    except Exception:
        return []

    result = []
    for class_id, data in zip(
        getattr(packet, "event_ids", []) or (),
        getattr(packet, "event_data", []) or (),
    ):
        raw = bytes(data or b"")
        if len(raw) < 4:
            continue
        embedded_class = struct.unpack("<i", raw[:4])[0]
        result.append({
            "class_id": int(class_id),
            "data_base64": base64.b64encode(raw).decode("ascii"),
            "embedded_class_id": int(embedded_class),
        })
    return result


def _collect_live_events(session, port, ai_player_id) -> list[dict[str, Any]]:
    """Keep a lossless in-process event history for the headless client mirror."""
    history = getattr(session, "_hex_original_ai_event_history", None)
    seen = getattr(session, "_hex_original_ai_seen_event_objects", None)
    sequence = getattr(session, "_hex_original_ai_event_sequence", 0)
    if history is None:
        history = []
        setattr(session, "_hex_original_ai_event_history", history)
    if seen is None:
        seen = set()
        setattr(session, "_hex_original_ai_seen_event_objects", seen)

    sink = getattr(port, "event_sink", None) if port is not None else None
    games = list(getattr(sink, "_unpublished", ()) or ())
    current = getattr(sink, "game", None) if sink is not None else None
    if current is not None:
        games.append(current)

    ai_uid = _uid64(ai_player_id)
    for game in games:
        for source_event in list(getattr(game, "events", ()) or ()):
            marker = id(source_event)
            if marker in seen:
                continue
            seen.add(marker)

            projected = _event_envelopes_from_game(game, ai_uid)
            # Preserve event ordering even if the visibility projection omitted
            # a private-to-human event: the authoritative event object itself
            # is the ordering marker.
            for item in projected:
                sequence += 1
                item["sequence"] = sequence
                history.append(item)

    # Persisted event logs are useful for reattached/PvP sessions. They are
    # loaded once and then combined with live PVE/Arena events.
    if not getattr(session, "_hex_original_ai_db_loaded", False):
        setattr(session, "_hex_original_ai_db_loaded", True)
        try:
            import hconnect_server
            db = hconnect_server._db
            rows = db.execute(
                "SELECT seq, event_class, event_bytes FROM session_events "
                "WHERE session_id=? AND target_player_uid=? ORDER BY seq",
                (str(_uid64(getattr(session, "session_id", 0))), str(ai_uid)),
            ).fetchall()
            for row in rows:
                raw = bytes(row[2] or b"")
                if len(raw) < 4:
                    continue
                sequence += 1
                history.append({
                    "sequence": sequence,
                    "class_id": int(row[1]),
                    "data_base64": base64.b64encode(raw).decode("ascii"),
                    "embedded_class_id": struct.unpack("<i", raw[:4])[0],
                })
        except Exception:
            pass

    setattr(session, "_hex_original_ai_event_sequence", sequence)
    # A safety ceiling prevents a pathological arena from growing without
    # bound. When it is reached, force the next worker generation to rebuild
    # from the retained suffix.
    max_events = 12000
    if len(history) > max_events:
        del history[:-max_events]
    return list(history)


def build_snapshot(handler, session, ai_player_id, human_player_id, battle_state,
                   port=None):
    state = dict(battle_state or {})
    phase = state.get("phase") or state.get("current_phase")
    ai_uid64 = _uid64(ai_player_id)
    human_uid64 = _uid64(human_player_id)
    session_uid64 = _uid64(getattr(session, "session_id", 0))

    snapshot = {
        "session_id": str(getattr(session, "session_id", "")),
        "session_uid64": session_uid64,
        "session_name": str(getattr(session, "session_name", "HEX AI Session") or
                            "HEX AI Session"),
        "phase": _jsonable(phase),
        "phase_idx": state.get("phase_idx"),
        "phase_key": (
            f"{state.get('turn_number', state.get('turn', 0))}/"
            f"{_jsonable(phase)}/{_jsonable(state.get('priority_pid', state.get('priority_player_id')))}"
        ),
        "turn_number": int(state.get("turn_number", state.get("turn", 0)) or 0),
        "priority_player_id": _jsonable(
            state.get("priority_pid", state.get("priority_player_id"))),
        "active_player_id": _jsonable(
            state.get("turn_player", state.get("active_player_id"))),
        "ai_player_id": _jsonable(ai_player_id),
        "human_player_id": _jsonable(human_player_id),
        "ai_player_uid64": ai_uid64,
        "human_player_uid64": human_uid64,
        "ai_position": int(state.get("ai_position", 1) or 1),
        "ai_health": int(state.get("ai_health", 20) or 0),
        "player_health": int(state.get("player_health", 20) or 0),
        "ai_resources": int(state.get("ai_resources", 0) or 0),
        "player_resources": int(state.get("player_resources", 0) or 0),
        "ai_threshold": _jsonable(state.get("ai_threshold", {})),
        "player_threshold": _jsonable(state.get("player_threshold", {})),
        "stack": _jsonable(state.get("stack", [])),
    }

    snapshot["events"] = _collect_live_events(session, port, ai_player_id)

    try:
        import hconnect_server
        db = hconnect_server._db
        columns = [
            row[1] for row in db.execute(
                "PRAGMA table_info(game_cards)").fetchall()
        ]
        rows = db.execute(
            "SELECT * FROM game_cards WHERE session_id=? ORDER BY id",
            (str(getattr(session, "session_id", "")),),
        ).fetchall()
        snapshot["game_cards"] = [
            {str(column): _jsonable(row[i]) for i, column in enumerate(rows_tuple)}
            for rows_tuple in []  # replaced below for clarity
        ]
        snapshot["game_cards"] = [
            {str(column): _jsonable(row[i]) for i, column in enumerate(columns)}
            for row in rows
        ]
    except Exception:
        snapshot["game_cards"] = []

    return snapshot
