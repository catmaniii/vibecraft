# 两层宏观策略架构设计（Two-Tier Strategy）

> Status: draft，等用户 review。  
> Author: claude（Opus 4.7） + catmaniii  
> Date: 2026-05-19  
> Related: replaces 3-tier (opening / midgame / lategame) model from §4.2 of `2026-05-14-vibecraft-design.md`

---

## 1. 动机

### 1.1 观察到的 bug

玩家手动 cancel 宏观策略 / LLM parse 失败 / 当前 strategy 自然完成后，bot 的 `active_recipe` 进入空状态 → IfElse 路由树落到 `sustain` plan → sustain 只做"补人口 + 守家 + 兵种产出 1 路"，**不再造兵 / 不扩张 / 不骚扰 / 不出门**。玩家观感：AI 摆烂了。

根因：当前架构允许"无宏观策略"状态存在，但 fallback (`sustain`) 实际上不足以撑起一局游戏的运营/战斗循环。

### 1.2 期望行为

**不变量：任意时刻 bot 必须有一个宏观策略在跑**（never null / never sustain）。开局结束后必须立刻进入持续运营策略，玩家可在持续策略间切换但不可"取消到空"。

---

## 2. 核心架构变更

### 2.1 从 3 层简化为 2 层

| 旧（3 层）| 新（2 层）|
|---|---|
| `opening_build` | `opening_build`（不变）|
| `midgame_stance` | **删** —— 内容下沉到 opening 或上升到 persistent |
| `lategame_doctrine` | `persistent_doctrine`（改名）|

**原 midgame_stance 重分类**：

| 现 yaml | 新 kind | 理由 |
|---|---|---|
| `iac_2base` | `opening_build` | 本质是 6:15 timing all-in build order |
| `dt_drop_iac` | `opening_build` | 本质是 ~8:00 timing all-in build order |
| `mutalisk_harass` | `opening_build` | 本质是中期 all-in 转换 |
| `roach_hydra` | `opening_build` | 本质是早期 roach 推进 |
| `bio_stim` | `opening_build` | 本质是 stim timing push |
| `two_base_tanks` | `opening_build` | 本质是 2 base tank timing |

**原 lategame_doctrine 改 persistent**：

| 现 yaml | 新 id | 新 kind |
|---|---|---|
| `skytoss` | `persistent_skytoss` | `persistent_doctrine` |
| `brood_corruptor` | `persistent_brood_corruptor` | `persistent_doctrine` |
| `bc_late` | `persistent_skyterran` | `persistent_doctrine` |

### 2.2 完整 18 个 persistent_doctrine（新增 / 重命名）

**神族 6**（仅 1 个 persistent_skytoss 是迁移过来的，5 个新建）：

| id | 中文名 | 核心组合 | counters_against | weak_against |
|---|---|---|---|---|
| `persistent_skytoss` | 天空神族 | 12 航母 + 3 风暴战舰 + HT + 母舰 | `mass_ground` / `zerg_roach_hydra` / `terran_bio` | `mass_corruptor` / `zerg_viper` / `mass_viking` |
| `persistent_ground_mech` | 地面机械流 | 6 不朽 + 4 巨像 + Sentry + 叉子 | `zerg_roach_hydra` / `zerg_ling_bane` / `terran_bio` | `mass_air` / `protoss_storm` / `mass_viking` |
| `persistent_iac_macro` | 叉光不朽运营 | 16 叉 + 4 不朽 + 6 白球 + HT | `terran_bio` / `zerg_ling_bane` | `terran_mech` / `mass_air` / `terran_mech_tank` |
| `persistent_blink_harass` | 闪追风筝双线 | 30 闪追 + Observer + 棱镜 | `terran_bio_no_stim` / `zerg_mutalisk` / `zerg_roach_hydra` | `terran_mech_tank` / `mass_marauder` / `mass_corruptor` |
| `persistent_phoenix_storm` | 凤凰风暴双线 | 12 凤凰 + 6 风暴战舰 + HT + Sentry | `mass_air` / `worker_harass` / `protoss_dt` | `mass_viking` / `terran_marine_widow` / `zerg_corruptor` |
| `persistent_dt_storm` | 暗使风暴持续骚扰 | 8+ DT + HT/Archon + Observer + Sentry | `no_detection_enemy` / `terran_bio` | `mass_observer` / `mass_overseer` / `terran_ghost` |

**虫族 6**：

| id | 中文名 | 核心组合 | counters_against | weak_against |
|---|---|---|---|---|
| `persistent_brood_corruptor` | 巢虫腐化运营 | 8 BL + 12 腐化 + 6 感染者 | `mass_ground` / `protoss_skytoss` | `mass_viking` / `protoss_mothership_carrier` |
| `persistent_mutalisk_ling` | 飞龙小狗双线 | 20 飞龙 + 30 小狗 + Queen | `terran_bio_no_thor` / `protoss_no_phoenix` | `terran_thor_marine_medivac` / `protoss_phoenix_storm` |
| `persistent_roach_hydra_macro` | 蟑螂刺蛇运营 | 30 蟑螂 + 20 刺蛇 + Lurker | `mass_light` / `protoss_chargelot` | `terran_mech_tank` / `protoss_storm` / `protoss_ground_mech` |
| `persistent_ling_bane_muta` | 狗爆飞 | 40 小狗 + 20 妖虫 + 10 飞龙 | `terran_bio_no_widow` / `protoss_no_charge` | `terran_marine_widow` / `protoss_storm` / `terran_thor_marine_medivac` |
| `persistent_lurker_hydra` | 潜伏者刺蛇龟缩 | 6 潜伏者 + 20 刺蛇 + Queen + Overseer | `terran_bio` / `protoss_ground_no_storm` | `terran_mech_tank` / `protoss_storm` |
| `persistent_swarm_host_brood` | 蝗虫巢虫消耗 | 6 蝗虫 + 4 BL + Viper + Queen | `terran_mech_tank` / `protoss_ground_mech` | `mass_air` / `mobile_army` |

**人族 6**：

| id | 中文名 | 核心组合 | counters_against | weak_against |
|---|---|---|---|---|
| `persistent_bio_medivac` | MMM 多线 | 60 枪兵 + 20 掠夺 + 12 医疗船 | `zerg_no_bane` / `protoss_no_storm` | `protoss_storm` / `protoss_ground_mech` / `zerg_ling_bane` |
| `persistent_mech_tank` | 坦克机械龟缩 | 12 坦克 + 8 雷神 + 火蝠 + 解放者 | `zerg_ground` / `protoss_ground` / `mass_air` | `protoss_skytoss` / `mass_voidray` / `protoss_disruptor` |
| `persistent_skyterran` | 战巡空军 | 10 战巡 + Raven + 维京 + 女妖 | `mass_ground` / `protoss_ground_mech` | `zerg_corruptor` / `zerg_viper` / `protoss_phoenix_storm` |
| `persistent_marine_widow` | 枪兵寡妇龟缩 | 30 枪兵 + 10 寡妇雷 + 解放者 + Raven | `zerg_mutalisk` / `protoss_dt` | `terran_mech_tank` / `protoss_storm` / `protoss_skytoss` |
| `persistent_thor_marine_medivac` | 雷神反空 | 6 雷神 + 30 枪兵 + 医疗船 | `zerg_mutalisk` / `protoss_phoenix` / `zerg_ling_bane` | `terran_ghost` / `protoss_storm` / `terran_mech_tank` |
| `persistent_ghost_bio` | 幽灵 EMP | 8 幽灵 + 40 枪兵 + 掠夺 + 医疗船 | `protoss_skytoss` / `zerg_ultra_brood` | `terran_mech_tank` / `protoss_disruptor` / `zerg_mutalisk` |

---

## 3. Schema 改动

### 3.1 `StrategyKind` enum

```python
# src/vibecraft/strategy/models.py
class StrategyKind(str, Enum):
    OPENING = "opening_build"
    PERSISTENT = "persistent_doctrine"
    # 删: MIDGAME / LATEGAME
```

### 3.2 重命名 / 修改 models

| 现 model | 改 |
|---|---|
| `OpeningBuild` | 不变 |
| `MidgameStance` | **删 class** |
| `LategameDoctrine` | 改名 `PersistentDoctrine`，加 `enemy_composition_tags: list[str]` 字段（counters_against / weak_against 已存在） |

`PersistentDoctrine` 新字段：

```python
class PersistentDoctrine(BaseModel):
    # ... 现有字段：id, display_name_zh, summary_zh, aliases,
    #             target_composition, required_tech, required_structures,
    #             engagement_doctrine, win_condition,
    #             counters_against, weak_against, ...
    
    # 新增：用于 transition_cost 公式
    gas_intensity: Literal["low", "medium", "high"] = "medium"
    # 描述这个 doctrine 的气矿需求强度。high = 双 VS / VR + Charge + Storm 都吃气。
    
    ramp_up_time_s: float = 90.0
    # 从 doctrine 开始运行到产出第一波目标组合的时间（秒）
    # 用于 W_BUILD 中的 ramp_factor。
```

### 3.3 OpeningBuild 加完成条件

```python
class OpeningBuild(BaseModel):
    # ... 现有字段 ...
    
    # 新增：开局完成判定（Q1 选 C：goal OR time 任一触发）
    completion: OpeningCompletion


class OpeningCompletion(BaseModel):
    """两条路径任一触发即完成。"""
    model_config = ConfigDict(extra="forbid")
    
    timeout_s: float = Field(description="兜底时间，超时强制完成（无论 goal 是否满足）")
    
    # goal 条件用现有 done_when DSL（复用 directives/done_when.py 的 evaluator）
    goal_when: dict[str, Any] | None = Field(
        default=None,
        description="完成判定（done_when schema）；None = 仅靠 timeout"
    )
```

例：

```yaml
# 4bg.yaml
completion:
  timeout_s: 420    # 7:00 兜底
  goal_when:
    kind: all_of
    conditions:
      - {kind: structure_count, structure_type: Gateway, op: ">=", value: 4}
      - {kind: own_unit_count, unit_type: Stalker, op: ">=", value: 8}
      - {kind: time_elapsed_since, seconds: 360, ref: game_start}  # 6:00
```

### 3.4 Enemy composition tag 表

固定 canonical tag 集（schema 验证 yaml 的 counters_against / weak_against 仅可用这些）：

```python
# src/vibecraft/strategy/enemy_tags.py
ENEMY_COMPOSITION_TAGS: frozenset[str] = frozenset({
    # 虫族
    "zerg_ling_bane", "zerg_roach_hydra", "zerg_mutalisk", "zerg_lurker",
    "zerg_brood", "zerg_corruptor", "zerg_ultra", "zerg_swarm_host",
    "zerg_ultra_brood",
    # 神族
    "protoss_skytoss", "protoss_chargelot", "protoss_blink", "protoss_dt",
    "protoss_phoenix", "protoss_storm", "protoss_disruptor",
    "protoss_ground_mech", "protoss_ground_no_storm",
    "protoss_no_charge", "protoss_no_phoenix",
    "protoss_phoenix_storm", "protoss_mothership_carrier",
    # 人族
    "terran_bio", "terran_bio_no_stim", "terran_bio_no_thor", "terran_bio_no_widow",
    "terran_mech", "terran_mech_tank",
    "terran_sky", "terran_marine_widow", "terran_thor_marine_medivac",
    "terran_ghost",
    "terran_no_detection",
    # 通用 (race-agnostic)
    "mass_air", "mass_ground", "mass_light", "mass_armored", "mass_massive",
    "mass_voidray", "mass_corruptor", "mass_viking", "mass_marauder",
    "mass_observer", "mass_overseer",
    "worker_harass", "no_detection_enemy", "mobile_army",
})
```

**侦察 → tag 计算**：

```python
# src/vibecraft/strategy/enemy_tags.py
def compute_enemy_composition_tags(
    enemy_summary: dict[str, int],   # 来自 ParseContext.enemy_summary
    enemy_race: str,                 # protoss / zerg / terran
    enemy_upgrades: set[str],        # 已观察到的对方升级
) -> set[str]:
    """从侦察数据推断当前敌方组合 tag。
    
    每条规则用 threshold（最少 N 个该单位）。多 tag 可同时成立。
    """
    tags: set[str] = set()
    
    # 通用 supply 阈值
    air_supply = sum(enemy_summary.get(u, 0) * AIR_UNIT_SUPPLY.get(u, 0) for u in AIR_UNITS)
    ground_supply = sum(enemy_summary.get(u, 0) * GROUND_SUPPLY.get(u, 0) for u in GROUND_UNITS)
    if air_supply >= 50:
        tags.add("mass_air")
    if ground_supply >= 50:
        tags.add("mass_ground")
    # ...
    
    # 种族特化（举例 zerg）
    if enemy_race == "zerg":
        if enemy_summary.get("Zergling", 0) >= 30 and enemy_summary.get("Baneling", 0) >= 5:
            tags.add("zerg_ling_bane")
        if enemy_summary.get("Roach", 0) >= 15 or enemy_summary.get("Hydralisk", 0) >= 10:
            tags.add("zerg_roach_hydra")
        if enemy_summary.get("Mutalisk", 0) >= 8:
            tags.add("zerg_mutalisk")
        # ...
    
    return tags
```

---

## 4. Transition cost 公式

### 4.1 完整公式

```python
# src/vibecraft/strategy/transition_cost.py

# 权重常量（v1，跑通后调）
W_BUILD = 1.0
W_TECH = 0.8
W_UNIT = 0.5
W_GAS_BN = 0.6
W_COUNTER = 2.0
W_OBSO = 0.3

GAS_MULTIPLIER = 1.5     # gas 比 mineral 更稀缺，权重 ×1.5
RAMP_FACTOR = 0.5        # 建筑造时折算资源等效系数
TECH_TIME_FACTOR = 0.7
COUNTER_VALUE = 50.0     # 每命中一个 enemy composition tag 的分值


def transition_cost(
    target: PersistentDoctrine,
    game_state: GameSnapshot,
    enemy_tags: set[str],
) -> float:
    """计算从当前状态转入 target persistent doctrine 的总成本。
    
    成本越低越好；可为负（counter bonus 大于其它成本时）。
    
    分量：
    1. 建筑差     —— 缺多少建筑、含 prereq 链
    2. 科技差     —— 缺多少升级
    3. 兵种差     —— 缺多少 target 兵种
    4. 气矿瓶颈   —— 目标 gas demand 超出当前 income
    5. counter    —— 命中敌方 composition tag 加 / 减成本
    6. 沉没成本   —— 当前 army 里跟 target 完全无关的单位（轻度 nudge）
    """
    # 1. 建筑差
    build_cost = 0.0
    for struct_type, target_count in target.required_structures.items():
        have = game_state.structure_count(struct_type)
        missing = max(0, target_count - have)
        if missing > 0:
            data = STRUCT_COSTS[struct_type]
            build_cost += missing * (
                data.mineral + data.gas * GAS_MULTIPLIER
                + data.build_time * RAMP_FACTOR
            )
        # prereq 链：如果 target 要 Carrier，必须 VS+VX 都存在
        for prereq in STRUCT_PREREQS.get(struct_type, []):
            if game_state.structure_count(prereq) == 0:
                pdata = STRUCT_COSTS[prereq]
                build_cost += pdata.mineral + pdata.gas * GAS_MULTIPLIER + pdata.build_time * RAMP_FACTOR
    
    # 2. 科技差
    tech_cost = 0.0
    for upgrade in target.required_tech:
        if not game_state.has_upgrade(upgrade) and not game_state.is_researching(upgrade):
            data = TECH_COSTS[upgrade]
            tech_cost += data.mineral + data.gas * GAS_MULTIPLIER + data.research_time * TECH_TIME_FACTOR
    
    # 3. 兵种差
    unit_cost = 0.0
    for unit_type, target_count in target.target_composition.items():
        have = game_state.unit_count(unit_type)
        missing = max(0, target_count - have)
        if missing > 0:
            data = UNIT_COSTS[unit_type]
            unit_cost += missing * (data.mineral + data.gas * GAS_MULTIPLIER)
    
    # 4. 气矿瓶颈
    target_gas_demand_per_min = estimate_gas_demand(target)
    gas_bottleneck_penalty = max(0, target_gas_demand_per_min - game_state.gas_income_per_minute)
    
    # 5. counter（负值 = 减成本）
    counter_hits = enemy_tags & set(target.counters_against)
    weak_hits = enemy_tags & set(target.weak_against)
    counter_bonus = -COUNTER_VALUE * len(counter_hits) + COUNTER_VALUE * len(weak_hits)
    
    # 6. 沉没成本
    target_unit_set = set(target.target_composition.keys())
    obsolete_cost = 0.0
    for unit_type, count in game_state.own_army_summary.items():
        if unit_type not in target_unit_set:
            data = UNIT_COSTS[unit_type]
            obsolete_cost += count * (data.mineral + data.gas * GAS_MULTIPLIER) * 0.3  # 30% 折扣
    
    return (
        W_BUILD * build_cost
        + W_TECH * tech_cost
        + W_UNIT * unit_cost
        + W_GAS_BN * gas_bottleneck_penalty
        + W_COUNTER * counter_bonus
        + W_OBSO * obsolete_cost
    )


def pick_best_persistent(
    game_state: GameSnapshot,
    enemy_tags: set[str],
    library: StrategyLibrary,
    my_race: str,
) -> tuple[str, float, dict[str, float]]:
    """返回 (chosen_id, cost, all_costs) —— 含完整成本表用于显示原因。"""
    costs: dict[str, float] = {}
    for doctrine in library.persistent_doctrines(race=my_race):
        costs[doctrine.id] = transition_cost(doctrine, game_state, enemy_tags)
    chosen = min(costs, key=costs.get)
    return chosen, costs[chosen], costs
```

### 4.2 单测设计

```python
# tests/unit/test_transition_cost.py
class TestTransitionCost:
    def test_zero_cost_when_already_at_target(self):
        """当前状态完全匹配 target → 成本 ≈ 0"""
    
    def test_cost_proportional_to_missing_buildings(self):
        """缺 2 个建筑成本 > 缺 1 个建筑"""
    
    def test_counter_bonus_reduces_cost(self):
        """侦察到敌方 composition 在 counters_against 里 → 成本下降"""
    
    def test_weak_against_increases_cost(self):
        """侦察到敌方 composition 在 weak_against 里 → 成本上升"""
    
    def test_pick_best_prefers_existing_tech(self):
        """1g_robo_immortal 完成时，pick_best 应选 ground_mech（VR 已有）而非 skytoss（要重建）"""
    
    def test_pick_best_prefers_counter_to_enemy(self):
        """敌方 mass mutalisk 时，选 phoenix_storm 而非 ground_mech"""
    
    def test_never_returns_none(self):
        """任何 game_state 下都有非空答案（满足 Q4 不变量）"""
```

### 4.3 worked example

**场景**：神族玩家完成 1g_robo_immortal 开局，时间 6:30。

```
当前状态：
  建筑: 2 NX, 1 BG, 1 BY, 1 VR, 2 BE, 2 BA, 1 BC(防御炮塔), 1 BF(锻炉)
  科技: WarpGate, ProtossGroundArmor +1
  兵种: 5 不朽, 3 哨兵, 2 探机巡逻
  Gas income: ~150/min
  侦察: terran_bio (敌方 30 marine + 5 medivac)

候选 persistent doctrine 成本计算：
  ┌──────────────────────────────┬────────┬───────┬────────┬───────┬──────────┬──────┬─────────┐
  │ doctrine                     │ build  │ tech  │ unit   │ gas_bn│ counter  │ obso │ total   │
  ├──────────────────────────────┼────────┼───────┼────────┼───────┼──────────┼──────┼─────────┤
  │ persistent_ground_mech       │   400  │   75  │  1200  │   30  │   -100   │   0  │  1605   │ ★最低
  │ persistent_iac_macro         │   650  │  175  │  1800  │   60  │   -100   │  30  │  2615   │
  │ persistent_blink_harass      │   200  │  100  │  1500  │   40  │   -100   │  90  │  1830   │
  │ persistent_skytoss           │  2200  │  500  │  3000  │  200  │      0   │ 150  │  6050   │
  │ persistent_phoenix_storm     │  1800  │  300  │  2400  │  150  │      0   │ 150  │  4800   │
  │ persistent_dt_storm          │   850  │  200  │  1500  │   80  │   -100   │  60  │  2590   │
  └──────────────────────────────┴────────┴───────┴────────┴───────┴──────────┴──────┴─────────┘

  → 选 persistent_ground_mech（成本 1605）
  
  PWA 推送理由：
    "已选: 地面机械流（成本最低 1605）
     原因: VR 已有 / counter 对方 bio
     备选: 闪追风筝（1830）/ 暗使风暴（2590）"
```

---

## 5. 状态机 + 不变量

### 5.1 State

```python
# src/vibecraft/bot/director.py（扩展）
@dataclass
class StrategyState:
    phase: Literal["opening", "persistent"]
    current_strategy_id: str  # 不变量：永远非空
    opening_completed_at: float | None  # game_time，None 表示开局未完成
```

### 5.2 状态转移

| Event | 当前 phase | 行为 |
|---|---|---|
| 游戏启动 | - | 设 `phase=opening`, `current_strategy_id=DEFAULT_OPENING`（默认 4bg / macro_hatch / reaper_expand） |
| `OPENING_COMPLETED`（goal 或 timeout 触发） | opening | `pick_best_persistent()` → `current_strategy_id = chosen`，`phase=persistent`，PWA 推送 |
| 玩家 PICK_OPENING(id) | opening | `current_strategy_id = id`（允许）|
| 玩家 PICK_OPENING(id) | persistent | **拒绝**，PWA 显示 "开局已结束，请选持续策略" |
| 玩家 PICK_PERSISTENT(id) | opening | 允许提前切（强制 `OPENING_COMPLETED`） |
| 玩家 PICK_PERSISTENT(id) | persistent | `current_strategy_id = id` |
| 玩家 CANCEL（任意 phase）| any | **拒绝 cancel 语义**，调 `pick_best_persistent()` 自动切（Q4 / Q5）+ PWA 推送 "不能 cancel，已自动切到 X，理由 Y" |
| LLM parse 失败 | any | 当前 strategy 不变（不动）|
| LLM emit `strategy_cancel` directive | any | 同玩家 CANCEL |

### 5.3 不变量 enforcement 点

```python
# src/vibecraft/bot/director.py
class Director:
    def _set_strategy(self, new_id: str, phase: str, now: float) -> None:
        """所有 strategy 修改必须走这一条路径。"""
        # 不变量 1: new_id 非空
        assert new_id and new_id != "sustain", f"forbidden empty/sustain: {new_id}"
        
        # 不变量 2: phase ∈ {opening, persistent}
        assert phase in ("opening", "persistent"), f"invalid phase: {phase}"
        
        # 不变量 3: phase=opening 时 id 必须是 opening
        if phase == "opening":
            assert self.library.kind_of(new_id) == StrategyKind.OPENING
        # phase=persistent 时必须是 persistent doctrine
        else:
            assert self.library.kind_of(new_id) == StrategyKind.PERSISTENT
        
        # 不变量 4: my_race 一致（沿用上一轮加的 race filter）
        assert self.library.race_of(new_id) == self.my_race.lower()
        
        self.facade.set_build(new_id)
        self.strategy_state.current_strategy_id = new_id
        self.strategy_state.phase = phase
        # log + push snapshot
```

### 5.4 删 `Sustain` plan

- 删 `src/vibecraft/bot/auto_combat/protoss/plans/sustain.py` 及 zerg/terran 对应
- 删所有 `Sustain` import
- `IfElse` 路由树的 default 分支改成 fallback persistent —— 实际不会走到（不变量保证），但要兜底防 KeyError
- 删 `docs/aliases/*.yaml` 里 sustain 相关 alias 项
- 删 test_*sustain* 相关单测

---

## 6. Cancel 语义 + UX

### 6.1 LLM prompt 改动

旧：`strategy_cancel` directive → 切 sustain

新：保留 `strategy_cancel` directive（兼容 LLM 现有输出），但 director 截获 → 调 `pick_best_persistent()` → 自动切（不真 cancel）

system prompt 加 1 段：

```
====== L1 strategy_cancel 政策（2026-05-19 更新）======

> 玩家说"取消剧本 / 停下 / 不要这个 / 别按剧本走" → 仍 emit strategy_cancel,
> 但 bot 不会进 sustain 摆烂状态。bot 会自动算 transition_cost 选一个
> 持续策略（如 ground_mech / skytoss / iac_macro）切过去。
>
> 玩家在 PWA 上会看到提示："不能 cancel，已自动切到 X（理由：转型代价
> 最低 / counter 对方 bio）"。
```

### 6.2 PWA 推送（不能 cancel + 自动切的提示）

新 WS frame 类型：

```typescript
interface StrategyAutoSwitchFrame {
  type: 'strategy_auto_switch'
  reason: 'opening_completed' | 'cancel_redirected' | 'parse_fail_redirected'
  from_strategy_id: string | null
  to_strategy_id: string
  to_display: string
  cost: number
  alternatives: Array<{id: string, display: string, cost: number}>  // top 3
  explanation_zh: string  // "VR 已有 / counter 对方 bio"
}
```

PWA 显示（轻量 toast，3 秒自动消失）：

```
┌─────────────────────────────────────────────┐
│ ⓘ 已自动切到「地面机械流」                    │
│   原因: VR 已有 / counter 对方 bio          │
│   备选: 闪追风筝 / 暗使风暴                 │
└─────────────────────────────────────────────┘
```

### 6.3 StrategyPicker 按 phase 过滤

```vue
<!-- web/src/components/StrategyPicker.vue -->
<!-- 当前 phase=opening 时显示 6-8 opening chip -->
<!-- 当前 phase=persistent 时显示 6 persistent chip -->
<!-- 显示当前 phase 的状态条："开局阶段（剧本：4bg）" / "持续运营（doctrine：地面机械流）" -->
```

PWA 增 `phase` 字段到 game_status frame，前端按 phase 切换显示。

---

## 7. 迁移 plan

### 7.1 现有 yaml 改动

| 文件 | 改动 |
|---|---|
| `strategies/protoss/iac_2base.yaml` | `kind: midgame_stance` → `kind: opening_build`，加 `completion: {timeout_s: 450, goal_when: ...}` |
| `strategies/protoss/dt_drop_iac.yaml` | 同上，timeout=510 |
| `strategies/protoss/skytoss.yaml` | `kind: lategame_doctrine` → `kind: persistent_doctrine`，id 改 `persistent_skytoss` |
| `strategies/zerg/brood_corruptor.yaml` | 同上，id `persistent_brood_corruptor` |
| `strategies/zerg/mutalisk_harass.yaml` | `kind: midgame_stance` → `opening_build` |
| `strategies/zerg/roach_hydra.yaml` | 同上 |
| `strategies/terran/bio_stim.yaml` | 同上 |
| `strategies/terran/two_base_tanks.yaml` | 同上 |
| `strategies/terran/bc_late.yaml` | `lategame_doctrine` → `persistent_doctrine`，id `persistent_skyterran` |

加 `completion` 字段到所有 opening（合计 ~14 个 yaml）。

### 7.2 新增 yaml + plan py

| 种族 | 新增 persistent yaml | plan py |
|---|---|---|
| protoss | ground_mech / iac_macro / blink_harass / phoenix_storm / dt_storm（5）| 每个对应 1 个 plan py |
| zerg | mutalisk_ling / roach_hydra_macro / ling_bane_muta / lurker_hydra / swarm_host_brood（5）| 同 |
| terran | bio_medivac / mech_tank / marine_widow / thor_marine_medivac / ghost_bio（5）| 同 |

合计：15 个新 persistent yaml + 15 个 plan py。

### 7.3 PWA 改动

- `StrategyPicker.vue` 按 phase 过滤
- 新增 `strategy_auto_switch` frame 处理（toast UI）
- `useWs.ts` 新增 `currentPhase` ref
- `game_status` frame 加 `phase` 字段

### 7.4 后端改动文件清单

| 文件 | 改动 |
|---|---|
| `src/vibecraft/strategy/models.py` | enum 改 / class 删 / 新字段 |
| `src/vibecraft/strategy/library.py` | `persistent_doctrines(race)` 方法 / `kind_of(id)` / 加载 `PersistentDoctrine` 替代 `LategameDoctrine` |
| `src/vibecraft/strategy/transition_cost.py` | **新建**：公式实现 |
| `src/vibecraft/strategy/enemy_tags.py` | **新建**：tag canonical 集 + `compute_enemy_composition_tags()` |
| `src/vibecraft/strategy/unit_data.py` | **新建**：STRUCT_COSTS / TECH_COSTS / UNIT_COSTS / PREREQ 表 |
| `src/vibecraft/bot/director.py` | 状态机：`StrategyState`、`_set_strategy()`、`pick_best_persistent()` 调用、cancel 拦截 |
| `src/vibecraft/bot/auto_combat/*/bot.py` | IfElse 默认分支改 fallback persistent；移除 sustain |
| `src/vibecraft/bot/auto_combat/*/plans/sustain.py` | **删** |
| `src/vibecraft/llm/prompt.py` | system prompt 加 cancel 政策段 |
| `src/vibecraft/server/game_process.py` | game_status frame 加 `phase`；新增 `strategy_auto_switch` push |
| `src/vibecraft/server/ws.py` | 新 frame 类型 |
| `tests/unit/test_transition_cost.py` | **新建** |
| `tests/unit/test_director.py` | 加状态机不变量测试 / cancel 重定向测试 |
| `tests/unit/test_protoss_bot_all_strategies_loaded.py` | 8 → 13（8 opening + 5 新 persistent，含 1 现有 skytoss 改名）|

---

## 8. 实施顺序（推荐 11 步）

```
P0: 基础设施（无 SC2 依赖，TDD）
─────────────────────────────────────────
Step 1: 新 schema (StrategyKind / PersistentDoctrine model / OpeningCompletion)
        + 测试 yaml 加载兼容（旧 lategame_doctrine alias 期）
Step 2: enemy_tags.py + unit_data.py + 单测
Step 3: transition_cost.py + pick_best_persistent + 完整单测套（含 worked example 验证）
Step 4: StrategyState + Director._set_strategy + 不变量 enforcement 单测

P1: 现有迁移（覆盖现 9 个 strategy）
─────────────────────────────────────────
Step 5: 重分类现有 yaml（kind 字段 + completion 字段 + 改名）
        改 LategameDoctrine 引用为 PersistentDoctrine
        IfElse 路由树跑通
Step 6: 删 Sustain plan 3 个 + 相关 import + 测试

P2: 新 persistent doctrine（15 个）
─────────────────────────────────────────
Step 7: 神族 5 个 plan py + yaml + 单测
Step 8: 虫族 5 个 plan py + yaml + 单测
Step 9: 人族 5 个 plan py + yaml + 单测

P3: 集成 + PWA
─────────────────────────────────────────
Step 10: LLM prompt cancel 政策段 + cancel 拦截 wire
         game_status frame 加 phase + strategy_auto_switch frame
Step 11: PWA StrategyPicker phase 过滤 + auto_switch toast UI
         端到端 manual 测：开局完成 → 自动切 / cancel → 自动切

P4（可选）: 调权重 + 调 doctrine
─────────────────────────────────────────
跑 5-10 局 vs 内置 AI，看 transition_cost 选择是否合理；
微调 W_* 常量；按需补 doctrine。
```

---

## 9. 风险 + 开放问题

### 9.1 风险

| 风险 | 缓解 |
|---|---|
| **15 个新 persistent plan py 写起来工程量大** | 复用 PersistentMacro（重新拣起来！）+ ZoneAttack + 通用战斗模板；每个 plan py 只需定 commitments 列表 + 触发条件 |
| **transition_cost 权重 W_* 不合理 → 永远选错** | TDD 验 worked example；端到端跑 5 局快速调权重；权重写常量易调 |
| **enemy composition tag 推断不准** | 初版宽松（threshold 较低），多 tag 同时成立更好；端到端跑后看准确率 |
| **cancel 重定向不符合玩家直觉** | PWA 推送解释清楚原因；如玩家反复 cancel 同一个 doctrine，下次推荐第二低成本的 |
| **删 Sustain plan 破坏其它代码路径** | 全仓库 grep + test 覆盖；保留一个简化版作 unit test fixture（不接入 IfElse） |

### 9.2 开放问题（已 resolve）

1. **PersistentMacro 复用？** → **不复用**。每个 doctrine 自己写完整 plan。
   依据用户原话："一个策略的兵种搭配，进攻和防守倾向，资源运行策略应该是一体的"。
   含义：skytoss 的扩张节奏（5 矿憋空军）跟 ground_mech（3-4 矿稳推）跟 blink_harass（2-3 矿多线骚扰）天然不同，
   不该被强制共享 macro 层。每个 plan py 是 self-contained 完整组合（worker chrono + AutoPylon + Expand + 兵种产线 + Attack 触发 + Defense）。
   PersistentMacro 文件保留但仍不接入（dead code，未来如有共性可拣起重写）。

2. **doctrine transition cooldown？** → **不加**。玩家想切就切，bot 不阻挠。

3. **opening 完成后锁定 active_recipe？** → **锁定**。Director 层 enforcement：
   - `phase=persistent` 时 `_set_strategy(opening_id, ...)` 抛错或 silent reject + log warning
   - StrategyPicker UI 已按 phase 过滤（§6.3），玩家点不到 opening chip
   - 玩家语音说"切回 4bg" → LLM emit `strategy_set(opening, 4bg)` → director 拒绝 + PWA 推 "已锁定 persistent 阶段，不可回到 opening。如想换 doctrine 请用 'X 切运营到 ...' 的话术。"

4. **doctrine 内部 phase？** → 现有 `Phase` 模型可用，按需加。skytoss 等长 doctrine 加 phase；短 doctrine 不加（PWA 显示空 phase stepper 即可）。本期暂不强制。

---

## 10. 测试策略

### 10.1 单测（无 SC2）

- `transition_cost.py`：12+ 用例覆盖各分量边界
- `enemy_tags.py`：每个 tag 的触发 threshold + 多 tag 共存
- `director.py`：状态机所有 transition + 不变量 enforcement
- `library.py`：18 persistent doctrine 加载、race filter、kind_of 查询

### 10.2 集成测（mock python-sc2）

- 完整流程：opening 完成 → auto-pick → set_build → IfElse 切 plan
- cancel 拦截：directive 来 → director 转 → auto-pick → PWA 推送

### 10.3 端到端（需 SC2）

- 4bg 开局 → 6:30 兜底完成 → 切 ground_mech → bot 持续运营
- 玩家 cancel → 收到 toast → 继续运营
- 玩家说"切到天空神族" → 切 persistent_skytoss

---

## 11. CHANGELOG（落地后写）

- DELETE: `Sustain` plan ×3, `MidgameStance` model, `LategameDoctrine` model
- ADD: `PersistentDoctrine` model, `transition_cost` 公式, `enemy_tags` 推断, 15 个 persistent doctrine
- BREAKING: yaml `kind` 字段变化（midgame_stance / lategame_doctrine → opening_build / persistent_doctrine）
- DOC: `CLAUDE.md` 加两层架构说明 + cancel 政策更新

---

## END

Review 项：
- [ ] 18 个 persistent doctrine 名单（§2.2）
- [ ] Schema 改动（§3）
- [ ] transition_cost 公式 + 权重（§4）
- [ ] 状态机 + 不变量（§5）
- [ ] Cancel 语义 + PWA toast（§6）
- [ ] 实施顺序（§8）
- [ ] 开放问题（§9.2）—— 特别是 PersistentMacro 复用 / transition cooldown / phase lock
