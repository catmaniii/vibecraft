"""vibecraft 两矿凤凰（Phoenix 2-base opener）plan。

战术核心
========
双星门持续 chrono 凤凰，以骚扰 + 吊资源打乱对方节奏：
  - PvZ：飞虫（Mutalisk）出现前先下手，吊 Overlord + 骚扰 drone line
  - PvT：吊 SCV + 骚扰 bio 集结，配合地面 Stalker 守家

关键路径
========
1. 快速双矿（~1:24 NX）+ 折跃
2. VT（TwilightCouncil）+ 闪烁备用（PvT 选研）
3. VS x2（双星门）→ 持续 chrono 凤凰
4. VR（Observer 反隐）+ Warp Prism（运载骚扰）
5. 三矿延续 / 转 Skytoss

Build 节奏（参考 spawningtool.com 126982，HuShang Double Stargate Phoenix PvZ）
=============================================================================
  1:24  NX（双矿）
  1:35  BY（CyberneticsCore）
  1:49  BA x2（第 2、3 气）
  2:00  Warp Gate @chrono
  2:17  Stalker @chrono（保家）
  2:32  VT（TwilightCouncil）
  2:41  Stalker
  2:59  VR（Robotics，Observer）
  3:19  Blink @chrono（选，PvT 有用）
  3:23  VS x2（双星门，凤凰产能核心）
  3:45  Pylon
  3:47  Observer
  3:52  Stalker @chrono
  4:08  Warp Prism @chrono
  4:30  BA（三气）
  ~5:00 凤凰集结 8 → 出门骚扰
  5:28  NX（三矿）
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


class Phoenix2Base(KnowledgeBot):  # type: ignore[misc]
    """两矿凤凰（Double Stargate Phoenix）— 双星门 chrono 凤凰骚扰，PvZ/PvT。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Phoenix 2-base")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # ---------- chrono ----------
            # 探机 chrono 直到 BY 出现
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
            # 凤凰 chrono：VS 完成后持续 chrono（核心骚扰单位产速）
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                ChronoUnit(UnitTypeId.PHOENIX, UnitTypeId.STARGATE),
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
            # ---------- 双矿气矿（凤凰吃气多）----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(2)),
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(3)),
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
            # ---------- 闪烁（Blink，PvT 有用；VT 完成后研）----------
            Step(UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1), Tech(UpgradeId.BLINKTECH)),
            # ---------- 双星门（核心！VS x2）----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.STARGATE, 2)),
            # ---------- 三矿延续（5:28 三矿）----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), Expand(3)),
            # ---------- 三气（三矿后补）----------
            Step(UnitExists(UnitTypeId.NEXUS, 3), BuildGas(4)),
            # ---------- 单位训练 ----------
            # Observer（VR 完成立刻，反隐必须）
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                ProtossUnit(UnitTypeId.OBSERVER, 2),
            ),
            # Warp Prism（1 个，运载 Stalker 骚扰）
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                ProtossUnit(UnitTypeId.WARPPRISM, 1),
            ),
            # 凤凰主力（双星门 chrono，target 12）
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                ProtossUnit(UnitTypeId.PHOENIX, 12),
            ),
            # Stalker 保家（2 个先出，后期补到 8）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.STALKER, 8, priority=True),
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
                # 8 凤凰 → 出门骚扰；玩家强制 attack 绕过
                Step(
                    lambda ai: (
                        self._ready_to_pressure(ai)
                        or getattr(ai.knowledge.vibecraft, "combat_intent_override", None)
                        == "attack"
                    ),
                    VibeCraftZoneAttack(8),  # 8 凤凰集结出门
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_pressure(ai: Any) -> bool:
        """两矿凤凰出门骚扰：双 VS + 8 凤凰 ready。

        等 8 个凤凰而非 4 个 —— 凤凰对地面没伤，孤注一掷骚扰需要数量保证存活率。
        """
        # 至少 1 VS ready
        stargate_ready = ai.structures(UnitTypeId.STARGATE).ready.exists
        if not stargate_ready:
            return False
        # 8 凤凰 ready
        phoenix_count = ai.units(UnitTypeId.PHOENIX).ready.amount
        return bool(phoenix_count >= 8)
