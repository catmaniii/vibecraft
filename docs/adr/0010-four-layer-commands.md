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

## 实施 phasing（P1-P6）

| Phase | 内容 | 工作量 | blocked by |
|---|---|---|---|
| **P0** | 本 ADR skeleton | 0.5d | — |
| **P1** | L3 Standing Orders：state + snapshot + UI + 撤销 + 修 schema mismatch（见后）| 1d | P0 |
| **P2** | L4 Production Overrides：state + snapshot + UI | 1d | P0 |
| **P3** | L2 Tactics：`TACTICAL_OBJECTIVE` + `ObjectiveExecutor` 框架 | 3d | P0 |
| **P5** | sharpy plan 让位机制扩展（reserved_tags 通用化）| 1d | P1 + P3 |
| **P4** | LLM prompt 重写：4 层例子 + 分类规则 | 0.5d | P1 + P2 + P3 |
| **P6** | 收尾：测试 + headless 验证 + 本 ADR 补 corner case | 0.5d | P5 + P4 |

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

- TBD（P1 开始后补）
