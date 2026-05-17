# TASKS.md

任务拆解 + 进展。设计真理源在 `docs/plans/2026-05-14-vibecraft-design.md`；
代码现状在 `ARCHITECTURE.md`；约定在 `CLAUDE.md`；已发版历史在 `CHANGELOG.md`。

**新 session 起手先看本文档「当前状态」段** —— 它取代了原 CLAUDE.md HANDOFF 块的角色。

---

## 当前状态（最近更新：2026-05-17 晚，HEAD = e2e 收尾 commit，tag `v0.1.0a4`）

- **里程碑**：M2 出口已 tag `v0.1.0a4` + push。**M2 收尾补丁 + 4 类指令 e2e 验证完成**：
  - SC2 卡死 watchdog（方案 1 简化 W3：子进程内 30s stall → 自动 kill SC2 + 退码 87）
  - 删除 view directive 类型（PWA 小地图已有，LLM 文字控视野路径不再需要）
  - 4 类指令端到端测试驱动 `scripts/e2e_4_directive_types.py` 4/4 PASS（L1/L2/L3/L4）
  - 两个 P6 后 critical fix 已 commit + push：task_monitor `.time` 兜底 + RELEASED dispatch
- **本次 session 关键节点**：
  - M1 端到端真实 SC2 verify（M1.6 + M5 + M4 mock 全 PASS）
  - voicecraft → vibecraft 全局改名（包路径 + GitHub repo + 文档 + PDF）
  - four-layer 指令架构 plan + ADR 0010（skeleton + P1/P2/P3 corner case）+ 4 决策拍板
  - **M2 P1+P2+P3 完成**：parallel subagent (Sonnet) + 主 agent (Opus) review 模式
    跑通；EventBus + 11 hook wire + L3/L4/L2 全套（state/snapshot/UI/撤销）+
    task_monitor + done_when 8-kind dispatcher + LLM prompt 教 done_when + 3 个
    新 PWA card
- **最近 commit（M2 P5 系列，按时间倒序）**：
  - `d1f795d` P5.G: target_destroyed 真路径 + 6 checker mock-bot 单测
  - `308513d` P5.E: standing order unit assign + sharpy 让位 + board.revoke committed
  - `855bc24` P5.D: publisher area inference (UNIT_DESTROYED + area)
  - `1b203e8` P5.C: Director bot backref + named_spots field
  - `8d2d87b` P5.B: vision_acquired 改 ts diff 而非 step count
  - `f32039e` P5.A: NamedSpotRegistry 新建（15 spot + closest 反查）
  - `7c6a8de` P3 收尾 + `936dcc2 ... 50aac9b`（P3 系列 7 commit）
  - `bfcc3c2` P2 收尾 + `8d00070` + `20982a5`（P2 系列）
  - `6665886` P1 收尾 + `d3e1a96 ... 83fddad`（P1 系列 8 个 commit）
- **GitHub repo**：`catmaniii/vibecraft`，远端跟本地 sync。**`v0.1.0a4` 已 tag + push**（M2 完成）

---

## ✅ SC2 卡死检测：方案 1 W3 简化版已实施（2026-05-17）

**user 设过 goal「直到 4 类指令全过」**，自动化路线选择更简化的 W3（不要 W2 PWA
banner，因为人不在）：

- **信号**：A（bot.time wall-clock 30s 不前进）
- **处置**：W3（子进程内 watchdog 自动 kill SC2_x64.exe + 子进程 `os._exit(87)`）
- **位置**：子进程内 daemon thread（`vibecraft.bot.watchdog.HangWatchdog`）
- **wire**：`_VibeCraftProtossBot.on_start` 启 watchdog，`on_end` 关停。
  `VIBECRAFT_DISABLE_HANG_WATCHDOG=1` 临时关掉（调试用）
- **测试**：4 个单测覆盖 advance / stall / stop idempotent / get_bot_time exception；
  e2e 跑 4 个 case watchdog 没误报 trigger（bot 都正常打完赢了）

W2 PWA banner / 父进程兜底层 / 重启 SC2 状态恢复 → 留 M3+（人在驾驶舱时才有用）。
预设的子问题（重启后恢复 standing orders / 阈值 / banner 设计）也都留 M3。

---

## ✅ 4 类指令 e2e 4/4 PASS（2026-05-17 晚）

`scripts/e2e_4_directive_types.py` — 每 case 独立 SC2 子进程 + fast mode +
VeryEasy 对手 + watchdog 兜底，全过：

| Case | inject | 验证 |
|---|---|---|
| L1 strategy_set | `切叉球一波` | snapshot stage=midgame id=iac_2base |
| L2 tactical_objective | `进攻对方自然` | events directive.committed + released |
| L3 unit_claim (standing) | `探机巡逻自然别动` | snapshot standing_orders 非空（Probe patrol natural） |
| L4 production_override | `下个 BG 出俩哨兵` | events directive.committed + released |

verify 设计兼容 task_monitor 立即 done 的 case（L2/L4 directive 进 board 后立即
被判 done，snapshot 窗口错过 —— events 兜底）。

---

- **下一步**：
  1. **M3 开始**：完整驾驶舱（剩余 PWA UI 精修）+ L4 production override sharpy
     真出兵 wire（task_monitor 检测 done 后让 sharpy plan 知道）+ phase stepper
     精确进度
  2. **backlog**：3 个 flaky cross-test pollution（`test_loads_real_strategies` /
     `test_transitions_of` /
     `test_not_triggered_when_visible_but_insufficient_duration`）排查；用 pytest-forked
     或 grep module-level mutable state
  3. **M3 时考虑 W2 父进程兜底层 + PWA banner**：W3 已够自动化测试用，但玩家在
     驾驶舱时该走 W2 让玩家自己决定 + 重启后恢复 standing orders
- **P1/P2/P3 deferred items（P5 已全部完成 ✅）**：
  - ✅ sharpy 真让位 standing order 单位 → P5.E
  - ✅ Director bot backref 让 6 game-state checker 真工作 → P5.C
  - ✅ `named_spot` registry 完整 → P5.A
  - ✅ vision_acquired 用 ts diff → P5.B
  - ✅ enemy_killed_in_area publisher area inference → P5.D
  - ✅ `board.revoke()` 支持 committed overlay → P5.E
  - ✅ target_destroyed 真路径 → P5.G
- **剩余 deferred 到 P6**：
  - headless_smoke 子进程 GameSession sinks（events.jsonl 空，无法 verify
    task_monitor 真触发 directive_completed event）
  - cross-test pollution flaky tests（`test_loads_real_strategies` /
    `test_transitions_of` / `test_not_triggered_when_visible_but_insufficient_duration`
    full suite 偶发 fail，单跑永远 PASS）
  - L4 production override 真 dispatch sharpy 出兵（P3 task_monitor mark completed
    但 sharpy 端不主动响应；P6 决定要不要做 / 或留 M3）
- **本次 session 协作模式**（已 verified）：parallel subagent + 主 agent review
  - 主 session (Opus, 我) = orchestrator + reviewer + debugger
  - subagent (Sonnet, fresh context, worktree isolation) = implementer + tester
  - 分波 dispatch (Wave 1: P1.0a+P1.1 / Wave 2: P1.0b+P1.2 / 等)
  - 每波 cherry-pick 到 main + verify + push
  - 实测：8 个 sub-task ~1d 完成（含 review + commit + e2e smoke + ADR notes）
- **Hidden SC2 调研结论（2026-05-16）**：Windows + retail SC2 **不能真 headless**。
  D3D9 在 non-interactive desktop 立刻 Lost；ShowWindow 来不及第一帧前 hide；
  `-windowx -5000` 被 SC2 clamp。Linux native 永久卡 4.10。**项目设计接受 SC2 可见**；
  `headless_smoke.py` 用 `--fast` 跑 ~60s wall-clock + 自动 kill。
- **service 状态**：用户 Ctrl+C 了。重启 `.\scripts\start.ps1 -Token vibecraft-dev`
- **模型**：M2 写代码用 Sonnet，debug 用 Opus
- **环境就绪情况**：
  - `SC2PATH` = `D:\StarCraft II`（user-level 永久）；地图 DaybreakLE 已就位
  - `DEEPSEEK_API_KEY` 已设 user-level 永久。`start.ps1` 会自动从 user 级刷到进程 env
  - `.venv` = Python 3.11.14；sharpy + ares 全家桶在 `sc2` extra。⚠️ `uv sync` /
    `uv run` 不带 `--extra sc2` 会**卸载** ares —— 跑 pytest / smoke 用 `uv run --no-sync`
  - `.venv/.../ares_sc2_src.pth`（内容 `src`）—— 修 ares src-layout 打包 bug；
    `uv sync` 不碰它，但**重建 venv 后需重新创建**（runbook §1.3）

---

## 用户环境关键事实（不在代码里，问一遍要花时间）

- SC2 装在 `D:\StarCraft II\`（非默认路径），版本 `Base96883`。`SC2PATH`
  环境变量已永久设好（user-level）
- 地图 `D:\StarCraft II\Maps\DaybreakLE.SC2Map` 已就位
- `.venv` = Python 3.11.14（**不能用 3.12**，sc2-helper 无 cp312 wheel）
- 用户 GitHub：`catmaniii`，gh CLI 已认证。
  remote `origin = https://github.com/catmaniii/vibecraft`

---

## 版本号 / 里程碑映射（详 CHANGELOG.md）

| 版本 | 对应里程碑 |
|---|---|
| `0.1.0a1` | M0a + M0b 完成 |
| `0.1.0a2` | M0c 完成 |
| `0.1.0a3` | M1 完成（M1.6 切剧本端到端 verify ✅，已 tag）|
| `0.1.0a4` | M2 完成（four-layer P1-P6）|
| `0.1.0a5` | M3 完成（完整驾驶舱）|
| `0.1.0b1` | M4 完成（LLM 解析 > 90% 正确率）|
| `0.1.0` | M5 MVP RC（vs Hard AI 调优达标）|

---

## Roadmap（产品演进）

| 版本 | 内容 |
|---|---|
| **MVP (v0.1)** | 神族 3 剧本 vs 内置 AI |
| v0.5 | 神族 8+ 剧本 + Web Inspector |
| v1.0 | 神族完整 + 两笔电 PvP + 本地 LLM fallback |
| v1.5 | 加虫族 / 人族 |
| v2.0 | `compile_strategy` 玩家口述生成新剧本 |

---

## 里程碑拆解

### M0 / M1 历史 — ✅ done

详 CHANGELOG.md `0.1.0a1` / `0.1.0a2` / `0.1.0a3`。最关键结论：
- Hook C（Unit Role）方案成立：把单位置入 ares `CONTROL_GROUP_ONE` role → 所有
  ares Manager 都 skip 它。sharpy 迁移后用同样机制（M4 `LLM_CONTROLLED` 隔离）。
- M1 端到端 verify（2026-05-17 fast smoke）：force `1g_robo_immortal` → inject
  「切 4BG」→ SNAPSHOT 切 4bg + 两 event；inject「切叉球一波」→ phase_change +
  midgame slot 的 `attack_window` / `micro_doctrine` 完整透传。
- M4 e2e 发现 LLM prompt ↔ Pydantic schema gap（`structure_type` / `selector.count`），
  归入 M2 P1 范围（下面）。

### M2 four-layer 指令架构  🔄 进行中（P0 done，P1 待开）

总览：`docs/plans/2026-05-16-four-layer-commands-design.md` + `docs/adr/0010-four-layer-commands.md`。

#### P0 ADR 0010 skeleton  ✅ done（commit `68f1ec5`）

固化 4 个决策，P1-P6 实施基线。

#### P1 L3 Standing Orders + EventBus 基建  ✅ done（2026-05-17，~1d 实际）

详 `docs/plans/2026-05-17-task-completion-and-eventbus-design.md`。8 个 sub-task：

- [x] **P1.0** EventBus skeleton + 11 hook publish + 单测（~2h）
  - `src/vibecraft/bot/event_bus.py`：`EventBus` 类 + `Event` dataclass +
    `EventKind` enum（11 个 + sc2_alert）
  - `_VibeCraftProtossBot` override 11 个 lifecycle hook，每个 publish 后
    `await super()`
  - 单测：subscribe/publish/filter/unsubscribe 基本路径
- [x] **P1.1** schema 改 + 修 M4 e2e schema gap（~1h）
  - `directives/models.py`：`UnitClaimPayload` 加 `persistent: bool = False`
  - 修 `target.kind` 接受 `'building_tag'` / `'named_spot'`，去 `selector.count`
  - LLM `prompt.py` 对应例子改用 schema 合法字段
- [x] **P1.2** Director state（~2h）
  - `self.standing_orders: list[StandingOrder]`
  - `_submit_directives` 按 `persistent` 路由（true 进 standing，false 进 in_flight）
- [x] **P1.3** Snapshot 加 `standing_orders` 字段 + 单测（~1h）
- [x] **P1.4** `revoke_directive {id}` 上行帧 + ws handler（~30min）
- [x] **P1.5** PWA `StandingOrdersCard.vue` + CockpitView 装载（~2h）
  - 替换 `M3Placeholder` "Standing Orders" 占位
  - 每条 standing order + × 撤销按钮
- [x] **P1.6** e2e smoke verify（~30min）
  - 重跑 inject「那个农民守气矿别动」case，验 schema 不再 fail + 进 standing list
- [x] **P1.7** 更新 ADR 0010 Implementation Notes corner case（~10min）

#### P2 L4 Production Overrides  ✅ done（2026-05-17，~0.5d 实际，2 subagent parallel）

3 个 sub-task：
- [x] **P2.a** 后端：`Director.production_overrides` list + L4 directive 路由 +
  `revoke_directive` unified method + snapshot + display formatter + 8 个新单测
- [x] **P2.b** 前端：`ProductionOverrideView` type + `ProductionOverridesCard.vue` +
  `CockpitView` 加 section + `useWs` 透传 + 3 个 vitest + PWA build
- [x] **P2.c** e2e smoke verify：inject "下个BG出2哨兵" → LLM 解析 OK + 2 个
  `directive.committed` event 触发（schema 路径通）

实施过程发现 1 个 stale test (`TestProductionDispatch::test_production_override`，
原期望 facade dispatch，P2 改路由后改成验 list 进 + facade 空)。

**dispatch 到 sharpy 实际生产 wire 留 P3 task_monitor**。

#### P3 L2 Tactics + task_monitor + done_when  ✅ done（2026-05-17，~1.5h wall-clock，3 波 parallel）

8 个 sub-task：
- [x] **P3.0** TaskMonitor skeleton + 2 reference checker (time_elapsed_since /
  unit_count_built_since via EventBus) + 22 个单测
- [x] **P3.1** TACTICAL_OBJECTIVE directive type + DoneWhen 10-kind discriminated
  union (8 kind + any_of/all_of) + _PayloadBase 加 done_when/timeout_s + 30 个单测
- [x] **P3.2** wire task_monitor 进 Director.on_tick + board.complete 方法 +
  bot.py 构造时传 event_bus + L2 路由进 _in_flight（fallback） + 5 个单测
- [x] **P3.3** 6 个 game-state checker（expansion_count / tech_done /
  target_destroyed / own_army_size_ratio / vision_acquired / enemy_killed_in_area）+
  any_of/all_of 复合 + retrofit attach_directive 接受 pydantic + 28 个单测
- [x] **P3.4** LLM prompt 加 11 verb + 8 kind 白名单 + 6 个 done_when few-shot +
  IntentParser validate retry (只对 done_when error) + fallback strip done_when +
  10 个单测
- [x] **P3.5** snapshot 加 active_tactics 字段 + _tactical_view formatter +
  web/types.ts TacticalObjectiveView + 5 个单测 + 顺手修 _UNIT_ZH/_TACTICAL_VERB_ZH
  baseline RUF012
- [x] **P3.6** PWA TacticsCard.vue + CockpitView section + 3 个 vitest + PWA build
- [x] **P3.7** e2e smoke verify：inject "30秒后撤" → LLM 完美生成
  `done_when={kind:"time_elapsed_since", seconds:30, ref:"directive_issued"}`
  + directive.committed event 触发。`directive_completed` event 实际触发要 P6
  全链路 verify（含 events.jsonl sinks 修复）。

P3 总计 527 后端 + 50 前端 passed（+109 since P2 done）。

#### P5 sharpy 让位 + named_spot + 6 deferred items  ✅ done（2026-05-17，~1h wall-clock，4 波）

7 个 sub-task：
- [x] **P5.A** NamedSpotRegistry 新建（15 spot + closest_named_spot 反查）+ 22 单测
- [x] **P5.B** vision_acquired 改 ts diff 而非 step count（dispatcher signature
  加 now 参数）+ 6 vision tests
- [x] **P5.C** Director bot backref + bot.named_spots field + task_monitor 用
  registry 解析 spot + 9 单测
- [x] **P5.D** publisher area inference (UNIT_DESTROYED + UNIT_TOOK_DAMAGE 加
  area 字段，用 closest_named_spot 反查) + 8 单测
- [x] **P5.E** standing order unit assign + sharpy 让位（_assign/_release standing
  order units + facade.release_unit_role）+ board.revoke 支持 committed overlay +
  11 单测
- [x] **P5.F** （含在 P5.E）：board.revoke committed overlay
- [x] **P5.G** target_destroyed 真路径（enemy_natural/third/main via registry）+
  6 checker 真实 mock-bot 测试 + 18 单测

P5 总计 591 后端 passed（+64 since P3 done）。1 个 P3 deferred 还在（L4 sharpy
真出兵）→ M3 范围。

#### P4 LLM prompt 精修  ✅ done（2026-05-17，~10min，1 subagent）

- [x] **P4** prompt.py 追加 "4 层指令分类" 段（L1/L2/L3/L4 规则）+ 5 个边界 case
  few-shot（复合 L1+L3 / L2 engagement+done / 撤销 / 含糊 ambiguous /
  unit_count_hint 教 LLM 不写 selector.count）+ 3 个新单测

#### P6 收尾 + e2e verify  ✅ done（2026-05-17，~30min）

- [x] **P6.A** Director 接 session → directives.jsonl 生命周期落盘
  (submitted/committed/released/rejected/revoked)
- [x] **P6.B** JsonlSink line-buffered fix（修 jsonl 子进程空 bug）+ e2e verify
- [x] **P6.C** ADR Implementation Notes 加 P4/P6 corner case + TASKS.md 收尾

**P6 deferred 到 backlog（不阻塞 v0.1.0a4 tag）**：
- 3 个 flaky cross-test pollution 排查
- 真实 SC2 长游戏 verify `directive_completed` event（fast mode bot 死太快）
- L4 production override sharpy 真出兵 wire → M3 范围

#### ~~P3 L2 Tactics~~ ✅ done（见上一段）

#### P5 sharpy plan 让位机制扩展  ⏸️ blocked by P1+P3（~1d）

`reserved_tags` 通用化：从只 reserve unit tag 扩成 reserve unit selector +
production / build slot。

#### P4 LLM prompt 重写  ⏸️ blocked by P1+P2+P3（~0.5d）

4 层例子 + 分类规则。

#### P6 收尾  ⏸️ blocked by P5+P4（~0.5d）

测试 + headless 验证 + ADR 0010 Implementation Notes 补 corner case。

### M3 / M4 / M5

设计文档 §13 已有粗轮廓，到时候再展开拆。M3 完整驾驶舱（剩余 M3Placeholder /
phase stepper 精确进度 / 撤销机制）部分被 M2 P1/P2 接管。

---

## 历史 / 已废决策

- 项目曾叫 `speech_craft` / `SpeechCraft`，后改 `VoiceCraft`，2026-05-16 又改
  `VibeCraft`（因为不再绑死语音输入）。备选 `Adjutant` 被用户否决（太 geek）。
- ares-sc2 → sharpy-sc2 全框架迁移（2026-05-16，ADR 0009）。原因：vibecraft 4 剧本
  在 ares 框架几乎无对应 Manager；sharpy dummy 直接覆盖 4 剧本中的 3 个。
- Windows + retail SC2 不能真 headless（2026-05-16 调研）；接受 SC2 可见，设计本来
  就是 PC 当显示器。
