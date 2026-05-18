"""人族双基地坦克压制 plan。

双矿坦克 + 枪兵阵地战：坦克展开防守线 + Marine 保护坦克，逐步推进对方。
对付蟑螂 / 不朽的核心手段。

设计参考：strategies/terran/two_base_tanks.yaml
"""

from __future__ import annotations

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
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


class TwoBaseTanks(KnowledgeBot):  # type: ignore[misc]
    """双基地坦克压制：坦克阵地战 + Marine 护卫推进。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft TwoBaseTanks")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 坦克阵地 build order
            SequentialList(
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 22),
                BuildGas(2),
                GridBuilding(UnitTypeId.FACTORY, 2),
                Expand(2),  # 开自然矿
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 28),
                Step(
                    UnitReady(UnitTypeId.FACTORY, 1),
                    BuildAddon(UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY),
                ),
                Step(
                    UnitReady(UnitTypeId.FACTORY, 2),
                    GridBuilding(UnitTypeId.STARPORT, 1),
                ),
                BuildOrder(
                    AutoDepot(),
                    MorphOrbitals(2),
                    Tech(UpgradeId.STIMPACK),
                    TerranUnit(UnitTypeId.SIEGETANK, 6),
                    TerranUnit(UnitTypeId.MARINE, 12),
                    Step(
                        UnitReady(UnitTypeId.STARPORT, 1),
                        TerranUnit(UnitTypeId.VIKING, 4),
                    ),
                    Step(
                        UnitReady(UnitTypeId.STARPORT, 1),
                        TerranUnit(UnitTypeId.MEDIVAC, 2),
                    ),
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
