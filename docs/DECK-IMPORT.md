# 卡组导入子系统 —— 技术细节记录

本文记录「在游戏卡牌收藏界面按 Ctrl+V 导入 Hex Codex 卡组」这条链路的
完整实现、协议契约与踩坑。目标是让后来者不必重新逆向一遍。

相关文档：

- 魔石编码格式 → [`GEM-ENCODING.md`](GEM-ENCODING.md)
- Windows 环境排障 → [`WINDOWS-SETUP.md`](WINDOWS-SETUP.md)

---

## 1. 需求与硬约束

| 约束 | 原因 |
|---|---|
| 从 **Hex Codex v1 分享链接**和**卡牌清单文本**两种格式导入 | 站点提供分享链接；用户往往只能复制页面上的文字 |
| 触发方式为**剪贴板监听** | 客户端是固定二进制，收藏界面**没有**粘贴处理器，触发器只能放在进程外 |
| 缺卡时**不报错**，能导入多少导多少 | 玩家卡不全是最常见情况；直接失败会让功能完全不可用 |
| 不伪造卡牌 | 导入只消费已有 `card_instances`，不制造卡 |
| 卡名歧义时**确定性选择并明确报告** | 同一卡名在 `card_templates` 中按印次重复多次，随机选择会导致重复导入不稳定 |
| 导入必须经过**服务器进程** | 只有服务器能跟进 profile-stream 推送，这是免重登刷新的唯一途径 |

---

## 2. 端到端数据流

```
 用户：在收藏界面按 Ctrl+V
        │
        ▼
 scripts/deck_clipboard_watch.py        （独立进程）
        │  ① 前台窗口必须是 Hex.exe
        │  ② Ctrl+V 边沿检测
        │  ③ 内容门控：分享链接 或 卡牌清单
        │  ④ 30 秒同内容去重
        │  ⑤ 写 JSON 请求（不碰数据库！）
        ▼
 hex-server/deck-inbox/<ts>-<pid>.json
        │
        ▼
 hex-server/hconnect_server.py main()   接受循环
        │  空闲 tick 调用 _process_deck_inbox()
        ▼
 hex-server/deck_inbox.py  process_pending()
        │  先 unlink 再处理（抢占式，避免重复重试）
        ▼
 integration/deck_import/hex_server_adapter.py
        │  构造 DeckImporter（选择链接路径或文本路径）
        ▼
 integration/deck_import/importer.py
        │  归一化成 _Resolved 行 → 共享装配器 _assemble()
        │  · 只取已拥有实例，缺卡记入 shortfalls
        │  · 卡组重名 → "name (2)"
        │  · 魔石 → 按槽位打包
        ▼
 profile_db.db_save_deck(..., conn=<共享连接>)
        │
        ▼
 deck_inbox._import()  →  hconnect_server._db.commit()
        │
        ▼
 _handler_for(user_id).push_profile_stream()
        │
        ▼
 客户端收藏界面当场出现新卡组
```

---

## 3. 触发层：`scripts/deck_clipboard_watch.py`

### 3.1 单实例锁

两把助手的后果是**同一份剪贴板被入队两次**。pidfile 解决不了：
PowerShell 写下的 pid 在 Git Bash 里 `kill -0` 解析不到。因此锁必须落在进程内。

```python
handle = open(lock_path, "a+")
handle.seek(0)                                   # ← 关键
msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
```

**必须显式 `seek(0)`**：`a+` 模式打开后流指针位于 EOF，而在 Windows 上锁定
EOF 之后的区域总是成功，于是每个后续进程都会拿到一个「空闲」区间，锁形同虚设。
锁文件用 `a+` 只是为了让文件在被删除后仍能自动重建；OS 在进程退出时释放锁，
所以残留的锁文件无害。

### 3.2 前台窗口判定

只有 `Hex.exe` 是前台窗口时才响应，避免干扰其他程序的正常 Ctrl+V。

```
GetForegroundWindow() → GetWindowThreadProcessId() → OpenProcess()
  → QueryFullProcessImageNameW() → 比较 basename 与 "Hex.exe"
```

### 3.3 按键边沿检测

```python
down = bool(GetAsyncKeyState(VK_CONTROL) & 0x8000
            and GetAsyncKeyState(VK_V) & 0x8000)
if down and not was_down and foreground_matches(args.game_exe):
    handle(read_clipboard())
```

`GetAsyncKeyState` 的**高字节**表示「当前按下」。判边沿（`not was_down`）是为了
按住不放时不会连续导入。

### 3.4 ctypes 陷阱

64 位句柄**必须显式声明 `restype`**，否则返回值被截断成 32 位，解引用直接访问违例：

```python
user32.GetClipboardData.restype = ctypes.c_void_p
kernel32.GlobalLock.restype      = ctypes.c_void_p
kernel32.OpenProcess.restype     = ctypes.c_void_p
user32.GetForegroundWindow.restype = ctypes.c_void_p
```

Windows 剪贴板存在争用，读写可能静默失败，因此 `read_clipboard()` 逐层判空返回
`None`，由主循环吞掉异常继续轮询。

### 3.5 内容门控与去重

- 分享链接：`codec.find_code(text) is not None`
- 卡牌清单：`looks_like_text_deck()` —— 长度 8..40000，且解析出
  **≥3 条**主牌/备牌条目，并且有英雄或主牌。

去重键是内容的 `sha256[:16]`，窗口 30 秒。没有它的话，用户「按了没反应就再按一次」
会生成一叠几乎相同的卡组。

### 3.6 为什么要做成独立进程

呼吸点在于**不能直连数据库**。直写 SQLite 会绕过服务器，客户端拿不到推送，
表现为「下次登录才出现」。这对用户是可见的失败。详见 §4。

---

## 4. 跨进程通道：`hex-server/deck_inbox.py`

### 4.1 请求格式

```json
{"player_id": 2318431741638412123, "text": "<分享链接或卡牌清单>", "name": null}
```

文件名 `<毫秒时间戳>-<pid>.json`，天然有序且无碰撞。

### 4.2 抢占式消费

```python
payload = json.loads(path.read_text(encoding="utf-8"))
path.unlink()            # 先认领再干活，慢导入不会永远重试
```

解析失败的文件**也删除**，否则一个坏文件会把循环卡死。

### 4.3 主循环空闲 tick

```python
if conn is None:
    # Idle tick: cheap poll for external deck imports.
    _process_deck_inbox()
    continue
```

放在接受循环的空闲分支：不占用正常请求路径，且天然串行化，无需额外加锁。
`_process_deck_inbox()` 自身吞掉所有异常 —— 一个坏请求不能打死服务器。

### 4.4 推送目标必须用 `_active_clients`

这是最容易踩的坑。服务器有两个「在线玩家」注册表：

| 注册表 | 写入时机 | 适用性 |
|---|---|---|
| `player_handlers` | **仅当玩家加入锦标赛** | ❌ 只登录并打开收藏的玩家不在里面 |
| `_active_clients` | **登录时**（`uid -> [(handler, time)]`） | ✅ |

用错注册表的后果是**静默降级**：导入成功、日志正常，但推送永远走
「玩家未连接，下次登录可见」分支。看起来像功能没做完。

读取时也要拿锁，并且**取最近一条**，旧条目可能是已失效的 socket：

```python
lock = getattr(hconnect_server, "_active_clients_lock", None)
clients = getattr(hconnect_server, "_active_clients", None)
...
for entry in reversed(entries):
    handler = entry[0] if isinstance(entry, (tuple, list)) else entry
```

跨线程推送是安全的：`HCPHandler.send()` 内部有 `self._send_lock`。

### 4.5 玩家不在线时

仍然导入，只是推不出去 —— 卡组在下次登录时出现。这是刻意的降级，不是失败。

### 4.6 连接与事务归属

`_import()` 用 `SimpleNamespace(user_profile={"id": ...})` 做 handler 垫片，
让适配器无需真实 socket。而 `HexServerDeckStorage` 把**服务器的 `_db` 共享连接**
交给 `db_save_deck(..., conn=...)`，因此**提交是调用方的责任**：

```python
importer.save(int(user_id), deck)
hconnect_server._db.commit()          # ← 少了这行，卡组写完就丢
```

> 历史 Bug：`db_save_deck` 传 `conn` 时不自行提交，导致卡组保存后消失
> （表现为「每次都报 deck #1」）。`profile_db.db_update_deck` 后来也补上了
> `conn=None` 参数 —— 它原本缺这个参数，导致**任何卡组保存都 NameError 崩溃**。

---

## 5. 导入器：`integration/deck_import/`

### 5.1 两种入口，一个装配器

```
build(user_id, link)          →  _from_link()  ─┐
build_from_text(user_id, txt) →  _from_text()  ─┤
                                                ├→ _assemble() → ImportedDeck
                                                ┘
```

两条路径都归一化成 `_Resolved(guid, display, copies, gem_guids)`，因此
拥有权、命名、魔石处理**不可能产生分歧**。

| 文件 | 职责 |
|---|---|
| `codec.py` | v1 code 解码（URL-safe base64 + CRC32 校验）、`find_code()` 从任意文本里挖 code |
| `site_ids.py` | 读 `ids.json` / `gems.json`，站点 id → GUID、宝石 GUID → 类型名 |
| `text_deck.py` | 分「站点渲染格式」与「单行格式」的状态机解析器 |
| `importer.py` | `ImportedDeck` / `DeckImporter` / 装配器 |
| `hex_server_adapter.py` | 落地到 hex-server 的持久化、卡名/宝石解析器 |

### 5.2 文本清单解析（`text_deck.py`）

输入有两大类，允许混合：

```
Bring Your Daughter To The Slaughter   ← 卡组名
Uzzu the Bonewalker                    ← 英雄（裸名）
Diamond                                ← 碎片

Troops · 16                            ← 分节头
4                                      ← 数量
Daughter of the Poet                   ← 卡名
2                                      ← 费用

4
Brilliant Annihilix
Major Diamond of Solidarity, Minor Diamond of Duty   ← 魔石（逗号分隔，可多颗）
3
```

难点是**数量 / 费用的歧义**：两者都是裸数字。解法是**靠位置消歧** ——
在一个分节内，条目总是「裸数字（数量）→ 卡名 → 若干魔石行 → 恰好一个费用行」。
费用行是数字或站点用于资源的破折号（`—`）。

认不出的行**收集而非拒绝**（这些文本来自网页和 OCR），进 `unparsed`。

### 5.3 缺卡策略

```python
take = min(entry.copies, available)
if take < entry.copies:
    shortfalls.append(ImportShortfall(entry.display, entry.guid,
                                      int(entry.copies), int(take)))
```

不抛错、不造牌。缺口汇总在 `ImportedDeck.shortfalls`，由聊天命令
`_summarise_import()` 或 inbox 日志报告。

**没有取到任何副本时跳过魔石解析** —— 卡都不在卡组里，解析它的魔石只会制造噪声警告。

### 5.4 重名处理

```python
base = (name or "Imported deck").strip() or "Imported deck"
while final.casefold() in existing:
    final = f"{base} ({suffix})"; suffix += 1
```

比较用 `casefold()`，并先对现有名字做 `strip()`。

### 5.5 卡名歧义解析（`hex_server_adapter.build_name_resolver`）

- **英雄和卡牌不在同一张表**：`card_templates` 完全没有 Champion 类型，
  英雄在 `champion_templates_extended`。
- `card_templates` 中同一卡名按**印次**重复多次。
- 两侧都做 `TRIM(LOWER(...))` —— 部分种子数据带**尾随空格**，
  精确相等会漏匹配（这正是最初 6 个名称解析不出来的原因）。
- 歧义时的确定性规则：**优先玩家已拥有的印次，否则取 guid 升序最小**。
  保证重复导入结果稳定。
- `m_DesignerCardId` **不是**卡牌身份 —— 共享美术 id 会让多张不同卡撞名，
  所以必须按全量记录建索引。

### 5.6 魔石

站点/文本里的宝石名 → `gem_templates.gem_type` → 按槽位打包（10 bit/槽 + 格式位）。
完整格式见 [`GEM-ENCODING.md`](GEM-ENCODING.md)。

解析器有三个，语义不同：

| 解析器 | 输入 | 用途 |
|---|---|---|
| `_resolve_gem_value` | 站点宝石 **GUID** | 分享链接路径，走 `gems.json` 的 `type` 名 |
| `build_gem_name_resolver` | 宝石**显示名** | 文本路径，直接查 `gem_templates.name` |
| `build_name_resolver` | 卡牌/英雄名 | 两条路径共用 |

映射不到时**只记 warning，不作废整个导入**。

两条路径的**入参不同**，但出口一致（都是 `gem_templates.gem_type`）：

- **链接路径**：站点 id → `ids.json` 的 GUID → `gems.json` 的 `type` 名 →
  按 `gem_type_name` 精确匹配 `gem_templates`。
  站点的 `type` 字符串与 `gem_templates.gem_type_name` **完全同名**
  （例：site `3494` → `Blood_Major_1` → `gem_type = 7`）。
- **文本路径**：直接按**显示名**匹配 `gem_templates.name`
  （例：`Minor Wild Orb of Conservation` → `gem_type = 1`）。

所以同一种宝石经两条路径得到**同一个 `gem_type`**；实测中两条路径数值不同只是因为
测试取了不同的宝石（链接用 Major Blood Orb，文本用 Minor Wild Orb），不是映射分歧。

---

## 6. 服务端契约

### 6.1 HConnect 协议 data_type

| dt | 名称 | 本子系统中的作用 |
|---|---|---|
| 2081 | GetPlayerDecks | 客户端拉取卡组列表 |
| 2083 | GetDeckInfo | 收藏界面打开卡组（**ActiveGems 走这里**） |
| 2089 | AddNewDeck | 新建卡组（**解析 ActiveGems**） |
| 2095 | UpdateDeck | 保存卡组（**解析 ActiveGems**） |
| 2127 | OpenCardPack | 开包 |
| 2205 | CardsAdded | 批量加卡 |
| 2207 | InventoryUpdated | 背包刷新 |
| 2210 | EncodedDecks | 登录时推送卡组列表（`ProfileDeckTemplate`） |
| 2211 | ProfileGenericUpdate | 通用 profile 更新分发 |

### 6.2 UID 编码

```
uid64 = (db_id << 8) | type        # Deck = 17, InventoryItem = 11/12
```

### 6.3 两条序列化路径形状不同

| 路径 | 形状 |
|---|---|
| `EncodedDecks`(2210) → `ProfileDeckTemplate` | 每卡 **varint 计数 + N 个 varint 单颗宝石** |
| `GetDeckInfo`(2083) / `UpdateDeck`(2095) → `ActiveGems` | 每卡**一个打包 `EGemTypesNew`(ulong)** |

混淆两者正是魔石功能长期失效的根因，详见 [`GEM-ENCODING.md`](GEM-ENCODING.md)。

### 6.4 修改落点

| 位置 | 说明 |
|---|---|
| `integration/` | **源头**，改这里 |
| `hex-server/integration/` | `apply_integration.sh` 生成的**副本**，改了会被覆盖 |
| `overlay/hex-server/*.py` | **源头**，由 `apply_integration.sh` 整文件拷进 `hex-server/` |
| `hex-server/*.py`（不在 overlay 列表内） | 直接改，如 `pvp_db.py`、`application/profile_stream.py` |

`apply_integration.sh` 覆盖的 8 个文件：
`hconnect_server.py` `deck_inbox.py` `static.py` `db.py` `profile_db.py`
`encoded_decks.py` `commands.py` `ai.py`。

它们全部是**整文件覆盖**，源头在 `overlay/hex-server/`。其中
`hconnect_server.py` 是上游最大的源文件（约 1.1 MB），携带本子系统的三处补丁
（deck-inbox tick、ActiveGems 64 位解析、`GetDeckInfo` 魔石透传）——
因此上游更新该文件时需要手工合并，见 README「`hconnect_server.py` 是整文件覆盖」一节。

---

## 7. 数据文件：`build/codex-data/`

由 `scripts/build_codex_ids.py` 从 Hex Codex 站点内联目录生成：

| 文件 | 结构 | 规模 |
|---|---|---|
| `ids.json` | `{"entries": [[site_id, guid, kind, name], ...]}`，kind ∈ `card`/`champion`/`gem` | 3124 条 |
| `gems.json` | `{"gems": [{"id": ..., "type": "Blood_Major_1"}, ...]}` | 73 条 |

环境变量 `HEX_CODEX_DATA` 指向该目录（也接受 `HEX_CODEX_DATA_PATH`）。
**只有分享链接路径需要它** —— 文本路径完全不需要数据文件。

---

## 8. 聊天命令

`overlay/hex-server/commands.py`：

| 命令 | 说明 |
|---|---|
| `/importdeck <分享链接>` | 需要 `HEX_CODEX_DATA` |
| `/importdecktext <清单>` | 不需要数据文件；聊天是单行，`;` 和 `|` 当换行 |

```
/importdecktext Champion: Ozawa ; 4x Chill ; Reserves: ; 2x Extinction
```

两者都调用 `_refresh_profile(handler)` → `push_profile_stream()`，与剪贴板路径
共享同一条免重登刷新逻辑。

---

## 9. 一键脚本与进程归属

| 脚本 | 作用 |
|---|---|
| `start.bat` / `start.sh` | 安装 + 自检 + 构建 + 启动服务器 |
| `start-game.bat` / `start-game.sh` | 服务器（若未运行）+ 剪贴板助手 + 游戏客户端 |
| `stop-game.bat` / `stop-game.sh` | 停服务器 + 停助手；`--all` 额外关游戏 |

`local-env.sh`（未跟踪）提供本机路径：

```sh
HEX_CLIENT_DIR="/d/game/HEX SHARDS OF FATE"
HEX_CODEX_DATA="/d/game/HEX-Private-Server/build/codex-data"
HEX_DECK_USER="123"
HEX_DECK_WATCH=1        # 1 = 同时拉起剪贴板助手
HEX_ORIGINAL_AI=1
```

### 为什么关掉启动窗口不会停掉进程

`start-game.sh` 刻意把服务**脱离控制台**：

- 服务：`setsid nohup ... < /dev/null &`
- 游戏：`cmd.exe /c start "" Hex.exe`

因此关掉那个控制台窗口不会影响它们 —— 这是为了让游戏能独立于启动器存活。
停服务要用 `stop-game.bat`。

`start-game.bat` / `stop-game.bat` 里的 `bash` 查找是**按路径探测**的，
不能用 `where bash`：标准 Git for Windows 安装只在 PATH 放 `Git\cmd`，
而 `bash.exe` 在 `Git\bin`，所以 `where bash` 必定失败。探测顺序为

```
%GIT_BASH% → %ProgramFiles%\Git\bin → %ProgramFiles(x86)%\Git\bin
→ %LOCALAPPDATA%\Programs\Git\bin → where bash → 从 where git 推导
```

> 注意：从 PowerShell 里直接调 `bash -lc "... start-game.sh"` 时，
> 启动器输出会出现 `echo: write error: Bad file descriptor`，
> 但**服务实际已经正常起来**。这是 stdout 管道被 detach 影响的结果，不是故障。

---

## 10. 踩坑清单

| 现象 | 根因 | 处理 |
|---|---|---|
| 导入成功但客户端看不到卡组 | 助手直写数据库，绕过服务器推送 | 改为 deck-inbox，由服务器消费 |
| 卡组导入后在收藏界面不出现，重登才有 | 推送目标用了 `player_handlers`（仅锦标赛写入） | 改用 `_active_clients`（登录时写入） |
| 卡组保存后消失 / 报 deck #1 | 传 `conn` 时不 commit | 调用方显式 `_db.commit()` |
| 保存卡组直接崩 | `db_update_deck` 签名缺 `conn` | 补 `conn=None` |
| 助手在跑但日志一片空白 | 输出被重定向到文件时 Python 默认全缓冲，日志滞留在缓冲区 | 以 `python -u` 启动（`start-game.sh` 已带 `-u`） |
| 助手起了两个，同一份卡组入队两次 | pidfile 在跨 shell 时不可靠 | `msvcrt.locking` 独占锁（锁定 byte 0） |
| 剪贴板读取访问违例 | ctypes 未声明 `restype`，64 位句柄被截断 | 显式声明 `c_void_p` |
| 6 个卡名/英雄名解析不出来 | 种子数据带尾随空格 + 按 `m_DesignerCardId` 误判卡牌身份 | 两侧 `TRIM`；按全量记录建索引（`m_DesignerCardId` 是美术 id） |
| 魔石不显示 | 见 [`GEM-ENCODING.md`](GEM-ENCODING.md) | 三处修复 |
| 停了服务但端口仍被占 | Git Bash 无 `pkill`/`setsid`/`fuser` | `%USERPROFILE%\bin` 放兼容脚本，见 [`WINDOWS-SETUP.md`](WINDOWS-SETUP.md) |
| `stop-game` 杀不掉助手 | 只匹配 `python.exe`，实际可能是 `python3.exe` | 同时匹配两个名字 |

---

## 11. 验证方法

### 单元测试

```bash
python -m unittest tests.test_deck_import tests.test_deck_text -v
```

覆盖：分享链接端到端、缺卡报告不报错、命名去重、文本解析、宝石名映射、
未映射宝石只告警、两槽位打包。

### 手动验证链路

1. 起服务与助手：`start-game.bat`（`HEX_DECK_WATCH=1`）
2. 打开游戏 → 进入卡牌收藏
3. 复制一份 Hex Codex 分享链接
4. 在游戏窗口内按 **Ctrl+V**
5. 助手日志（`build/deck-watch.log`）应出现
   `queued a share link (...) as <file>.json`
6. 服务器日志应出现
   `deck inbox: imported '<name>' as deck #<id> (N cards) and pushed to the client`
7. 收藏界面立刻出现该卡组

失败时按序检查：

- 助手在跑吗？（`build/deck-watch.lock` 是否被占用、日志末行）
- `Hex.exe` 真的是前台窗口吗？
- 内容门控是否拦掉了？（链接能被 `find_code` 认出来吗）
- inbox 目录里是否堆积了未消费的 `.json`？（主循环是否在 tick）
- 日志里是 `pushed to the client` 还是 `not connected`？（后者说明 `_active_clients` 没命中）
