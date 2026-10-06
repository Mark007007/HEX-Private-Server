#!/usr/bin/env python3
"""Import a copied HEX deck with Ctrl+V while the game window is focused.

The client is a fixed binary: its card collection screen has no paste handler,
so the trigger has to live outside it.  This helper watches for Ctrl+V while
HEX is the foreground window and, when the clipboard holds a deck, hands it to
the running server -- from the player's side it is just "press Ctrl+V in the
collection and the deck appears".

The deck is *not* written to the database here.  It is queued for the server
(``hex-server/deck-inbox/``), because the server is the only thing that can
follow the import with a profile-stream push, and that push is what refreshes
the collection in a live session instead of waiting for a re-login.

Two triggers are supported:

    --trigger hotkey   Ctrl+V while the game has focus (default)
    --trigger poll     any clipboard change at all

Usage::

    HEX_CODEX_DATA=build/codex-data python scripts/deck_clipboard_watch.py --player 123
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
from pathlib import Path
import sys
import time

REPO = Path(__file__).resolve().parents[1]
# hex-server goes on the path first, but the repo root must win so the root
# ``integration`` package is not shadowed by hex-server/integration.
sys.path.insert(0, str(REPO / "hex-server"))
sys.path.insert(0, str(REPO))

from integration.deck_import.codec import find_code  # noqa: E402
from integration.deck_import.text_deck import parse_deck_text  # noqa: E402

CF_UNICODETEXT = 13
VK_CONTROL = 0x11
VK_V = 0x56
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 64-bit handles must be declared, or the value is truncated to 32 bits and the
# dereference faults.
user32.OpenClipboard.argtypes = [ctypes.c_void_p]
user32.OpenClipboard.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.CloseClipboard.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.OpenProcess.restype = ctypes.c_void_p


def acquire_single_instance(lock_path: Path):
    """Take an exclusive lock so a second helper cannot start.

    Two helpers would queue the same copy twice.  A pidfile cannot prevent that:
    a pid written by PowerShell is not resolvable by ``kill -0`` in Git Bash, so
    the check has to live in the process.  The OS releases the lock on exit, so a
    stale file is harmless.
    """
    import msvcrt

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Opened for append so the file is created if missing, but the lock is always
    # taken on byte 0: in "a+" mode the stream sits at end of file, and locking
    # past EOF succeeds on Windows, which would hand every later process a "free"
    # range and defeat the lock entirely.
    handle = open(lock_path, "a+")
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def read_clipboard() -> str | None:
    if not user32.OpenClipboard(None):
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _process_name(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value)
    finally:
        kernel32.CloseHandle(handle)
    return ""


def foreground_matches(exe_name: str) -> bool:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return False
    return _process_name(pid.value).casefold() == exe_name.casefold()


def ctrl_v_down() -> bool:
    # High bit of GetAsyncKeyState means "currently down".
    return bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
                and user32.GetAsyncKeyState(VK_V) & 0x8000)


def wants_link(text: str) -> bool:
    return find_code(text) is not None


def looks_like_text_deck(text: str) -> bool:
    """Cheap gate so unrelated clipboard content is ignored."""
    if len(text) < 8 or len(text) > 40000:
        return False
    spec = parse_deck_text(text)
    return len(spec.main) + len(spec.reserves) >= 3 and bool(spec.champion or spec.main)


def queue_for_server(inbox: Path, user_id: int, text: str) -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / f"{int(time.time() * 1000)}-{os.getpid()}.json"
    target.write_text(json.dumps(
        {"player_id": int(user_id), "text": text, "name": None}),
        encoding="utf-8")
    return target


_recent: dict[str, float] = {}
DEDUP_SECONDS = 30.0


def _is_duplicate(text: str) -> bool:
    """True when the same deck was queued moments ago.

    Without this, pressing Ctrl+V repeatedly (the natural reaction when nothing
    appears to happen) creates one near-identical deck per press.
    """
    import hashlib

    digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]
    now = time.monotonic()
    for key in [k for k, seen in _recent.items() if now - seen > DEDUP_SECONDS]:
        _recent.pop(key, None)
    if digest in _recent:
        return True
    _recent[digest] = now
    return False


def resolve_player(conn, who: str | None) -> int:
    if who:
        row = None
        try:
            row = conn.execute("SELECT id FROM users WHERE id=?", (int(who),)).fetchone()
        except ValueError:
            pass
        if not row:
            row = conn.execute("SELECT id FROM users WHERE LOWER(name)=LOWER(?)",
                               (who,)).fetchone()
        if not row:
            raise SystemExit(f"player {who!r} not found")
        return int(row[0])
    rows = conn.execute("SELECT id, name FROM users ORDER BY id").fetchall()
    if len(rows) == 1:
        print(f"using the only player in the database: {rows[0][1]!r}")
        return int(rows[0][0])
    raise SystemExit("several players exist; pass --player <name>")


def main() -> int:
    import sqlite3

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--player", default=os.environ.get("HEX_DECK_USER"),
                        help="player name or id (default: the only one in the database)")
    parser.add_argument("--db", default=str(REPO / "hex-server" / "hconnect.db"))
    parser.add_argument("--trigger", choices=("hotkey", "poll"), default="hotkey",
                        help="hotkey: Ctrl+V while the game is focused (default); "
                             "poll: any clipboard change")
    parser.add_argument("--game-exe", default="Hex.exe",
                        help="process that must own the foreground window for hotkey mode")
    parser.add_argument("--interval", type=float, default=0.15,
                        help="key/clipboard poll interval in seconds")
    parser.add_argument("--include-current", action="store_true",
                        help="also queue whatever is on the clipboard at startup")
    parser.add_argument("--once", action="store_true",
                        help="queue the current clipboard content and exit")
    args = parser.parse_args()

    inbox = Path(os.environ.get("HEX_DECK_INBOX")
                 or (REPO / "hex-server" / "deck-inbox"))

    conn = sqlite3.connect(args.db, timeout=10)
    user_id = resolve_player(conn, args.player)
    conn.close()

    def handle(text: str | None) -> None:
        if not text or not text.strip():
            return
        if not (wants_link(text) or looks_like_text_deck(text)):
            return
        if _is_duplicate(text):
            print("same deck was queued a moment ago; skipping "
                  "(wait a moment and press again to force a second copy)",
                  flush=True)
            return
        try:
            target = queue_for_server(inbox, user_id, text)
        except OSError as exc:
            print(f"could not queue the import: {exc}", flush=True)
            return
        kind = "share link" if wants_link(text) else "card list"
        print(f"queued a {kind} ({len(text)} chars) as {target.name}; "
              f"the server will import it on its next tick", flush=True)

    if args.once:
        handle(read_clipboard())
        return 0

    lock = acquire_single_instance(REPO / "build" / "deck-watch.lock")
    if lock is None:
        print("another helper is already running; exiting")
        return 0

    if args.trigger == "hotkey":
        print(f"watching for Ctrl+V while {args.game_exe} is focused "
              f"(player id {user_id}); Ctrl+C to stop", flush=True)
    else:
        print(f"watching the clipboard for decks (player id {user_id}); "
              f"Ctrl+C to stop", flush=True)

    last = None
    if not args.include_current:
        try:
            last = read_clipboard()
        except Exception:
            last = None
        if last:
            print("ignoring the content already on the clipboard; "
                  "copy a deck to import it (or pass --include-current)", flush=True)

    was_down = False
    while True:
        try:
            if args.trigger == "hotkey":
                down = ctrl_v_down()
                # Fire on the edge so holding the keys does not import twice.
                if down and not was_down and foreground_matches(args.game_exe):
                    handle(read_clipboard())
                was_down = down
            else:
                current = read_clipboard()
                if current and current != last:
                    last = current
                    handle(current)
        except Exception as exc:
            print(f"watch error: {exc!r}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nstopped")