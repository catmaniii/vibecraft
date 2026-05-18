"""虫族蟑螂刺蛇中期 plan。

双矿蟑螂 + 刺蛇中期：蟑螂开路 + 刺蛇跟进，蟑螂速 + 刺蛇射程升级。
timing 7:30-8:30 出门压制。

设计参考：strategies/zerg/roach_hydra.yaml
"""

from __future__ import annotations

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import (
    ActUnit,
    BuildGas,
    Expand,
    GridBuilding,
    MineOpenBlockedBase,
    Tech,
)
from sharpy.plans.acts.zerg import AutoOverLord, ZergUnit
from sharpy.plans.require import UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.zerg import InjectLarva


class RoachHydra(KnowledgeBot):  # type: ignore[misc]
    """蟑螂刺蛇中期：双矿暴兵 timing 压制。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft RoachHydra")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 蟑螂刺蛇 build order
            SequentialList(
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 22),
                BuildGas(2),
                GridBuilding(UnitTypeId.ROACHWARREN, 1),
                Expand(3),  # 三矿
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 36),
                Step(
                    UnitReady(UnitTypeId.ROACHWARREN, 1),
                    GridBuilding(UnitTypeId.HYDRALISKDEN, 1),
                ),
                BuildOrder(
                    AutoOverLord(),
                    Tech(UpgradeId.GLIALRECONSTITUTION),  # 蟑螂速
                    Tech(UpgradeId.EVOLVEGROOVEDSPINES),  # 刺蛇射程
                    Tech(UpgradeId.EVOLVEMUSCULARAUGMENTS),  # 刺蛇速
                    Step(
                        UnitReady(UnitTypeId.ROACHWARREN, 1),
                        ZergUnit(UnitTypeId.ROACH, 16),
                    ),
                    Step(
                        UnitReady(UnitTypeId.HYDRALISKDEN, 1),
                        ZergUnit(UnitTypeId.HYDRALISK, 10),
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
