# ADR 0011: L2 战术执行器 + L4 done_when 扩词表 + 统一命令卡片

**日期**: 2026-05-17
**状态**: Accepted（m4-l2-l4-executor 分支已实施，726 unit tests pass）
**决策者**: catmaniii
**关联文档**: `docs/plans/2026-05-17-l2-l4-executor-and-cards-design.md`

---

## 背景

ADR 0010 定义了四层指令架构，P3（L2 战术指令）实施后发现 `tactical_objective` 走到
`_exec_tactical_objective` 时是死路 —— 方法存在但没有实际执行路径，bot 行为没有变化。
同时发现：

1. **L2 dead-end**：`tactical_objective` directive commit 后，facade 端无 override
   flag，sharpy plan 系统不感知，bot 照跑默认逻辑。
2. **L4 done_when 词表太窄**：最初 8 个 kind 没覆盖建筑数量 / 资源门槛 / 战场感知，
   LLM 做「补 8 BG」/ 「进攻自然」类指令时无法表达完成条件。
3. **命令卡片碎片化**：StandingOrdersCard / TacticsCard / ProductionOverridesCard
   三张独立卡片，UI 层重复（各自有空态 / 标题 / 样式 / revoke 事件），但 snapshot
   已有统一的 `command_cards` 数组，没有对应的 UI 消费者。
4. **revoke 事件缺 `type:"event"` 字段**：`revoke_tactical` / `revoke_strategy` 推出
   的 WS 帧没有 `type:"event"`，PWA `switch (frame.type)` 走 default 静默丢弃。

## 决策

### 1. A 类 L2：facade override flag 路径（全军指令）

```
attack / defend / retreat / hold / vision
```

走 `set_combat_intent_override(verb)` + `set_attack_target_override(point)`（新增 2 个
facade 方法），并 fork `PlanZoneAttack` 为 `VibeCraftZoneAttack`，在 `_should_attack` /
`_get_target` 里响应 override flag。bot 的 6 个 sharpy plan 全部换用 `VibeCraftZoneAttack`。

**设计决定**：A 类 `done_when=None`（由玩家在 PWA 点 × 解除）。LLM prompt 明确禁止 A 类
设 `done_when`，否则 task_monitor 立即判 done。

### 2. B 类 L2：TacticalSquad 路径（特定单位 squad）

```
harass / scout
```

`Director` 用 `TacticalSquad` 数据类追踪分组 unit tags，每 sharpy step 在
`execute_tactics_step` 里调 sharpy `GroupCombatManager`（`cm.add_units` + `cm.execute`）。
revoke 时 `facade.release_unit_role` 归还 sharpy。B 类必须有 `done_when` + `unit_count_hint`，
LLM 输出缺省 → ambiguous 二次确认。

### 3. L4 done_when 扩 7 个新 kind

| kind | 用途 |
|---|---|
| `structure_count` | 「补 8 BG」→ 当前 + pending >= N |
| `minerals` | 「矿 600 再动」|
| `own_unit_count` | 「8 追猎出来了」|
| `enemy_unit_count` | 敌空中 < N |
| `enemy_structure_count` | 敌防御建筑 <= N |
| `enemy_visible_in_area` | 「看到敌人了」|
| `base_taken` | 「占了第三矿」|

和 `structure_override` directive type（新 L4 type，用于「补 N 建筑」指令）。

### 4. 统一命令卡片（`command_cards` → `CommandCardStack`）

`build_snapshot` 构建一个统一的 `command_cards: list[CommandCardView]` 数组（L1/L2/L3/L4
全部），PWA 侧单一 `CommandCardStack` 组件消费，替换原来 3 张独立卡片。每张卡片含 `id` /
`layer` / `type` / `display` / `status` / `revokable`，revoke 点击统一发 `revoke_directive`
帧（已有）。

### 5. L1 cancel 统一走 board.submit

原来 `STRATEGY_CANCEL` 走 `_dispatch_cancel` 旁路方法，不经过 board 的 delay / 优先级。
改成 `board.submit(directive)` 后 `_apply_to_facade` 分发，和其他 directive 路径对齐。

## 关键约束

- **A 类 done_when=None 硬规则**：LLM prompt + task_monitor 都强制。玩家点 × 才解除，
  不靠自动完成。
- **facade 2 方法**：`set_attack_target_override(point | None)` + `set_combat_intent_override(str | None)`
  进 `Sc2Facade` Protocol + `FakeFacade`（单测 mock 路径完整）。
- **`_cached_combat_manager` 不缓存 None**：首次 lookup 失败（bot 未初始化）不写入
  `_cm_cache`，下次 step 重试。
- **`_current_l2_global_directive` revoke 时同步清**：A 类 revoke 清 `_current_l2_global_id`
  的同时清 `_current_l2_global_directive`，防止悬空 Directive 引用。
- **revoke event 必须带 `type:"event"`**：`revoke_tactical` / `revoke_strategy` 的
  `_push_event` 调用加 `"type": "event"` key，PWA dispatch 才能识别。

## 实施 phasing（P0a-P0k）

| Phase | 内容 | 状态 |
|---|---|---|
| **P0a** | facade Protocol + FakeFacade 2 方法 + VibeCraftZoneAttack fork + 6 plan 替换 | ✅ done |
| **P0b** | TacticalSquad + execute_tactics_step | ✅ done |
| **P0c** | L1 cancel 统一走 board.submit | ✅ done |
| **P0d** | 7 新 DoneWhen kind + DONE_CHECKERS 注册 | ✅ done |
| **P0e** | STRUCTURE_OVERRIDE directive type + prereq table + exec | ✅ done |
| **P0f** | snapshot 统一 command_cards build | ✅ done |
| **P0g** | director.revoke_directive 扩 L2/L1 | ✅ done |
| **P0h** | PWA：CommandCardView type + CommandCard/Stack 组件 + CockpitView 接入 | ✅ done |
| **P0i** | LLM prompt 加 structure_override + 7 done_when + A/B 规则 + 5 few-shot | ✅ done |
| **P0j** | llm_eval 6 个新 case | ✅ done |
| **P0k** | e2e_4 driver + verify_log + O 系列 case | ✅ done |

## 实施过程 corner case

- **conftest `_BOT_PREFIXES` 漏 `"sc2"`**：`fake_sharpy_bot_env` teardown 只清 sharpy /
  vibecraft 前缀，`FakeUnitTypeId`（不支持 subscript）留在 `sc2.ids.unit_typeid`，跨文件
  污染 `test_structure_override_exec`（全 suite 6 fail，单文件跑 0 fail）。修：`_BOT_PREFIXES`
  加 `"sc2"`，`test_sharpy_adapter.py` 的 `_clean_sharpy_modules` 也加 `"sc2"`。
- **revoke event `type:"event"` 缺失**：运行时 PWA 会静默丢弃 revoke 后的 event 帧
  （`switch (frame.type)` default case），snapshot 不 push，卡片不消失。修：两处
  `_push_event` 调用加 `"type": "event"` 字段。
- **`uv run` trampoline 问题**：本 session 所有 pytest / mypy 调用改走
  `.venv\Scripts\python.exe -m pytest`，CI 不受影响（见 ADR 0010 §P5 实施）。

## 不在范围

- B 类 squad `GroupCombatManager` 真实路径（需 SC2 客户端 + sharpy 真 knowledge）—— unit test 只 mock cm。
- `structure_override` sharpy 真出建筑（需 facade.set_build 扩）—— M3 阶段做。
- vision_acquired tick 累计精度（step vs ts）—— 见 ADR 0010 §P5 backlog。
- `named_spot` 完整 registry —— ADR 0010 §P5 backlog。

## Consequences

**优**：
- 「进攻自然」/ 「守家」/ 「撤退」类 A 系指令真正影响 bot sharpy plan 行为。
- 「补 8 BG」/ 「补 cannon」等建筑数量指令走 structure_override + done_when 自动结束。
- 命令卡片统一：UI 只需一个 CommandCardStack，后端 `command_cards` 一个数组。
- revoke 链路完整：PWA 点 × → WS → Director → facade clear → snapshot 更新 → PWA 卡片消失。

**劣 / 风险**：
- A 类全军 override 粒度粗（无法同时「守家」一组 + 「骚扰」另一组，两条 A 类指令互斥）。
  → 后续若需 per-group A 类，需引入多 `_current_l2_global` slot（ADR 范围外）。
- `VibeCraftZoneAttack` fork 长期需要跟上游 `PlanZoneAttack` 同步（sharpy 版本升级时）。
