"""Safety policy for original AI with deterministic Python fallback."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from time import monotonic
from typing import Any, Callable
from .adapter import AiDecision, OriginalAiAdapter
from .client import AiBridgeError

@dataclass
class AiSafetyState:
    awaiting_since: float | None = None
    resyncs: int = 0
    submitted: int = 0
    last_progress_key: str | None = None
    progress_at: int = 0
    last_signature: str | None = None
    repeats: int = 0
    suppressed: set[str] | None = None

class HybridAi:
    def __init__(self, original: OriginalAiAdapter | None,
                 fallback: Callable[..., AiDecision], *,
                 stall_seconds: float = 15.0,
                 max_resyncs: int = 3,
                 livelock_moves: int = 5000) -> None:
        self.original = original
        self.fallback = fallback
        self.stall_seconds = float(stall_seconds)
        self.max_resyncs = int(max_resyncs)
        self.livelock_moves = int(livelock_moves)
        self.state_by_session: dict[str, AiSafetyState] = defaultdict(AiSafetyState)

    def decide(self, session_id: str, ai_player_id: int, snapshot: dict[str, Any],
               *, personality: str = "Comfortable") -> AiDecision:
        state = self.state_by_session[session_id]
        phase_key = str(snapshot.get("phase_key", ""))
        if phase_key and phase_key != state.last_progress_key:
            state.last_progress_key = phase_key
            state.progress_at = state.submitted
            state.suppressed = set()
            state.last_signature = None
            state.repeats = 0
        state.awaiting_since = monotonic()
        try:
            if self.original is None:
                raise AiBridgeError("original AI unavailable")
            decision = self.original.decide(
                session_id=session_id, ai_player_id=ai_player_id,
                snapshot=snapshot, personality=personality)
        except (AiBridgeError, TimeoutError, OSError):
            decision = self.fallback(
                session_id=session_id, ai_player_id=ai_player_id,
                snapshot=snapshot, personality=personality)
        state.awaiting_since = None
        state.resyncs = 0
        return decision

    def note_submission(self, session_id: str, signature: str, *,
                        phase_key: str) -> bool:
        state = self.state_by_session[session_id]
        state.submitted += 1
        if phase_key != state.last_progress_key:
            state.last_progress_key = phase_key
            state.progress_at = state.submitted
            state.last_signature = None
            state.repeats = 0
            state.suppressed = set()
        if signature and signature == state.last_signature:
            state.repeats += 1
        else:
            state.last_signature = signature
            state.repeats = 0
        if state.repeats >= 2:
            if state.suppressed is None:
                state.suppressed = set()
            state.suppressed.add(signature)
            return True
        return False

    def check_livelock(self, session_id: str, phase_key: str) -> None:
        state = self.state_by_session[session_id]
        if phase_key != state.last_progress_key:
            state.last_progress_key = phase_key
            state.progress_at = state.submitted
            return
        if state.submitted - state.progress_at >= self.livelock_moves:
            raise RuntimeError(f"AI livelock in {phase_key}")

    def check_stall(self, session_id: str, *,
                    resync: Callable[[], None],
                    void: Callable[[str], None]) -> None:
        state = self.state_by_session[session_id]
        if state.awaiting_since is None:
            return
        if monotonic() - state.awaiting_since < self.stall_seconds:
            return
        if state.resyncs < self.max_resyncs:
            state.resyncs += 1
            state.awaiting_since = monotonic()
            resync()
            return
        void(f"AI stalled after {self.max_resyncs} resyncs")
