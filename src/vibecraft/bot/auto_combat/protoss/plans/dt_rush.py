"""vibecraft 暗使偷家（DT Rush）plan。

战术核心
========
极速 VB（DarkShrine），~4:10 首波 3 DT 到达对方主基地偷家：
  - 早期：速 BY + 折跃 + VT + VB
  - 出击：3 DT 直接走（无 Warp Prism，标准路线）
  - 安全底：4 BG + 二矿，若 DT 被克（Detection）立转中期追猎

关键升级
========
1. WarpgateResearch（必，BY 一好立刻研，全程 chrono）
2. BlinkTech 不研（节省矿和时间，资源全给 VB）
3. DT 生产 chrono 加速（VB 好后切 chrono 给 DT warp-in）

Build 节奏（参考 spawningtool.com 47308，~4:10 首波）
=====================================================
  0:14  BE（Pylon）
  0:35  BG（Gateway）
  0:47  BA x2（双气：DT 极度吃气）
  1:22  BY（CyberneticsCore）
  1:58  research 折跃 @chrono
  2:00  VT（TwilightCouncil）
  2:23  补 BG（总 4 BG，偷家成功后追猎产能充足）
  2:36  VB（DarkShrine）
  2:48  BE + 二矿
  3:50  warp DT × 3（VB 完成立刻，chrono 加速 DT warp-in）
  4:10  **DT 抵达**（对方主基地或 natural）
  后续：持续 warp DT（target 5）+ 追猎保家
"""

from __future__ import annotations

from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import (
    AutoPylon,
    ChronoTech,
    ChronoUnit,
    ProtossUnit,
    RestorePower,
)
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack import VibeCraftZoneAttack


class DtRush(KnowledgeBot):  # type: ignore[misc]
    """暗使偷家（DT Rush）— ~4:10 首波 DT 偷家，VT + VB 极速科技线。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft DT Rush")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # ---------- chrono：折跃研究全程 chrono ----------
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
                skip_until=UnitExists(UnitTypeId.ASSIMILATOR, 1),
            ),
            # 折跃 chrono：BY 完成即刻开始，直到折跃 99%
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ChronoTech(AbilityId.RESEARCH_WARPGATE, UnitTypeId.CYBERNETICSCORE),
            ),
            # DT warp-in chrono：VB 完成后立刻 chrono DT（加速偷家 timing）
            # 标准 DT Rush 在 DT 出来后全力 chrono DT warp-in，vibecraft 原来缺少这项
            Step(
                UnitReady(UnitTypeId.DARKSHRINE, 1),
                ChronoUnit(UnitTypeId.DARKTEMPLAR, UnitTypeId.WARPGATE),
            ),
            # ---------- 早期主线（严守顺序）----------
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                GridBuilding(UnitTypeId.PYLON, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 15),
                GridBuilding(UnitTypeId.GATEWAY, 1),
                # DT 极度吃气 → 早期双气
                BuildGas(1),
                BuildGas(2),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 19),
            ),
            # ---------- BY 一好 → 折跃 + VT + VB 科技线 ----------
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), Tech(UpgradeId.WARPGATERESEARCH)),
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),
            # ---------- VT 一好 → VB（DarkShrine，~2:36）----------
            Step(UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1), GridBuilding(UnitTypeId.DARKSHRINE, 1)),
            # ---------- 补 4 BG + 二矿（安全底）----------
            # 标准 build 共 4 BG（偷家成功后追猎产能充足）；原来补到 3 不够
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.GATEWAY, 4)),
            # 二矿（VB 建造期间开，保证 DT rush 失败后有经济延续）
            Step(UnitExists(UnitTypeId.DARKSHRINE, 1), Expand(2)),
            # ---------- 第三气矿（DT 出兵需要）----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(3)),
            # ---------- 单位训练 ----------
            # 2 追猎保家（BY 好立刻，防被 early rush）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.STALKER, 2, priority=True),
            ),
            # DT 主力（VB 完成立刻 warp，首波 3 出门，target 5 持续补充）
            # target=8 会推迟偷家 timing 约 60-90s；标准 DT Rush 首波 3 DT 就出门
            Step(
                UnitReady(UnitTypeId.DARKSHRINE, 1),
                ProtossUnit(UnitTypeId.DARKTEMPLAR, 5),
            ),
            # 后期补追猎（DT 被克时的硬质输出）
            Step(
                UnitReady(UnitTypeId.GATEWAY, 3),
                ProtossUnit(UnitTypeId.STALKER, 10),
            ),
            # ---------- 经济 ----------
            AutoPylon(),
            ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 44),
            # ---------- 战术 / 维护 / 攻击触发 ----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # DT 到位 → 出门偷家；玩家强制 attack 直接绕过
                Step(
                    lambda ai: (
                        self._ready_to_pressure(ai)
                        or getattr(ai.knowledge.vibecraft, "combat_intent_override", None)
                        == "attack"
                    ),
                    VibeCraftZoneAttack(3),  # 3 DT 就可以偷家
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_pressure(ai: Any) -> bool:
        """DT Rush 出门条件：VB 完成 + 至少 3 DT ready。

        不等折跃完成 —— DT 偷家 timing 关键，晚一秒可能被 scan/Detection 侦察。
        """
        # VB 必须 ready（DT 的前置建筑）
        darkshrine_ready = ai.structures(UnitTypeId.DARKSHRINE).ready.exists
        if not darkshrine_ready:
            return False
        # 至少 3 DT ready（凑首波）
        dt_count = ai.units(UnitTypeId.DARKTEMPLAR).ready.amount
        return bool(dt_count >= 3)
