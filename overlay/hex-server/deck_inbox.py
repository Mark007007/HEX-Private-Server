"""Deck imports requested from outside the server process.

The clipboard/hotkey helper runs as its own process, so it must not write the
database behind the server's back: that leaves the running client with a stale
deck list until it re-logs.  Instead the helper drops a small JSON request here
and the server's accept loop picks it up, imports through the same importer the
chat commands use, and pushes the profile stream -- which is what makes the
card collection refresh in a live session.

Request file::

    {"player_id": <int>, "text": "<deck link or card list>", "name": null}

Processed files are deleted; a malformed one is deleted too so it cannot wedge
the loop.  A player who is not currently connected is still imported -- the deck
appears at their next login, it just cannot be pushed mid-session.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

DEFAULT_INBOX = Path(__file__).resolve().parent / "deck-inbox"


def inbox_dir() -> Path:
    return Path(os.environ.get("HEX_DECK_INBOX") or DEFAULT_INBOX)


def _handler_for(user_id: int):
    """The live connection for a player, if they have one.

    ``_active_clients`` is the login-time registry (uid -> [(handler, time)]),
    and it is the right one here: ``player_handlers`` is only populated when a
    player joins a tournament, so a player who merely logged in and opened their
    collection is absent from it -- which silently turned every collection
    refresh into "shows up at your next login".
    """
    import hconnect_server

    uid = int(user_id)
    lock = getattr(hconnect_server, "_active_clients_lock", None)
    clients = getattr(hconnect_server, "_active_clients", None)
    if clients is not None:
        if lock is not None:
            with lock:
                entries = list(clients.get(uid) or ())
        else:
            entries = list(clients.get(uid) or ())
        # Most recent connection wins; older ones may be stale sockets.
        for entry in reversed(entries):
            handler = entry[0] if isinstance(entry, (tuple, list)) else entry
            if handler is not None:
                return handler

    from gamemodes.tournament_engine import player_handlers

    return player_handlers.get(uid)


def _import(user_id: int, text: str, name: str | None):
    """Run the import for a player id, without needing their socket."""
    from integration.deck_import.codec import find_code
    from integration.deck_import.hex_server_adapter import (
        build_hex_server_importer, build_text_importer)

    shim = SimpleNamespace(user_profile={"id": int(user_id)})
    if find_code(text) is not None:
        data_root = (os.environ.get("HEX_CODEX_DATA")
                     or os.environ.get("HEX_CODEX_DATA_PATH"))
        if not data_root:
            raise RuntimeError(
                "this is a Hex Codex share link but HEX_CODEX_DATA is not set")
        importer = build_hex_server_importer(shim, data_root)
        deck = importer.build(int(user_id), text, name=name)
    else:
        importer = build_text_importer(shim)
        deck = importer.build_from_text(int(user_id), text, name=name)
    deck_id = importer.save(int(user_id), deck)
    # The importer is handed the shared connection, so committing is our job.
    import hconnect_server

    hconnect_server._db.commit()
    return deck, deck_id


def process_pending(log=print) -> int:
    """Import every queued request.  Never raises: this runs in the accept loop."""
    root = inbox_dir()
    if not root.is_dir():
        return 0

    processed = 0
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log(f"deck inbox: discarding {path.name} ({exc!r})")
            path.unlink(missing_ok=True)
            continue

        # Claim it before the work so a slow import cannot be retried forever.
        try:
            path.unlink()
        except OSError:
            pass

        try:
            user_id = int(payload["player_id"])
            text = str(payload["text"])
            name = payload.get("name") or None
        except Exception as exc:
            log(f"deck inbox: bad request in {path.name} ({exc!r})")
            continue

        try:
            deck, deck_id = _import(user_id, text, name)
        except Exception as exc:
            log(f"deck inbox: import failed for player {user_id}: {exc}")
            continue

        missing = f", {len(deck.shortfalls)} short" if deck.shortfalls else ""
        handler = _handler_for(user_id)
        if handler is not None:
            try:
                handler.push_profile_stream()
                log(f"deck inbox: imported '{deck.name}' as deck #{deck_id} "
                    f"({len(deck.cards)} cards{missing}) and pushed to the client")
            except Exception as exc:
                log(f"deck inbox: imported '{deck.name}' as deck #{deck_id} "
                    f"but the push failed: {exc!r}")
        else:
            log(f"deck inbox: imported '{deck.name}' as deck #{deck_id} "
                f"({len(deck.cards)} cards{missing}); player {user_id} is not "
                f"connected, the deck shows up at their next login")
        processed += 1
    return processed