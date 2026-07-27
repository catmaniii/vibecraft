"""ArchonAfterStorm: 战斗中能量不足以再放心灵风暴时，就地合电兵 → 白球（plan 层，不改 vendor）。

设计动机
========
sharpy 原生 Archon act 见到 2+ idle 电兵立刻合并，不看能量和战场状态。
电兵叉球一波的战术要求：
  1. 电兵上战场先放心灵风暴（每次消耗 75 能量）
  2. 能量低于 75（放不下下一发心灵风暴）时，就地合成白球
  3. 不在战场（周围没有敌方战斗单位）的电兵不合——避免在家合掉还没上战场的电兵

两个控制条件（同时满足才合）：
  - energy_threshold（默认 75）：电兵 energy < threshold → 能量不够再放心灵风暴
  - require_combat（默认 True）：电兵周围 combat_radius 格内有敌方战斗单位（排除农民）

    为什么 threshold=75？
    心灵风暴费用 = 75 能量。energy < 75 表示放不下下一发 → 该合了。
    fresh 电兵出生 energy=50，50 < 75 满足能量条件，
    但没有 require_combat 保护会在家就合；
    有 require_combat=True 时，家里没敌人 → 不合 ✓

合并机制 mirror sharpy.plans.acts.protoss.Archon（raw MORPH_ARCHON 命令）。
不继承 sharpy Archon 避免 vendor 改动。

使用方法
========
替换 iac_2base 里的::

    Step(UnitExists(HIGHTEMPLAR, 2), Archon([HIGHTEMPLAR]))

改为::

    ArchonAfterStorm()  # 默认 energy_threshold=75, require_combat=True

这样电兵上战场放完心灵风暴（energy < 75），周围有敌方战斗单位时，就地合白球；
在家待命的电兵（周围无敌）不会被合掉。
"""

from __future__ import annotations

import logging
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit
from sharpy.managers.core.roles import UnitTask
from sharpy.plans.acts.act_base import ActBase

logger = logging.getLogger(__name__)

# 心灵风暴能量费用（SC2 机制）：电兵 energy < 此值 = 放不下下一发
PSI_STORM_ENERGY_COST: int = 75

# 电兵通过折跃门出生时能量固定值（SC2 机制）
HT_STARTING_ENERGY: int = 50

# 战斗判定半径（格）：与 sharpy 战斗管理保持一致
_DEFAULT_COMBAT_RADIUS: float = 15.0

# 不算战斗单位的农民类型
_WORKER_TYPES = frozenset(
    [
        UnitTypeId.PROBE,
        UnitTypeId.SCV,
        UnitTypeId.DRONE,
    ]
)


class ArchonAfterStorm(ActBase):  # type: ignore[misc]
    """战斗中能量不足以再放心灵风暴时，就地合电兵 → 白球。

    两层优先级（每 tick）：
      优先级 1（HT 总数 > max_ht_count）：
        强制合——不看战斗判定，不看 energy 阈值。
        按 energy 升序，抓 energy 最少的对合，直到 HT 总数 <= max_ht_count。
        公式：pairs_needed = (current - max_ht_count + 1) // 2
        示例：8 HT → 1 对 → 留 6；7 HT → 1 对 → 留 5；9 HT → 2 对 → 留 5。
      优先级 2（HT 总数 <= max_ht_count）：
        走常规逻辑（require_combat + energy < threshold）。

    Args:
        energy_threshold: 电兵 energy 低于此值才参与合并候选（优先级 2）。
            默认 100（2026-06-02 用户:战斗电兵能量 < 100 立刻合白球）。
            传 201（> 电兵最大能量 200）等价于"全部无脑合"（复现 sharpy Archon 原有行为）。
        require_combat: 默认 True。True 时还要求电兵周围有敌方战斗单位才合（优先级 2），
            防止在家待命的电兵（fresh 或被 Feedback 过）被误合。
            优先级 1（超额强制合）忽略此参数。
        combat_radius: 战斗判定半径（格）。默认 15.0，与 sharpy 战斗管理一致。
        max_ht_count: 部队电兵上限。超出时优先级 1 强制合 energy 最低的电兵。
            默认 4（2026-06-02 用户:电兵最多 4 个，超出合能量最少的）。
    """

    def __init__(
        self,
        energy_threshold: int = 100,
        require_combat: bool = True,
        combat_radius: float = _DEFAULT_COMBAT_RADIUS,
        max_ht_count: int = 4,
    ) -> None:
        self.energy_threshold: int = energy_threshold
        self.require_combat: bool = require_combat
        self.combat_radius: float = combat_radius
        self.max_ht_count: int = max_ht_count
        # 已发出合并命令的电兵 tag（防重复；合完变 idle 后清除）
        self.already_merging_tags: list[int] = []
        super().__init__()

    async def start(self, knowledge: Any) -> None:
        await super().start(knowledge)
        knowledge.register_on_unit_destroyed_listener(self.on_unit_destroyed)
        # vibecraft: iac_2base 电兵安全 micro 激活 — 本 act 只在 iac_2base plan 里使用；
        # start() 时向 knowledge.vibecraft 注册 ht_safe_micro=True，让
        # MicroHighTemplars 走 vibecraft 安全路径（保持安全距离 + 不 attack）。
        # 用 getattr 兜底：单测 / 其他 plan 偶发 start 时 vibecraft ns 可能不存在。
        _vbc = getattr(knowledge, "vibecraft", None)
        if _vbc is not None and hasattr(_vbc, "ht_safe_micro"):
            _vbc.ht_safe_micro = True
            logger.info("ArchonAfterStorm.start: 已启用电兵安全 micro（ht_safe_micro=True）")
        # 2026-06-02 用户:去掉"放电期间维持队形(叉子等白球才冲)"功能 —— 叉子立刻顶上去
        # 当肉盾保护电兵放电,电兵少死。原 zealot_hold_until_archon 机制已移除。

    def _can_merge(self, ht: Unit) -> bool:
        """True 当且仅当电兵能量低于阈值（默认=75，即放不下下一发心灵风暴）。"""
        return ht.energy < self.energy_threshold

    def _in_combat(self, ht: Unit) -> bool:
        """True 当且仅当电兵周围 combat_radius 格内有敌方战斗单位（不含农民）。

        用 ai.all_enemy_units.closer_than 判定，排除探机/SCV/工虫（不算战斗单位）。
        """
        nearby = self.ai.all_enemy_units.closer_than(self.combat_radius, ht.position)
        fighters = nearby.filter(lambda u: u.type_id not in _WORKER_TYPES)
        return fighters.amount > 0

    async def execute(self) -> bool:
        try:
            hts = self.cache.own(UnitTypeId.HIGHTEMPLAR).ready
        except Exception:
            return True

        # 合并完成（变 idle）的电兵 → 清 tag + 解除 Reserved
        for ht in hts:
            if ht.is_idle and ht.tag in self.already_merging_tags:
                self.roles.clear_task(ht)
                self.already_merging_tags.remove(ht.tag)

        available = hts.tags_not_in(self.already_merging_tags)

        # 优先级 1（HT 总数 > max_ht_count）：强制合——不看战斗判定、不看能量阈值。
        # 按 energy 升序抓能量最少的两个合，每 tick 合 1 对，逐步降到 <= max_ht_count。
        if available.amount > self.max_ht_count:
            by_energy = sorted(available, key=lambda h: h.energy)
            await self._issue_merge(by_energy[0], by_energy[1], reason="over_cap")
            return True

        # 优先级 2：未在合并中 + 能量低于阈值（默认 100）+ (require_combat 时)在战场。
        # 用户 2026-06-02：战斗电兵能量 < 100 立刻合白球（threshold=100）。
        candidates = available.filter(self._can_merge)
        if self.require_combat:
            candidates = candidates.filter(self._in_combat)

        if candidates.amount > 1:
            unit: Unit = candidates[0]
            target: Unit = candidates.tags_not_in([unit.tag]).closest_to(unit)
            await self._issue_merge(unit, target, reason=f"energy<{self.energy_threshold}")

        return True  # never block

    async def _issue_merge(self, unit: Unit, target: Unit, reason: str) -> None:
        """对 unit + target 下达 MORPH_ARCHON 合白球；标 Reserved + 记 merging tag。"""
        self.already_merging_tags.append(unit.tag)
        self.already_merging_tags.append(target.tag)
        # Reserved：防 sharpy ZoneAttack 把待合电兵派前线
        self.roles.set_task(UnitTask.Reserved, unit)
        self.roles.set_task(UnitTask.Reserved, target)
        logger.info(
            "ArchonAfterStorm 合白球[%s]: 电兵 tag=%d energy=%.0f + tag=%d energy=%.0f",
            reason,
            unit.tag,
            unit.energy,
            target.tag,
            target.energy,
        )
        from s2clientprotocol import raw_pb2 as raw_pb
        from s2clientprotocol import sc2api_pb2 as sc_pb

        command = raw_pb.ActionRawUnitCommand(
            ability_id=AbilityId.MORPH_ARCHON.value,
            unit_tags=[unit.tag, target.tag],
            queue_command=False,
        )
        action = raw_pb.ActionRaw(unit_command=command)
        await self.ai._client._execute(
            action=sc_pb.RequestAction(actions=[sc_pb.Action(action_raw=action)])
        )

    def on_unit_destroyed(self, event: Any) -> None:
        if event.unit_tag in self.already_merging_tags:
            self.already_merging_tags.remove(event.unit_tag)
