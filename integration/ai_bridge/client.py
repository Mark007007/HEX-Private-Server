"""Persistent JSONL worker client with automatic restart on failure/timeout."""
from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections import deque
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
    """Keep one worker alive so its headless Game.Shared mirror keeps state."""

    def __init__(self, config: ProcessConfig) -> None:
        self.config = config
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[str | None] = queue.Queue()
        self._stderr = deque(maxlen=40)
        self._lock = threading.RLock()
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    def _start_locked(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._responses = queue.Queue()
        self._stderr.clear()
        self._process = subprocess.Popen(
            list(self.config.command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.config.cwd,
            bufsize=1,
        )
        self._generation += 1

        def read_stdout(proc: subprocess.Popen[str]) -> None:
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = line.strip()
                    if line:
                        self._responses.put(line)
            finally:
                self._responses.put(None)

        def read_stderr(proc: subprocess.Popen[str]) -> None:
            try:
                assert proc.stderr is not None
                for line in proc.stderr:
                    line = line.strip()
                    if line:
                        self._stderr.append(line)
            except Exception:
                pass

        threading.Thread(
            target=read_stdout, args=(self._process,),
            name="hex-original-ai-stdout", daemon=True
        ).start()
        threading.Thread(
            target=read_stderr, args=(self._process,),
            name="hex-original-ai-stderr", daemon=True
        ).start()

    def _stop_locked(self) -> None:
        proc = self._process
        self._process = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        finally:
            try:
                proc.stdin.close() if proc.stdin is not None else None
            except Exception:
                pass

    def restart(self) -> None:
        with self._lock:
            self._stop_locked()
            self._start_locked()

    def close(self) -> None:
        with self._lock:
            self._stop_locked()

    def call(self, request_id: str, action: str,
             payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._start_locked()
            proc = self._process
            if proc is None or proc.poll() is not None or proc.stdin is None:
                raise AiBridgeError("AI worker could not be started")

            try:
                proc.stdin.write(
                    encode_request(AiRequest(request_id, action, payload)) + "
"
                )
                proc.stdin.flush()
                raw_line = self._responses.get(
                    timeout=self.config.timeout_seconds
                )
            except queue.Empty:
                self._stop_locked()
                raise TimeoutError(
                    f"AI worker timed out after {self.config.timeout_seconds:g}s"
                )
            except (BrokenPipeError, OSError) as exc:
                self._stop_locked()
                raise AiBridgeError(
                    "AI worker pipe failed" +
                    (f": {exc}" if str(exc) else "")
                ) from exc

            if raw_line is None:
                stderr = "; ".join(self._stderr)
                self._stop_locked()
                raise AiBridgeError(
                    "AI worker exited without a response" +
                    (f": {stderr}" if stderr else "")
                )

            try:
                raw = json.loads(raw_line)
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
                raise AiBridgeError(
                    "AI worker response identity/protocol mismatch"
                )
            if not response.ok:
                raise AiBridgeError(
                    response.error or "AI worker rejected request"
                )
            return response.payload

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
