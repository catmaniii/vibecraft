"""人族枪兵速攻 plan。

1 BB 速出枪兵骚扰：早期兵营 + 刺激剂研发，快速推进对方矿线。
持续骚扰打乱对方节奏。

设计参考：strategies/terran/marine_rush.yaml
"""

from __future__ import annotations

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.terran import AutoDepot, BuildAddon, TerranUnit
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


class MarineRush(KnowledgeBot):  # type: ignore[misc]
    """枪兵速攻开局：1BB + 刺激剂早期骚扰。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft MarineRush")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 速攻 build order
            SequentialList(
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 12),
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(1),
                Step(
                    UnitReady(UnitTypeId.BARRACKS, 1),
                    BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS),
                ),
                Tech(UpgradeId.STIMPACK),
                BuildOrder(
                    AutoDepot(),
                    TerranUnit(UnitTypeId.MARINE, 12),
                    Step(
                        UnitExists(UnitTypeId.SUPPLYDEPOT, 2),
                        GridBuilding(UnitTypeId.BARRACKS, 2),
                    ),
                    TerranUnit(UnitTypeId.MARINE, 24),
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
