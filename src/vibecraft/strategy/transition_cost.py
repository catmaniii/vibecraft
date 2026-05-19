"""Persistent doctrine 转型成本公式 + pick_best_persistent 选择器。

P0 Step 3：两层架构的核心算法。开局完成 / 玩家 cancel / parse fail 时，
Director 调 pick_best_persistent() 选成本最低的 doctrine 自动切。

公式 6 分量（见 docs/plans/2026-05-19-two-tier-strategy-design.md §4）：
  1. 建筑差 (W_BUILD)    —— 缺建筑 × 造价 × ramp_factor + transitive prereq
  2. 科技差 (W_TECH)     —— 缺升级 × 造价 × research_time_factor
  3. 兵种差 (W_UNIT)     —— 缺兵种 × 造价 × count
  4. 气矿瓶颈 (W_GAS_BN) —— gas_intensity 推估需求超出当前 income
  5. counter (W_COUNTER) —— enemy_tags ∩ counters_against 减成本 / ∩ weak_against 加成本
  6. 沉没成本 (W_OBSO)   —— 当前 army 里跟 target 完全无关的单位（轻度 nudge 选已建用得上的）

权重为常量；跑通后看 worked example 输出调。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from vibecraft.strategy.unit_data import (
    get_struct_cost,
    get_tech_cost,
    get_unit_cost,
    transitive_prereqs,
)

if TYPE_CHECKING:
    from vibecraft.strategy.library import StrategyLibrary
    from vibecraft.strategy.models import PersistentDoctrine


# =========================================================================
# 权重 + 常量
# =========================================================================

W_BUILD: float = 1.0
W_TECH: float = 0.8
W_UNIT: float = 0.5
W_GAS_BN: float = 0.6
W_COUNTER: float = 2.0
W_OBSO: float = 0.3

GAS_MULTIPLIER: float = 1.5  # gas 比 mineral 更稀缺
RAMP_FACTOR: float = 0.5  # 建造时间折算资源等效系数
TECH_TIME_FACTOR: float = 0.7
COUNTER_VALUE: float = 50.0  # 每命中一个 enemy tag 的分值
OBSOLETE_DISCOUNT: float = 0.3  # 沉没成本只算原资源的 30%

# 工人 / supply provider 不算"沉没"
_WORKER_OR_SUPPLY: frozenset[str] = frozenset(
    {"Probe", "Drone", "SCV", "MULE", "Overlord", "Overseer"}
)


# =========================================================================
# 游戏状态快照（不依赖 sc2 SDK，单元测试可 mock）
# =========================================================================


@dataclass
class GameSnapshot:
    """transition_cost 计算所需的游戏状态。

    Director 从 facade.get_state() 构造；测试可手动构造。
    """

    structures: dict[str, int] = field(default_factory=dict)  # {type_name: count}
    units: dict[str, int] = field(default_factory=dict)  # {type_name: count}
    upgrades: set[str] = field(default_factory=set)  # 已完成升级
    researching: set[str] = field(default_factory=set)  # 正在研究的升级
    gas_income_per_minute: float = 0.0

    def structure_count(self, name: str) -> int:
        return self.structures.get(name, 0)

    def unit_count(self, name: str) -> int:
        return self.units.get(name, 0)

    def has_upgrade(self, name: str) -> bool:
        return name in self.upgrades

    def is_researching(self, name: str) -> bool:
        return name in self.researching

    @property
    def own_army_summary(self) -> dict[str, int]:
        """战斗单位（去掉 worker / supply provider）"""
        return {k: v for k, v in self.units.items() if k not in _WORKER_OR_SUPPLY}


# =========================================================================
# 辅助：gas 需求估算
# =========================================================================


def estimate_gas_demand(target: PersistentDoctrine) -> float:
    """从 doctrine.gas_intensity 推估每分钟 gas 需求（粗略）。

    用于 transition_cost 的 gas bottleneck 分量：
    target gas_intensity = "high" 但当前 gas income 只有 100/min → bottleneck = 250。
    """
    return {
        "low": 100.0,
        "medium": 200.0,
        "high": 350.0,
    }.get(target.gas_intensity, 200.0)


# =========================================================================
# 完整 cost 公式
# =========================================================================


def transition_cost(
    target: PersistentDoctrine,
    game: GameSnapshot,
    enemy_tags: set[str],
) -> float:
    """从当前 game state 转入 target persistent doctrine 的总成本。

    成本越低越好；可能为负（counter bonus 巨大时）。
    """
    # ---- 1. 建筑差 + transitive prereq ----
    build_cost = 0.0
    counted_prereqs: set[str] = set()  # 避免一个 prereq 多次被算
    required_set = set(target.required_structures.keys())

    for struct_type, target_count in target.required_structures.items():
        have = game.structure_count(struct_type)
        missing = max(0, target_count - have)
        if missing > 0:
            data = get_struct_cost(struct_type)
            build_cost += missing * (
                data.mineral
                + data.gas * GAS_MULTIPLIER
                + data.build_time * RAMP_FACTOR
            )
        # 加 transitive prereq（只算缺的、只算一次、不重复算 required_structures 已含的）
        for prereq in transitive_prereqs(struct_type):
            if prereq in counted_prereqs or prereq in required_set:
                continue
            counted_prereqs.add(prereq)
            if game.structure_count(prereq) == 0:
                pdata = get_struct_cost(prereq)
                build_cost += (
                    pdata.mineral
                    + pdata.gas * GAS_MULTIPLIER
                    + pdata.build_time * RAMP_FACTOR
                )

    # ---- 2. 科技差 ----
    tech_cost = 0.0
    for upgrade in target.required_tech:
        if not game.has_upgrade(upgrade) and not game.is_researching(upgrade):
            data = get_tech_cost(upgrade)
            tech_cost += (
                data.mineral
                + data.gas * GAS_MULTIPLIER
                + data.build_time * TECH_TIME_FACTOR
            )

    # ---- 3. 兵种差 ----
    unit_cost = 0.0
    for unit_type, target_count in target.target_composition.items():
        have = game.unit_count(unit_type)
        missing = max(0, target_count - have)
        if missing > 0:
            data = get_unit_cost(unit_type)
            unit_cost += missing * (data.mineral + data.gas * GAS_MULTIPLIER)

    # ---- 4. 气矿瓶颈 ----
    target_demand = estimate_gas_demand(target)
    gas_bottleneck = max(0.0, target_demand - game.gas_income_per_minute)

    # ---- 5. counter（负值 = 减成本）----
    counter_hits = enemy_tags & set(target.counters_against)
    weak_hits = enemy_tags & set(target.weak_against)
    counter_bonus = -COUNTER_VALUE * len(counter_hits) + COUNTER_VALUE * len(weak_hits)

    # ---- 6. 沉没成本（30% 折扣）----
    target_unit_set = set(target.target_composition.keys())
    obsolete_cost = 0.0
    for unit_type, count in game.own_army_summary.items():
        if unit_type not in target_unit_set:
            data = get_unit_cost(unit_type)
            obsolete_cost += count * (data.mineral + data.gas * GAS_MULTIPLIER)

    return (
        W_BUILD * build_cost
        + W_TECH * tech_cost
        + W_UNIT * unit_cost
        + W_GAS_BN * gas_bottleneck
        + W_COUNTER * counter_bonus
        + W_OBSO * obsolete_cost * OBSOLETE_DISCOUNT
    )


# =========================================================================
# 主选择器
# =========================================================================


def pick_best_persistent(
    game: GameSnapshot,
    enemy_tags: set[str],
    library: StrategyLibrary,
    my_race: str,
) -> tuple[str, float, dict[str, float]]:
    """从 library 里所有 persistent doctrine 选成本最低的一个。

    Returns:
        (chosen_id, chosen_cost, all_costs)
        - chosen_id: 最低成本 doctrine 的 id
        - chosen_cost: 该 doctrine 的成本值
        - all_costs: {doctrine_id: cost} 全表（用于 PWA 推送理由）

    Raises:
        ValueError: 当前 race 没有任何 persistent doctrine 可选
    """
    costs: dict[str, float] = {}
    for doctrine in library.persistent_doctrines(race=my_race):
        costs[doctrine.id] = transition_cost(doctrine, game, enemy_tags)

    if not costs:
        raise ValueError(
            f"No persistent doctrine registered for race {my_race!r}; "
            "check strategies/<race>/*.yaml has kind=persistent_doctrine"
        )

    chosen = min(costs, key=lambda k: costs[k])
    return chosen, costs[chosen], costs
