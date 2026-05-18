"""人族 Sustain plan（空剧本兜底）。

玩家 voice 取消当前剧本后，active_recipe 切到 "sustain"，IfElse 路由树
降级到这个 plan。

行为：
- 持续 macro：SCV 出齐 / OC 升轨道 / CallMule 骡补矿
- 持续从 Barracks 出 Marine 防守
- DistributeWorkers / SpeedMining 等家事 Manager 照跑
- **不主动出门**：没有 PlanZoneAttack，只 PlanZoneDefense + PlanFinishEnemy（收尾用）
- AutoDepot + LowerDepots 自动补人口

设计意图：bot 不抢决策权，等玩家下个剧本指令。
"""

from __future__ import annotations

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase
from sharpy.plans.acts.terran import AutoDepot, MorphOrbitals, TerranUnit
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.terran import CallMule, LowerDepots, Repair


class TerranSustain(KnowledgeBot):  # type: ignore[misc]
    """空剧本兜底：macro + 守家，不主动出门。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Terran Sustain")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 主线：建立基础经济 + 科技
            SequentialList(
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 14),
                GridBuilding(UnitTypeId.SUPPLYDEPOT, 1),
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 16),
                GridBuilding(UnitTypeId.BARRACKS, 1),
                BuildGas(1),
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 19),
                Expand(2),  # 开自然矿
                BuildGas(2),
                ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 22),
                BuildOrder(
                    AutoDepot(),
                    MorphOrbitals(2),
                    Step(
                        UnitReady(UnitTypeId.BARRACKS, 1),
                        TerranUnit(UnitTypeId.MARINE, 16),
                    ),
                    Step(
                        UnitExists(UnitTypeId.COMMANDCENTER, 2),
                        ActUnit(UnitTypeId.SCV, UnitTypeId.COMMANDCENTER, 44),
                    ),
                ),
            ),
            # 家事 + 守家（没有 PlanZoneAttack）
            SequentialList(
                LowerDepots(),
                CallMule(50),
                Repair(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 注意：无 PlanZoneAttack —— sustain 不主动出门
                PlanFinishEnemy(),  # 敌方残血时收尾
            ),
        )
