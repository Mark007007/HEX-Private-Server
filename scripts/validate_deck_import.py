#!/usr/bin/env python3
"""Validate the deck import pipeline in the deployed hex-server tree.

A clone becomes usable by running ``scripts/apply_integration.sh``, which
copies the overlay into ``hex-server/``.  Nothing in the test suite catches a
stale deploy -- edit ``integration/`` or ``overlay/`` and forget to re-apply,
and the tests still pass while the running server keeps the old code.  This
script checks the deployed tree instead:

  1. the overlay is actually applied (deck_inbox.py present, the upstream
     32-bit gem mask gone)
  2. a card list and a Hex Codex share link both import through
     ``hex-server/deck-inbox/`` -- the same channel the Ctrl+V helper uses
  3. the outbound profile payload carries individual gems, not packed values

It never writes the live database.  ``hex-server/hconnect.db`` is copied with
SQLite's backup API into a temporary file, ``HEX_DB_PATH`` points the server at
that copy, and the copy is deleted afterwards.  The inbox is redirected to a
temporary directory for the same reason.

``local-env.sh`` is read first, so the run defaults to the same player, catalog
and database the launcher uses.  Precedence is ``--player`` > environment >
``local-env.sh``.

Usage::

    python scripts/validate_deck_import.py
    python scripts/validate_deck_import.py --player 123

Exit code is 0 when every check passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
HEX = REPO / "hex-server"
LIVE_DB = HEX / "hconnect.db"
CODEX_DATA = REPO / "build" / "codex-data"
LOCAL_ENV = REPO / "local-env.sh"

# The upstream parse of the client's saved gems, before this project patched it.
UPSTREAM_MASK_BUG = "unhexlify(seg[17]))[0] & 0xFFFFFFFF"

_results: list[tuple[bool, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    _results.append((bool(ok), label, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))
    return bool(ok)


def section(title: str) -> None:
    print(f"\n{title}")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
# Git Bash writes paths as /d/game/... because bash wants that form.
_POSIX_DRIVE = re.compile(r"^/([A-Za-z])/(.*)$")


def to_native_path(value: str) -> str:
    """Translate the Git Bash path form into a native one.

    ``start-game.sh`` can use ``/d/game/...`` directly; Python cannot -- it
    resolves that to ``D:\\d\\game\\...`` and the catalog is not found.  Only
    values that start with a slash are touched, which is every path in
    ``local-env.sh`` and no other setting it holds.
    """
    if os.name != "nt":
        return value
    match = _POSIX_DRIVE.match(value)
    if match:
        return f"{match.group(1).upper()}:\\" + match.group(2).replace("/", "\\")
    return value


def load_local_env(path: Path) -> tuple[dict[str, str], list[str]]:
    """Fill unset environment variables from ``local-env.sh``.

    ``start-game.sh`` sources this file, so reading it here keeps the validator
    on the same player, database and catalog the launcher uses -- otherwise
    every run needs ``--player`` even though the launcher already knows it.

    Precedence is ``--player`` > environment > this file: a variable already
    present in the environment is left alone, so an ad-hoc override still wins.

    The file is a plain KEY=VALUE list.  Shell expansion is not performed, so a
    value that looks like it needs it is reported and skipped rather than read
    literally and silently used.
    """
    if not path.is_file():
        return {}, []

    applied: dict[str, str] = {}
    warnings: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGNMENT.match(raw)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if "$" in value or "`" in value:
            warnings.append(f"{key} 的值含 shell 展开（{value}），未解析")
            continue
        if value.startswith("/"):
            value = to_native_path(value)
        if key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied, warnings


# --- 1. the overlay must actually be applied --------------------------------
def check_deployed() -> bool:
    """True when the overlay has been applied to hex-server/."""
    hs = read(HEX / "hconnect_server.py")
    ed = read(HEX / "encoded_decks.py")
    imp = read(HEX / "integration" / "deck_import" / "importer.py")

    mask_hits = hs.count(UPSTREAM_MASK_BUG)
    checks = [
        ("hex-server/deck_inbox.py 存在（overlay 已应用）", (HEX / "deck_inbox.py").is_file()),
        ("integration/deck_import/text_deck.py 已注入",
         (HEX / "integration" / "deck_import" / "text_deck.py").is_file()),
        ("hconnect_server.py 含 _process_deck_inbox 定义", "def _process_deck_inbox()" in hs),
        ("hconnect_server.py 不含上游的 32 位截断掩码", mask_hits == 0),
        ("importer.py 含 GEM_FORMAT_BIT", "GEM_FORMAT_BIT" in imp),
        ("encoded_decks.py 含 GEM_FORMAT_BIT", "GEM_FORMAT_BIT" in ed),
    ]
    section("[1] 部署状态：overlay 是否已应用")
    for label, ok in checks:
        check(label, ok)
    if mask_hits:
        print(f"\n  -> hconnect_server.py 里有 {mask_hits} 处未打补丁的解析。"
              f"请先运行: bash scripts/apply_integration.sh")
    return all(ok for _, ok in checks)


# --- link encoding (codec.py only decodes) ----------------------------------
def encode_link(champion_site_id: int, entries, name: str | None = None) -> str:
    """Build a v1 code.  decode() always reads two lists, so both are written."""

    def uv(value: int) -> bytes:
        out = bytearray()
        while True:
            b = value & 0x7F
            value >>= 7
            out.append(b | 0x80 if value else b)
            if not value:
                return bytes(out)

    body = bytearray()
    body += uv(1) + uv(champion_site_id)
    body += uv(len(entries))
    previous = 0
    for site_id, copies, gems in entries:
        body += uv(site_id - previous)
        previous = site_id
        body += uv(copies * 2 + (1 if gems else 0))
        if gems:
            body += uv(len(gems))
            for gem in gems:
                body += uv(gem)
    body += uv(0)  # reserves
    if name:
        raw = name.encode("utf-8")
        body += uv(1) + uv(len(raw)) + raw
    crc = (binascii.crc32(bytes(body)) & 0xFFFFFFFF).to_bytes(4, "big")
    return "v1" + base64.urlsafe_b64encode(bytes(body) + crc).decode().rstrip("=")


def pick_player(db, wanted: str | None) -> int:
    if wanted:
        row = None
        try:
            row = db.execute("SELECT id FROM users WHERE id=?", (int(wanted),)).fetchone()
        except ValueError:
            pass
        if not row:
            row = db.execute("SELECT id FROM users WHERE LOWER(name)=LOWER(?)",
                             (wanted,)).fetchone()
        if not row:
            raise SystemExit(f"player {wanted!r} not found")
        return int(row[0])
    rows = db.execute("SELECT id, name FROM users ORDER BY id").fetchall()
    if len(rows) == 1:
        return int(rows[0][0])
    raise SystemExit(f"{len(rows)} players exist; pass --player <name or id>")


def make_isolated_db(workdir: Path) -> Path:
    """Consistent copy of the live database (WAL included), read-only source."""
    target = workdir / "hconnect.db"
    source = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return target


def cleanup_workdir(workdir: Path, keep: bool) -> None:
    """Remove the temporary database and inbox.

    Windows will not delete a file that still has an open handle, and the
    server keeps its connection open for the whole process, so the connection
    has to be released first -- otherwise the copy (tens of MB) is silently
    left behind.
    """
    if keep:
        print(f"\n  临时目录保留在: {workdir}")
        return
    import gc
    import shutil

    gc.collect()
    for obj in list(gc.get_objects()):
        if isinstance(obj, sqlite3.Connection):
            try:
                obj.close()
            except Exception:
                pass
    shutil.rmtree(workdir, ignore_errors=True)
    if workdir.exists():
        print(f"\n  WARN: 临时目录未能删除，请手工清理: {workdir}")


def main() -> int:
    # Read the launcher's config first, so --player can default to the same
    # player, database and catalog start-game.sh would use.
    applied, env_warnings = load_local_env(LOCAL_ENV)

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--player", default=os.environ.get("HEX_DECK_USER"),
                        help="player name or id (default: HEX_DECK_USER, else the "
                             "only player in the database)")
    parser.add_argument("--keep-workdir", action="store_true",
                        help="keep the temporary database and inbox for inspection")
    args = parser.parse_args()

    print("deck import validation")
    print("=" * 62)

    section("[0] 前置条件")
    if LOCAL_ENV.is_file():
        if applied:
            print(f"  INFO  local-env.sh 已加载，填充: {', '.join(sorted(applied))}")
        else:
            print("  INFO  local-env.sh 已读取（各项均已被环境变量覆盖）")
    else:
        print("  INFO  local-env.sh 不存在，使用环境变量 / --player")
    for warning in env_warnings:
        print(f"  WARN  {warning}")

    if not check("hex-server/hconnect.db 存在（已建库）", LIVE_DB.is_file(), str(LIVE_DB)):
        print("\n  -> 先建库: HEX_GAMEDATA=<客户端>/Data/gamedata "
              "bash scripts/prepare_client_records.sh")
        return 1
    check("build/codex-data 存在（分享链接路径需要）",
          (CODEX_DATA / "ids.json").is_file() and (CODEX_DATA / "gems.json").is_file())

    if not check_deployed():
        print("\n  -> 部署树是旧的，后续检查没有意义。先运行: "
              "bash scripts/apply_integration.sh")
        return 1

    workdir = Path(tempfile.mkdtemp(prefix="hex-verify-"))
    inbox = workdir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    isolated_db = make_isolated_db(workdir)

    # Point the server at the copy BEFORE importing it, and redirect the inbox.
    os.environ["HEX_DB_PATH"] = str(isolated_db)
    os.environ["HEX_DECK_INBOX"] = str(inbox)
    os.environ.setdefault("HEX_CODEX_DATA", str(CODEX_DATA))
    sys.path.insert(0, str(HEX))
    sys.path.insert(0, str(REPO))

    try:
        section("[2] 加载服务器代码（只读副本，不触碰真库）")
        import hconnect_server
        import deck_inbox
        import encoded_decks

        db = hconnect_server._db
        live_path = db.execute("PRAGMA database_list").fetchone()[2]
        check("连接指向临时副本", Path(live_path).resolve() == isolated_db.resolve(),
              live_path)
        check("HEX_CODEX_DATA 已提供", bool(os.environ.get("HEX_CODEX_DATA")),
              os.environ["HEX_CODEX_DATA"])

        player = pick_player(db, args.player)
        print(f"  玩家 id = {player}")

        # --- pick real, resolvable data --------------------------------
        ids_entries = json.loads((CODEX_DATA / "ids.json").read_text(encoding="utf-8"))["entries"]
        site_of_guid = {str(e[1]).lower(): e for e in ids_entries}
        gem_type_of_site = {}
        for entry in ids_entries:
            if len(entry) < 3 or str(entry[2]) != "gem":
                continue
            row = db.execute(
                "SELECT gem_type FROM gem_templates WHERE gem_type_name=? "
                "ORDER BY gem_type LIMIT 1", (str(entry[1]),)).fetchone()
            if row:
                gem_type_of_site[int(entry[0])] = int(row[0])
        link_gem_sites = sorted(gem_type_of_site)[:2]
        champion_site = next(int(e[0]) for e in ids_entries
                             if len(e) > 2 and str(e[2]) == "champion")

        owned = db.execute(
            "SELECT template_guid FROM card_instances WHERE user_id=? "
            "GROUP BY template_guid HAVING COUNT(*)>=2 ORDER BY template_guid",
            (player,)).fetchall()
        card_guid = next((str(r[0]) for r in owned
                          if str(r[0]).lower() in site_of_guid), None)
        if card_guid is None:
            check("数据库里有可导入的卡（已拥有 ≥2 张且能映射到站点 id）", False)
            return 1
        card_site = site_of_guid[card_guid.lower()][0]
        card_name = db.execute("SELECT name FROM card_templates WHERE guid=?",
                               (card_guid,)).fetchone()[0].strip()
        champ_name = db.execute(
            "SELECT name FROM champion_templates_extended WHERE name IS NOT NULL "
            "ORDER BY name LIMIT 1").fetchone()[0].strip()
        text_gems = [r[0] for r in db.execute(
            "SELECT name FROM gem_templates WHERE name IS NOT NULL "
            "ORDER BY gem_type LIMIT 2")]

        check("链接路径：宝石站点 id 能映射到 gem_templates", len(link_gem_sites) >= 2,
              f"sites={link_gem_sites}")
        check("文本路径：能取到 2 个宝石显示名", len(text_gems) >= 2, str(text_gems))

        before = db.execute("SELECT COUNT(*) FROM decks WHERE user_id=?", (player,)).fetchone()[0]
        print(f"\n  测试素材: 卡={card_name!r} 英雄={champ_name!r} 宝石={text_gems}")
        print(f"  导入前卡组数 = {before}")

        # --- 3. card list through the inbox (the Ctrl+V channel) -------
        section("[3] 牌表文本 → deck-inbox → 服务器消费")
        text = "\n".join([
            f"Champion: {champ_name}",
            "Troops · 4",
            "",
            "2",
            card_name,
            ", ".join(text_gems),
            "1",
        ])
        (inbox / "text.json").write_text(
            json.dumps({"player_id": player, "text": text, "name": "VERIFY-TEXT"}),
            encoding="utf-8")
        log: list[str] = []
        processed = deck_inbox.process_pending(log=log.append)
        check("消费了 1 个请求", processed == 1,
              f"processed={processed}  {log[-1] if log else ''}")
        check("请求文件已被抢占式删除", not (inbox / "text.json").exists())
        check("无客户端在线时降级为「下次登录可见」",
              any("not connected" in m for m in log))

        rows = db.execute("SELECT active_gems FROM decks WHERE user_id=? AND deck_name=?",
                          (player, "VERIFY-TEXT")).fetchall()
        check("卡组已入库", len(rows) == 1)
        if rows:
            packed = json.loads(rows[0][0])
            check("每张卡一个打包值", len(packed) == 2, str(packed))
            check("置了 bit 62 格式位", all(v >> 62 == 1 for v in packed.values()),
                  str([hex(v) for v in packed.values()]))
            slots = [[(v >> (10 * s)) & 0x3FF for s in range(6)] for v in packed.values()]
            check("两槽位各解出一颗宝石",
                  all(len([x for x in s if x]) == 2 for s in slots),
                  str([[x for x in s if x] for s in slots]))

        # --- 4. share link through the same channel --------------------
        section("[4] Hex Codex v1 分享链接 → deck-inbox → 服务器消费")
        link = encode_link(champion_site, [(card_site, 2, link_gem_sites)],
                           name="VERIFY-LINK")
        print(f"  合成链接: {link[:52]}…  ({len(link)} 字符)")
        (inbox / "link.json").write_text(
            json.dumps({"player_id": player, "text": link, "name": "VERIFY-LINK"}),
            encoding="utf-8")
        log2: list[str] = []
        processed2 = deck_inbox.process_pending(log=log2.append)
        check("消费了 1 个链接请求", processed2 == 1,
              f"processed={processed2}  {log2[-1] if log2 else ''}")

        rows2 = db.execute("SELECT active_gems FROM decks WHERE user_id=? AND deck_name=?",
                           (player, "VERIFY-LINK")).fetchall()
        check("链接卡组已入库", len(rows2) == 1)
        if rows2:
            packed2 = json.loads(rows2[0][0])
            check("链接路径同样置 bit 62",
                  all(v >> 62 == 1 for v in packed2.values()),
                  str([hex(v) for v in packed2.values()]))
            expected = sorted(gem_type_of_site[s] for s in link_gem_sites)
            per_card = [sorted(g for g in ((v >> (10 * s)) & 0x3FF for s in range(6)) if g)
                        for v in packed2.values()]
            check("每张卡解出的 gem_type 与站点 id 一致",
                  bool(per_card) and all(g == expected for g in per_card),
                  f"得到 {per_card}，期望每张 {expected}")

        after = db.execute("SELECT COUNT(*) FROM decks WHERE user_id=?", (player,)).fetchone()[0]
        check("卡组总数 +2", after == before + 2, f"{before} -> {after}")

        # --- 5. outbound payload --------------------------------------
        section("[5] 出站 EncodedDecks 载荷：宝石必须是单颗而非打包值")
        blob = encoded_decks.encode_encoded_decks(
            hconnect_server.db_get_decks(player), player)
        pos = 8
        count = int.from_bytes(blob[4:8], "little")

        def varint(buf, index):
            value = 0
            shift = 0
            while True:
                byte = buf[index]
                index += 1
                value |= (byte & 0x7F) << shift
                if not byte & 0x80:
                    return value, index
                shift += 7

        found = None
        for _ in range(count):
            length = int.from_bytes(blob[pos:pos + 4], "little")
            pos += 4
            pt = blob[pos:pos + length]
            pos += length
            cursor = 0
            name_len, cursor = varint(pt, cursor)
            deck_name = pt[cursor:cursor + name_len].decode("utf-8")
            cursor += name_len + 32
            _, cursor = varint(pt, cursor)
            cards, cursor = varint(pt, cursor)
            gems_seen = []
            for _ in range(cards):
                cursor += 16
                _, cursor = varint(pt, cursor)
                cursor += 3
                gem_count, cursor = varint(pt, cursor)
                group = []
                for _ in range(gem_count):
                    gem, cursor = varint(pt, cursor)
                    group.append(gem)
                if group:
                    gems_seen.append(tuple(group))
            if deck_name.startswith("VERIFY-"):
                found = gems_seen
            pos += 16 + 8 + 4
            for _ in range(3):
                _, pos = varint(blob, pos)
            coin_len, pos = varint(blob, pos)
            pos += coin_len

        check("载荷里找到验证卡组", found is not None)
        if found is not None:
            check("每张卡发出的是单颗枚举值 1..73，不是打包整数",
                  all(all(1 <= g <= 73 for g in group) for group in found),
                  str(found))
    finally:
        cleanup_workdir(workdir, args.keep_workdir)

    failed = [label for ok, label, _ in _results if not ok]
    print("\n" + "=" * 62)
    passed = len(_results) - len(failed)
    if failed:
        print(f"结果: {passed} 项通过, {len(failed)} 项失败")
        for label in failed:
            print("  - " + label)
        return 1
    print(f"结果: {passed} 项全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
