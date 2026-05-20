# Build Order 验收测试框架设计

> brainstorming session 2026-05-20 产出。配套实施 plan 见 writing-plans 阶段。

**Goal**：建立一套"引入新开局策略"的可复用流程 —— 对每个 build order 写好基于
标准节奏的验收脚本，自动开 non-realtime SC2 实例跑测试，通过结构化 telemetry log
判定 build 是否执行到位。框架建成后第一批工作是用它审计所有现存 build。

**Tech Stack**：python-sc2 BotAI 钩子 + sharpy act + jsonl telemetry + 现有
`GameProcess` spawn 机制。

---

## 背景：现状与缺口

当前日志：
- `server_*.log` —— sharpy + vibecraft 的 Python logging，纯文本混在一起
- `logs/game_*/` 下的 jsonl 流（events/directives/decisions/...）—— 都是 **Director 层**
  （策略切换、directive），不是 SC2 游戏内状态
- `LogStream.METRICS` 枚举存在但**无人写入**
- **没有结构化的"SC2 游戏内实际发生了什么"telemetry** —— `Gate4StateLogger` 是最接近
  的东西，但 gate4 专属、写进文本 server-log、不可机读

缺口：要自动验收 build order，必须有机读的游戏内状态时间线。本设计补这个。

---

## §1 架构总览

5 个组件：

| # | 组件 | 形态 |
|---|---|---|
| 1 | Telemetry 采集 | `GameTelemetryLogger`（always-on sharpy act，挂公共层）→ `logs/game_*/telemetry.jsonl` |
| 2 | Acceptance spec | `tests/build_acceptance/<strategy_id>.yaml` —— 我 deep research 在线收集标准 timing 后手写 |
| 3 | Test runner | `scripts/build_acceptance.py <strategy_id>` —— 用 Bash 调用 |
| 4 | Verifier | runner 内部模块 —— 解析 telemetry 对比 spec，出 pass/fail 报告 |
| 5 | Process doc | `docs/process/new-opening-strategy.md` —— 7 步可复用流程 |

数据流：

```
SC2 game ──> GameTelemetryLogger ──> telemetry.jsonl
                                          │
acceptance spec.yaml ──> Verifier <───────┘
                            │
                            └──> 报告 (pass/fail + 每条断言 actual vs expected)
```

Telemetry 采集分两路：**离散事件**（BotAI 钩子，精确 game_time）+ **周期快照**（每 2s）。

设计决策：telemetry **默认一直开**（项目开发完成前），既服务验收，也让日常 debug
直接读机读时间线。

---

## §2 Telemetry 数据 schema

`telemetry.jsonl` 每行一个 JSON record，三种类型。

**开局 record**（一次，记地图锚点供 verifier 解析命名位置）：
```json
{"t": 0.0, "kind": "game_start",
 "home": [127.5,119.5], "enemy_main": [48.5,28.5], "natural": [145.5,98.5],
 "active_recipe": "dt_drop_iac", "my_race": "Protoss"}
```

**离散事件**（`t` = game_time 秒）：
```json
{"t": 18.3,  "kind": "building_started",  "unit": "GATEWAY", "tag": 123, "pos": [94.4,104.4]}
{"t": 138.2, "kind": "building_complete", "unit": "GATEWAY", "tag": 123, "pos": [94.4,104.4]}
{"t": 211.0, "kind": "upgrade_complete",  "upgrade": "WARPGATERESEARCH"}
{"t": 280.5, "kind": "unit_created",      "unit": "DARKTEMPLAR", "tag": 456, "pos": [80,40]}
{"t": 290.1, "kind": "unit_destroyed",    "unit": "STALKER", "tag": 789}
```
- 建筑记 `started` + `complete` 两个事件（"第几分钟下了 BF" 看 started；"科技几分钟好" 看 complete）
- BotAI 钩子：`on_building_construction_started/complete`、`on_upgrade_complete`、
  `on_unit_created`、`on_unit_destroyed`

**周期快照**（每 2s）：
```json
{"t": 120.0, "kind": "snapshot",
 "supply_used": 24, "supply_cap": 39, "workers": 22, "army_supply": 4,
 "minerals": 150, "vespene": 80, "bases": 2,
 "army_center": [100,110],
 "units": {"STALKER":2, "ZEALOT":0, "DARKTEMPLAR":0, "ARCHON":0, "WARPPRISM":1},
 "key_units": {"WARPPRISM": [[114,115]]},
 "phase": "tech", "active_recipe": "dt_drop_iac"}
```

不记敌方信息（验收只关心自己的 build，YAGNI）。

---

## §3 Acceptance spec 格式 + 容差模型

每个 build 一个文件 `tests/build_acceptance/<strategy_id>.yaml`，内容由 deep research
在线收集标准 timing 后填。文件头注释记录 research 来源链接。

```yaml
strategy_id: dt_drop_iac
my_race: Protoss
# research 来源:spawningtool /68902, Liquipedia DT-drop, ...

checks:
  - id: gateway_1
    type: building_started
    unit: GATEWAY
    by: "0:35"              # 只验上界

  - id: dark_shrine
    type: building_complete
    unit: DARKSHRINE
    at: "3:14"
    tol: 25                 # ±25 秒窗口

  - id: warpgate_research
    type: upgrade_complete
    upgrade: WARPGATERESEARCH
    by: "3:30"

  - id: workers_4min
    type: worker_count
    at: "4:00"
    min: 40

  - id: dt_first_wave
    type: unit_count
    unit: DARKTEMPLAR
    at: "4:50"
    min: 4

  - id: prism_at_forward
    type: key_unit_at
    unit: WARPPRISM
    at: "4:30"
    near: enemy_main        # 命名锚点
    within: 25

  - id: attack_moveout
    type: attack_moveout
    by: "8:30"
```

断言类型：`building_started` / `building_complete` / `upgrade_complete` /
`worker_count` / `unit_count` / `key_unit_at` / `army_gather` / `attack_moveout`。

时间表达：`at + tol`（窗口）或 `by`（只验上界）。位置类用命名锚点
（`home` / `enemy_main` / `natural`）+ `within` 距离容差，verifier 用 `game_start`
record 解析锚点坐标。

**两档容差，单文件**：
- spec 里的 `tol` = VeryEasy 精确档
- CheatMoney 跑时 verifier 自动放宽：`tol × 2` + 跳过位置类断言（抗压下位置必乱）
  + 只保留 `by` 类。即 CheatMoney 档本质验"build 没崩、骨架还在"。

`attack_moveout` 判定：扫 snapshot `army_center` 序列，找第一次离 `home` 超过阈值
（如 > 60 距）的 `t`。

---

## §4 Test runner + Verifier + infra-fail 重试

**Runner：`scripts/build_acceptance.py <strategy_id> [--opponent veryeasy|cheatmoney]`**

复用现有 `GameProcess`（headless_smoke.py 已示范 spawn bot+SC2）：

1. spawn **non-realtime** SC2 —— 指定 my_race + `active_recipe=<strategy_id>` +
   对手难度 + 固定地图
2. 跑到 **game-time 上限 ~10 分钟**（600 game-sec）结束 —— 验收只需覆盖到"出门攻击"
3. 全程监控子进程状态

**infra-fail vs acceptance-fail 分流**：
- 子进程报 `crashed`/`error`（watchdog hang / SC2 崩溃）或异常退出码 →
  **infra-fail** → 自动 retry，**最多 3 次**
- 3 次都 infra-fail → 报 "INFRA BROKEN" 并停下（要人看，不是 build 的问题）
- 游戏正常跑完 + telemetry 完整 → 进 verifier

依据：`watchdog.py` 的 `hang_watchdog`（`_STALL_THRESHOLD_S=30s`）已经能检测
SC2 client 端 hang（`bot.time` 不前进）并 kill + 回调通知父进程 —— runner 直接
读这个信号判定 infra-fail。**残局 hang 根因（SC2 client 卡死）不深挖，watchdog
兜底足够。**

**Verifier**（runner 内部模块）：
- 读 telemetry.jsonl 全部 record + `game_start` 锚点
- 逐条 check 判定，输出 `PASS/FAIL + actual vs expected`
  - 例：`dark_shrine  FAIL  expected 3:14±25s, actual complete @ 4:02 (晚 23s)`
- 总报告 `N/M passed`，写 `logs/build_acceptance/<id>_<ts>.txt` + 打 stdout

**修复循环**：Bash 调 runner → 读 stdout 报告 → acceptance-fail 就分析 telemetry +
改 plan + 重跑，循环到全 PASS。架构层面偏离（要动设计决策）才停下问。

---

## §5 Process doc + 首次落地

**Process doc**：`docs/process/new-opening-strategy.md` —— 引入一个新开局策略的
7 步可复用流程：

1. **Deep research** —— 搜 spawningtool / Liquipedia / TeamLiquid / 高手录像，
   收集该 build 的标准 timing 节点
2. 写 acceptance spec —— `tests/build_acceptance/<id>.yaml`（头注释记来源）
3. 写 / 改 plan 代码 —— `<id>.py` + strategy 定义 `<id>.yaml`
4. 跑 runner（VeryEasy）—— `uv run python scripts/build_acceptance.py <id>`
5. 读报告，acceptance-fail 改 plan 重跑，循环到全 PASS
6. VeryEasy 全过 → 跑 CheatMoney ×3 → 看通过率
7. 沉淀 —— research 来源链接记进 spec 文件头

**首次落地** = 用该流程审计所有现存 build。按 race 分批，先 protoss（玩家主用神族）：

opening builds：`4bg` / `iac_2base` / `dt_drop_iac` / `1g_robo_immortal` /
`blink_stalker` / `dt_rush` / `phoenix_2base` / `cannon_rush`，然后 zerg / terran。

每个 build 审计 = research 标准节奏 → 对比 plan 代码找不合理（如 dt_drop_iac
"隐刀前出叉子哨兵"）→ 修 → 写 spec → 测。

---

## 实施顺序（组件依赖）

1. **Telemetry**（`GameTelemetryLogger` + telemetry.jsonl）—— 基础，没它什么都验不了。
   先做，并用一局真实游戏验证 jsonl 内容正确
2. **Runner + Verifier 框架** —— 先拿一个已知 build（如 4bg）写最小 spec，验证
   整条 pipeline（spawn → telemetry → verifier → 报告）跑通
3. **Process doc** —— 把 1+2 跑通的经验固化成 7 步流程
4. **批量审计现存 build** —— 按流程逐个走，先 protoss opening

---

## 附：独立待办（不属于本框架）

PWA 宏观策略选择界面看不到全部策略（尤其中后期 persistent doctrine）—— 独立 bug，
本框架之外单独排查。部分原因是 persistent doctrine 目前每族只有 1 个（task #127/128/129
计划每族再加 5 个但未做），部分可能是 `StrategyPicker.vue` 显示 bug。
