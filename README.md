# HEX Private Server

一个把 HEX 私服服务器、原客户端 AI、Deck Import 和 Frost Ring Arena 兼容逻辑整合起来的项目。

> 核心原则：hex-server 是唯一的规则与游戏状态权威。Dingler 只作为参考/集成来源，不运行第二套服务器规则。

## 🚀 最简单的安装与测试

Windows 用户推荐直接使用 Git Bash。

需要：

- Git
- Python 3
- .NET 10 SDK

### 1. 下载

    git clone --recurse-submodules https://github.com/Mark007007/HEX-Private-Server.git
    cd HEX-Private-Server

### 2. 初始化

    bash scripts/pull_upstreams.sh
    bash scripts/apply_integration.sh

### 3. 测试

    python -m unittest discover -s tests -v
    dotnet build legacy-ai-worker/LegacyAiWorker.csproj -c Release --nologo

看到测试通过、C# 构建成功，就说明项目的基础安装已经正确。

> 注意：这一步不需要先准备 Records。Records 只在真正启动完整 HEX 游戏服务器、进行真实对局时才需要。

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

## 🧪 推荐流程

    ① clone
       ↓
    ② pull_upstreams.sh
       ↓
    ③ apply_integration.sh
       ↓
    ④ Python tests
       ↓
    ⑤ C# build
       ↓
    ⑥ AI health / probe
       ↓
    ⑦ 准备自己的 Records
       ↓
    ⑧ 启动 hex-server
       ↓
    ⑨ 测试真实对局

前 6 步都不需要完整 Records 数据。

## 📁 项目结构

    HEX-Private-Server/
    ├── hex-server/                 # 唯一权威服务器 / RulesPort / 游戏状态
    ├── upstream/
    │   ├── Dingler-FrostRingArena/ # Dingler 参考实现
    │   ├── INTEGRATION_STATUS.md
    │   └── UPSTREAM.lock
    ├── integration/
    │   ├── deck_import/            # Hex Codex v1
    │   └── ai_bridge/              # 原版 AI JSONL 桥接
    ├── overlay/hex-server/         # 应用到 hex-server 的集成修改
    ├── legacy-ai-worker/           # 原版 Game.Shared.AI Headless Worker
    ├── client-runtime/             # HEX 客户端 managed DLL
    ├── tests/                      # 集成与回归测试
    └── scripts/                    # 安装 / 同步 / Records 工具

## 🔗 固定上游版本

| 组件 | Repository | 固定版本 |
|---|---|---|
| 权威服务器 | IanUtley/hex-server | main @ c65f2cf7e78797fb6d9da9a3401345da7cead71d |
| Arena / 原版 AI 参考 | RomoSJR/Dingler-FrostRingArena | arena @ 8c06748080ab3fd15d67a6b2f7193615ffd2db02 |

## 功能概览

### Deck Import

支持 Dingler 兼容的 Hex Codex v1，包括 deck code 解码、CRC 校验、主牌组 / Reserve、命名 section 和明确的验证错误。

Import 使用玩家现有 card_instances，不会因为导入 deck 而无条件伪造卡牌。

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
