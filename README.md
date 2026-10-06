# HEX Private Server

一个把 HEX 私服服务器、原客户端 AI、Deck Import 和 Frost Ring Arena 兼容逻辑整合起来的项目。

> **目标：尽量做到拿下来就能跑。**
>
> Windows 用户优先：**双击 `start.bat`**。
> 
> Git Bash / Linux / macOS：执行 **`bash start.sh`**。

核心原则：**`hex-server/` 是唯一的规则与游戏状态权威。Dingler 只作为参考/集成来源，不运行第二套服务器规则。**

> **首次安装** → `start.bat`　**日常开服** → `start-game.bat`　**关服** → `stop-game.bat`

## 📚 文档索引

| 文档 | 内容 |
|---|---|
| [`docs/WINDOWS-SETUP.md`](docs/WINDOWS-SETUP.md) | Windows 原生环境搭建、5 个必踩的坑（`python3` 存根 / 缺 `setsid`·`pkill` / MSYS 路径 / Worker 相对路径 / Records 缓存） |
| [`docs/DECK-IMPORT.md`](docs/DECK-IMPORT.md) | 卡组导入子系统：HConnect 协议契约、剪贴板触发层、跨进程 inbox 通道、导入器设计、踩坑清单 |
| [`docs/GEM-ENCODING.md`](docs/GEM-ENCODING.md) | 魔石 `EGemTypesNew` 打包格式（bit 62 格式位 / 每槽 10 bit）、两条序列化路径的差异、修复与验证 |


---

## 🚀 一键安装 / 自检 / 启动

### Windows

安装好 Git for Windows、Python 3 和 .NET 10 SDK 后：

**直接双击：**

```text
start.bat
```

脚本会自动：

```text
同步固定版本的 upstream
        ↓
应用 integration overlay
        ↓
创建 Python 虚拟环境
        ↓
安装服务器依赖
        ↓
运行全部 integration tests
        ↓
构建 Original-AI Worker
        ↓
加载客户端 AI DLL
        ↓
执行 AI health + session probe
        ↓
检测 Records
        ↓
有 Records → 自动启动服务器
没有 Records → 明确提示缺少 Data/gamedata
```

### Git Bash / Linux / macOS

```bash
bash start.sh
```

不需要手工执行一串测试命令。

---

## 🕹 日常开服 / 关服

自检通过后，日常只需要两个脚本。

### 启动

Windows 双击，或 Git Bash 里执行：

```text
start-game.bat          # 或 bash scripts/start-game.sh
```

它会依次完成：

```text
服务器未运行则启动（restart.sh）
        ↓
等待 9933 / 8081 端口真正监听
        ↓
按 HEX_DECK_WATCH 启动剪贴板卡组助手
        ↓
启动 Hex.exe
        ↓
打印端口、客户端 config.ini 实际指向、日志位置
```

### 停止

```text
stop-game.bat           # 或 bash scripts/stop-game.sh
stop-game.bat --all     # 额外关掉游戏客户端
```

> ⚠️ **直接关掉启动器的控制台窗口不会停掉任何服务。**
>
> 这是刻意设计：服务用 `setsid nohup` 脱离控制台、游戏用 `cmd /c start`
> 拉起，目的是让游戏独立于启动器存活。要停服务请用 `stop-game`。

### 本机路径配置：`local-env.sh`

`start-game.sh` 会 source 仓库根目录下的 `local-env.sh`（未跟踪），
所有机器相关的路径都放这里，脚本本身不含任何硬编码路径。

> **这个文件必须手工创建**（仓库里没有、也不该有）。少了它 `HEX_CLIENT_DIR`
> 为空，`start-game.sh` 会直接报错退出；`HEX_CODEX_DATA` 未设时分享链接导入
> 会失败（牌表文本导入不受影响）。

```sh
HEX_CLIENT_DIR="/d/game/HEX SHARDS OF FATE"     # 游戏安装目录（POSIX 形式）
HEX_CODEX_DATA="/d/game/HEX-Private-Server/build/codex-data"
HEX_DECK_USER="123"          # 卡组导入写进哪个玩家（名字或 id）
HEX_DECK_WATCH=1             # 1 = 同时拉起剪贴板助手
HEX_ORIGINAL_AI=1            # 1 = 原版客户端 AI
```

也可以全部用环境变量临时覆盖，例如：

```bash
HEX_SKIP_CLIENT=1 bash scripts/start-game.sh    # 只起服务，不启动游戏
```

---

## 🎮 真正开服只剩一个外部数据依赖

项目可以自己完成安装、构建和 AI 自检。

真正运行 HEX 对局时，服务器还需要**你自己的 HEX 客户端安装里的**：

```text
Data/gamedata
```

因此完整开服命令只多一个参数：

```bash
HEX_GAMEDATA="/你的HEX安装目录/Data/gamedata" bash start.sh
```

脚本会自动：

1. 提取 Records；
2. 添加服务器需要的 Records v1 header；
3. 检查 15 个 Records section；
4. 通过后自动启动 `hex-server`。

也就是说，**不需要你先手工跑 Records 提取、测试、build、restart。**

## 🤖 测试原版 AI

项目已经包含原版 HEX 客户端运行时 DLL，位于 client-runtime/。

构建完成后运行：

    export HEX_CLIENT_DLL="$PWD/client-runtime/Assembly-CSharp-firstpass.dll"
    dotnet legacy-ai-worker/bin/Release/net10.0/LegacyAiWorker.dll

输入：

    {"protocol":1,"request_id":"1","action":"health","payload":{}}

正常情况下会得到 ready 状态。

然后可以测试 AI 会话创建：

    {"protocol":1,"request_id":"2","action":"probe","payload":{"session_uid64":10001,"ai_player_uid64":20001,"human_player_uid64":20002,"ai_position":1,"session_name":"HEX AI Probe","session_flags":128}}

这一步成功，说明客户端 AI DLL、Game.Shared.AI 和 Headless ClientSessionBase 基本桥接正常。

## 🎮 真正启动服务器

如果只是确认项目能运行，做到上面的测试就够了。

要真正开始 HEX 对局，还需要你自己的 HEX 安装中的：

    Data/gamedata

### 生成 Records

执行：

    HEX_GAMEDATA="/你的HEX安装目录/Data/gamedata" bash scripts/prepare_client_records.sh

脚本会自动完成 Records 提取、header 添加和完整性检查。

生成位置：

    hex-server/Records/

完整服务器需要以下 15 个 section：

    AbilityEffectConditionTemplate.jsonl
    AbilityEffectTemplate.jsonl
    AbilityTargetTemplate.jsonl
    AbilityTemplate.jsonl
    CardCounterTemplate.jsonl
    CardTemplate.jsonl
    ChampionClassData.jsonl
    ChampionTalentData.jsonl
    ChampionTemplate.jsonl
    ConversationTemplate.jsonl
    DeckTemplate.jsonl
    EncounterDeck.jsonl
    InventoryItemData.jsonl
    QuestTemplate.jsonl
    SceneData.jsonl

这些数据来自客户端安装，不应在没有相关权利的情况下重新发布。

### 启动

    cd hex-server
    bash restart.sh

服务器详细配置见 hex-server/HOWTO.md。

## 🃏 在游戏里按 Ctrl+V 导入卡组

客户端是固定二进制，**卡牌收藏界面没有粘贴处理器**，所以触发器放在进程外：
一个剪贴板助手监听 `Ctrl+V`，把内容交给正在运行的服务器导入，再由服务器
**推送 profile stream** —— 因此卡组是**当场出现**，不需要重新登录。

### 用法

1. `start-game.bat`（确保 `HEX_DECK_WATCH=1`）—— 服务器 + 助手 + 游戏一起起来
2. 进入游戏 → 打开**卡牌收藏**
3. 复制一份 Hex Codex 分享链接，或复制页面上的牌表文本
4. **在游戏窗口内按 Ctrl+V**
5. 卡组立刻出现在收藏列表里

助手日志：`build/deck-watch.log`；服务器日志：`/tmp/hconnect_log.txt`。

### 支持的两种输入

| 输入 | 说明 |
|---|---|
| Hex Codex **v1 分享链接** | 需要 `HEX_CODEX_DATA` 指向 `build/codex-data`（**已随仓库提供**，见下方「Hex Codex 目录数据」） |
| **牌表文本** | 站点渲染的多行格式和 `4x 卡名` 单行格式都支持，**不需要任何数据文件** |

### Hex Codex 目录数据

`build/codex-data/` 已随仓库提交，所以**全新 clone 无需联网即可导入分享链接**：

| 文件 | 内容 |
|---|---|
| `ids.json` | 3124 条站点 id → 游戏 GUID 映射（`card` / `champion` / `gem` 三类，id 区间互不重叠） |
| `gems.json` | 73 条宝石「类型名 → 显示名」映射 |

只有需要**刷新**（站点新增卡牌）时才重新生成；它需要本机已建库，并访问 Hex Codex 卡组构建页：

```bash
python scripts/build_codex_ids.py --cards-html <卡组构建页URL或本地保存的.html> --player 123
# 默认输出到 build/codex-data；--records / --db 可覆盖默认路径
```

站点名到 GUID 的解析按**名称**匹配本地数据库（三类均 100% 命中）。卡名按印次重复，
因此解析用的是基于 Records 去重后的目录：每个 `m_DesignerCardId` 一行（`DELETE*` 条目丢弃），
无 designer id 的行按 (name, cost, type, subtype) 合并；仍有多个候选时优先玩家已拥有的印次，
否则取 guid 最小 —— 保证重复运行结果稳定。

> 与 `hex-server/Records/` 同性质：数据来自客户端/站点，**再分发以其适用授权条款为准**。

### 设计取舍

- **缺卡不报错**：能导入多少导多少，缺口在日志和聊天命令里明确报告；**绝不伪造卡牌**
- **卡名歧义确定性选择**：同名卡按印次重复，优先玩家已拥有的印次，否则取 guid 最小
- **卡组重名自动加后缀**：`FRA Hieroplants 4000 (2)`
- **同内容 30 秒去重**：避免「按了没反应就再按一次」生成一叠重复卡组
- **只在 `Hex.exe` 是前台窗口时响应**，不干扰其他程序

> 技术细节（协议契约、跨进程通道、踩坑清单）见
> [`docs/DECK-IMPORT.md`](docs/DECK-IMPORT.md)。

### 也可以不走剪贴板

游戏内聊天命令（同一套导入器、同样免重登刷新）：

```text
/importdeck <Hex Codex 分享链接>
/importdecktext Champion: Ozawa ; 4x Chill ; Reserves: ; 2x Extinction
```

## 🧪 全新 clone 流程（已在干净 clone 中实测）

```bash
# 1. 拉取（含 submodule，固定到 UPSTREAM.lock 的 commit）
git clone --recurse-submodules https://github.com/Mark007007/HEX-Private-Server.git
cd HEX-Private-Server

# 2. 固定上游 + 应用 overlay（8 个整文件 + integration 包）
bash scripts/pull_upstreams.sh
bash scripts/apply_integration.sh

# 3. 测试（28 项）
python -m unittest discover -s tests

# 4. 构建原版 AI Worker（需 .NET 10 SDK）
dotnet build legacy-ai-worker/LegacyAiWorker.csproj -c Release --nologo

# 5. 建库：从自己的客户端提取 Records（唯一的外部数据依赖）
HEX_GAMEDATA="<客户端>/Data/gamedata" bash scripts/prepare_client_records.sh

# 6. 创建 local-env.sh —— 未跟踪，必须手工创建（内容见上节）
#    HEX_CLIENT_DIR / HEX_CODEX_DATA / HEX_DECK_USER / HEX_DECK_WATCH=1

# 7. 开服（服务器 + 剪贴板助手 + 游戏）
start-game.bat
```

Hex Codex 目录数据（`build/codex-data`）**已随仓库提交**，因此第 6 步之后即可直接导入
分享链接，**不需要联网、不需要额外生成步骤**。

### 已验证 / 未验证

**已在干净 clone（含 submodule）中实测通过：**

| 检查项 | 结果 |
|---|---|
| `apply_integration.sh` | 拷入 8 个 overlay 文件；`deck_inbox.py`、`integration/deck_import/text_deck.py` 从无到有 |
| `hconnect_server.py` 打补丁前后 | `_process_deck_inbox` 出现（定义+调用）；魔石解析的 `& 0xFFFFFFFF` 掩码 **2 处 → 0 处** |
| `python -m unittest discover -s tests` | 28 tests OK |
| 牌表文本 → deck-inbox → 服务器消费 | 卡组入库；`active_gems` 含 bit 62；两槽位各解出一颗宝石 |
| Hex Codex v1 分享链接 → 同一通道 | 同上（合成链接走真实 codec + CRC 校验） |
| 出站 `EncodedDecks` 载荷 | 每卡发出的是**单颗**宝石枚举值，不是打包整数 |

> 上游 `hconnect_server.py` **本身就带那个 32 位截断 bug**（2 处），所以「打补丁前」的
> 对照是在干净 clone 上直接观察到的，不是推测。

**仍需在游戏里手动确认**（无法脚本化）：

- 收藏界面按 **Ctrl+V** 的真实热键路径（需要真人按键 + `Hex.exe` 为前台窗口）
- 魔石在游戏内卡牌上的显示与对局中生效
- Original AI 全链路（见文末「已知未自动化验证的部分」）

### 一键复跑这套检查

```bash
python scripts/validate_deck_import.py
```

脚本会先读 `local-env.sh`，所以默认就用启动器那套玩家/目录/数据库配置，通常无需任何参数。
优先级：`--player` > 环境变量 > `local-env.sh`（文件里的 Git Bash 形式路径
`/d/game/...` 会自动转成原生路径）。

它验证的是**部署后的 `hex-server/` 树**（也就是 clone + `apply_integration.sh` 的结果），
因此能抓到测试套件抓不到的那类问题：**改了 `integration/` 或 `overlay/` 却忘了重新 apply**
—— 单元测试照样全绿，而运行中的服务器还是旧代码。

1. overlay 是否真的应用（`deck_inbox.py` 在不在、上游那个 32 位截断掩码是否已消失）
2. 牌表文本与 Hex Codex 分享链接**是否都能经 `hex-server/deck-inbox/` 真正入库**
   —— 就是 Ctrl+V 助手走的那条通道
3. 出站 profile 载荷里的宝石**是否为单颗枚举值**，而不是打包整数

**它不碰你的真库**：`hex-server/hconnect.db` 经 SQLite backup API 复制到临时文件，
`HEX_DB_PATH` 指向副本，跑完删除；`HEX_DECK_INBOX` 同样重定向到临时目录。
退出码 `0` = 全部通过，`1` = 有失败项（CI 可用）。

## 📁 项目结构

    HEX-Private-Server/
    ├── hex-server/                 # 唯一权威服务器 / RulesPort / 游戏状态
    │   ├── deck_inbox.py           # 外部导入请求的消费端（见 docs/DECK-IMPORT.md）
    │   └── deck-inbox/             # 剪贴板助手投递的 JSON 请求（运行时生成）
    ├── upstream/
    │   ├── Dingler-FrostRingArena/ # Dingler 参考实现
    │   ├── INTEGRATION_STATUS.md
    │   └── UPSTREAM.lock
    ├── integration/
    │   ├── deck_import/            # 卡组导入：v1 codec / 文本状态机 / 装配器
    │   └── ai_bridge/              # 原版 AI JSONL 桥接
    ├── overlay/hex-server/         # 整文件覆盖进 hex-server/ 的 8 个 .py（源头）
    │   ├── hconnect_server.py      #   含 deck-inbox tick / 魔石 64 位解析补丁
    │   └── deck_inbox.py           #   卡组导入的跨进程通道
    ├── legacy-ai-worker/           # 原版 Game.Shared.AI Headless Worker
    ├── client-runtime/             # HEX 客户端 managed DLL
    ├── tests/                      # 集成与回归测试
    ├── scripts/                    # 安装 / 同步 / Records / 启动 / 助手 / 验证
    ├── docs/                       # 技术细节记录
    │   ├── WINDOWS-SETUP.md        # Windows 原生环境搭建与排障
    │   ├── DECK-IMPORT.md          # 卡组导入子系统（协议/通道/踩坑）
    │   └── GEM-ENCODING.md         # 魔石 EGemTypesNew 编码格式
    ├── build/                      # 除 codex-data 外均为运行时产物（已 gitignore）
    │   ├── codex-data/             # ids.json(3124) + gems.json(73) —— 已随仓库提交
    │   └── deck-watch.log / .lock  # 剪贴板助手日志与单实例锁（忽略）
    ├── local-env.sh                # 本机路径（未跟踪）
    ├── start.bat / start.sh        # 安装 + 自检 + 构建
    ├── start-game.bat / .sh        # 服务器 + 助手 + 游戏
    └── stop-game.bat / .sh         # 停服务（--all 连游戏一起关）

### `integration/` 与 `overlay/` 是源头，不要改副本

| 路径 | 角色 |
|---|---|
| `integration/` | **源头** |
| `hex-server/integration/` | `apply_integration.sh` 生成的**副本**，改了会被覆盖 |
| `overlay/hex-server/*.py` | **源头**，由 `apply_integration.sh` 拷进 `hex-server/`（8 个文件） |

改完源头记得跑一次 `bash scripts/apply_integration.sh`。

### ⚠️ `hconnect_server.py` 是整文件覆盖，上游更新需手工合并

`overlay/hex-server/` 里的 8 个文件是**整文件覆盖**，其中
`hconnect_server.py` 是上游最大的源文件（约 1.1 MB），携带本项目三处关键补丁：

| 补丁 | 作用 |
|---|---|
| `_process_deck_inbox()` + 主循环空闲 tick | 消费剪贴板助手投递的卡组导入请求 |
| ActiveGems 64 位解析（去掉 `& 0xFFFFFFFF`） | 保住 `EGemTypesNew` 的 bit 62 格式位与第 4/5/6 槽 |
| `GetDeckInfo` 魔石整值透传 | 收藏界面正确显示多槽位魔石 |

**代价**：上游改动 `hconnect_server.py` 时，`git submodule update` 拉下来的新版本
会被 overlay 覆盖。合并上游更新的流程是

```bash
git -C hex-server fetch && git -C hex-server diff HEAD..origin/main -- hconnect_server.py
# 手工把上游改动并入 overlay/hex-server/hconnect_server.py，再：
bash scripts/apply_integration.sh
```

（`deck_inbox.py` 是本项目新增文件，不存在上游冲突问题。）

## 🔗 固定上游版本

| 组件 | Repository | 固定版本 |
|---|---|---|
| 权威服务器 | IanUtley/hex-server | main @ c65f2cf7e78797fb6d9da9a3401345da7cead71d |
| Arena / 原版 AI 参考 | RomoSJR/Dingler-FrostRingArena | arena @ 8c06748080ab3fd15d67a6b2f7193615ffd2db02 |

## 功能概览

### Deck Import

三种触发方式共用同一个导入器，行为完全一致：

| 触发 | 入口 |
|---|---|
| 收藏界面按 **Ctrl+V** | `scripts/deck_clipboard_watch.py` → `hex-server/deck-inbox/` → 服务器消费 |
| 聊天 `/importdeck` | `overlay/hex-server/commands.py` |
| 聊天 `/importdecktext` | 同上（纯文本，不需要数据文件） |

支持 Dingler 兼容的 Hex Codex v1，包括 deck code 解码、CRC 校验、主牌组 / Reserve、命名 section 和明确的验证错误。

Import 使用玩家现有 card_instances，不会因为导入 deck 而无条件伪造卡牌；缺卡按 `min(需求, 已有)` 取用并把缺口报告出来。

### 免重登刷新

外部助手**不直接写数据库** —— 那会让运行中的客户端一直显示旧的卡组列表，直到重新登录。
助手改为投递请求，由服务器进程消费并调用 `push_profile_stream()`，
收藏界面**当场**刷新。详见 [`docs/DECK-IMPORT.md`](docs/DECK-IMPORT.md)。

### 魔石（Gems）

客户端 `EGemTypesNew` 是**按槽位打包的 ulong 位域**：bit 62 是格式标志，
每槽 10 bit。卡组的「列表路径」（`EncodedDecks`）和「字典路径」
（`GetDeckInfo` / `UpdateDeck`）序列化形状**不同**，混淆两者会让魔石全部消失。

格式的权威定义、三处修复与验证方式见 [`docs/GEM-ENCODING.md`](docs/GEM-ENCODING.md)。

### Reserve

Reserve 独立保存在 decks.reserves，并在客户端 profile 编码时保留真实 Reserve 标记。

### 原版 AI

原版 AI 的数据流：

    Game.Shared.AI
        ↓
    decision intent
        ↓
    RulesTransaction
        ↓
    hex-server RulesPort
        ↓
    authoritative state

Worker 只产生意图，最终规则验证仍由 hex-server 完成。

### AI 安全机制

- 15 秒 AI stall timeout
- 最多 3 次 resync
- 重复 transaction 抑制
- 每个 phase key 最多 5000 次动作的 livelock 检测
- 原版 AI 故障时回退到 Python AI

### Arena 兼容

Dingler 的 Arena 源码只用于 live event routing、AI session hosting、resync / livelock protection、Deck Import 和 Frost Ring Arena 行为参考。

不会把 Dingler 的 server engine 当成第二个权威规则引擎。

## GitHub Actions

CI 会自动检查：

- 固定 upstream commit
- integration tests 和 Python syntax
- Records 完整性以及可用时的 server startup / regression
- Windows 下 Original AI Worker 构建
- 7 个客户端 DLL
- Original AI runtime load 和 AI session probe
- Dingler reference build

因此本地不需要一开始就把所有运行环境全部配齐。

## 当前架构

    HEX Private Server
           │
           ▼
    hex-server RulesPort
           │
      ┌────┴────┐
      │         │
     玩家      AI
               │
        Original AI Worker
               │
          typed intent
               │
               ▼
        RulesPort validation
               │
               ▼
       authoritative state

## Client DLLs

client-runtime/ 当前包含：

- Assembly-CSharp-firstpass.dll
- ICSharpCode.SharpZipLib.dll
- NCalc.dll
- SampleClassLibrary.dll
- System.EnterpriseServices.dll
- System.Web.Services.dll
- UnityEngine.dll

这些 DLL 是客户端运行时输入。是否允许重新分发，应以适用的 HEX / Unity / 客户端授权条款为准；项目本身的 AGPL 许可不会自动授予第三方客户端二进制的再分发权。

## 一句话

先执行下面 4 行即可完成基础安装和自检：

    git clone --recurse-submodules https://github.com/Mark007007/HEX-Private-Server.git
    cd HEX-Private-Server
    bash scripts/pull_upstreams.sh && bash scripts/apply_integration.sh
    python -m unittest discover -s tests -v

测试通过后再执行：

    dotnet build legacy-ai-worker/LegacyAiWorker.csproj -c Release --nologo

只有要跑真实 HEX 对局时，才继续做 Data/gamedata → Records → hex-server 启动。

之后日常就一条：

    start-game.bat          # 服务器 + 剪贴板助手 + 游戏；停用 stop-game.bat

---

## ⚠️ 已知未自动化验证的部分

- **Original AI 全链路**：Worker 是惰性启动的，只在第一场带 AI 的对局走到
  `ai.py` 的 `try_native_original_ai()` 时才 spawn。需要在游戏里实操验证
  （建议从 Practice/PvE 开始，不要一上来就跑完整 Frost Ring Arena）。
- **`tests_combat.py` 有 3 个既有失败**（37 PASS / 3 FAIL）：
  `GameStarted chain auto-pass`、`Speed troop attacks same turn`、
  `Deck-search prompt target id`。原因是测试替身缺少 `user_profile` /
  `client_reck_id` 属性，属测试脚手架问题，与卡组/魔石无关。
