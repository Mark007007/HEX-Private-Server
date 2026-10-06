"""Build an immutable JSON-compatible snapshot for an AI worker."""
from __future__ import annotations

def _jsonable(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(getattr(value, "name", value))

def build_snapshot(handler, session, ai_player_id, human_player_id, battle_state):
    state = dict(battle_state or {})
    phase = state.get("phase") or state.get("current_phase")
    snapshot = {
        "session_id": str(getattr(session, "session_id", "")),
        "phase": _jsonable(phase),
        "phase_idx": state.get("phase_idx"),
        "phase_key": (
            f"{state.get('turn_number', state.get('turn', 0))}/"
            f"{_jsonable(phase)}/"
            f"{_jsonable(state.get('priority_pid', state.get('priority_player_id')))}"
        ),
        "turn_number": int(state.get("turn_number", state.get("turn", 0)) or 0),
        "priority_player_id": _jsonable(
            state.get("priority_pid", state.get("priority_player_id"))),
        "active_player_id": _jsonable(
            state.get("turn_player", state.get("active_player_id"))),
        "ai_player_id": _jsonable(ai_player_id),
        "human_player_id": _jsonable(human_player_id),
        "ai_health": int(state.get("ai_health", 20) or 0),
        "player_health": int(state.get("player_health", 20) or 0),
        "ai_resources": int(state.get("ai_resources", 0) or 0),
        "player_resources": int(state.get("player_resources", 0) or 0),
        "ai_threshold": _jsonable(state.get("ai_threshold", {})),
        "player_threshold": _jsonable(state.get("player_threshold", {})),
        "stack": _jsonable(state.get("stack", [])),
    }
    try:
        import hconnect_server
        db = hconnect_server._db
        columns = [row[1] for row in db.execute(
            "PRAGMA table_info(game_cards)").fetchall()]
        rows = db.execute(
            "SELECT * FROM game_cards WHERE session_id=? ORDER BY id",
            (str(session.session_id),)).fetchall()
        snapshot["game_cards"] = [
            {str(column): _jsonable(row[i]) for i, column in enumerate(columns)}
            for row in rows
        ]
    except Exception:
        snapshot["game_cards"] = []
    return snapshot
