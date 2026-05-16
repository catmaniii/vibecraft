# M0 Smoke Test 运行手册

**目的**：在真实 SC2 + ares-sc2 环境里，验证一个核心假设——
**当我们把若干探机置入一个 "vibecraft 自用 role" 后，ares 的所有 Manager 都会跳过这些单位，既不改它们的 role，也不下达行动指令**。

实现细节：ares 的 `UnitRole` 是固定 StrEnum（无法动态加成员），所以我们把
"LLM_CONTROLLED" 实际**映射到 ares 的 `UnitRole.CONTROL_GROUP_ONE`**——这是
ares 源码注释里明确"留给用户的空槽"（`# control groups, use for anything not
specified`），且 ares 源码 grep 验证过：**没有任何 Manager 内部引用它**。
ares 官方教程 `docs/tutorials/unit_squads_group_behaviors.md` 也是这个用法。

设计文档 §3.4 把这个假设列为 M0 的"唯一存疑点"。本测试通过了，整个 ares hook C
方案才成立；不通过则需要回退到 Manager wrap 或继承子类的方案。

预期看点：**两个不动的叉子**全程站在初始位置不动，没有去采矿、没有去防守、没有 attack。

---

## 0. 前置准备

需要 Windows 机器 + 已安装零售 StarCraft II 客户端。Linux/Mac 不行（SC2 仅 Windows 有渲染版）。

### 0.1 SC2 路径（非默认安装位置必做）

python-sc2 默认只在 `C:\Program Files (x86)\StarCraft II\` 和 `C:\Program Files\StarCraft II\` 找客户端。如果你装在别处（比如本机用户装在 `D:\StarCraft II\`），**必须设环境变量 `SC2PATH`**，指向你的 SC2 安装根目录（即包含 `Versions\` 子目录的那一级）。

PowerShell 当前 session 临时设（推荐先这样试）：

```powershell
$env:SC2PATH = "D:\StarCraft II"
```

确认：

```powershell
Test-Path "$env:SC2PATH\Versions"   # 应该输出 True
```

要永久生效（下次开 shell 不用重设）：

```powershell
[Environment]::SetEnvironmentVariable("SC2PATH", "D:\StarCraft II", "User")
# 然后**重开** PowerShell window
```

### 0.2 地图目录

python-sc2 找地图的顺序：`$SC2PATH\Maps\` → `Documents\StarCraft II\Maps\`。
**两个目录都可能不存在**（特别是从未跑过自定义游戏 / 编辑器的客户端）。

```powershell
# 选一个目录创建（推荐放 SC2 安装目录下）
New-Item -ItemType Directory -Force -Path "$env:SC2PATH\Maps" | Out-Null
```

### 0.3 测试地图

python-sc2 需要 `Maps\` 目录下有 `.SC2Map` 文件。两个来源：

**来源 A：从 Battle.net Cache 提取（推荐，不用下载）**

只要你在 SC2 客户端里浏览 / 玩过某张 1v1 ladder 图，它的完整地图 archive 就缓存
在 `C:\ProgramData\Blizzard Entertainment\Battle.net\Cache\` 下（`.s2ma` 文件，
本质就是 MPQ archive，改扩展名为 `.SC2Map` 即可用）。本手册验证时用的是
`(2)DaybreakLE`，已提取为 `D:\StarCraft II\Maps\DaybreakLE.SC2Map`。

> 注：cache 里的 `.s2ma` 是 hash 命名的，认出哪个是哪张图需要解析 cache 元数据
>（`.s2mh` 头部有明文图名 + 引用的 `.s2ma` hash）。MVP 阶段是手动做的，未来可
> 脚本化。最省事的办法：在 SC2 客户端开一局 vs AI 的自定义对局、选张 1v1 图，
> 这张图的 `.s2ma` 就会完整缓存。

**来源 B：直接下载 `.SC2Map`**

AI Arena / SC2 社区的 ladder 地图包，下载后放进 §0.2 创建的目录。

### 0.4 客户端初始化

1. **打开 SC2 一次**，登录战网账户，让客户端把地图缓存下载完。
2. **关闭 SC2 客户端**，python-sc2 会自己启 / 接管它。

---

## 1. 装依赖

### 1.1 Python 版本：必须 3.11

`sc2-helper`（ares 间接依赖的 combat simulator）只发布到 `cp311` wheel，
**Python 3.12 装不上**。仓库根目录的 `.python-version` 已锁定 `3.11`，`uv` 会
自动选用。还没装 3.11 的话：

```powershell
uv python install 3.11
```

如果 `.venv` 是用 3.12 建的（`uv run python --version` 查），重建：

```powershell
Remove-Item -Recurse -Force .venv
uv venv          # 读 .python-version，用 3.11
```

### 1.2 装 vibecraft + ares 全家桶

ares-sc2 / burnysc2 / map-analyzer 不在 PyPI（git 源），sc2-helper 在 PyPI。
它们都归到 `pyproject.toml` 的 `sc2` extra，git URL 已写进 `[tool.uv.sources]`：

```powershell
# 在项目根目录（注意目录名是 vibecraft，不是 voice_craft）
cd D:\code\claudecode\vibecraft

uv sync --extra dev --extra sc2     # 一条命令装全：dev 工具链 + ares 全家桶
```

**注意**：日常开发若用不到 ares（M1.2-M1.4 的 server / PWA / LLM 都不需要），
`uv sync --extra dev` 即可；但**一旦 `uv sync` 时漏了 `--extra sc2`，ares 全家桶
会被卸载**（uv 让 venv 严格匹配启用的 extra）。跑 smoke / 碰 `ares_adapter` 时
务必带上 `--extra sc2`。

pip 用户（不走 uv）`[tool.uv.sources]` 不生效，仍需手动 git install：

```powershell
pip install "git+https://github.com/AresSC2/ares-sc2@main" sc2-helper
```

### 1.3 修 ares-sc2 的 src-layout 打包问题

ares-sc2 3.7.2 用 `uv_build` backend，会把包错装到 `site-packages/src/ares/`
而非 `site-packages/ares/`，导致 `import ares` 失败。修法：在 site-packages 放
一个内容为 `src` 的 `.pth` 文件：

```powershell
$sp = (uv run python -c "import sysconfig; print(sysconfig.get_path('purelib'))")
"src" | Out-File -FilePath "$sp\ares_sc2_src.pth" -Encoding ascii -NoNewline
```

> **重建 `.venv` 后这个 `.pth` 会丢，要重新创建。**

### 1.4 验证

```powershell
uv run python -c "import ares, sc2, vibecraft; print('ok')"
```

输出 `ok` 即环境就绪。如果报 `os error 32` / `DLL load failed` / 误报
"未安装 ares-sc2"——是 Windows Defender 在扫新解压的文件，**重试几次**即可
（见 §4）。

---

## 2. 跑 smoke

```powershell
uv run python scripts/smoke_test.py --map "DaybreakLE"
```

默认参数：对手 `Random Easy`、监测 60 秒、2 个探机置入 `CONTROL_GROUP_ONE` role。
`--map` 传 `Maps\` 目录里 `.SC2Map` 文件的文件名（去掉扩展名）。

可调：

```powershell
uv run python scripts/smoke_test.py `
  --map "DaybreakLE" `
  --opponent-difficulty Easy `
  --opponent-race Zerg `
  --llm-controlled-probes 3 `
  --observation-seconds 90
```

SC2 会自动启动 → 进对局 → 跑 60 秒 → bot 自己 `leave` → 客户端回主菜单。

---

## 3. 看结果

脚本会在 `logs/<game_id>/` 下落：

- `smoke_report.json` —— **直接看的结论**
- `events.jsonl` —— 时间线（包含 smoke_started / role_change / snapshot 等）

`smoke_report.json` 顶层字段：

```json
{
  "verdict": "pass" | "fail",
  "anomaly_count": 0,
  "anomalies": [],
  "anomalies_by_kind": {},
  "snapshots": [
    { "ts": 5.9, "probes": [
        { "tag": 12345, "alive": true, "in_role": true,
          "position": [44.3, 23.5], "drift_from_initial": 0.0,
          "order_count": 0, "orders": [] },
        ...
    ]},
    ...
  ]
}
```

### 怎么算 pass

- 全过程 `anomalies` 为空
- 每个被托管探机的 `in_role` 始终是 `true`（没被某个 Manager 改走 role）
- 每个被托管探机的 `order_count` 始终是 0（enroll 时已 `stop()` 清掉开局默认
  采矿 order，之后没有任何 Manager 再给它下指令）
- `drift_from_initial` 几乎为 0（探机 stop 后站在原地不动）

> 注意：探机开局 0s 就会自动采矿（SC2 引擎默认行为，非 bot 指令）。smoke 在
> t=5s 把探机置入 role 后会立刻 `unit.stop()` 清掉这条旧 order —— 所以"零
> order"指的是 stop **之后**。若 stop 后 order 又冒出来，才是真的有 Manager
> 在主动接管（`received_orders` 异常）。

### 怎么算 fail

| `anomaly.kind` | 含义 | 后续 |
|---|---|---|
| `role_changed_away` | 某个 Manager 把 role 改了 | 需要识别是哪个 Manager。临时方案：在 ares_adapter 里 monkey-patch 该 Manager 的 role override 调用；正式方案：OverrideMediator wrap query |
| `received_orders` | base bot 给了行动 | 同上 |
| `assign_role_failed` | 我们调 `mediator.assign_role` 自己就挂了 | 检查 ares API 名是否对（设计文档假设的 API 可能与 ares 实际有差） |
| `probe_died` | 探机被对方杀了 | 对手太强 / 监测时间太长。重试，调 `--observation-seconds` 短一点或换更弱对手 |

---

## 4. 常见问题

### `uv pip install ... ares-sc2` 失败

`ares-sc2` 自己用 poetry 声明 dep，里面还引用了 git 的 burnysc2 和 map-analyzer。
若解析失败，手动逐个装：

```powershell
uv pip install "git+https://github.com/august-k/python-sc2@develop"
uv pip install "git+https://github.com/spudde123/SC2MapAnalysis@develop"
uv pip install "git+https://github.com/AresSC2/ares-sc2@main"
```

### `sc2-helper` 装不上：`no wheels with a matching Python ABI tag`

`.venv` 是 Python 3.12 建的。`sc2-helper` 只有到 `cp311` 的 wheel。回到 §1.1
用 3.11 重建 `.venv`。

### `os error 32` / `DLL load failed` / 误报"未安装 ares-sc2"

Windows Defender 实时扫描刚解压的 `.exe` / `.dll`（standalone Python、ares
全家桶、cython 扩展），会短暂锁住文件，导致紧接着的命令失败：

- `uv` 报 `error: ... failed with ... os error 32`
- `python` 报 `ImportError: DLL load failed while importing ...`
- `smoke_test.py` 误报 `[smoke] 未安装 ares-sc2 / burnysc2`（它把任何
  `ImportError` 都当成"没装"，但 DLL 锁也是 `ImportError`）

**都不是真错误，等几秒重试即可。** 装完依赖后先空跑几次
`uv run python -c "import ares"` 让 Defender 扫完一波，再跑 smoke 就稳了。

### `import ares` 报 `No module named 'ares'`（但 `ares-sc2` 已装）

ares-sc2 3.7.2 的 src-layout 打包问题，见 §1.3 的 `.pth` 修复。

### `SC2 not found` 报错

python-sc2 找不到 SC2 安装路径。回到 §0.1，确认 `$env:SC2PATH` 已设且
`$env:SC2PATH\Versions` 存在；新开 shell 之后 env var 没继承的话需要重设
（或永久设 + 重开 shell）。

### 地图不存在

回到 §0.2 + §0.3：确认 `.SC2Map` 文件确实在 `$env:SC2PATH\Maps\` 或
`Documents\StarCraft II\Maps\`，重试。

### bot 进游戏后客户端崩

通常是 burnysc2 / SC2 patch 不同步。看 `python-sc2` 的 release tag 与你 SC2 客户端版本，必要时换 burnysc2 的 commit。

---

## 5. 结论的下游影响

| smoke verdict | 后续 |
|---|---|
| pass | M0 出口达成。可以开 M1（Bot service + WS endpoint + 手机 PWA 框架）|
| fail，仅个别 Manager 不 respect role | 在 `ares_adapter.py` 里给那个 Manager 加 wrap；保留主架构 |
| fail，所有 Manager 都不 respect role | 退回到 Hook B OverrideMediator wrap 方案，或者继承 Manager 子类写 override |

---

## 6. 一键回归

通过后建议把 smoke 加入 nightly 回归（L4 端到端层，设计文档 §11.3）。命令同上，把 report 落到一个累积目录，做趋势监测。
