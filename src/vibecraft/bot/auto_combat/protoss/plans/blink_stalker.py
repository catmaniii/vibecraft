"""vibecraft 闪追压制（Blink Stalker timing）plan。

战术核心
========
双矿 4 BG 闪烁追猎，~5:07 timing 出门（Harstem / PartinG 标准）：
  - 主力：Blink Stalker × 11-14（微操核心：低血 blink 撤）
  - 骚扰：Warp Prism 2 Stalker drop，双线压力
  - 侦察：Observer（反隐 + 确认敌方组合）
  - 哨兵：Adept × 1（骚扰 / 守场）

关键路径
========
1. 快速双矿（~1:24 NX）+ 折跃
2. VT（TwilightCouncil）→ 研 Blink（~3:19，chrono 加速）
3. VR（Robotics）→ Observer + Warp Prism
4. 4 BG 补全（~3:23-3:39）
5. 5:07 benchmark：68 supply / 11 stalkers / 1 adept / 1 warp prism

Build 节奏（参考 spawningtool.com 178931，Harstem 4 Gate Blink PvT）
===================================================================
  1:24  NX（双矿）
  1:35  BY（CyberneticsCore）
  1:40  Pylon
  1:49  BA x2
  1:57  Adept @chrono
  2:00  Warp Gate @chrono
  2:17  Stalker @chrono
  2:32  VT（TwilightCouncil）
  2:41  Stalker
  2:59  VR（Robotics）
  3:19  Blink @chrono（关键！）
  3:23  BG x2（补到 3 BG）
  3:39  BG（第 4 门）
  3:47  Observer
  4:08  Warp Prism @chrono
  4:30  BA（三气）
  **5:07 出门 benchmark**（11 Stalkers + 1 Adept + 1 Warp Prism）
  5:28  NX（三矿延续）
"""

from __future__ import annotations

from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import (
    AutoPylon,
    ChronoTech,
    ChronoUnit,
    ProtossUnit,
    RestorePower,
)
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack import VibeCraftZoneAttack


class BlinkStalker(KnowledgeBot):  # type: ignore[misc]
    """闪追压制（Blink Stalker timing）— 双矿 4 BG 闪烁，5:07 出门。PvT/PvP。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Blink Stalker")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # ---------- chrono ----------
            # 探机 chrono 到 BY 出现
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
                skip_until=UnitExists(UnitTypeId.ASSIMILATOR, 1),
            ),
            # 折跃 chrono
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ChronoTech(AbilityId.RESEARCH_WARPGATE, UnitTypeId.CYBERNETICSCORE),
            ),
            # Blink chrono（VT 完成后立刻 chrono 闪烁，timing 关键）
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                ChronoTech(AbilityId.RESEARCH_BLINK, UnitTypeId.TWILIGHTCOUNCIL),
            ),
            # ---------- 早期主线 ----------
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                GridBuilding(UnitTypeId.PYLON, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 15),
                GridBuilding(UnitTypeId.GATEWAY, 1),
                BuildGas(1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 19),
            ),
            # ---------- 快速双矿（~1:24）+ BY ----------
            Step(UnitReady(UnitTypeId.GATEWAY, 1), Expand(2)),
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            # ---------- 双矿气矿 ----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(2)),
            # ---------- 折跃研究 ----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), Tech(UpgradeId.WARPGATERESEARCH)),
            # ---------- VT（TwilightCouncil，闪烁前置）----------
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),
            # ---------- VR（Robotics，Observer + Warp Prism）----------
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.ROBOTICSFACILITY, 1),
            ),
            # ---------- 闪烁研究（Blink，关键升级 ~3:19）----------
            Step(UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1), Tech(UpgradeId.BLINKTECH)),
            # ---------- 补 4 BG（~3:23-3:39）----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.GATEWAY, 4)),
            # ---------- 三气（4:30）----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(3)),
            # ---------- 三矿延续（5:28）----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), Expand(3)),
            # ---------- 单位训练 ----------
            # Observer（VR 一好立刻，反隐必须）
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                ProtossUnit(UnitTypeId.OBSERVER, 2),
            ),
            # Warp Prism（1 个，Stalker drop 骚扰）
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                ProtossUnit(UnitTypeId.WARPPRISM, 1),
            ),
            # Adept × 1（哨兵 / 骚扰）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.ADEPT, 1, priority=True),
            ),
            # Stalker 主力（持续 warp，target 14）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.STALKER, 14),
            ),
            # ---------- 经济 ----------
            AutoPylon(),
            ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 60),
            # ---------- 战术 / 维护 / 攻击触发 ----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 闪烁完成 + 10 Stalker → 出门；玩家强制 attack 绕过
                Step(
                    lambda ai: (
                        self._ready_to_pressure(ai)
                        or getattr(ai.knowledge.vibecraft, "combat_intent_override", None)
                        == "attack"
                    ),
                    VibeCraftZoneAttack(10),  # 10 Stalker 出门，等闪烁完成
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_pressure(ai: Any) -> bool:
        """闪追出门 timing：Blink 完成 + 4 BG + 10 Stalker ready。

        Harstem 标准：5:07 出门 benchmark = Blink 完成 + 11 Stalker；
        本判定按状态触发（Blink 完成比 timer 更可靠）。
        """
        # Blink 必须完成（没闪烁的追猎对 bio 微操优势全失）
        blink_done = (
            ai.already_pending_upgrade(UpgradeId.BLINKTECH) >= 1.0
            or UpgradeId.BLINKTECH in ai.state.upgrades
        )
        if not blink_done:
            return False
        # 4 BG 就绪
        gate_count = ai.structures.of_type({UnitTypeId.GATEWAY, UnitTypeId.WARPGATE}).ready.amount
        if gate_count < 4:
            return False
        # 10 Stalker ready
        stalker_count = ai.units(UnitTypeId.STALKER).ready.amount
        return bool(stalker_count >= 10)
