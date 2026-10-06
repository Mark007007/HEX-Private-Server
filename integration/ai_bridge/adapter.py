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
        payload = {
            "session_id": session_id,
            "player_id": int(ai_player_id),
            "personality": personality,
            "snapshot": snapshot,
        }
        result = self.client.call(
            f"{session_id}:{ai_player_id}", "decide", payload)
        kind = str(result.get("kind", ""))
        if not kind:
            raise AiBridgeError("worker did not return a decision kind")
        decision_payload = result.get("payload", {})
        if not isinstance(decision_payload, dict):
            raise AiBridgeError("worker decision payload is not an object")
        return AiDecision(kind, decision_payload)
