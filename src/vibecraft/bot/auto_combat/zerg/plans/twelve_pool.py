"""虫族 12pool 开局 plan。

12 农民就开 SpawningPool，极速出狗压制对方早期扩张节奏。
build order：12 农 → BS → 持续出 Zergling + 补 OL → 早期骚扰。

设计参考：strategies/zerg/12pool.yaml
"""

from __future__ import annotations

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase
from sharpy.plans.acts.zerg import AutoOverLord, ZergUnit
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.zerg import InjectLarva


class TwelvePool(KnowledgeBot):  # type: ignore[misc]
    """12pool 速狗开局。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft 12pool")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 极速母池 build order
            SequentialList(
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 12),
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
                BuildGas(1),
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 1),
                ),
                ZergUnit(UnitTypeId.ZERGLING, 6),
                Expand(2),
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 18),
                BuildOrder(
                    AutoOverLord(),
                    ZergUnit(UnitTypeId.ZERGLING, 20),
                    Step(
                        UnitExists(UnitTypeId.HATCHERY, 2),
                        ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 2),
                    ),
                ),
            ),
            # 家事 + 出门骚扰
            SequentialList(
                InjectLarva(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                PlanZoneAttack(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                PlanFinishEnemy(),
            ),
        )
