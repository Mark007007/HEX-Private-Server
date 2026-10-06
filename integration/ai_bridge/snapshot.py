"""Build the authoritative event replay payload used by the original-AI mirror."""
from __future__ import annotations

import base64
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


def build_snapshot(handler, session, ai_player_id, human_player_id, battle_state):
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

    # The authoritative server already records exactly the per-player event
    # stream that was sent to the client. Replaying this stream reconstructs
    # the Game.Shared ClientSessionBase state much more faithfully than a
    # hand-written state snapshot.
    try:
        import hconnect_server
        db = hconnect_server._db
        rows = db.execute(
            "SELECT seq, event_class, event_bytes "
            "FROM session_events "
            "WHERE session_id=? AND target_player_uid=? "
            "ORDER BY seq",
            (str(session_uid64), str(ai_uid64)),
        ).fetchall()
        snapshot["events"] = [
            {
                "sequence": int(row[0]),
                "class_id": int(row[1]),
                "data_base64": base64.b64encode(bytes(row[2])).decode("ascii"),
            }
            for row in rows
            if row[2] is not None
        ]
    except Exception:
        snapshot["events"] = []

    try:
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
            {str(column): _jsonable(row[i]) for i, column in enumerate(columns)}
            for row in rows
        ]
    except Exception:
        snapshot["game_cards"] = []

    return snapshot
