
# HEX Private Server

> **目标：恢复原版 HEX 客户端可识别的服务器端，而不是重新发明一个“类似 HEX”的游戏。**
>
> 本分支采用 **C# / .NET 10 + 原客户端最大化复用 + HConnect 协议兼容 + Headless 规则核心 + 可选 RabbitMQ**。
>
> 核心策略：
>
> **原客户端是协议规范、原版 Shared Mechanics 是规则 Oracle；现代 .NET 服务器负责把这些能力从 Unity 客户端环境中解放出来。**

---

# 1. 当前分支定位

本分支：

~~~text
dotnet-rewrite
~~~

是一次重新设计，不沿用旧项目的“Python 服务端 + Dingler overlay + 原版 AI Worker + 多个辅助进程”作为新的权威核心。

旧实现仍可作为历史参考，但新服务器的目标是：

~~~text
Original HEX Client
        │
        ▼
   HConnect Protocol
        │
        ▼
.NET 10 HexServer
        │
        ├── Transport
        ├── Protocol
        ├── Services
        ├── Session
        ├── Transactions
        ├── Game Rules
        ├── Profile
        ├── SQLite
        └── Replay
~~~

RabbitMQ、原版 AI、Arena、Deck Import 等都降级为**可插拔能力**，不是第一阶段的硬依赖。

---

# 2. 为什么不再从头重写 HEX

本次对完整：

~~~text
Managed.zip
├── Assembly-CSharp.dll
└── Assembly-CSharp-firstpass.dll
~~~

进行静态反编译、CLI 元数据、IL 与依赖调查后，发现一个关键事实：

> **HEX 客户端并不只是 UI。**

其中存在大量 *Game.Shared.* 代码，而且有一部分正是服务器最需要的：

~~~text
Game.Shared.Mechanics
Game.Shared.Session
Game.Shared.AuthoritativeSessionBase
Game.Shared.Mechanics.Transactions
Game.Shared.Network.HConnect
Game.Shared.Network.DataWrapper
Game.Shared.Network.EncData
Game.Shared.Network.ObjFmt
SessionEventArgs
~~~

因此最有效的方法不是：

~~~text
DLL
 ↓
理解
 ↓
全部重写
~~~

而是：

~~~text
DLL
 ↓
识别可复用部分
 ↓
原样 / 最小改造复用
 ↓
剥离 Unity / Client-only 依赖
 ↓
现代 .NET 10 Server
~~~

---

# 3. 逆向调查的方法

本项目的逆向不是“看字符串猜协议”，而采用证据链。

## 3.1 PE / CLI 元数据

首先解析：

~~~text
PE
 └── CLR Header
      └── Metadata Root
           ├── #~ / #-
           ├── #Strings
           ├── #Blob
           ├── #GUID
           └── #US
~~~

重点读取：

~~~text
TypeDef
Field
MethodDef
Param
TypeRef
MemberRef
AssemblyRef
MethodSpec
~~~

从而恢复：

~~~text
Namespace
Type
Field
Method
Generic instantiation
Assembly dependency
~~~

## 3.2 IL 调用图

对于关键类型：

~~~text
Game.Shared.Mechanics
Game.Shared.Session
Game.Shared.AuthoritativeSessionBase
HConnect
EncData
ObjFmt
~~~

读取方法体 IL，并跟踪：

~~~text
call
callvirt
newobj
ldfld
stfld
ldsfld
stsfld
MethodSpec
MemberRef
~~~

目的是区分：

~~~text
纯规则
   VS
客户端逻辑
   VS
网络层
   VS
Unity 依赖
~~~

## 3.3 依赖分层

每个类型最终标记为：

| 等级 | 含义 |
|---|---|
| A — DIRECT REUSE | 可以在服务器直接保留 |
| B — COMPATIBILITY REUSE | 逻辑本身可用，只需要把旧客户端输入/运行时换掉 |
| C — REIMPLEMENT | 协议/服务器基础设施需要现代化重写 |
| D — CLIENT ONLY | UI、渲染、输入等直接排除 |

---

# 4. 证据等级

所有协议结论按照以下等级理解：

| 等级 | 含义 |
|---|---|
| **CONFIRMED** | 从 DLL 元数据 / IL / 常量直接确认 |
| **HIGH CONFIDENCE** | 多处代码相互印证，但尚未动态抓包 |
| **RECONSTRUCTED** | 根据多个方法行为恢复出的算法，仍应做 byte-for-byte 验证 |
| **UNVERIFIED** | 尚无足够证据，不允许写死到正式协议 |

特别原则：

> **“看起来合理”绝不等于“HEX 协议就是这样”。**

---

# 5. HConnect：目前已经确认的真实外层协议

## 5.1 标识

**CONFIRMED**

HConnect 使用：

~~~text
~HCP~
~~~

长度：

~~~text
5 bytes
~~~

不是：

~~~text
0x4858
~~~

也不是之前假设的 6-byte Magic + Length + CmdId。

## 5.2 UInt32 编码

**CONFIRMED**

HConnect 的 UInt32 解析：

~~~text
b0 << 24
b1 << 16
b2 << 8
b3
~~~

所以：

> **Big Endian**

## 5.3 HCP 外层 Frame

准确布局：

~~~text
┌──────────────────────────────┐
│ "~HCP~"                 5 B  │
├──────────────────────────────┤
│ contentSize             4 B  │  uint32 BE
├──────────────────────────────┤
│ headerSize               4 B  │  uint32 BE
├──────────────────────────────┤
│ header                  N B  │
├──────────────────────────────┤
│ bodySize                4 B  │  uint32 BE
├──────────────────────────────┤
│ body                    M B  │
└──────────────────────────────┘
~~~

其中：

~~~text
contentSize = 4 + N + 4 + M
            = 8 + N + M
~~~

总长度：

~~~text
5 + 4 + contentSize
= 17 + N + M
~~~

这个布局来自：

~~~text
Game.Shared.Network.HConnect.Proto.MakeMessagePacket
Game.Shared.Network.HConnect.Proto.HasMessage
~~~

不是猜测。

---

# 6. HConnect 的 TCP 粘包 / 半包行为

**CONFIRMED**

客户端并不假设一次 Read 就能得到完整消息。

它维护：

~~~text
_IncommingData
~~~

然后重复调用：

~~~text
Proto.HasMessage(...)
~~~

直到：

~~~text
NeedMore == false
~~~

所以服务器实现必须支持：

~~~text
一个 TCP read
    → 半个 frame

一个 TCP read
    → 一个 frame

一个 TCP read
    → 多个 frame
~~~

.NET 10 实现建议：

~~~text
Socket
 ↓
NetworkStream / Socket
 ↓
System.IO.Pipelines
 ↓
HcpFrameCodec
~~~

---

# 7. HConnect Message：Header 与 Body 是不同层

## 7.1 Header

**CONFIRMED**

*Game.Shared.Network.HConnect.Message* 保存：

~~~text
Headers : Dictionary<string, object>
Body    : byte[]
BOffset : int
BLen    : int
~~~

Header 会被：

~~~text
UTF-8
 ↓
JSON Reader
 ↓
Dictionary<string, object>
~~~

读取。

所以：

> **HCP Header 是 UTF-8 JSON。**

## 7.2 Body

Body 不等于 JSON。

*Message.BodyJsonDeserialize<T>()* 虽然存在，但更底层的服务器对象编码路径会进入：

~~~text
EncData
  ↓
ObjFmt / Encoder
~~~

因此必须把：

~~~text
HCP JSON header
~~~

和：

~~~text
HEX custom object body
~~~

严格区分。

---

# 8. HConnect Session：可靠有序通道

客户端 *Game.Shared.Network.HConnect.Session* 存在：

~~~text
_version
_tags
_sessionId

CCnt
SCnt

_Outgoing
_Received
_bufferedSent

_resend
_lastReq
_IncommingData
~~~

说明 HConnect 并不只是裸 TCP。

## 8.1 出站 Header

**CONFIRMED**

发送消息时会加入：

~~~text
ccnt
scnt
time
sid
version
tags
~~~

其中：

~~~text
ccnt = Client message counter
scnt = Server message counter
sid  = HConnect session id
~~~

并通过 Interlocked 读取 / 增加计数器。

## 8.2 入站顺序检查

**CONFIRMED**

客户端要求：

~~~text
expected scnt = local SCnt + 1
~~~

并记录：

~~~text
SCnt
CCnt
~~~

发现丢包 / 乱序时会进入：

~~~text
resend
~~~

逻辑。

## 8.3 重传

**CONFIRMED**

内部存在：

~~~text
_tracked
_buffered_messages
_resend
_lastReq
~~~

并有：

~~~text
target = "rsnd"
instance = req
~~~

相关路径。

所以新服务器必须保留：

~~~text
ordered delivery
+
duplicate suppression
+
missing sequence detection
+
resend
~~~

---

# 9. HConnect Session 建立

## 9.1 Client → Server

**CONFIRMED**

客户端创建 session 时发送：

~~~text
target = "newsession"
~~~

## 9.2 Server → Client

客户端建立成功时等待：

~~~text
issuer = "Session"
target = "create"
sid = <session id>
~~~

成功后：

~~~text
_sessionId = sid
~~~

并进入：

~~~text
CONNECTED
~~~

---

# 10. 默认本地端口

**HIGH CONFIDENCE**

客户端 Connector 的 fallback/default 路径中发现：

~~~text
port = 0x26cd
~~~

即：

~~~text
9933
~~~

注意：

> 这证明**当前客户端默认/回退配置**使用 9933，不能单凭这个事实断言历史线上服务器永远只使用 9933。

因此本地服务器可以默认监听：

~~~text
127.0.0.1:9933
~~~

但必须允许配置覆盖。

---

# 11. DataWrapper

类型：

~~~text
Game.Shared.Network.DataWrapper
~~~

关键字段：

~~~text
RequestId
DataType
Bytes
BytesList
RequestHandlerSessionId
Comp
Tags
FromUid
FromId
PP
ConH
JsonReq
JsonRes
~~~

核心方法：

~~~text
ToBytes<T>
FromBytes<T>
FromBytes
~~~

---

# 12. DataWrapper → EncData

**CONFIRMED**

*ToBytes<T>* 的调用链：

~~~text
DataWrapper.ToBytes<T>
        ↓
EncData.Encode<T>
        ↓
EncodeCustom / Encoder
~~~

不是简单：

~~~text
JsonConvert.SerializeObject
~~~

直接作为最终 HEX Body。

---

# 13. EncData：自定义编码入口

类型：

~~~text
Game.Shared.Network.EncData
~~~

有：

~~~text
Encode(object, ...)
Encode<T>(...)
Decode(...)
Decode<T>(...)
EncodeCustom(...)
DecodeCustom(...)
DecodeCustomInto(...)
DescribeTypes(...)
QuerySerializableMembers(...)
CheckProp(...)
~~~

## 13.1 useCUSTOM

**CONFIRMED**

*EncData.Encode/Decode* 首先读取：

~~~text
GPG.Core.TDF.useCUSTOM
~~~

当 custom 模式打开时，进入：

~~~text
EncodeCustom
DecodeCustom
~~~

否则不支持的编码方式会抛出：

~~~text
Encode object isn't supported by this encode type
Decode object isn't supported by this encode type
~~~

因此：

> 本项目必须优先实现 HEX 的 **CUSTOM ObjFmt 路径**，而不是自作主张替换成 JSON。

---

# 14. ObjFmt：类型系统

已确认的类型判断器：

~~~text
IsCollection
IsBool
IsString
IsNumber
IsDateTime
IsEnum
IsUserStruct
~~~

说明编码器是：

~~~text
reflection-driven
type-directed
recursive
~~~

结构，而不是固定 DTO layout。

---

# 15. Encoder：类型表与长度表

Encoder 字段：

~~~text
_types : List<string>
_sizes : List<long>
_sep   : byte[]
~~~

## 15.1 separator

**CONFIRMED**

静态构造函数把：

~~~text
_sep = UTF8(";")
~~~

因此：

~~~text
separator = ';'
~~~

## 15.2 Type Table

**CONFIRMED**

*WriteTypeTable* 顺序写：

~~~text
_types[0]
;
_types[1]
;
_types[2]
...
~~~

最终通过 UTF-8 Writer 写入。

## 15.3 Size Table

**CONFIRMED**

*WriteSizeTable* 遍历：

~~~text
_sizes
~~~

元素之间写：

~~~text
;
~~~

说明对象编码最终包含：

~~~text
type table
+
size table
~~~

辅助数据。

---

# 16. Encoder：对象的基本结构

从 *Encoder.Encode* IL 可以确认至少存在以下顺序：

~~~text
[optional type/name prefix]
;
[size reference]
;
[type index]
;
[member payload ...]
[object size patched later]
~~~

更具体地：

1. 记录当前 Stream.Position。
2. 写入某个名字/类型前缀。
3. 写 separator。
4. 调用 allocSizeRef()。
5. 写入 size placeholder。
6. 写 separator。
7. 调用 FindType(...)。
8. 写入 type index。
9. 写 separator。
10. 查询 Serializable Members。
11. 对每个属性/字段递归 Encode。
12. 记录结束位置。
13. 调用 setSizeRef(index, end - start) 回填对象大小。
14. 顶层编码追加 TypeTable。
15. 写 LF。
16. 追加 SizeTable。

这里要区分：

> **对象 size table 的索引行为已经从 IL 确认，但完整 wire 中 TypeTable / SizeTable 的整体边界仍应通过 byte-for-byte 测试封存。**

因此这一段标记为：

**RECONSTRUCTED**

---

# 17. Encoder：基础类型

## Bool

**CONFIRMED**

布尔值直接写：

~~~text
"1"
~~~

或：

~~~text
"0"
~~~

之后：

~~~text
;
~~~

## Guid

**CONFIRMED**

Guid 使用：

~~~text
ToString()
~~~

随后编码长度 + separator + Guid 文本。

## String

**CONFIRMED**

首先：

~~~text
Encoding.UTF8.GetByteCount(string)
~~~

然后：

~~~text
byte length
;
UTF8 bytes
~~~

## Number

**CONFIRMED**

调用：

~~~text
ObjFmt.EncodeNumber(value, type)
~~~

---

# 18. ObjFmt 数字编码

这是目前已经可以写成明确规范的一段。

*ObjFmt.EncodeNumber* 对：

~~~text
Byte
SByte
Int16
Int32
Int64
UInt16
UInt32
UInt64
Single
Double
Decimal
~~~

执行对应的：

~~~text
BitConverter.GetBytes(...)
~~~

然后调用：

~~~text
ToHex(...)
~~~

因此：

> **数字 = primitive bytes + hex text。**

对于当前 Windows 客户端：

~~~text
BitConverter
   ↓
little-endian primitive bytes
   ↓
hex text
~~~

例如概念上：

~~~text
Int32(1)
 ↓
01 00 00 00
 ↓
"01000000"
~~~

这不是把数字简单转成十进制字符串。

---

# 19. Decimal

**CONFIRMED**

Decimal：

~~~text
Decimal.GetBits()
~~~

得到 4 个 Int32。

每个 Int32：

~~~text
BitConverter.GetBytes
~~~

拼成：

~~~text
16 bytes
~~~

最后：

~~~text
ToHex
~~~

所以：

~~~text
Decimal = 16-byte primitive representation → hex text
~~~

---

# 20. Byte Array

Encoder 存在：

~~~text
WriteByteArray(Stream, byte[], long len)
~~~

行为：

1. 如果 len > actual length，则截到实际长度。
2. 写入 length。
3. 写入原始 bytes。

同时存在：

~~~text
WriteHexByteArray
~~~

又提供：

~~~text
byte → x2 hex text
~~~

路径。

所以：

> **不能简单假设所有 byte[] 都使用一种 wire representation。**

真正编码形式由：

~~~text
ObjFmt
+
Encoder.Encode
+
field type
~~~

共同决定。

---

# 21. Collections / Arrays

已确认：

~~~text
Collection / Array
    ↓
count
;
element #0
element #1
element #2
...
~~~

集合元素会取得运行时类型，再递归调用 Encode。

*byte[]* 则走专门分支。

---

# 22. Enum

**CONFIRMED**

Enum 不直接写底层整数。

Encoder 使用：

~~~text
Enum.GetName(...)
~~~

因此形式是：

~~~text
Enum Name
;
~~~

所以服务端不要看到 enum 就强制写整数。

---

# 23. DateTime

**CONFIRMED**

使用：

~~~text
ToString(InvariantCulture)
~~~

然后：

~~~text
byte length
;
datetime string
~~~

---

# 24. Decoder

Decoder 存在：

~~~text
_types
_sizes
_byte_c
~~~

以及：

~~~text
ReadTypeTable
ReadSizeTable
ReadToChar
ReadToSeperator
Decode<T>
Decode(...)
~~~

其行为与 Encoder 对称。

类型恢复涵盖：

~~~text
Bool
String
Number
DateTime
Enum
Collection
Array
KeyValuePair
UserStruct
~~~

对于集合：

~~~text
count
↓
repeat Decode(element)
~~~

对于 KeyValuePair：

~~~text
Key
Value
~~~

分别寻找对应字段。

---

# 25. DataWrapper 的 compression

*DataWrapper.FromBytes* 明确区分：

~~~text
compression == 0
compression == 1
~~~

并存在：

~~~text
Unknown compression type
~~~

异常路径。

因此 *Comp* 不是装饰性字段。

但当前还不足以单凭这一层把：

~~~text
0 = 某算法
1 = 某算法
~~~

写成最终规范。

状态：

**HIGH CONFIDENCE / 待动态验证**

---

# 26. Request / Response 的真实层次

HEX 网络对象应理解为：

~~~text
HCP Frame
    │
    ├── JSON Headers
    │
    └── Binary Body
            │
            ▼
        DataWrapper
            │
            ├── RequestId
            ├── DataType
            ├── Comp
            ├── Tags
            ├── FromUid
            └── payload bytes
                    │
                    ▼
                 EncData
                    │
                    ▼
                  ObjFmt
                    │
                    ▼
             Request / Response
~~~

---

# 27. Service ID

目前从服务初始化代码恢复出：

| Service | ID |
|---|---:|
| Tournaments | 242 / 0xF2 |
| Monitor | 243 / 0xF3 |
| Profile | 245 / 0xF5 |
| GameSession | 246 / 0xF6 |
| Matchmaking | 247 / 0xF7 |
| AI | 248 / 0xF8 |
| Escrow | 249 / 0xF9 |
| GM | 251 / 0xFB |
| Mail | 252 / 0xFC |
| Campaign | 253 / 0xFD |
| LoadBalancer | 254 / 0xFE |

这些属于：

**CONFIRMED / HIGH CONFIDENCE**

---

# 28. GameSession 方法 ID

从 *GameSessionService.Initialize()* 恢复：

| ID | Method |
|---:|---|
| 3003 | TryReconnectionToDisconnectedGame |
| 3005 | StartSession |
| 3007 | StartEncounter |
| 3009 | FindReconnectionInformation |
| 3011 | FindSession |
| 3013 | JoinDisconnectedGame |
| 3015 | JoinSession |
| 3019 | ReadyForGameSetup |
| 3025 | LeaveSession |
| 3027 | EndSession |
| 3031 | GetSessionList |
| 3047 | FindSessionById |
| 3049 | SessionResync |
| 3050 | PlayerAdded |
| 3051 | PlayerRemoved |
| 3052 | GameContinue |
| 3053 | GameStarted |
| 3054 | GameEnded |
| 3055 | SessionSyncEvent |
| 3056 | ChampionStatsUpdated |

同时存在对应的：

~~~text
RequestArgs
ResponseArgs
~~~

例如：

~~~text
StartSessionRequestArgs
StartSessionResponseArgs

JoinSessionRequestArgs
JoinSessionResponseArgs

PlayerTransactionRequestArgs
PlayerTransactionResponseArgs

ReadyForGameSetupRequestArgs
ReadyForGameSetupResponseArgs
~~~

因此可以自动生成新的 *HexServer.Contracts*，而不需要人工猜 DTO。

---

# 29. Game.Shared.Mechanics：最大的资产

目前已分析：

~~~text
Game.Shared.Mechanics
≈ 261 个直接命名空间类型

Game.Shared.Mechanics.*
≈ 706 个类型
~~~

重要类型族：

~~~text
Card
CardData
CardTemplate
Ability
AbilityInstance
Effect
EffectInstance
GenericEffect
Target
Requirement
Modifier
Filter
Chain
Combat
Cost
Trigger
...
~~~

更重要：

静态 IL 调用扫描中，这些 Mechanics 类型没有发现直接：

~~~text
UnityEngine.*
Game.Client.*
~~~

调用。

因此：

# **DIRECT REUSE 是最高优先级**

---

# 30. Game.Shared.Session

这一层已经存在大量真正游戏操作：

~~~text
CanPlayCard
CanPlayResourceCard
PayCardCost
PayAbilityCost

CreateAbility
PushAbilityOnChain
FinishAbilityOnChain
ActivateAbility
ActivateTriggeredAbility
ResolveTopOfChain

DrawCard
DestroyCard
GraveyardCard
DiscardCard
MoveCard
TapCard
UntapCard
ReadyCard

PlayResource
PlayPermanent
CastSpell
PlaySpell
PlayChampion

Attack
Combat
Priority
Turn
~~~

静态检查同样没有发现这部分直接调用：

~~~text
UnityEngine.*
Game.Client.*
~~~

因此 *Game.Shared.Session* 很可能本来就是共享游戏状态 / 规则运行层。

---

# 31. AuthoritativeSessionBase

这是整个逆向结果中最重要的类型之一：

~~~text
Game.Shared.AuthoritativeSessionBase
~~~

关键方法：

~~~text
StartGame
InitializeGame
InitEncounter
LoadDeckForPlayers
InstantiateDeck

Mulligan
PlayerDrawStartingHand
DrawCard

CastSpell
ActivateAbility
ActivateTriggeredAbilities
ActivateAutomaticAbilities

HandleTriggeredAbilities
HandleTransaction
HandleGameEvent

PushGameAction
DetectStateBasedTriggers
RecalculateSessionEffects

DeclareAttack
AdvanceToNextTurnState

EndGame
CheckForVictory
~~~

关键字段：

~~~text
m_ActionStack
m_NextSessionCardId
m_NextAbilityInstanceId

m_RandomNumberGeneratorW
m_RandomNumberGeneratorZ

m_EventQueue
m_PendingTriggers
m_HandledTriggers

m_CurrentPriorityPlayer
~~~

这基本就是：

~~~text
权威状态机
+
交易 / 事件驱动
+
确定性随机
~~~

的服务器级核心。

---

# 32. AuthoritativeSessionBase 的最小旧版依赖

进一步做 IL 外部调用扫描后：

## 没有发现：

~~~text
UnityEngine.*
~~~

直接调用。

这是极大的利好。

## 但是存在少量：

~~~text
Game.Client.Network.Profile.GetDeckDetailsResponse
Game.Client.Network.Campaign.GetArenaBattleModsResponse
~~~

主要集中在：

~~~text
OnReceiveDeckDetailsResponse
<ApplyEncounterStartModifications>m__1
~~~

因此：

> **不要重写整个 AuthoritativeSessionBase。**

应该切成：

~~~text
AuthoritativeSessionBase
       │
       ├── Game Rules        ← 原版尽量保留
       │
       └── external data     ← Server Adapter
~~~

---

# 33. Server Adapter

建议：

~~~csharp
public interface IServerProfileProvider
{
    DeckData GetDeck(...);
    ChampionData GetChampion(...);
}
~~~

以及：

~~~csharp
public interface IEncounterModificationProvider
{
    IReadOnlyList<EncounterModification> GetModifications(...);
}
~~~

运行期间：

~~~text
Legacy Client Response
        ↓
Legacy Adapter

Local SQLite/Profile
        ↓
Server Provider
~~~

最终删除：

~~~text
Game.Client.Network.*
~~~

依赖。

---

# 34. Transactions：不重新发明

已发现：

~~~text
Game.Shared.Mechanics.Transactions
~~~

有：

~~~text
Transaction
SubmitTransaction

PlayTroopTransaction
PlaySpellTransaction
PlayChampionTransaction
PlayResourceTransaction

ActivateAbilityTransaction
PassPriorityTransaction
DiscardTransaction
MulliganTransaction

CommitTroopsToAttackTransaction
CommitTroopsToDefenseTransaction
AssignDamageOrderTransaction

ReadyCardTransaction
QuitGameTransaction
...
~~~

*Transaction* 自身有：

~~~text
Initialize
Require
Validate
Resolve
GetSerializedBytes
~~~

所以服务器处理流程应该是：

~~~text
Client Request
      ↓
Decode
      ↓
Create Transaction
      ↓
Initialize
      ↓
Require / Validate
      ↓
Resolve
      ↓
GameState changed
      ↓
SessionEvent emitted
~~~

而不是另造第二套命令体系。

---

# 35. SessionEvent：继续复用原版

存在：

~~~text
CardDrawnSessionEventArgs
CardMovedSessionEventArgs
CardDestroyedSessionEventArgs
CardDiscardedSessionEventArgs

CardTappedSessionEventArgs
CardUntappedSessionEventArgs

TroopCardPlayedSessionEventArgs
SpellCardPlayedSessionEventArgs
ResourceCardPlayedSessionEventArgs
ChampionCardPlayedSessionEventArgs

AttackDeclaredSessionEventArgs
BlockersAssignedSessionEventArgs

BeginCombatResolutionSessionEventArgs
EndCombatResolutionSessionEventArgs
CombatPhaseResolvedSessionEventArgs

TurnPhaseUpdatedSessionEventArgs
PlayerCurrentResourcePoolChangedSessionEventArgs
PlayerTotalResourcePoolChangedSessionEventArgs

AbilityActivationDataRequiredSessionEventArgs
AbilityCancelledSessionEventArgs
AbilityPushedOnChainSessionEventArgs
TriggeredAbilityActivationDataRequiredSessionEventArgs
~~~

并存在：

~~~text
ToByteArray
BeginWrite
EndWrite
BeginRead
EndRead
~~~

以及：

~~~text
NetworkPacketSessionEventArgs
~~~

因此：

> **服务器 Event 尽量产生原版 Event，而不是新发 JSON Event。**

---

# 36. 最佳服务器结构

~~~text
HEX-Private-Server/
│
├── src/
│   ├── HexServer.Server/
│   │
│   ├── HexServer.Protocol/
│   │   ├── HConnect/
│   │   ├── ObjFmt/
│   │   ├── EncData/
│   │   ├── DataWrapper/
│   │   └── Services/
│   │
│   ├── HexServer.Contracts/
│   │   ├── Requests/
│   │   ├── Responses/
│   │   ├── Events/
│   │   ├── Services/
│   │   └── Enums/
│   │
│   ├── HexServer.Game/
│   │   ├── Shared/
│   │   ├── Session/
│   │   ├── Mechanics/
│   │   ├── Transactions/
│   │   ├── Abilities/
│   │   └── Effects/
│   │
│   ├── HexServer.Profile/
│   │
│   └── HexServer.Storage/
│
├── legacy/
│   └── HexLegacyCompat/
│
├── tools/
│   ├── HexExtractor/
│   ├── HexProbe/
│   └── HexReplay/
│
├── data/
├── captures/
├── logs/
│
├── HexServer.sln
└── start.bat
~~~

---

# 37. 三层复用模型

## A — 原样复用

优先目标：

~~~text
Game.Shared.Mechanics
Game.Shared.Session
Transactions
SessionEvents
Card / Ability / Effect
~~~

## B — 兼容复用

目标：

~~~text
AuthoritativeSessionBase
HConnect logical behavior
ObjFmt
EncData
DataWrapper
~~~

做：

~~~text
minimum compatibility shim
~~~

## C — 现代化重写

目标：

~~~text
Socket transport
Pipelines
Service host
Session scheduler
SQLite persistence
configuration
logging
replay
~~~

---

# 38. 为什么采用 Modular Monolith

第一版不要拆：

~~~text
Gateway.exe
Logic.exe
Profile.exe
Battle.exe
RabbitMQ
Redis
...
~~~

而是：

~~~text
HexServer.exe
~~~

内部：

~~~text
Transport
Protocol
Services
Session
Game
Profile
Storage
~~~

这样：

~~~text
1 个进程
1 个权威状态
1 个调试入口
1 个启动脚本
~~~

更适合 Windows 本地私服、双人测试和持续逆向。

---

# 39. RabbitMQ 的最终位置

保留：

~~~csharp
public interface IMessageBus
{
    Task PublishAsync<T>(string topic, T message);
}
~~~

实现：

~~~text
InMemoryMessageBus
RabbitMqMessageBus
~~~

默认：

~~~text
InMemory
~~~

RabbitMQ 只用于真正需要：

~~~text
异步工作
日志
统计
后台任务
AI worker
邮件
批处理
~~~

玩家关键游戏动作默认：

~~~text
Client
 ↓
Session
 ↓
Transaction
 ↓
State
 ↓
Event
 ↓
Client
~~~

不经过 MQ。

---

# 40. Session 并发模型

每局：

~~~text
Session
   ↓
Channel<GameCommand>
   ↓
single ordered consumer
   ↓
Transaction
~~~

严格：

~~~text
Transaction #1
Transaction #2
Transaction #3
Transaction #4
~~~

顺序执行。

这非常适合：

~~~text
priority
stack
combat
trigger
~~~

---

# 41. Deterministic Replay

原版 *AuthoritativeSessionBase* 已存在：

~~~text
m_RandomNumberGeneratorW
m_RandomNumberGeneratorZ
~~~

因此新服务器应把：

~~~text
Match Seed
~~~

作为正式状态的一部分。

保存：

~~~text
match/
├── seed
├── transactions
└── events
~~~

同一：

~~~text
Seed
+
Transaction sequence
~~~

应该产生相同结果。

这提供：

~~~text
Bug reproduce
Replay
Differential testing
Desync detection
~~~

---

# 42. Legacy Oracle：最大化利用原客户端

开发期间可以同时运行：

~~~text
Original Shared Logic
        ↓
       Oracle

New Server
        ↓
       Result
~~~

对同一 Transaction 比较：

~~~text
State
Events
Cards
Resources
Priority
Stack
~~~

即：

~~~text
Original State
      VS
New State
~~~

第一次不一致的位置就是：

~~~text
First Divergence
~~~

这比手工打一万局游戏更可靠。

---

# 43. 为什么这比直接“复制 DLL”更好

不要：

~~~text
HexServer
   ↓
Assembly-CSharp.dll
   ↓
Unity dependency
   ↓
Mono dependency
   ↓
越来越大
~~~

而是：

~~~text
Original DLL
    ↓
dependency analysis
    ↓
minimal reuse set
    ↓
compatibility adapter
    ↓
modern server
~~~

随着服务器成熟：

~~~text
Legacy dependency
      ↓
逐步减少
~~~

最终目标：

~~~text
正式服务器
   ↓
.NET 10
   ↓
不依赖 Unity
   ↓
不依赖完整原客户端 DLL
~~~

但保留：

~~~text
Original Oracle
~~~

作为长期回归基准。

---

# 44. HexExtractor 应该做什么

输入：

~~~text
Assembly-CSharp.dll
Assembly-CSharp-firstpass.dll
~~~

输出：

~~~text
artifacts/re/
├── types.json
├── fields.json
├── methods.json
├── services.json
├── request-response.json
├── events.json
├── transactions.json
├── dependencies.json
└── callgraph.json
~~~

---

# 45. 请求 / 响应自动生成

例如检测到：

~~~text
JoinSessionRequestArgs
JoinSessionResponseArgs
~~~

就生成：

~~~text
HexServer.Contracts
    ├── JoinSessionRequest.cs
    └── JoinSessionResponse.cs
~~~

Service：

~~~text
GameSession
ID = 246
~~~

Method：

~~~text
JoinSession
ID = 3015
~~~

形成：

~~~text
Service 246
Method 3015
Request JoinSessionRequestArgs
Response JoinSessionResponseArgs
~~~

---

# 46. 不允许猜协议

禁止：

~~~text
❌ 假设 Magic = 0x4858
❌ 假设 6-byte header
❌ 假设 packet cmd 是 ushort
❌ 假设 body = JSON
❌ 假设 RabbitMQ 是玩家协议
❌ 假设一定是 AES
❌ 假设一定是 RSA
❌ 假设一定是 SmartFox
~~~

除非：

~~~text
IL
+
metadata
+
动态行为
~~~

有足够证据支持。

---

# 47. 加密 / 认证当前状态

目前确认：

~~~text
Auth
Authenticator
AuthenticationSDK
HexAuthPS4
auth:req
~~~

以及：

~~~text
action
user
pass
token
region
lang
mac
platform
~~~

但目前静态证据不足以证明：

~~~text
HCP body = AES / RSA / DH ...
~~~

因此：

~~~text
IHandshake
IAuthenticator
IEncryptor
~~~

全部保留接口。

但不先制造假的加密协议。

---

# 48. Card Data 的边界

Managed.zip 中已经有：

~~~text
Card
CardData
CardTemplate
~~~

等运行时类型。

但是不能因此声称：

> 所有完整卡牌数据都已经包含在 DLL。

真正的卡牌定义可能来自：

~~~text
Data/gamedata
Resources
AssetBundle
Records
外部配置
~~~

所以：

~~~text
代码 = DLL
数据 = GameData / Records / Assets
~~~

必须分开调查。

---

# 49. 第一阶段明确不实现

~~~text
❌ Dingler 作为权威规则引擎
❌ Frost Ring Arena
❌ Python AI 作为核心
❌ RabbitMQ 强制依赖
❌ 多进程微服务
❌ 商城
❌ 拍卖
❌ 邮件
❌ Tournament
❌ 全部 PvE
❌ 全部卡牌 UI
❌ 重写所有 Mechanics
❌ 猜加密
❌ 猜包头
~~~

---

# 50. 真正开发路线

## P0 — 逆向资料库

~~~text
Managed.zip
 ↓
HexExtractor
 ↓
Type / Field / Method / Service / Dependency Catalog
~~~

## P1 — HConnect

实现：

~~~text
~HCP~
uint32 BE
headerSize
JSON header
bodySize
body
~~~

以及：

~~~text
partial frame
multiple frames
sequence
resend
heartbeat
~~~

## P2 — DataWrapper / EncData / ObjFmt

实现：

~~~text
DataWrapper
 ↓
EncData
 ↓
ObjFmt
~~~

并用原客户端产生的结果做：

~~~text
byte-for-byte
~~~

对照。

## P3 — Service Router

实现：

~~~text
Service UID
Method ID
Request
Response
~~~

首批只做：

~~~text
GameSession
Profile
~~~

## P4 — Session

实现：

~~~text
newsession
create
sid
StartSession
FindSession
JoinSession
ReadyForGameSetup
ReadyForGameEvents
ReadyToStartGame
~~~

## P5 — 原版规则核心

优先接入：

~~~text
Game.Shared.Mechanics
Game.Shared.Session
AuthoritativeSessionBase
Transactions
SessionEvents
~~~

只剥离：

~~~text
Game.Client.*
Unity.*
~~~

依赖。

## P6 — 两客户端对战

目标：

~~~text
Client A
    ↕
.NET Server
    ↕
Client B
~~~

实现：

~~~text
Game Start
Mulligan
Draw
Resource
Play Card
Attack
Block
Damage
Priority
Turn End
Game Over
~~~

## P7 — Replay / Oracle

加入：

~~~text
seed
transaction log
event log
state checksum
differential comparison
~~~

## P8 — 完整 Profile / Card Data

继续：

~~~text
cards
decks
champions
inventory
records
~~~

---

# 51. 最终产品形态

Windows 目标：

~~~text
HEX-Private-Server/
├── HexServer.exe
├── appsettings.json
├── data/
└── logs/
~~~

双击：

~~~text
start.bat
~~~

即可。

默认开发模式：

~~~text
SQLite
+
InMemory MessageBus
+
HConnect localhost
~~~

RabbitMQ：

~~~text
optional
~~~

---

# 52. 当前逆向结论总表

| 项目 | 当前状态 |
|---|---|
| HCP magic | **CONFIRMED** — ~HCP~ |
| Frame length | **CONFIRMED** — uint32 |
| Endianness | **CONFIRMED** — Big Endian |
| Header layout | **CONFIRMED** |
| Header format | **CONFIRMED** — UTF-8 JSON |
| Body | **CONFIRMED** — byte[] |
| Session ccnt/scnt | **CONFIRMED** |
| Resend | **CONFIRMED** |
| Session create | **CONFIRMED** |
| Default local port | **HIGH CONFIDENCE** — 9933 |
| DataWrapper | **CONFIRMED** |
| EncData custom path | **CONFIRMED** |
| ObjFmt separator | **CONFIRMED** — ; |
| Number encoding | **CONFIRMED** |
| Type table | **CONFIRMED** |
| Size table | **CONFIRMED** |
| Recursive object layout | **RECONSTRUCTED** |
| Compression 0/1 meaning | **UNVERIFIED** |
| Service IDs | **CONFIRMED / HIGH CONFIDENCE** |
| GameSession method IDs | **CONFIRMED** |
| Mechanics Unity dependency | **未发现直接 Unity 调用** |
| Session Unity dependency | **未发现直接 Unity 调用** |
| AuthoritativeSessionBase Unity dependency | **未发现直接 Unity 调用** |
| AuthoritativeSessionBase Client dependency | **少量，已定位** |
| RabbitMQ = player transport | **NOT PROVEN** |
| AES/RSA game packet encryption | **NOT PROVEN** |
| SmartFox/SFS = exact third-party protocol | **NOT PROVEN** |

---

# 53. 最重要的工程结论

本项目真正应该做的是：

~~~text
          ORIGINAL HEX
               │
       ┌───────┴────────┐
       │                │
   Protocol         Shared Rules
       │                │
       ▼                ▼
    HConnect       Mechanics
    ObjFmt         Session
    EncData        Transactions
    DataWrapper    Events
       │                │
       └───────┬────────┘
               ▼
        Compatibility Layer
               │
               ▼
          .NET 10 Server
~~~

也就是：

## **不是仿造 HEX。**

而是：

## **把原 HEX 中已经存在的协议与游戏核心，恢复成一个现代、无 Unity、可独立运行的服务器。**

---

# 54. 下一阶段

优先顺序：

~~~text
1. 完成 HexExtractor
2. 完成 EncData / ObjFmt 的 byte-for-byte encoder
3. 完成 Request / Response 自动 catalog
4. 完成 HConnect reliable channel
5. 把 AuthoritativeSessionBase 的外部依赖替换为 Server Adapter
6. 建立 Legacy Oracle
7. 打通两客户端本地对战
~~~

只有这条链打通之后，才开始扩充：

~~~text
Profile
Deck
Card Data
AI
Arena
RabbitMQ
~~~

---

# 55. 反编译结果的使用原则

本项目的反编译工作不是为了“复制整个客户端”，而是为了恢复最小必要的：

~~~text
Protocol contract
Serialization contract
Game rule contract
Session contract
Transaction contract
Event contract
~~~

最终的服务器实现应该满足：

~~~text
客户端看到的协议
        ==
服务器发送的协议

原版规则输入
        ==
新服务器验证后的规则输入

原版状态演化
        ==
新服务器状态演化
~~~

达到这一条件后，才可以逐步移除 Legacy Compatibility。

---

# Reverse Engineering Source

本 README 的调查基础：

~~~text
Managed.zip
├── Assembly-CSharp.dll
└── Assembly-CSharp-firstpass.dll
~~~

重点类型：

~~~text
Game.Shared.Network.HConnect.Proto
Game.Shared.Network.HConnect.Message
Game.Shared.Network.HConnect.Session
Game.Shared.Network.DataWrapper
Game.Shared.Network.EncData
Game.Shared.Network.ObjFmt
Game.Shared.Network.Encoder
Game.Shared.Network.Decoder

Game.Shared.Mechanics
Game.Shared.Session
Game.Shared.AuthoritativeSessionBase
Game.Shared.Mechanics.Transactions
SessionEventArgs
~~~

> 本文中的“CONFIRMED”来自静态 DLL 元数据 / IL 分析；尚未动态连接原版客户端的部分，会继续通过 HexProbe 与 byte-for-byte capture 验证。
