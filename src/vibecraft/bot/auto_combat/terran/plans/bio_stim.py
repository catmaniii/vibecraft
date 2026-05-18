"""人族生化刺激剂中期 plan。

双矿生化 + 刺激剂：Marine + Marauder + Medivac 三件套。
刺激剂 + 医疗船全力压制，timing 8:00-9:30 出门。

设计参考：strategies/terran/bio_stim.yaml
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


class BioStim(KnowledgeBot):  # type: ignore[misc]
    """生化刺激剂中期：MMM timing 压制。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft BioStim")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 生化 build order
            SequentialList(
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 22),
                BuildGas(2),
                GridBuilding(UnitTypeId.BARRACKS, 3),
                Expand(3),  # 三矿
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 36),
                Step(
                    UnitReady(UnitTypeId.BARRACKS, 2),
                    BuildAddon(UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS),
                ),
                Step(
                    UnitReady(UnitTypeId.BARRACKS, 3),
                    GridBuilding(UnitTypeId.STARPORT, 1),
                ),
                BuildOrder(
                    AutoDepot(),
                    MorphOrbitals(3),
                    Tech(UpgradeId.STIMPACK),
                    Tech(UpgradeId.COMBATSHIELD),
                    Tech(UpgradeId.PUNISHERGRENADES),  # ConcussiveShells
                    TerranUnit(UnitTypeId.MARINE, 20),
                    TerranUnit(UnitTypeId.MARAUDER, 10),
                    Step(
                        UnitReady(UnitTypeId.STARPORT, 1),
                        TerranUnit(UnitTypeId.MEDIVAC, 4),
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
