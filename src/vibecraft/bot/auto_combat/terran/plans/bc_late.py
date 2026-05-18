"""人族战列巡洋舰后期 plan。

后期战巡 + 渡鸦控场：4-6 BC + Raven 干扰 + Ghost EMP。
靠 BC 超级武器 + Raven 干扰拿下后期资源战。

设计参考：strategies/terran/bc_late.yaml
"""

from __future__ import annotations

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase
from sharpy.plans.acts.terran import AutoDepot, BuildAddon, MorphOrbitals, TerranUnit
from sharpy.plans.require import UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.terran import CallMule, LowerDepots, Repair


class BcLate(KnowledgeBot):  # type: ignore[misc]
    """战列巡洋舰后期：BC + Raven 控场制空。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft BcLate")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 战巡科技 build order
            SequentialList(
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 36),
                BuildGas(4),
                Expand(4),  # 四矿后期经济
                GridBuilding(UnitTypeId.STARPORT, 2),
                GridBuilding(UnitTypeId.GHOSTACADEMY, 1),
                Step(
                    UnitReady(UnitTypeId.STARPORT, 1),
                    BuildAddon(UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT),
                ),
                Step(
                    UnitReady(UnitTypeId.STARPORTTECHLAB, 1),
                    GridBuilding(UnitTypeId.FUSIONCORE, 1),
                ),
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44),
                BuildOrder(
                    AutoDepot(),
                    MorphOrbitals(4),
                    Step(
                        UnitReady(UnitTypeId.FUSIONCORE, 1),
                        TerranUnit(UnitTypeId.BATTLECRUISER, 6),
                    ),
                    TerranUnit(UnitTypeId.RAVEN, 3),
                    TerranUnit(UnitTypeId.GHOST, 4),
                    TerranUnit(UnitTypeId.VIKING, 4),
                    TerranUnit(UnitTypeId.MARINE, 8),
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
