"""Authoritative-server adapter for Original AI worker decisions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import AiBridgeClient, AiBridgeError


@dataclass(frozen=True)
class AiDecision:
    kind: str
    payload: dict[str, Any]


class OriginalAiAdapter:
    def __init__(self, client: AiBridgeClient) -> None:
        self.client = client

    def decide(self, *, session_id: str, ai_player_id: int,
               snapshot: dict[str, Any],
               personality: str = "Comfortable") -> AiDecision:
        worker_snapshot = dict(snapshot or {})
        events = list(worker_snapshot.get("events", []) or ())
        payload = {
            "session_id": session_id,
            "session_uid64": int(worker_snapshot.get("session_uid64", 0) or 0),
            "session_name": str(worker_snapshot.get("session_name", "") or ""),
            "player_id": int(ai_player_id),
            "ai_player_uid64": int(
                worker_snapshot.get("ai_player_uid64", ai_player_id) or ai_player_id
            ),
            "human_player_uid64": int(
                worker_snapshot.get("human_player_uid64", 0) or 0
            ),
            "ai_position": int(worker_snapshot.get("ai_position", 1) or 1),
            "personality": personality,
            "events": events,
            "snapshot": worker_snapshot,
        }
        result = self.client.call(
            f"{session_id}:{ai_player_id}", "decide", payload
        )
        kind = str(result.get("kind", "") or "").strip()
        if not kind:
            raise AiBridgeError("worker did not return a decision kind")
        decision_payload = result.get("payload", {})
        if not isinstance(decision_payload, dict):
            raise AiBridgeError("worker decision payload is not an object")
        return AiDecision(kind, decision_payload)
