# Windows 原生环境搭建与排障指南

本项目可以在 Windows 上**不依赖 WSL 或 Docker** 运行，但 Git for Windows 与上游
`hex-server`（一个按 Linux 开发环境编写的服务端）之间存在几处必须处理的差异。
本文记录这些差异的根因与修复方式。

适用环境：Windows 11 + Git for Windows（含 Git Bash）+ Python 3.12 + .NET 10 SDK。

---

## 1. 前置要求

| 组件 | 要求 | 说明 |
|---|---|---|
| Git for Windows | 需包含 Git Bash | `start.bat` 依赖 `bash` |
| Python | 3.12.x | 需在 Git Bash 中可解析为 `python3`（见坑 2） |
| .NET SDK | **必须是 10.x** | 见下方说明 |
| HEX 客户端 | 自备 | 提供 `Data/gamedata` 与 7 个托管 DLL |

### 为什么必须用 .NET 10 SDK

`legacy-ai-worker` 的 TargetFramework 是 `net10.0`，并且 `bin/Release/net10.0/`
这个输出目录被两处跨语言硬编码引用：

- `scripts/apply_integration.sh` —— 把客户端 DLL stage 进
  `upstream/Dingler-FrostRingArena/Dingler.Terminal/bin/Release/net10.0/`
- `integration/ai_bridge/live.py` —— Worker 的默认启动命令是
  `dotnet legacy-ai-worker/bin/Release/net10.0/LegacyAiWorker.dll`

用 .NET 8/9 SDK 编译会产出 `net8.0/`，上述路径全部落空，表现为 Worker 的
`health` 动作返回 `"status": "blocked"`，而错误信息不会直接指向版本问题。

### 客户端需要提供什么

- `Data/gamedata` —— 一个 gzip 文件（通常 6–7 MB），**注意它是文件而不是目录**，
  `HEX_GAMEDATA` 应指向该文件本身
- `Hex_Data/Managed/` 下的 7 个托管 DLL —— 建议直接复制到本项目的 `client-runtime/`，
  使 7 个 DLL 与 `Assembly-CSharp-firstpass.dll` **位于同一目录**（原因见坑 4）

---

## 2. 标准流程

```bash
git clone --recurse-submodules https://github.com/Mark007007/HEX-Private-Server.git
cd HEX-Private-Server

bash scripts/pull_upstreams.sh        # 固定上游到 UPSTREAM.lock 的 commit
bash scripts/apply_integration.sh     # 应用 overlay / integration / DLL staging

python -m unittest discover -s tests -v
dotnet build legacy-ai-worker/LegacyAiWorker.csproj -c Release --nologo
```

验证 Original AI 桥接（此时还不需要 Records）：

```bash
export HEX_CLIENT_DLL="$PWD/client-runtime/Assembly-CSharp-firstpass.dll"
printf '%s\n' '{"protocol":1,"request_id":"1","action":"health","payload":{}}' \
  | dotnet legacy-ai-worker/bin/Release/net10.0/LegacyAiWorker.dll
```

期望 `"status":"ready"`。若是 `blocked`，先检查 `HEX_CLIENT_DLL` 指向的目录里
7 个 DLL 是否齐全。

生成 Records 并启动服务器：

```bash
export HEX_GAMEDATA="/path/to/Data/gamedata"
bash scripts/prepare_client_records.sh          # 期望 Records validation PASS

cd <repo>                                        # 重要：必须以仓库根为 cwd，见坑 4
HEX_USE_SUPERVISOR=0 HEX_ORIGINAL_AI=1 \
HEX_CLIENT_DLL="$PWD/client-runtime/Assembly-CSharp-firstpass.dll" \
  bash hex-server/restart.sh
```

服务端口：TCP HConnect `9933`、HTTP Auth Proxy `8081`。

日志（Git Bash 的 `/tmp` 即 `%LOCALAPPDATA%\Temp`）：

```bash
tail -f /tmp/hconnect_log.txt
tail -f /tmp/proxy_log.txt
```

---

## 3. Windows 特有的五个坑

### 坑 1：`python3` 被 Microsoft Store 存根劫持

**现象**：`prepare_client_records.sh` 在给 `restart.sh` 打补丁的步骤失败，报

```
Python was not found; run without arguments to install from the Microsoft Store, ...
```

**根因**：`%LOCALAPPDATA%\Microsoft\WindowsApps\python3.exe` 是一个 **0 字节**的
Microsoft Store 占位存根。真实 Python 安装目录通常只有 `python.exe`，没有
`python3.exe`，于是 PATH 查找 `python3` 时落到该存根上。

**隐蔽之处**：`start.sh` 用 `command -v python3` 做前置检查，存根会让这个检查
**误判为「已安装」**，问题推迟到实际调用时才暴露。而 `hex-server/restart.sh`
里有 9 处 `python3` 调用，会直接导致服务起不来。

**修复**：在真实 Python 目录中补齐 `python3.exe`（该目录在 PATH 中通常位于
WindowsApps 之前，因此会优先命中）：

```powershell
Copy-Item "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" `
          "$env:LOCALAPPDATA\Programs\Python\Python312\python3.exe" -Force
```

验证：在 Git Bash 中执行 `command -v python3` 应指向真实 Python，而非 WindowsApps。

> 等效替代方案：设置 → 应用 → 高级应用设置 → 应用执行别名 → 关闭 `python3.exe`。

---

### 坑 2：Git Bash 没有 `setsid` / `pkill`

**现象**：`bash hex-server/restart.sh` 报端口等待超时：

```
[restart] Waiting for server to listen on :9933 ...
[restart] ERROR: HConnect server failed to bind :9933
```

**根因**：`restart.sh` 用 `setsid nohup <cmd> &` 拉起后台服务，用
`pkill -9 -f <pattern>` 停止旧进程。Git for Windows **不提供** `setsid`、`pkill`、
`pgrep`、`fuser` 和 `sqlite3` —— 这些都是 util-linux / procps 工具。

其中只有前两个会造成实际故障：

| 命令 | 在 `restart.sh` 中的用途 | 缺失后果 |
|---|---|---|
| `setsid` | 后台启动 4 个服务（5 处） | 服务起不来，端口永不监听 |
| `pkill` | 停止旧进程（8 处） | 旧进程占着端口，重启时新进程绑定失败 |
| `fuser` | 兜底释放端口 | 有 `command -v` 守卫，无影响 |
| `sqlite3` | 查询 tournament 房间数 | 有 `\|\| echo 0` 守卫，日志会显示 `0 waiting room(s)` |

> 顺带一提：日志里的 `Tournament scheduler: 0 waiting room(s) ready` 是无害的假警报，
> 它只是 `sqlite3` CLI 缺失被 `|| echo 0` 兜底的结果。真实状态见
> `hconnect_log.txt` 中的 `[tournament_server] Pool seeded (2 types)`。

**修复**：Git for Windows 的 `/etc/profile.d/env.sh` 中有

```sh
export PATH="$HOME/bin:$PATH"
```

也就是用户主目录下的 `bin`（`%USERPROFILE%\bin`）会**自动出现在任何 Git Bash 会话
的 PATH 最前**，且属于当前用户、无需管理员权限。把两个兼容脚本放进去即可全局生效
（`start.bat` / `start.sh` 也能用到）。

`%USERPROFILE%\bin\setsid`：

```sh
#!/bin/sh
# Git for Windows ships no setsid(1). restart.sh only uses it to launch
# background services detached from job control:
#     setsid nohup <cmd> ... &
# The caller's '&' already backgrounds the job, so exec'ing the target here
# keeps $! pointing at the real service PID, which restart.sh later checks
# with 'kill -0'.
exec "$@"
```

`%USERPROFILE%\bin\pkill`：

```sh
#!/bin/sh
# Git for Windows ships no pkill(1). restart.sh only uses the form
#     pkill -9 -f <pattern>
# and only ever targets its own Python services, so this shim restricts
# matching to python processes. That also keeps it from killing its own
# calling shell, whose command line contains the pattern.
#
# The pattern travels through an environment variable so it never has to be
# quoted into the PowerShell command line. PowerShell is addressed by absolute
# path because this environment's PATH can contain unexpanded %SystemRoot%
# entries.

pattern=""
while [ $# -gt 0 ]; do
  case "$1" in
    -f)
      shift
      pattern="$1"
      ;;
  esac
  shift
done

[ -n "$pattern" ] || exit 0

PS="/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
[ -x "$PS" ] || PS="powershell.exe"

PKILL_PATTERN="$pattern" "$PS" -NoProfile -NonInteractive -Command \
  "Get-CimInstance Win32_Process | Where-Object { \$_.Name -match '^python' -and \$_.CommandLine -and \$_.CommandLine.ToLower().Contains(\$env:PKILL_PATTERN.ToLower()) } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }" \
  >/dev/null 2>&1

exit 0
```

两个文件都必须用 **LF** 换行保存。若用 PowerShell 写入，显式指定：

```powershell
$text = $text -replace "`r`n", "`n"
[System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
```

> `pkill` 兼容层刻意只匹配 `python` / `python3` / `pythonw` 进程。
> 通用地匹配命令行会把它**自己的调用方 shell** 一起杀掉 —— 因为那个 shell 的命令行
> 里正好含有该 pattern。

---

### 坑 3：`restart.sh` 中嵌入 `python3 -c` 的 POSIX 路径会落到错误位置

**现象**：数据库没有建在 `hex-server/hconnect.db`，而是在盘符根下多出一棵
`<盘符>:\d\...` 的目录树。

**根因**：MSYS（Git Bash）只会把**看起来像路径的独立参数或环境变量**从 POSIX 形式
转换为 Windows 形式。而 `restart.sh` 建库步骤把路径**嵌在代码字符串内部**：

```bash
python3 -c "
import sqlite3, static
db = sqlite3.connect('$BASE_DIR/hconnect.db')
...
"
```

`$BASE_DIR` 展开为 `/d/game/...` 形式，而参数整体是一段以换行开头的代码，不像路径，
因此不会被转换。Windows Python 把以 `/` 开头的路径解释为**当前盘符根目录下的绝对
路径**，于是写到 `D:\d\game\...`。

**规避**：预先在正确位置把数据库建好，让 `if [[ ! -f "$BASE_DIR/hconnect.db" ]]`
判断为假从而跳过该步骤：

```powershell
cd <repo>\hex-server
python -c "import sqlite3, static; db = sqlite3.connect(r'<repo>\hex-server\hconnect.db'); static.ensure_schema(db); db.close()"
```

`restart.sh` 其余 Python 调用（`py_compile`、starter deck 生成、`proxy.py` 等）
都传入**独立参数**，MSYS 转换正常，无需处理。

---

### 坑 4：Worker 默认命令是相对路径

**现象**：服务器起来了，但 AI 决策时报 Worker 启动失败。

**根因**：`integration/ai_bridge/live.py` 的默认 Worker 命令是相对路径：

```
dotnet legacy-ai-worker/bin/Release/net10.0/LegacyAiWorker.dll
```

它以**服务进程的 cwd** 为基准解析。若从 `hex-server/` 目录启动服务器，该路径会指向
`hex-server/legacy-ai-worker/...`（不存在）。

**修复**：始终以**仓库根目录**为 cwd 启动：

```bash
cd <repo> && bash hex-server/restart.sh
```

`start.sh` 本身就会 `cd "$ROOT"`，所以用一键脚本不会遇到这个问题。

替代做法：设置 `HEX_ORIGINAL_AI_CWD=<repo>`，或直接用 `HEX_ORIGINAL_AI_WORKER`
指定绝对命令。

> 相关：Worker 通过自定义 `AssemblyLoadContext` 按**文件名**从
> `HEX_CLIENT_DLL` 所在目录解析依赖。因此 `HEX_CLIENT_DLL` 所在目录必须同时包含
> `NCalc.dll`、`ICSharpCode.SharpZipLib.dll`、`UnityEngine.dll` 等全部 7 个 DLL。
> 不要直接指向客户端的 `Hex_Data/Managed/`（该目录缺少 `NCalc.dll` 等）。

---

### 坑 5：Records 提取结果有磁盘缓存

**现象**：修正了 Records 文件内容，重新建库后 `card_templates` 等表仍然是 0。

**根因**：`AssetExtraction/gamedata_seed.extract()` 会把提取结果 pickle 缓存到
`/tmp/hex_records_seed_<hash>.pkl`，缓存 key 是 **Records 目录的 `os.stat`
（`mtime_ns` + `size`）**。

若你是在原地改写同名文件，**目录 mtime 不变**，缓存不会失效，于是继续返回旧结果。
（正常走 `prepare_client_records.sh` 时它会 `mv` 新文件进来，目录 mtime 会更新，
不会触发这个问题。）

**处理**：手工删除缓存后重跑。

```powershell
Remove-Item "$env:SystemDrive\tmp\hex_records_seed_*.pkl" -Force
```

> Windows Python 下 `/tmp` 解析为当前盘符根目录，即 `<盘符>:\tmp`。

---

## 4. 环境变量速查

| 变量 | 值 | 说明 |
|---|---|---|
| `HEX_CLIENT_DLL` | `<repo>/client-runtime/Assembly-CSharp-firstpass.dll` | 必需；其所在目录需含全部 7 个 DLL |
| `HEX_GAMEDATA` | `<客户端>/Data/gamedata` | 提取 Records 用，指向**文件**本身 |
| `HEX_ORIGINAL_AI` | `1` | 服务端启用原版 AI；手工启动 Worker 时无效 |
| `HEX_ORIGINAL_AI_WORKER` | 默认见坑 4 | 自定义 Worker 命令；相对路径以服务 cwd 为基准 |
| `HEX_ORIGINAL_AI_CWD` | 可选 | Worker 进程的工作目录 |
| `HEX_ORIGINAL_AI_TIMEOUT` | 默认 `15`（秒） | AI 决策超时 |
| `HEX_USE_SUPERVISOR` | 建议 `0` | `start.sh` 默认即 0；supervisord 在 Windows 上不可靠 |

> 手工启动 Worker 时，判断依据是 `HEX_CLIENT_DLL`，**不是** `HEX_ORIGINAL_AI`。

---

## 5. 完成判据

按顺序应全部通过：

```
[✓] git submodule status 前缀为空格（不是 '-'）
[✓] python -m unittest discover -s tests -v        -> 15 tests OK
[✓] dotnet build legacy-ai-worker/...csproj        -> 0 warnings 0 errors
[✓] Worker health / probe                          -> "status":"ready"
[✓] prepare_client_records.sh                      -> Records validation PASS
[✓] hconnect_log.txt                               -> HConnect server listening on port 9933
[✓] 端口 9933 / 8081 处于 LISTENING
```

数据库播种自检（在 `hex-server` 目录下执行）：

```bash
python -c "import sqlite3; c=sqlite3.connect('hconnect.db'); print([ (t, c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]) for t in ('card_templates','ability_effects','champion_templates') ])"
```

`card_templates` 应在 7000 量级。若为 0，说明 Records 或缓存有问题（见坑 5）。

---

## 6. 对宿主环境做的改动（均可回滚）

| 路径 | 类型 | 回滚方式 |
|---|---|---|
| `%USERPROFILE%\bin\setsid` | 新增文件 | 删除 |
| `%USERPROFILE%\bin\pkill` | 新增文件 | 删除 |
| `<Python 安装目录>\python3.exe` | 新增文件（`python.exe` 的副本） | 删除 |
| `hex-server/Records/*.jsonl` | 由 `prepare_client_records.sh` 生成 | 重新生成 |
| `hex-server/hconnect.db` | 由 `restart.sh` / `static.ensure_schema` 生成 | 删除后重建 |
| `hex-server/generated/starter_decks.json` | 由 `restart.sh` 生成 | 删除 |

以上改动**不需要**注册表修改、系统 PATH 修改，也不需要安装任何系统组件。

---

## 7. 仍未自动化验证的部分

Original AI 的 Worker 是**惰性启动**的：只在第一场带 AI 的对局走到
`ai.py` 中的 `try_native_original_ai()` 时才会 spawn。因此下面这条链需要在游戏中实操验证：

```
真实服务器 → 真实 SessionState → client-compatible events
  → Original AI → AI transaction → RulesPort → 服务器状态变化
```

建议按递进顺序验证，不要第一步就打完整 Frost Ring Arena：
创建 Practice/PvE → AI 接收真实 SessionEvent → Original AI 产生 transaction →
RulesPort 验证 transaction → 真正进行一回合。