"""Optional Original-AI integration with authoritative RulesPort submission and Python fallback."""
from __future__ import annotations

import json
import os
import shlex

from .adapter import OriginalAiAdapter
from .client import AiBridgeClient, AiBridgeError, ProcessConfig
from .decision import submit_ai_decision
from .snapshot import build_snapshot


def _enabled() -> bool:
    return os.environ.get("HEX_ORIGINAL_AI", "").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def _command() -> list[str]:
    raw = os.environ.get(
        "HEX_ORIGINAL_AI_WORKER",
        "dotnet legacy-ai-worker/bin/Release/net10.0/LegacyAiWorker.dll",
    )
    try:
        return json.loads(raw) if raw.lstrip().startswith("[") else shlex.split(raw)
    except (ValueError, TypeError, json.JSONDecodeError):
        return shlex.split(raw)


def _adapter_for(handler) -> OriginalAiAdapter:
    current = getattr(handler, "_hex_original_ai_adapter", None)
    if current is not None:
        return current
    timeout = float(os.environ.get("HEX_ORIGINAL_AI_TIMEOUT", "15"))
    client = AiBridgeClient(
        ProcessConfig(
            command=_command(),
            cwd=os.environ.get("HEX_ORIGINAL_AI_CWD"),
            timeout_seconds=timeout,
        )
    )
    current = OriginalAiAdapter(client)
    handler._hex_original_ai_adapter = current
    return current


def try_native_original_ai(
    handler, session, native_ai_id, human_id, battle_state, port
) -> bool:
    """Use original Game.Shared.AI; false means keep the existing Python AI."""
    if not _enabled() or port is None:
        return False

    snapshot = build_snapshot(
        handler, session, native_ai_id, human_id, battle_state, port=port)
    personality = (
        getattr(handler, "_ai_deck_personality", None)
        or getattr(handler, "_ai_campaign_personality", None)
        or getattr(handler, "_ai_personality", None)
        or "Comfortable"
    )
    adapter = _adapter_for(handler)
    try:
        decision = adapter.decide(
            session_id=str(session.session_id),
            ai_player_id=int(getattr(native_ai_id, "uid64", native_ai_id)),
            snapshot=snapshot,
            personality=str(personality),
        )
        signature = json.dumps(
            {"kind": decision.kind, "payload": decision.payload},
            sort_keys=True, separators=(",", ":"),
        )
        if not submit_ai_decision(port, native_ai_id, decision, snapshot):
            # A rejected original transaction means its mirror is no longer
            # authoritative. Restart the worker so the next attempt rebuilds
            # its client-side session from the Python event history.
            adapter.client.restart()
            return False
        handler._hex_original_ai_last_signature = signature
        handler._hex_original_ai_last_phase = snapshot.get("phase_key")
        return True
    except (AiBridgeError, TimeoutError, OSError, ValueError, RuntimeError):
        try:
            adapter.client.restart()
        except Exception:
            pass
        return False
