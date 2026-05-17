"""vibecraft Sustain plan(空剧本兜底)。

玩家 voice 取消当前剧本后,active_recipe 切到 "sustain",IfElse 路由树
降级到这个 plan。

行为:
- 持续 macro:探机出齐 / chrono / 自动开矿到 2 矿(适应 PvX 任何对面)
- 持续从 BG 出 Stalker 防守
- DistributeWorkers / SpeedMining 等家事 Manager 照跑
- **不主动出门**:没有 PlanZoneAttack,只 PlanZoneDefense + PlanFinishEnemy(收尾用)
- AutoPylon 自动补人口

设计意图:bot 不抢决策权,等玩家下个剧本指令。
"""

from __future__ import annotations

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import AutoPylon, ChronoUnit, ProtossUnit, RestorePower
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)


class Sustain(KnowledgeBot):  # type: ignore[misc]
    """空剧本兜底:macro + 守家,不主动出门。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Sustain")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # macro:探机持续 chrono(到 44 个停,2 矿饱和)
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.PROBE, 44, include_pending=True),
            ),
            # 主线:13 农 BG → 上 BY → 折跃 → 第二矿
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                GridBuilding(UnitTypeId.PYLON, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 16),
                GridBuilding(UnitTypeId.GATEWAY, 1),
                BuildGas(1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 20),
                Expand(2),  # 自动开 2 矿
                Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
                BuildGas(2),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 22),
                BuildOrder(
                    AutoPylon(),
                    Tech(UpgradeId.WARPGATERESEARCH),
                    Step(
                        UnitExists(UnitTypeId.NEXUS, 2),
                        ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 44),
                    ),
                    # 守家用 stalker(够用就行,不暴兵)
                    Step(
                        UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                        ProtossUnit(UnitTypeId.STALKER, 15),
                    ),
                    # 三矿 BG 总数到 4(防守底线)
                    Step(
                        UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
                        GridBuilding(UnitTypeId.GATEWAY, 4),
                    ),
                ),
            ),
            # 家事 + 守家(没有 PlanZoneAttack)
            SequentialList(
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 注意:无 PlanZoneAttack —— sustain 不主动出门
                PlanFinishEnemy(),  # 敌方残血时收尾(避免对方逃出局面)
            ),
        )
