"""虫族宏观双孵 plan。

17 农民开第二孵化场，先扩后池，稳扎稳打的经济流开局。
build order：9 OL → 17 Hatchery → 17 BS → 女王注射 + 蟑螂路线。

设计参考：strategies/zerg/macro_hatch.yaml
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


class MacroHatch(KnowledgeBot):  # type: ignore[misc]
    """宏观双孵开局：先扩后池，稳定经济。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft MacroHatch")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 宏观双孵 build order
            SequentialList(
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 13),
                BuildGas(1),
                Expand(2),  # 17 孵化场
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 2),
                ),
                BuildGas(2),
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 22),
                ZergUnit(UnitTypeId.ZERGLING, 4),
                Step(
                    UnitExists(UnitTypeId.HATCHERY, 2),
                    GridBuilding(UnitTypeId.ROACHWARREN, 1),
                ),
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 28),
                BuildOrder(
                    AutoOverLord(),
                    Step(
                        UnitReady(UnitTypeId.ROACHWARREN, 1),
                        ZergUnit(UnitTypeId.ROACH, 16),
                    ),
                ),
            ),
            # 家事 + 进攻
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
