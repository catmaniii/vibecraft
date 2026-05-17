# ADR 0010: 四层指令架构（L1 宏观 / L2 战术 / L3 standing / L4 产能）

**日期**: 2026-05-17
**状态**: Accepted（决策固定，P1-P6 实施进行中）
**决策者**: catmaniii
**关联文档**: `docs/plans/2026-05-16-four-layer-commands-design.md`

---

## 背景

vibecraft M1 出口验证（v0.1.0a3）后，玩家指令系统现状只有 L1 strategy_set 一层
能贯通：手机说「切 4BG」→ LLM → directive → board → sharpy 切 recipe。但实际玩家
脑里的指令颗粒度有 4 层差别：

| Layer | 例子 | 持续性 |
|---|---|---|
| **L1 宏观策略** | 4bg / IAC / Skytoss | 整阶段（开局/中期/后期）|
| **L2 战术指令**（不指单位）| 进攻自然 / 探中场 / 凤凰骚扰对面 | 一次性（完成/失败/超时）|
| **L3 standing order**（单位/建筑持久行为）| 3 凤凰巡逻自然 / 2 追猎 hold 桥头 | 持久（玩家撤销 / 单位全死）|
| **L4 产能 override** | 下个 BG 出 2 哨兵 / 优先研闪烁 | 直到完成 |

现状只有 L1（完整）+ L3/L4 部分 directive type 存在但 layer state 缺。L2 完全没有。
M4 e2e 测试也暴露了 L3 standing order 的 LLM prompt ↔ schema mismatch（见后）。

**优先级金字塔规则**：4 层都没玩家指令 → bot 自决策；**有指令的"那块"被锁定，
bot 不能动该资源**；其它资源仍 bot 自主。

## 决策

### 1. 四层 directive 架构

每条 directive 明确属于 L1/L2/L3/L4 之一，由 type 推断（不是单独字段）：

| Layer | Directive type | 现状 |
|---|---|---|
| L1 | `STRATEGY_SET` / `STRATEGY_CANCEL` | ✓ 完整（v0.1.0a3 已 verify）|
| L2 | **`TACTICAL_OBJECTIVE`**（新）+ `ENGAGEMENT_CONSTRAINT` | P3 实施 |
| L3 | `UNIT_CLAIM` / `SCOUT` / `MOVE` / `BUILD_AT` / `UNIT_RELEASE` | directive 已有，layer state + UI 缺，P1 实施 |
| L4 | `PRODUCTION_OVERRIDE` / `TECH_OVERRIDE` / `EXPANSION_OVERRIDE` | directive 已有，layer state + UI 缺，P2 实施 |

### 2. TACTICAL_OBJECTIVE verb enum 固定 11 个

`attack / defend / scout / expand / harass / drop / vision / raze / retreat / regroup / split`

实施中发现不够再加（不预 over-engineer）。

### 3. UNIT_CLAIM 跟 standing order 的关系：同 directive 加 `persistent: bool`

```python
@dataclass
class UnitClaimPayload:
    selector: UnitSelector
    task: UnitTask
    persistent: bool = False   # ← 新
    # False = 一次性（任务完成自动归还 base bot）
    # True  = standing order（永久占用，等玩家显式 release）
```

`persistent=True` 的 directive 进 `Director.standing_orders` 列表（snapshot 透传给
PWA），`False` 走原有 `_in_flight` 流程。

### 4. bot 自决策 vs 玩家指令的 UI 显示语义：override 隐藏

bot 状态机仍照常推断（attack / defend / expanding / scouting / sustaining
stance；singular unit rationale；自动出兵决策），但 **UI 层 `v-if !override`**：
- 玩家有 L2 active tactics → `BotDecisionCard` 隐藏（L2 override 了 bot 的 stance）
- 玩家给某单位 L3 standing order → 该单位不出现在 bot 决策流的 unit rationale
- 玩家有 L4 production override → bot 的"自动出兵推断"那条不显示

玩家撤销 override 后 bot 决策项自动浮回。**不是两块独立显示**（避免玩家分不清
"这个 stance 是我下的还是 bot 想的"）。

### 5. Director 数据结构

```python
class Director:
    # L1 已有
    self.board: DirectiveBoard
    self._pending_recommendation: Recommendation | None
    self._pending_force_strategy: tuple[Directive, list[str]] | None
    # L2 新（P3）
    self.active_tactics: list[TacticalObjective]
    # L3 新（P1）
    self.standing_orders: list[StandingOrder]
    # L4 新（P2）
    self.production_overrides: list[ProductionOverride]
```

### 6. Snapshot 新字段

```python
{
  "strategy": { ... },                # L1 已有
  "active_tactics": [TacticalObjectiveView, ...],         # L2 新
  "standing_orders": [StandingOrderView, ...],            # L3 新
  "production_overrides": [ProductionOverrideView, ...],  # L4 新
  "tactics": BotTacticsView,          # bot 推断的 stance（已有，UI override 隐藏）
  ...
}
```

### 7. 新上行帧

- `revoke_directive {id}` —— 撤销 L2/L3/L4 中某条
- 已有的 `confirm_recommendation` / `confirm_force_strategy` 等保持

### 8. L2/L4 完成判定：LLM 输出 structured done_when + bot 内 task_monitor

详 `docs/plans/2026-05-17-task-completion-and-eventbus-design.md`。要点：

- **Director 加 `task_monitor`**，每 sharpy step（45ms）check in_flight directive
  完成状态。bot 内闭环，**不调 LLM**
- **LLM 在 IntentParser 同一次 call 输出 directive + done_when**（pydantic
  discriminated union，不是 DSL 字符串），LLM prompt 教 8 个 condition kind
- **8 个起步 kind**：`unit_count_built_since` / `tech_done` / `expansion_count`
  / `target_destroyed` / `own_army_size_ratio` / `vision_acquired` /
  `enemy_killed_in_area` / `time_elapsed_since`，加 `any_of` / `all_of` 复合
- **validate + retry 1 次**：pydantic 不通过 → 错误回灌 LLM 重写；仍不通过 →
  降级 EPHEMERAL + echo 告诉玩家
- **每个 done_when 必带 `timeout_s`** 兜底（默认 by verb，见 design doc §五 决策 5）
- **DSL 留给剧本 YAML 阵地不扩**（剧本 enter_when / abort_signals / reactions
  仍走 DSL；LLM 即时生成场景走 structured done_when）

### 9. EventBus（vibecraft 自建独立层）

详 `docs/plans/2026-05-17-task-completion-and-eventbus-design.md` §三。要点：

- **新文件 `src/vibecraft/bot/event_bus.py`**：`EventBus.subscribe(kind, handler,
  filter)` / `unsubscribe(sub_id)` / `publish(event)`
- `_VibeCraftProtossBot` override 11 个 python-sc2 lifecycle hook（`on_unit_created`
  / `on_unit_destroyed` / `on_upgrade_complete` / ...），每个内部 publish 到
  EventBus，然后 `await super()` 让 sharpy 自己的逻辑跑
- **task_monitor 是首批 subscriber**：`unit_count_built_since` 订阅 `UNIT_CREATED`、
  `enemy_killed_in_area` 订阅 `UNIT_DESTROYED` filter `owner=enemy`、`tech_done`
  订阅 `UPGRADE_COMPLETE`。其它 kind 走 game_state polling（vision / army_ratio /
  target_destroyed / expansion_count / time_elapsed）
- **不复用 sharpy `register_on_unit_destroyed_listener`**：sharpy 只覆盖 1 个 hook
  + 无 filter；vibecraft 自己造 EventBus 覆盖 11 个 hook + 支持 filter（by unit_type /
  area / owner），且不碰 `vendor/sharpy/`
- **handler 同步**（不 async），sharpy step 是同步调用栈
- **directive complete/expire 时统一 unsubscribe** 避免内存泄漏（attach_directive
  返回 sub_id list，TaskMonitor 跟踪）
- **EventBus 是 vibecraft 内部实现细节，不暴露给 LLM**：LLM 看到的还是 8 个 kind，
  EventBus 只是 kind 的"高效实现技术"

## 实施 phasing（P1-P6）

| Phase | 内容 | 工作量 | blocked by |
|---|---|---|---|
| **P0** | 本 ADR skeleton（含 §8 完成判定 + §9 EventBus）| 0.5d | — |
| **P1** | L3 Standing Orders：state + snapshot + UI + 撤销 + 修 schema mismatch + **EventBus skeleton + 11 hook publish + 单测**（done_when=`unit_count_built_since` 作为 reference 实现）| 1.5d | P0 |
| **P2** | L4 Production Overrides：state + snapshot + UI + L4 走 done_when（决策 D 同 L2，3 个 kind 复用 P1 EventBus）| 1d | P1 |
| **P3** | L2 Tactics：`TACTICAL_OBJECTIVE` + `ObjectiveExecutor` + **task_monitor 完整实现** + 8 个 kind dispatcher + validate retry + timeout | 3d | P1 + P2 |
| **P5** | sharpy plan 让位机制扩展（`reserved_tags` 通用化）+ directive completed → release `LLM_CONTROLLED` tags | 1d | P1 + P3 |
| **P4** | LLM prompt 重写：4 层例子 + 分类规则 + done_when few-shot + 8 kind schema | 0.5d | P1 + P2 + P3 |
| **P6** | 收尾：测试 + headless 验证（inject「切 4BG，打到对方自然 OR 损失 70% 撤」）+ 本 ADR 补 corner case | 0.5d | P5 + P4 |

**总 ~7.5d**（含 EventBus）。建议次序 P1 → P2 → P3 → P5 → P4 → P6。

总 ~7 天。建议次序 P1 → P2 → P3 → P5 → P4 → P6。

## 已知 schema mismatch（P1 实施时必修）

v0.1.0a3 M4 e2e 测 inject「那个农民守气矿别动」暴露 3 个 validation error：

```
3 validation errors for Directive
- payload.unit_claim.selector.count: Extra inputs not permitted (input_value=1)
- payload.unit_claim.task.primary_action.target.kind:
    Should be 'point'/'unit_tag'/'building_tag'/'named_spot'/'unit_type'
    (input='structure_type')
- payload.unit_claim.task.primary_action.target.structure_type:
    Extra inputs not permitted (Assimilator)
```

P1 实施时定义 standing 守建筑的 schema 形态（**倾向**：用 `target.kind='building_tag'`
+ `target.building_tag=<tag>`，selector 不需要 `count`；prompt 例子改成 schema 合法
字段）。原始 LLM 输出在 `logs/game_*/llm_calls/call_001.json`。

## 不在范围

- **元指令**（撤销 / 暂停 / 解释 / 回滚）—— UI 按钮，不进 directive
- **询问指令**（"矿够吗" / "敌方科技"）—— LLM 直接读 ParseContext 答，不进 directive
- **复合指令**（一句话多层）—— LLM 已经能拆，UI 分别归对应层显示

## Consequences

**优**:
- 玩家指令颗粒度清晰；override 语义可预测（bot 不抢 player 的话语权）
- standing order 列表 + 撤销机制 = 玩家不需要每秒重复发指令
- 4 层各自独立 state，directive 之间互不污染
- bot 自决策仍跑（兜底），玩家随时撤 override 后接管

**劣 / 风险**:
- Director 状态膨胀（4 个独立 list 要 GC / persistence semantic）
- LLM prompt 复杂度上升（要给 4 层分类规则）—— P4 重写
- snapshot 帧体积变大（4 个 list）；可能要按需 partial snapshot
- sharpy plan 让位机制要泛化（M4 当前只 reserve unit tag，要扩成 reserve unit
  selector + production / build slot）—— P5

## Implementation Notes

实施过程发现的 corner case 在此追加（边写边改）：

### P1 实施（2026-05-17）

- **schema gap 实际只缺 `persistent` 字段**：plan P1.1 写要修 `target.kind`
  加 `building_tag` / `named_spot` + `Selector` `extra="forbid"`，实测发现这两项
  已经在代码里。真正缺的只有 `UnitClaimPayload.persistent: bool = False`。Plan
  对 M4 e2e 输出的归因不够精细（M4 e2e 失败 actually 是 LLM prompt 教 LLM 输出
  schema 不支持的字段，而非 schema 真缺）。
- **类名跟 plan 不符**：plan 用 `UnitSelector / UnitTask / Target`，实际代码是
  `Selector / Task / TargetSpec`。`UnitClaimPayload` 名字对的。后续 plan 写作要
  先 grep 现有 schema 类名。
- **`board.revoke()` 对已 committed standing order 返回 `False`**：standing 路径
  下，directive 一秒后 board 把它从 pending 切到 committed overlays，此时
  `board.revoke()` 看不到它在 pending → 返回 `False`。`revoke_standing_order`
  方法目前从 `self.standing_orders` list 移除 + 调 board.revoke + push snapshot，
  即使 board.revoke 返回 False 也算成功。**P5 实现 sharpy 让位 + reserved_tags 通用化
  时**要让 `board.revoke` 支持 committed overlays 的 revocation。
- **`_dispatch_committed_to_facade` 跳过 standing order id**：directive 不进
  `_in_flight`，dispatch loop 找不到 id 静默跳过 —— standing order 在 P1 阶段
  **只进列表 + UI 可见，sharpy 端不真的 hold 单位**。**P5 实现** sharpy 让位机制
  时要让 dispatch 知道 standing_orders 也要处理。
- **`named_spot` 注册不完整**：LLM P1.6 e2e 输出 `named_spot="enemy_main_gas"`，
  但 sharpy zone manager 是否注册了这个 spot name 未实际验证。当前 named_spot 的
  到 sharpy game-state 坐标的解析 未实现。**P5 阶段**做 spot name registry
  （natural / main_ramp / enemy_main / enemy_natural / *_gas 等），含 fallback
  `closest_to(known_spot)`。
- **`_VibeCraftProtossBot` `__init__` 绕过 issue**：旧测试 `TestLLMControlledTags`
  用 `object.__new__` + `FakeKnowledgeBot.__init__` 绕过 `_VibeCraftProtossBot.__init__`，
  导致 `event_bus` / `_enemy_units_dict` 未初始化。`_publish_unit_destroyed` helper
  对两个 dict 用 `getattr(..., {})` 兜底，`on_unit_destroyed` 加 `hasattr` guard
  保持向后兼容。**未来重写测试**时应走 `_VibeCraftProtossBot()` 正常构造。
- **worktree 共享主 `.venv` editable install**：parallel subagent 在 worktree 跑
  时，`.venv` editable install 指向主仓 `src/`，pytest 跑 worktree 自己的代码需要
  `PYTHONPATH=worktree/src` 或 `pyproject.toml` 加 `pythonpath = ["src"]`。P1.2
  subagent 已加 pyproject 配置作为永久 workaround。
- **`uv run` trampoline canonicalize error**：本 session cherry-pick 后
  `uv run --no-sync pytest` / `mypy` 突然报 "uv trampoline failed to canonicalize
  script path"（但 `uv run --no-sync python -c '...'` OK）。原因未确认，可能跟
  worktree 切换 / uv cache 状态有关。**workaround**：直接调
  `.venv/Scripts/python.exe -m pytest` / `-m mypy` 绕开 uv shim。CI 不受影响
  （fresh venv 没这问题）。

### P2 实施（2026-05-17）

- **L4 payload 类已存在**：`ProductionOverridePayload` / `TechOverridePayload` /
  `ExpansionOverridePayload` 在 P0/M0b 阶段就加好了（含 `unit_type` / `count` /
  `upgrade_id` / `target_count` 等字段），P2 只是把它们 wire 到 `Director.production_overrides`
  list + snapshot + UI。**没新建 payload 类**。
- **`TestProductionDispatch::test_production_override` 旧测试需更新**：原 test
  期望 `facade.production_overrides` 被 dispatch 调用，但 P2 改了路由（L4 directive
  进 `production_overrides` list 不进 `_in_flight`），dispatch 移到 **P3 task_monitor**。
  test 改成验 `len(director.production_overrides) == 1 and facade.production_overrides == []`。
- **`revoke_directive` unified**：P1.4 引入 `revoke_directive` 上行帧时只调
  `Director.revoke_standing_order`，P2 加 `Director.revoke_directive(id, now)`
  统一方法（try standing → 再 try production），`ws.py` 和 `bot.py` 都改调
  unified method。前端不动（共用 P1.5 的 `revokeDirective(id)`）。
- **L4 directive display 格式**：`production_override → "出 N <unit_type 中文>"`
  / `tech_override → "研 <upgrade_id>"` / `expansion_override → "开 N 矿"`。
  alias table 把 `Sentry → 哨兵` 等翻译，未注册的回退英文。
- **subagent API 500 error**：P2.a parallel dispatch 跑到 ~95% commit 前断了
  （server 500，commit 没发出但 working tree 改动都在）。主 agent 手动 inspect
  worktree diff + 修一个 stale test + commit 完成。新协作模式 lesson：
  subagent 失败时 worktree 内 partial work 可恢复，**主 agent 直接 inspect +
  commit** 比 retry subagent 快。
- **CockpitView 没有 M3Placeholder 剩了**：P1.5 把 "Standing Orders" 占位换成
  StandingOrdersCard，资源条占位之前删了，剩下唯一一个 M3 import 已 orphan。
  P2.b 在 StandingOrdersCard 下方加 ProductionOverridesCard section（不替换占位）。
  未来 P3 可能加 TacticsCard 也是同样模式 —— 直接加 section 不依赖 M3。

### P3 实施（2026-05-17）

- **task_monitor 默认 game_state=None**：Director 没 `_bot` backref，`on_tick` 调
  `task_monitor.tick(now, game_state=None)`。time_elapsed_since(ref=directive_issued)
  不依赖 game_state 能 work，但 6 个 game-state-dependent checker 都 short-circuit
  返回 False。**P5 阶段**给 Director 加 bot backref，让所有 kind 在真实 SC2 跑通。
- **named_spot 在 P3 只支持白名单 {natural, third, main}**：target_destroyed /
  vision_acquired 等 area-based checker 用 `_resolve_named_spot`，白名单外返回
  None + log warning。**P5 阶段**做完整 named_spot registry（含 enemy_main_gas /
  third_gas / *_ramp 等）。
- **own_army_size_ratio initial snapshot 在 tick 首次拍**：attach_directive 时
  game_state 可能 None（Director 没 bot backref），所以 initial supply 不在 attach
  拍。改成 tick() 首次执行该 directive 时拍。**副作用**：如果 attach 到 first tick
  之间发生 army 损失（不太可能，gap < 1 tick），ratio 计算从损失后开始。
- **enemy_killed_in_area filter 假设 publisher 在 payload 加 area 字段**：
  task_monitor `EnemyKilledInArea` checker 的 EventBus filter 用 `event.payload["area"]`
  匹配。当前 `_publish_unit_destroyed` (P1.0b) 没填 `area` 字段（payload 只有
  unit_tag/unit_obj）。**P5/P6 时给 publisher 加 area inference**（position →
  named_spot 反查），或者 checker 改用 `event.position` + 自己算 area。
- **vision_acquired 每 tick 累计 1.0s**：注释里写 "1 step ≈ 1s"，实际 sharpy
  realtime step ≈ 0.045s。**bug**：counter 累加快 22x。**P5 修**：用 ts diff 而
  不是 step count。
- **LLM prompt validate retry 只对 done_when error**：P3.4 subagent 设计决定：
  非 done_when ValidationError（如 unit_type 缺）不 retry（保留现有
  test_invalid_directive_payload 行为）。理由：done_when 是 LLM 新学的字段、
  容易错；其它字段是历史稳定 schema，retry 只是浪费 token。
- **fallback strip done_when**：如果 retry 后仍 invalid，把 directive 的 done_when
  设 None（降级为 EPHEMERAL）+ result.notes 加 "[完成条件无效已降级为 EPHEMERAL]"。
  EPHEMERAL directive 走旧路径（一次执行后失效）。
- **顺手修 baseline RUF012**：`_UNIT_ZH` + 新加的 `_TACTICAL_VERB_ZH` 都改 ClassVar
  注解，clean baseline lint。
- **e2e P3.7 部分 verify**：headless_smoke inject "30 秒后撤" → LLM 生成
  `done_when={kind:"time_elapsed_since", seconds:30, ref:"directive_issued"}`
  完美正确 + directive.committed event 触发。**没看到 directive_completed
  event**（events.jsonl 空，headless_smoke 子进程 GameSession 没 wire sinks，
  pre-existing issue）。task_monitor.tick 触发 board.complete 实际效果要 P6
  全链路 verify（含 sinks fix + 真实 SC2 30s 等待）。

### P5 实施（2026-05-17）

- **NamedSpotRegistry 完整**：15 个已知 spot（natural / third / main / enemy_*
  / *_ramp / *_gas 变种）。`resolve(name, bot)` 走 sharpy zone_manager 或
  python-sc2 fallback。`closest_named_spot(point, bot)` 反向查找（publisher
  area inference 用）。
- **vision_acquired 改 ts diff**：从 step count 累加（22x bug）改成
  `_vision_first_visible_ts[id]` 记 spell 开始 wall-clock ts，每 tick 算
  `now - first_ts >= hold_seconds`。dispatcher signature 加 `now` 参数，所有 8
  checker 都加 `now: float = 0.0` 默认参数兼容。
- **Director._bot backref**：`__init__(bot=None)`（向后兼容），`on_start` 时
  `self.director._bot = self`（避免连锁修改 game_process / sharpy_adapter）。
  task_monitor.tick(game_state=bot) 让 6 个 game-state checker 真工作。
- **task_monitor._resolve_named_spot 优先 registry**：`isinstance(game_state.named_spots,
  NamedSpotRegistry)` 检查（防 MagicMock 误匹配），fallback P3 白名单兼容旧测试。
- **publisher area inference (UNIT_DESTROYED / UNIT_TOOK_DAMAGE)**：
  `_publish_unit_destroyed` 在 `payload["area"]` 填 `bot.named_spots
  .closest_named_spot(unit.position, bot)`（max_distance=15）。`MagicMock` 测试
  用 `del bot.named_spots` 显式删除自动属性走 None 路径。
- **standing order unit assign + sharpy 让位**：persistent unit_claim 进
  standing_orders 时 resolve selector + `bot.facade.set_unit_role(LLM_CONTROLLED)`
  + 记 `Director._standing_order_tags[id] = set(tags)`。revoke 时调
  `bot.facade.release_unit_role(tag)` 让 sharpy 重新接管。
- **board.revoke 支持 committed overlay**：从 pending miss → 检 overlays，
  删除 + `_release_claims_for` + DIRECTIVE_REVOKED event。配套 standing order
  revoke 链路完整。
- **target_destroyed P5 真路径**：原 P3 natural/third/main hardcoded 返回 False。
  P5 改：`target_kind` ∈ {natural, third, main} → 前缀 "enemy_" → registry
  resolve → `enemy_structures.closer_than(8, pos)`。target_kind="unit_type"
  现有路径保留。
- **flaky cross-test pollution**：本 session 多次见 `test_loads_real_strategies`
  / `test_transitions_of` / `test_not_triggered_when_visible_but_insufficient_duration`
  full suite 偶发 fail，单跑永远 PASS。推测 pytest fixture / module-level state
  污染。**P6 排查**：用 `pytest -p no:randomly` 或 `--forked` 隔离，或 grep
  module-level mutable state。
