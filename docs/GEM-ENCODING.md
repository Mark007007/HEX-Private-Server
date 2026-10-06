# 魔石（Gem）编码格式与导入修复记录

本文记录「导入的套牌在客户端不显示魔石」这一问题的排查结论与修复。
核心结论来自**客户端反编译源码**，不是推测。

---

## 1. 权威格式

反编译产物（由本机 `Assembly-CSharp-firstpass.dll` 反编译得到）：

- `%LOCALAPPDATA%\Temp\hexdecomp_fp\Assembly-CSharp-firstpass.decompiled.cs`

### 1.1 `EGemTypesNew`（`Game.Shared.Mechanics`，行 197243）

```csharp
public enum EGemTypesNew : ulong     // 注意：不是 [Flags]，底层类型是 ulong
{
    Gem1 = 1023uL,                   // 0x3FF
    Gem2 = 1047552uL,                // 0x3FF << 10
    Gem3 = 1072693248uL,             // 0x3FF << 20
    Gem4 = 1098437885952uL,          // 0x3FF << 30
    Gem5 = 1124800395214848uL,       // 0x3FF << 40
    Gem6 = 1151795604700004352uL,    // 0x3FF << 50
    Unknown = 4611686018427387904uL, // 0x4000000000000000
    GemFormatBit = Unknown,
    Wild_Minor_1 = 1uL,              // … 顺序枚举到 73
    Doombringer_Gem_Wild_Minor_2 = 73uL,
}
```

要点：

- 真正的宝石编号是 **1..73**，与服务端 `gem_templates.gem_type` **一一对应**。
- 多槽位是**打包值**：每槽 10 bit，槽 N 右移 `10*(N-1)`。
- **bit 62（`GemFormatBit`）是「打包形式」标志位**。客户端的
  `GemHelper.AddGemToGem` 第一步就是 `gems |= Unknown`，所以客户端存盘的
  任何带宝石的卡**都会置上 bit 62**。

### 1.2 两条完全不同的序列化路径

| 路径 | 类型 | 形状 |
|---|---|---|
| 登录卡组列表（`EncodedDecks` dt=2210） | `ProfileDeckTemplate.CardDescriptor.Gems`，类型 `List<ulong>` | **varint 计数 + N 个 varint 单颗宝石值** |
| 卡组编辑器（`GetDeckInfo` / `UpdateDeck`） | `deck_bits.ActiveGems`，类型 `Dictionary<ulong, EGemTypesNew>` | **每卡一个打包 `ulong`** |

`ProfileDeckTemplate.ToBytes()`（行 237287-237327）：

```csharp
binaryWriter.Write(card.Temp.guid.ToByteArray());
writeVarInt(binaryWriter, (uint)card.Count);
binaryWriter.Write(card.Res);
binaryWriter.Write(card.Ext);
binaryWriter.Write(card.Foil);
writeVarInt(binaryWriter, (uint)card.Gems.Count);   // 计数
foreach (ulong gem in card.Gems)                    // 逐颗
    writeVarInt(binaryWriter, gem);
```

客户端从模板重建 `ActiveGems`（行 237110），这行是理解打包规则的钥匙：

```csharp
deck_bits2.ActiveGems[num2] = (EGemTypesNew)item.Gems.Aggregate(
    4611686018427387904uL,                                        // 种子 = GemFormatBit
    (ulong t, ulong x) => (ulong)GemHelper.AddGemToGem((EGemTypesNew)x, (EGemTypesNew)t));
```

即：`packed = GemFormatBit | Σ(gem_i << 10*i)`。

---

## 2. 三个 Bug

### Bug 1：解包时把 64 位截断成 32 位

`hex-server/hconnect_server.py`，两处（`UpdateDeck` dt=2095 与 `AddNewDeck` dt=2089）：

```python
# 修复前
v_val = struct.unpack("<Q", unhexlify(seg[17]))[0] & 0xFFFFFFFF
```

`& 0xFFFFFFFF` 直接丢掉 **bit 32 以上的全部内容**，其中包括
**bit 62 格式标志**和**第 4/5/6 槽**。

实测证据：用户手动镶 2 颗魔石（55 + 63）保存后，客户端实际发出
`0x400000000000FC37`，被截断成 `64567`，格式标志丢失 →
客户端用 `IsNewGemFormat()` 判定为**旧格式** → 按旧位域解码 → 魔石消失。

**修复**：去掉掩码。

### Bug 2：服务端把打包值当成单颗宝石发回去

`overlay/hex-server/encoded_decks.py` 的 `load_gems()` 把
`{"5726": 4611686018427452471}` 原样转成 `[4611686018427452471]`，
于是 `ProfileDeckTemplate` 写出：

```
计数=1, 值=4611686018427452471
```

而客户端拿到的是 `List<ulong>`，会把它当成**一颗编号为 4611686018427452471 的宝石**
（合法枚举只有 1..73）→ 全部丢弃。

**修复**：`load_gems()` 按槽位**解包**成单颗宝石列表：

```python
if packed & GEM_FORMAT_BIT:
    gems = [(packed >> (10 * slot)) & 0x3FF for slot in range(6)]
    gems = [g for g in gems if g > 0]
else:
    gems = [packed] if packed else []
```

### Bug 3：导入器没有置格式标志位

`integration/deck_import/importer.py` 打包时从 0 开始 OR：

```python
# 修复前
packed = 0
```

导入出来的卡组因此**没有 bit 62**，与客户端自己存盘的形状不一致，
客户端一律按旧格式解析。

**修复**：种子改为 `GEM_FORMAT_BIT`。

---

## 3. 一并修正的连带问题

| 位置 | 问题 |
|---|---|
| `hex-server/hconnect_server.py` `GetDeckInfo`(dt=2083) 编码 | 旧代码取 `v_val[0]`（列表时代的写法），会把多槽位压成第 1 颗；现改为整值透传，并保留对历史列表行的兼容 |
| `hex-server/pvp_db.py` `db_card_gem_ability_guids` | 本来就用 `raw & (1<<62)` 判断打包形式，逻辑正确；**因为 Bug 1 丢了标志位才失效**，无需改动 |
| `scripts/stop-game.sh` | 杀助手进程只匹配 `Name='python.exe'`；一键启动用裸 `python` 拉起助手时确实是 `python.exe`，但 `restart.sh` 走的是 `python3`，故改为同时匹配两者 |

---

## 4. 数据迁移

已有卡组需要重新打包（补上 bit 62）。对 `decks.active_gems` 逐行归一化：
列表或旧式单值 → `GemFormatBit | Σ(gem_i << 10*i)`。

迁移结果：

| deck | 迁移前 | 迁移后 |
|---|---|---|
| 3 `新套牌 1` | `{"5726":64567}` | `{"5726":4611686018427452471}` |
| 6 / 16 / 17 `FRA Hieroplants 4000` | `{"6810":33793, …}` | `{"6810":4611686018427421697, …}` |

`4611686018427452471 = 0x400000000000FC37` → 槽1=55、槽2=63 ✔
`4611686018427421697 = 0x4000000000008401` → 槽1=1、槽2=33 ✔

`decks.gem_abilities` **无需重算**：`_resolve_gem_abilities()` 本来就按槽位解包，
迁移前后结果一致。

---

## 5. 验证方式

单元测试（`tests/test_deck_import.py`）：

```bash
python -m unittest tests.test_deck_import -v
```

其中 `test_two_socketed_gems_are_packed_into_one_value` 锁定两槽位打包规则。

端到端校验出站报文：调用 `encode_encoded_decks()` 后按
`ProfileDeckTemplate` 布局解出每张卡的宝石列表，应得到**单颗宝石**而非打包值：

```
'新套牌 1':                gem-groups={(55, 63): [1], (): [...59 张]}
'FRA Hieroplants 4000':    gem-groups={(1, 33): [1, 1, 1, 1], (): [...12 张]}
```

修复前这里会是 `(4611686018427452471,)` 这样的单元素元组。

> 客户端需重启（服务端重启会断开连接）。反编译源码是判断此类格式问题的
> 唯一权威依据，遇到「服务端数据正确但客户端不认」时优先查它。
