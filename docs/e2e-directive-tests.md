# Directive 全覆盖端到端测试

VibeCraft directive 系统按 four-layer 架构（详 ADR 0010）分 L1/L2/L3/L4 四层。
本文档是端到端自动化测试的 **spec**：每个测试项的注入文本、测试流程、验收
通过条件都精确写下来，让任何人（含未来的 Claude session）能照单复跑。

- 测试驱动脚本：`scripts/e2e_4_directive_types.py`
- 首次实施：2026-05-17（M2 收尾 4 case 4/4 PASS）
- 全覆盖扩展：2026-05-17 晚（14 case 覆盖 10/12 directive 类型 + 9/10 done_when kind）

---

## 测试如何跑（通用流程）

driver 串行跑每个 case，**每个 case 拉一个独立 SC2 子进程**（不是共享一局
游戏），单 case 流程：

```
1. driver 设 VIBECRAFT_FORCE_INITIAL_OPENING=<id>（默认 1g_robo_immortal）
2. driver 启 GameProcess(realtime=False, opponent_difficulty=<--difficulty>)
   ↳ 子进程 spawn 拉 SC2 + sharpy bot + vibecraft Director
3. driver async for 收上行流（snapshots / events / status）
4. 等 sc2 状态进 playing
5. 再等 case.inject_after 秒（默认 3）
6. driver 经 down_q 注入 {"type": "command", "text": case.inject}
   ↳ 子进程 _tick_view_channel 起 asyncio task 调 director.on_player_command
   ↳ Director 调 IntentParser → LLM(DeepSeek V4) → JSON-Directive 列表
   ↳ Director 路由 directive（strategy_set / production_override 等各自路径）
   ↳ board.submit → 1.5s commit delay → board.tick fires COMMITTED
   ↳ _dispatch_event 把 BoardEvent 转 WS event 帧推上行队列
7. driver 持续收集 snapshots[] + events[] 直到：
   - sc2 状态变 ended / crashed → 再 drain 2s 退出（捞 in-flight event）
   - wall-clock 到 --seconds（默认 90）→ stop + 强制退出
8. 调对应 verify 函数（见下「验收通过条件」）→ PASS / FAIL
9. case 之间 sleep 5s（让 SC2 进程清理 + watchdog 收尾）
```

为啥每个 case 独立游戏：directive 进 board 后会改全局 state（active strategy /
standing_orders 等），跨 case 共享会污染 verify 结果。SC2 启动 ~15s + bot 进
playing 几秒，对 e2e 来说独立拉是干净 baseline 的代价。

---

## 验收通过条件（4 种 verify 策略）

driver 4 种 verify 函数，每个 case `verify_field` 指定走哪一种：

### 1. `strategy_changed`（L1 strategy_set 用）

**通过条件**（任一成立即 PASS）：
- snapshot 任一 `strategy.opening.id` / `strategy.midgame.id` / `strategy.lategame.id`
  ≠ 初始 `1g_robo_immortal`
- snapshot 出现 `pending_force_strategy` 字段（LLM 识别成功但被
  `_check_strategy_obsolete` 拦下等玩家硬转确认，业务上算成功）
- events 出现 `strategy.set` 或 `strategy.phase_change`

**失败原因解读**：LLM 没解析成 `strategy_set` / 解析的 strategy_id 不在 catalog /
`_check_strategy_obsolete` 拦下但 snapshot 没及时推。

### 2. `strategy_cleared`（L1 strategy_cancel 用）

**通过条件**：snapshot 先看到 `opening.id == 初始`，后续 snapshot `opening == None`。

**实现说明**：`strategy_cancel` 走 `Director._dispatch_cancel`（不进 board），
直接清 `board.slots[stage] = None` + 主动 `_push_snapshot`，所以下一个
snapshot 必反映清空状态。

**失败原因解读**：LLM 没解析成 `strategy_cancel`（可能识别成 `engagement_constraint`
defend 之类）。

### 3. `<snapshot 字段>`（L3a / L4a / L4b / L4c / L2a / L2b / L2c 用）

`verify_field` 设成 `standing_orders` / `production_overrides` / `active_tactics`
之一。

**通过条件**（任一成立即 PASS）：
- snapshot 该字段 list 非空（说明 directive 进 board 后在该字段在 in-flight）
- events 出现 `directive.committed`（兜底：task_monitor 可能立即判 done →
  从 list pop，snapshot 推送窗口 ~2s 可能完全错过）

**失败原因解读**：LLM 解析失败 / directive 类型路由错位 / `_check_strategy_obsolete`
意外拦下（仅 strategy_set）。

### 4. `any_directive_committed`（L2d / L2e / L3b / L3c / L3d 用）

`engagement_constraint` / `scout` / `build_at` / ephemeral `unit_claim` 等进
`_in_flight` 但 snapshot 不暴露字段，唯一可见信号是 events 流。

**通过条件**：events 出现 `directive.committed`。

**失败原因解读**：LLM 没识别 / directive 被拒进 board。

---

## 14 个具体测试项

每行：编号 + name + 注入文本（中文原话） + 预期 LLM 解析的 directive +
verify 策略 + PASS 条件（具体到字段/event）。

### L1 宏观策略（2 case）

| # | name | 注入 | 预期 directive | verify | PASS 条件具体描述 |
|---|---|---|---|---|---|
| 1 | L1a strategy_set | `切叉球一波` | `strategy_set(stage=midgame, strategy_id=iac_2base)` | `strategy_changed` | snapshot `strategy.midgame.id == "iac_2base"` 或 events 含 `strategy.set` |
| 2 | L1b strategy_cancel | `取消所有剧本` | `strategy_cancel(stage="all")` | `strategy_cleared` | snapshot 先见 `opening.id == "1g_robo_immortal"`，后续 `opening == None` |

### L2 战术目标（5 case）

| # | name | 注入 | 预期 directive | verify | PASS 条件具体描述 |
|---|---|---|---|---|---|
| 3 | L2a tactical_attack | `进攻对方二矿` | `tactical_objective(verb=attack, target_area=enemy_natural, done_when=any_of([target_destroyed, own_army_size_ratio]))` | `active_tactics` | snapshot `active_tactics` list 非空 或 events 含 `directive.committed` |
| 4 | L2b tactical_scout + vision_acquired | `看一眼对方主基地` | `tactical_objective(verb=scout, target_area=enemy_main, done_when=vision_acquired(area=enemy_main, hold_seconds=5))` | `active_tactics` | 同上 |
| 5 | L2c tactical_harass + enemy_killed_in_area | `凤凰打死对方 5 个农民就回` | `tactical_objective(verb=harass, unit_type_hint=[Phoenix], done_when=enemy_killed_in_area(unit_type=Probe, op=">=", value=5))` | `active_tactics` | 同上 |
| 6 | L2d engagement_defend | `守家别出门` | `engagement_constraint(stance=defend)` | `any_directive_committed` | events 含 `directive.committed` |
| 7 | L2e engagement_retreat + time_elapsed_since | `30 秒后撤` | `engagement_constraint(stance=retreat, done_when=time_elapsed_since(seconds=30, ref=directive_issued))` | `any_directive_committed` | events 含 `directive.committed` |

### L3 单位 / 常驻 / 建造（4 case）

| # | name | 注入 | 预期 directive | verify | PASS 条件具体描述 |
|---|---|---|---|---|---|
| 8 | L3a unit_claim persistent | `探机巡逻二矿别动` | `unit_claim(selector={unit_type:Probe}, task={verb:patrol, target:{named_spot:natural}}, persistent=true)` | `standing_orders` | snapshot `standing_orders` list 非空（含一条 Probe patrol natural） |
| 9 | L3b unit_claim ephemeral | `让那个探机移动到气矿` | `unit_claim(selector={unit_type:Probe}, task={verb:move_to, target:gas}, persistent=false)` | `any_directive_committed` | events 含 `directive.committed` |
| 10 | L3c scout | `侦察一下对方主基地` | `scout(target={named_spot:enemy_main})` | `any_directive_committed` | events 含 `directive.committed` |
| 11 | L3d engagement_hold (3rd stance) | `所有人原地待命别动` | `engagement_constraint(stance=hold)` | `any_directive_committed` | events 含 `directive.committed` |

### L4 产能调整（3 case）

| # | name | 注入 | 预期 directive | verify | PASS 条件具体描述 |
|---|---|---|---|---|---|
| 12 | L4a production_override + unit_count_built_since | `下个 BG 出俩哨兵` | `production_override(unit_type=Sentry, count=2, done_when=unit_count_built_since(Sentry, op=">=", value=2))` | `production_overrides` | snapshot `production_overrides` 非空 或 events 含 `directive.committed` |
| 13 | L4b tech_override + tech_done | `先研闪烁` | `tech_override(upgrade_id=BlinkTech, done_when=tech_done(BlinkTech))` | `production_overrides` | 同上 |
| 14 | L4c expansion_override + expansion_count | `马上去开三矿` | `expansion_override(target_count=3, done_when=expansion_count(op=">=", value=3))` | `production_overrides` | 同上 |

---

## 覆盖矩阵

### 12 个 directive 类型

| Directive type | 覆盖 case | 备注 |
|---|---|---|
| `strategy_set` | #1 | ✅ |
| `strategy_cancel` | #2 | ✅ |
| `tactical_objective` (11 verb 抽 3 个) | #3 attack / #4 scout / #5 harass | ✅ 部分 |
| `engagement_constraint` (4 stance 抽 2 个) | #6 defend / #7 retreat | ✅ 部分 |
| `unit_claim` (persistent) | #8 | ✅ |
| `unit_claim` (ephemeral) | #9 | ✅ |
| `scout` | #10 | ✅ |
| `build_at` | — | ❌ LLM 限制（"11 点"被当 "o'clock" 字符串,LLM 不会算地图坐标;build_at 设计上给 PWA UI 玩家点击坐标用） |
| `move` | — | ❌ 不覆盖（LLM 容易把 move 解析成 `tactical_objective(attack)`，区分不开） |
| `unit_release` | — | ❌ 不覆盖（要先有 standing order 才能 release，单一 case e2e 复杂） |
| `production_override` | #12 | ✅ |
| `tech_override` | #13 | ✅ |
| `expansion_override` | #14 | ✅ |

**覆盖率：9/12 directive 类型有 case**（L3d 从 build_at 改为 engagement_constraint
hold stance 后 build_at 不再有 case；engagement_constraint 多覆盖一个 stance）。

### 10 个 done_when kind

| done_when kind | 覆盖 case |
|---|---|
| `unit_count_built_since` | #12 |
| `tech_done` | #13 |
| `expansion_count` | #14 |
| `target_destroyed` | #3（嵌在 `any_of` 里） |
| `own_army_size_ratio` | #3（同上） |
| `vision_acquired` | #4 |
| `enemy_killed_in_area` | #5 |
| `time_elapsed_since` | #7 |
| `any_of` | #3 |
| `all_of` | — ❌ LLM 罕用此复合，没必要硬造 case |

**覆盖率：9/10 done_when kind 有 case**。

---

## 命令行用法

```bash
# 全 14 case（默认 CheatMoney 难度,每 case 60s wall）
.venv/Scripts/python.exe scripts/e2e_4_directive_types.py --seconds 60

# 切其他难度（sc2.data.Difficulty 10 档：VeryEasy / Easy / Medium /
# MediumHard / Hard / Harder / VeryHard / CheatVision / CheatMoney / CheatInsane）
.venv/Scripts/python.exe scripts/e2e_4_directive_types.py --difficulty VeryEasy

# 单跑某个 layer / case
.venv/Scripts/python.exe scripts/e2e_4_directive_types.py --only L1
.venv/Scripts/python.exe scripts/e2e_4_directive_types.py --only "L3b"

# 自定 map / opening
.venv/Scripts/python.exe scripts/e2e_4_directive_types.py --map "Goldenaura LE"
.venv/Scripts/python.exe scripts/e2e_4_directive_types.py --initial-opening 4bg
```

**退出码**：全 case PASS = 0，任一 FAIL = 1。

**输出汇总例**（每个 case 一行结果）：

```
PASS L1a strategy_set       — stage=midgame id=iac_2base (snapshots=22, 32.1s)
PASS L4a production_override— events directive.committed+released (snapshots=25, 32.9s)
FAIL L3d build_at           — events 无 directive.committed (snapshots=18, 30.2s)
...
结果: 13/14 通过
```

---

## 防 SC2 卡死：两层 watchdog（避免测试卡死）

### 第一道防线：子进程内 `HangWatchdog`

`vibecraft.bot.watchdog.HangWatchdog` —— daemon thread，每 5s 检查 `bot.time`：

- bot.time wall-clock **30s** 不前进 → 判定 SC2 卡死
- psutil kill 所有 `SC2_x64.exe`（taskkill 兜底）
- `os._exit(87)` 强制子进程退出
- `_VibeCraftProtossBot.on_start` 启，`on_end` 关停
- `VIBECRAFT_DISABLE_HANG_WATCHDOG=1` 环境变量临时禁用（调试用）

### 第二道防线：父进程 `GameProcess` 兜底

子进程内 watchdog 自身也可能挂（thread 死锁 / `os._exit` 没执行 / 子进程
multiprocessing.Queue 卡）。父进程 `GameProcess.raw_events()` 维护
`last_msg_wall`，每条上行消息更新：

- wall-clock `_PARENT_WATCHDOG_STALE_S`（**120s**）无任何消息 + 子进程仍 alive
  → 强制 `_terminate_and_join()` + emit `crashed` 状态
- driver 读到 `sc2=crashed` 判该 case **FAIL**，自动继续下一个 case
- 阈值 120s（不是 30s/60s）：launching 阶段 SC2 启动可能 ~60s 无消息，宽
  余量；子进程 watchdog 30s 是更敏感的第一层

### sc2=ended 后 drain 2s

SC2 ended 事件到达父进程后，子进程最后几条 directive event 可能还在
multiprocessing.Queue 里没被消费。driver 看到 `sc2=ended` 后**再 drain 2s**
才退出 collect loop，避免漏抓 in-flight committed/released event 导致
verify 误判 FAIL。

---

## 用例选取的取舍说明

### 为什么 L1a 用 midgame `切叉球一波` 而不是 opening `切 4BG`

`切 4BG` 触发 `_check_strategy_obsolete` 的 **OpeningBuild 时机检测**：fast mode
下 inject 时 game 内时间已数分钟，bot 已造 RoboticsFacility 等 4bg 不需要的
科技建筑 → directive 被拦下进 `_pending_force_strategy`，**不进 board**，
snapshot 不会变化。

`切叉球一波` 走 midgame_stance（iac_2base），midgame 没有 obsolete 检测，直接
落 board。`叉球一波` 在 `strategies/protoss/iac_2base.yaml` aliases 里，LLM
解析稳定。

driver 同时把 `pending_force_strategy` 出现也算 PASS（LLM 识别成功，业务上
只是被多一道 confirm 拦截，等价成功）。

### 为什么 L2/L4 靠 events 兜底而不只看 snapshot

L2 attack 的 done_when 通常是 `any_of([target_destroyed natural,
own_army_size_ratio<0.3])`。当前 game state 中敌方 natural 可能根本不存在 →
`target_destroyed` 立即满足 → directive 进 board → task_monitor 同 tick
判 done → 从 `_in_flight` pop。snapshot 推送窗口（~2s）可能完全错过 in-flight
状态。

L4 production_override 的 done_when 是 `unit_count_built_since`。bot 可能已有
≥目标数（哨兵 ≥2 这种 commonly true）→ 立即满足，同样错过 snapshot。

verify 改成 "snapshot 字段非空 **OR** events 出现 `directive.committed`"。
后者证明 directive 真的进了 board + 触发 committed event，业务上等价 PASS。

### 为什么 L2 engagement / L3 scout / L3 build_at 用 `any_directive_committed`

snapshot 只暴露 4 个 list 字段（standing_orders / production_overrides /
active_tactics / pending_force_strategy）。`engagement_constraint` / `scout` /
`build_at` 等进 `_in_flight` 不暴露字段，**唯一可见信号是 events 流**的
`directive.committed`。

### 为什么用 CheatMoney 而不是 VeryEasy

VeryEasy 难度 bot 30s 内一波打赢 AI（实测 L1 case 32s 就 `sc2=ended Victory`），
所以 inject 注入和 verify 信号都来得及。但**对手 AI 几乎没行为** —— bot 周围
没敌情，`vision_acquired` / `enemy_killed_in_area` 这种 done_when 没机会触发
真路径，testing 价值低。

CheatMoney AI 资源白送，会有大量单位攻击，bot 有真实压力，directive 进 board
路径更接近生产场景。bot 可能 30-50s 死，但 inject 注入 + LLM 解析 +
`directive.committed` 通常 inject 后 ~5-15s 内完成，足够 verify。

---

## 加新测试 case 怎么改

`scripts/e2e_4_directive_types.py` 的 `CASES` 列表加一行：

```python
Case(
    name="L2f vision standalone",  # 描述性 name（--only 子串匹配）
    inject="去对方二矿探一下",
    inject_after=3,
    verify_field="active_tactics",  # 或 any_directive_committed
),
```

需要自定 verify 逻辑（比如要看 specific snapshot 字段变化）→ 在 `run_one_case`
加分支调专门函数（参考 `_verify_strategy_changed` / `_verify_strategy_cleared`）。

加新 verify_field 字符串 → 在 `run_one_case` 内的 if/elif 链加一支 + 写一个
`_verify_xxx` 函数。
