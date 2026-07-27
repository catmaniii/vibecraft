"""虫族蟑螂一波 all-in plan。

两矿蟑螂 all-in：BS → 二矿 → Roach Warren 尽早 → 双气 →
蟑螂速(Glial) + 小狗速(ZerglingSpeed) → 全力爆蟑螂，~5:30-6:00 一波推掉。
不开三矿，不转后期，这是 all-in。

设计参考：strategies/zerg/roach_allin.yaml
"""

from __future__ import annotations

from typing import Any

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

from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct


class RoachAllin(KnowledgeBot):  # type: ignore[misc]
    """蟑螂一波 all-in：两矿速爆蟑螂，5:30 出门推掉对方。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft RoachAllin")

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：蟑螂 ≥ 10 → 通知 Director 切持续 doctrine。

        蟑螂 all-in 兵力到位信号：ROACH ≥ 10 时主力已够出门。
        Director 收到信号 → 推荐 toast 转 persistent_roach_hydra_viper。
        """
        try:
            return bool(ai.units(UnitTypeId.ROACH).amount >= 10)
        except Exception:
            return False

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成 → Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._opening_done),
            # 蟑螂一波 build order
            SequentialList(
                # 阶段1：奠基 —— BS → 二矿 → Roach Warren → 双气矿
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 12),
                GridBuilding(UnitTypeId.SPAWNINGPOOL, 1),
                Expand(2),  # 二矿，all-in 只开两矿
                BuildGas(2),  # 双气矿全开，供蟑螂速 + 蟑螂产能
                Step(
                    UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                    GridBuilding(UnitTypeId.ROACHWARREN, 1),
                ),
                # 阶段2：科技 + 暴兵
                ActUnit(UnitTypeId.DRONE, UnitTypeId.LARVA, 18),  # 控农民 ~18，all-in 不要过多
                BuildOrder(
                    AutoOverLord(),
                    # Queen 注浆 + 防守用(priority=True:不抢 larva,从 HATCHERY 训)
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ActUnit(UnitTypeId.QUEEN, UnitTypeId.HATCHERY, 2, priority=True),
                    ),
                    # 提速小狗 —— 两翼包抄用
                    Tech(UpgradeId.ZERGLINGMOVEMENTSPEED),
                    # 注:不研蟑螂速(Glial)—— 需 Lair 前置,2 矿 all-in 升 Lair
                    # (主巢 morph ~57s 掉幼虫产能)不值,保持纯一本速暴蟑螂。
                    # 全力爆蟑螂,all-in 目标 14+ 只(priority=True 抢 larva + 矿,
                    # 不被小狗/Queen reserve 阻塞 —— ling_bane 同样问题的修法)。
                    Step(
                        UnitReady(UnitTypeId.ROACHWARREN, 1),
                        ZergUnit(UnitTypeId.ROACH, 14, priority=True),
                    ),
                    # 小狗辅助包抄,8 只够用(gate 在 BS ready 避免提前 reserve)。
                    Step(
                        UnitReady(UnitTypeId.SPAWNINGPOOL, 1),
                        ZergUnit(UnitTypeId.ZERGLING, 8),
                    ),
                ),
            ),
            # 家事 + 出门 all-in
            SequentialList(
                InjectLarva(),
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                # DistributeWorkers / SpeedMining / Gather 必须排在 PlanZoneAttack
                # 之前：PlanZoneAttack.execute() 正常对局每帧 return False（sharpy
                # 源码 zone_attack.py:123 "Blocks!"），SequentialList 一旦遇 False
                # 就停 —— 排在它后面的 act 整局不执行。
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # start_attack_power 设低 —— all-in 早出门，不等攒满
                # roach_hydra 用默认值（~20），这里设 8 让 5:30 节奏出门
                PlanZoneAttack(start_attack_power=8),
                PlanFinishEnemy(),
            ),
        )
