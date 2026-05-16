# 四层指令架构设计

> 创建于 2026-05-16,跟用户 brainstorm 后定稿。等待用户拍若干关键决策后挨个分期实施。

## 一、核心思路

玩家对 bot 的指令按粒度分 4 层。优先级金字塔:**4 层都没玩家指令 → bot 自决策;有指令的"那块"被锁定,bot 不能动该资源,其它资源仍自主**。

| Layer | 含义 | 例子 | 持续性 |
|---|---|---|---|
| L1 宏观策略 | 剧本级,持续整阶段 | 4bg / IAC / Skytoss | 阶段(开局/中期/后期) |
| L2 战术指令 | 阶段性 objective,**不指单位** | 进攻自然 / 探中场 / 凤凰骚扰对面 | 一次性(完成/失败/超时) |
| L3 standing order | 特定单位/建筑持久行为 | 3 凤凰巡逻自然 / 2 追猎 hold 桥头 | 持久(玩家撤销 / 单位全死) |
| L4 产能 override | 改造的东西 | 下个 BG 出 2 哨兵 / 优先研闪烁 | 直到完成 |

## 二、数据模型

### Directive type → Layer 映射

| Layer | Directive type | 现状 |
|---|---|---|
| L1 | `STRATEGY_SET` / `STRATEGY_CANCEL` | ✓ 完整 |
| L2 | **`TACTICAL_OBJECTIVE`**(新)+ `ENGAGEMENT_CONSTRAINT` | 缺新 type |
| L3 | `UNIT_CLAIM` / `SCOUT` / `MOVE` / `BUILD_AT` / `UNIT_RELEASE` | directive 有,layer state 缺 |
| L4 | `PRODUCTION_OVERRIDE` / `TECH_OVERRIDE` / `EXPANSION_OVERRIDE` | directive 有,layer state 缺 |

### Director 新数据结构

```python
class Director:
    # L1 已有
    self.board: DirectiveBoard
    self._pending_recommendation: Recommendation | None
    self._pending_force_strategy: tuple[Directive, list[str]] | None
    # L2 新增
    self.active_tactics: list[TacticalObjective]
    # L3 新增
    self.standing_orders: list[StandingOrder]
    # L4 新增
    self.production_overrides: list[ProductionOverride]
```

### L2 TacticalObjective

```python
@dataclass
class TacticalObjective:
    id: str
    verb: Literal["attack","defend","scout","expand","harass",
                  "drop","vision","raze","retreat","regroup","split"]
    target_area: Point2 | str | None     # 坐标 / "enemy_natural" / None=bot 选
    unit_count_hint: int | None          # None=bot 决定
    unit_type_hint: list[str] | None     # 限定兵种,None=bot 决定
    issued_at: float
    status: Literal["pending","executing","completed","failed","cancelled"]
    assigned_unit_tags: set[int]         # bot 实际分配的单位
    eta: float | None
```

### L3 StandingOrder

```python
@dataclass
class StandingOrder:
    id: str
    summary: str                         # "3 凤凰 → 巡逻自然分矿"
    directive_type: DirectiveType
    payload: Any                         # 原 directive payload
    unit_tags: set[int]                  # 锁定单位 tag(全死 → auto-remove)
    issued_at: float
    persistent: bool = True
```

### L4 ProductionOverride

```python
@dataclass
class ProductionOverride:
    id: str
    summary: str                         # "下个 BG 出 2 哨兵"
    directive_type: DirectiveType
    payload: Any
    progress: tuple[int, int]            # (done, target)
    issued_at: float
```

## 三、bot 端组件

### L2 ObjectiveExecutor(新 ActBase)

每 step:
1. 看 `Director.active_tactics`
2. 对每个 `status=pending`:从 idle/free army(非 reserved)选合适单位(数量+类型) → `set Reserved` + move/attack/scout 命令 → `status=executing`
3. 对 `status=executing`:检测完成(到达 / 击杀 / 单位全死 / 超时) → `status=completed|failed`
4. completed/failed/cancelled 自动从列表移除

### L4 plan 让位机制(扩展现有 `_llm_controlled_tags`)

复用 sharpy `UnitTask.Reserved`:任何被 L2 / L3 占用的 unit tag 都 set Reserved → sharpy 的 GroupCombatManager / DistributeWorkers 看 free_units 时自动过滤 → bot 自决策只用没被锁的单位。

## 四、Snapshot 新结构

```typescript
{
  strategy: { current_stage, opening, midgame, lategame,
              recommendation?, pending_force_strategy? },
  bot_decision: { stance, label, reason },           // 原 tactics 字段重命名
  active_tactics: [{id, verb, summary, units, eta, status}],
  standing_orders: [{id, summary, units_count}],
  production_overrides: [{id, summary, progress}],
  recent_commands: ...
}
```

## 五、PWA 布局

```
┌─ 资源条占位 ─────────────────────────────────┐
├─────────────────┬──────────────────────────┤
│ 小地图          │ 当前宏观策略 (L1)        │
│ 触摸板          │ + 推荐/硬转 (L1)         │
├─────────────────┴──────────────────────────┤
│ 🎯 bot 当前决策 BotDecisionCard (只读)    │
│ ⚔️ Active Tactics (L2) [每条 × 撤销]      │  ← 新
│ 🛡️ Standing Orders (L3) [每条 × 撤销]     │  ← 新
│ ⚙️ Production Overrides (L4) [progress] │  ← 新
│ 📜 Bot Decision Feed                       │
│ 📝 最近指令                                │
└────────────────────────────────────────────┘
固定底部:发号施令输入框
```

## 六、上行帧

新增:
- `revoke_directive {id}` — 撤销 L2/L3/L4 中某条
- 已有的 `confirm_recommendation` / `confirm_force_strategy` 等保持

## 七、实施分期

| Phase | 内容 | 工作量 |
|---|---|---|
| **P1** | L3 Standing Orders:state + snapshot + UI + 撤销 | ~1d |
| **P2** | L4 Production Overrides:state + snapshot + UI | ~1d |
| **P3** | L2 Tactics:`TACTICAL_OBJECTIVE` + `ObjectiveExecutor` 框架 | ~3d |
| **P4** | LLM prompt 重写:4 层例子 + 分类规则 | ~0.5d |
| **P5** | sharpy plan 让位机制扩展(reserved_tags 通用化) | ~1d |
| **P6** | 收尾:测试 + headless 验证 + ADR 0010 | ~0.5d |

**总 ~7 天**。建议次序 P1 → P2 → P3 → P5 → P4 → P6。

## 八、待用户拍的决策

1. **TACTICAL_OBJECTIVE verb enum**:列了 11 个(attack/defend/scout/expand/harass/drop/vision/raze/retreat/regroup/split),够吗?
2. **bot 自决策 vs 玩家战术 UI 关系**:`BotDecisionCard` 显示 bot 推断的 stance,L2 active_tactics 显示玩家命令 — 倾向**两块独立**,玩家一眼分清"bot 想干嘛 vs 我让它干嘛"
3. **`UNIT_CLAIM` 跟 standing 的关系**:同一 directive 加 `persistent: bool` 字段(`true` 进 standing 列表)
4. **要 ADR**:`docs/adr/0010-four-layer-commands.md` 等 P6 写

## 九、不在范围

- **元指令**(撤销/暂停/解释/回滚) — UI 按钮,不进 directive
- **询问指令**("矿够吗""敌方科技") — LLM 直接读 ParseContext 答,不进 directive
- **复合指令**(一句话多层) — LLM 已经能拆,UI 分别归对应层显示
