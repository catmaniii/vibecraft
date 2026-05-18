"""虫族飞龙骚扰中期 plan。

双矿飞龙骚扰：快出尖塔 → 12+ 飞龙持续骚扰，干扰对方生产 + 拖延经济。
配合地面狗形成多线威胁。

设计参考：strategies/zerg/mutalisk_harass.yaml
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


class MutaliskHarass(KnowledgeBot):  # type: ignore[misc]
    """飞龙骚扰中期：快速飞龙 + 地面配合多线骚扰。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft MutaliskHarass")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 飞龙骚扰 build order
            SequentialList(
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 22),
                BuildGas(2),
                GridBuilding(UnitTypeId.SPIRE, 1),
                Expand(2),  # 确保双矿
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 28),
                BuildOrder(
                    AutoOverLord(),
                    Tech(UpgradeId.ZERGLINGMOVEMENTSPEED),  # 狗速
                    Step(
                        UnitReady(UnitTypeId.SPIRE, 1),
                        ZergUnit(UnitTypeId.MUTALISK, 12),
                    ),
                    # 配合地面 Zergling
                    ZergUnit(UnitTypeId.ZERGLING, 20),
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
