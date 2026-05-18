"""虫族巢虫领主腐化者后期 plan。

后期巢虫领主 + 腐化者空军组合：BL 出小虫消耗对方，腐化者护卫 + 对空。
搭配感染虫 / 毒蛇控场，慢推胜利。

设计参考：strategies/zerg/brood_corruptor.yaml
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
from sharpy.plans.acts.zerg import AutoOverLord, MorphBroodLord, MorphGreaterSpire, ZergUnit
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


class BroodCorruptor(KnowledgeBot):  # type: ignore[misc]
    """巢虫领主腐化者后期：BL + 腐化者 + 感染虫控场。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft BroodCorruptor")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 后期科技树 build order
            SequentialList(
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 36),
                BuildGas(4),
                Expand(4),  # 四矿后期经济
                GridBuilding(UnitTypeId.SPIRE, 1),
                GridBuilding(UnitTypeId.INFESTATIONPIT, 1),
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 44),
                Step(
                    UnitReady(UnitTypeId.SPIRE, 1),
                    MorphGreaterSpire(),
                ),
                BuildOrder(
                    AutoOverLord(),
                    Tech(UpgradeId.ZERGFLYERWEAPONSLEVEL1),
                    Tech(UpgradeId.ZERGFLYERARMORSLEVEL1),
                    # 先积累腐化者，再转化 BL
                    Step(
                        UnitReady(UnitTypeId.SPIRE, 1),
                        ZergUnit(UnitTypeId.CORRUPTOR, 8),
                    ),
                    Step(
                        UnitExists(UnitTypeId.CORRUPTOR, 6),
                        MorphBroodLord(10),
                    ),
                    # 感染虫控场
                    Step(
                        UnitReady(UnitTypeId.INFESTATIONPIT, 1),
                        ZergUnit(UnitTypeId.INFESTOR, 4),
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
