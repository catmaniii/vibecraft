"""人族死神扩张 plan。

死神骚扰 + 快扩：1 BB 出死神骚扰对方工人 → 同时开扩张，积累经济优势。
稳扎稳打的主流 TvX 开局。

设计参考：strategies/terran/reaper_expand.yaml
"""

from __future__ import annotations

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.terran import AutoDepot, BuildAddon, MorphOrbitals, TerranUnit
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.terran import CallMule, LowerDepots, Repair


class ReaperExpand(KnowledgeBot):  # type: ignore[misc]
    """死神扩张开局：死神骚扰 + 快速开扩张。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft ReaperExpand")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 死神扩张 build order
            SequentialList(
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 12),
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(1),
                Step(
                    UnitReady(UnitTypeId.BARRACKS, 1),
                    BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS),
                ),
                TerranUnit(UnitTypeId.REAPER, 1),
                Expand(2),  # 快扩
                MorphOrbitals(2),
                BuildGas(2),
                Tech(UpgradeId.STIMPACK),
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 24),
                BuildOrder(
                    AutoDepot(),
                    Step(
                        UnitExists(UnitTypeId.COMMANDCENTER, 2),
                        TerranUnit(UnitTypeId.MARINE, 16),
                    ),
                    GridBuilding(UnitTypeId.FACTORY, 1),
                ),
            ),
            # 家事 + 进攻
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                PlanZoneAttack(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                PlanFinishEnemy(),
            ),
        )
