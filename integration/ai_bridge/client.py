"""Synchronous JSONL worker client."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .protocol import AiRequest, AiResponse, encode_request


class AiBridgeError(RuntimeError):
    pass


@dataclass
class ProcessConfig:
    command: Sequence[str]
    cwd: str | Path | None = None
    timeout_seconds: float = 15.0


class AiBridgeClient:
    def __init__(self, config: ProcessConfig) -> None:
        self.config = config

    def call(self, request_id: str, action: str,
             payload: dict[str, Any]) -> dict[str, Any]:
        proc = subprocess.run(
            list(self.config.command),
            input=encode_request(
                AiRequest(request_id, action, payload)) + "\n",
            capture_output=True,
            text=True,
            cwd=self.config.cwd,
            timeout=self.config.timeout_seconds,
            check=False,
        )
        if proc.returncode:
            raise AiBridgeError(
                (proc.stderr or proc.stdout or
                 "AI worker exited non-zero").strip())
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        if not lines:
            raise AiBridgeError("AI worker returned no response")
        try:
            raw = json.loads(lines[-1])
            response = AiResponse(
                request_id=str(raw.get("request_id", "")),
                ok=bool(raw.get("ok", False)),
                action=str(raw.get("action", "")),
                payload=dict(raw.get("payload", {}) or {}),
                error=raw.get("error"),
                protocol=int(raw.get("protocol", 0)),
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise AiBridgeError("malformed AI worker response") from exc
        if response.protocol != 1 or response.request_id != request_id:
            raise AiBridgeError("AI worker response identity/protocol mismatch")
        if not response.ok:
            raise AiBridgeError(response.error or "AI worker rejected request")
        return response.payload
