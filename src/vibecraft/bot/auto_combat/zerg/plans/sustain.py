"""虫族 Sustain plan（空剧本兜底）。

玩家 voice 取消当前剧本后，active_recipe 切到 "sustain"，IfElse 路由树
降级到这个 plan。

行为：
- 持续 macro：Drone 出齐 / 女王注射 / 自动 OL 补人口
- 持续从 Hatchery 出 Roach 防守
- DistributeWorkers / SpeedMining 等家事 Manager 照跑
- **不主动出门**：没有 PlanZoneAttack，只 PlanZoneDefense + PlanFinishEnemy（收尾用）
- AutoOverLord 自动补人口

设计意图：bot 不抢决策权，等玩家下个剧本指令。
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
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)
from sharpy.plans.tactics.zerg import InjectLarva


class ZergSustain(KnowledgeBot):  # type: ignore[misc]
    """空剧本兜底：macro + 守家，不主动出门。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Zerg Sustain")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 主线：建立基础经济 + 科技
            SequentialList(
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 13),
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
                BuildGas(1),
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 16),
                Expand(2),  # 开自然矿
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 2),
                ),
                BuildGas(2),
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 22),
                Step(
                    UnitExists(UnitTypeId.HATCHERY, 2),
                    GridBuilding(UnitTypeId.ROACHWARREN, 1),
                ),
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 28),
                BuildOrder(
                    AutoOverLord(),
                    Step(
                        UnitReady(UnitTypeId.ROACHWARREN, 1),
                        ZergUnit(UnitTypeId.ROACH, 10),
                    ),
                ),
            ),
            # 家事 + 守家（没有 PlanZoneAttack）
            SequentialList(
                InjectLarva(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 注意：无 PlanZoneAttack —— sustain 不主动出门
                PlanFinishEnemy(),  # 敌方残血时收尾
            ),
        )
