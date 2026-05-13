# M0 Smoke Test 运行手册

**目的**：在真实 SC2 + ares-sc2 环境里，验证一个核心假设——
**当我们把若干探机置入一个 "voicecraft 自用 role" 后，ares 的所有 Manager 都会跳过这些单位，既不改它们的 role，也不下达行动指令**。

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

1. **打开 SC2 一次**，登录战网账户，让客户端把地图缓存下载完。
2. **下载测试地图**到 `Documents\StarCraft II\Maps\`。AI Arena 推荐的 ladder 地图都行；本手册默认 `Goldenaura LE`。如果你不知道地图叫什么，可以打开 SC2 → 自定义游戏 → 创建房间 → 看可选地图列表，挑一张 1v1 ladder 地图。
3. **关闭 SC2 客户端**，python-sc2 会自己启 / 接管它。

---

## 1. 装依赖

```powershell
# 在项目根目录
cd D:\code\claudecode\voice_craft

# 装 voicecraft + dev 依赖
uv sync --extra dev

# 装 ares-sc2 + burnysc2 + map-analyzer（git）
uv pip install "git+https://github.com/AresSC2/ares-sc2@main"
```

如果 `uv pip install` 拉 git 失败（公司网 / 防火墙），可以手 clone 然后 `uv pip install -e ../ares-sc2`。

---

## 2. 跑 smoke

```powershell
uv run python scripts/smoke_test.py
```

默认参数：地图 `Goldenaura LE`、对手 `Random Easy`、监测 60 秒、2 个探机置入 LLM_CONTROLLED。

可调：

```powershell
uv run python scripts/smoke_test.py `
  --map "Equilibrium LE" `
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
    { "ts": 5.5, "probes": [
        { "tag": 12345, "alive": true, "role": "LLM_CONTROLLED",
          "position": [38.0, 142.0], "order_count": 0, "orders": [] },
        ...
    ]},
    ...
  ]
}
```

### 怎么算 pass

- 全过程 `anomalies` 为空
- 每个被托管探机的 `role` 始终是 `"LLM_CONTROLLED"`
- 每个被托管探机的 `order_count` 始终是 0（没有任何 base bot 给的指令）
- `position` 几乎不变（探机站在初始点）

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

### `SC2 not found` 报错

python-sc2 找不到 SC2 安装路径。要么：
- 默认装 `C:\Program Files (x86)\StarCraft II\` 应该自动检测
- 或设环境变量 `SC2PATH` 指向你的安装根目录

### 地图不存在

把地图 `.SC2Map` 文件放到 `Documents\StarCraft II\Maps\`，重试。

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
