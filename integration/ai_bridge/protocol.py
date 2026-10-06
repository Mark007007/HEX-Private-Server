"""JSONL IPC protocol for the original Hex AI bridge.

One request produces exactly one JSON response. The protocol is transport
agnostic: stdin/stdout, named pipes, Unix sockets, or TCP can all be adapters.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
import json

PROTOCOL_VERSION = 1

@dataclass(frozen=True)
class AiRequest:
    request_id: str
    action: str
    payload: dict[str, Any]
    protocol: int = PROTOCOL_VERSION

@dataclass(frozen=True)
class AiResponse:
    request_id: str
    ok: bool
    action: str
    payload: dict[str, Any]
    error: str | None = None
    protocol: int = PROTOCOL_VERSION

def encode_request(request: AiRequest) -> str:
    return json.dumps(asdict(request), ensure_ascii=False, separators=(",", ":"))

def decode_request(line: str) -> AiRequest:
    raw = json.loads(line)
    if int(raw.get("protocol", 0)) != PROTOCOL_VERSION:
        raise ValueError("unsupported AI bridge protocol")
    request_id = str(raw.get("request_id", ""))
    action = str(raw.get("action", ""))
    payload = raw.get("payload", {})
    if not request_id or not action or not isinstance(payload, dict):
        raise ValueError("invalid AI request")
    return AiRequest(request_id, action, payload, PROTOCOL_VERSION)

def encode_response(response: AiResponse) -> str:
    return json.dumps(asdict(response), ensure_ascii=False, separators=(",", ":"))

def ok(request_id: str, action: str, payload: dict[str, Any] | None = None) -> AiResponse:
    return AiResponse(request_id, True, action, payload or {})

def fail(request_id: str, action: str, message: str) -> AiResponse:
    return AiResponse(request_id, False, action, {}, str(message))
