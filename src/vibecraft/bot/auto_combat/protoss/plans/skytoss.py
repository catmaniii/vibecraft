"""vibecraft Skytoss 后期航母流 plan。

不像 4bg 是固定开局 build，Skytoss 是**后期组合驱动**：玩家从 IAC 或 1g_robo 中
期转过来时已经有 2-3 矿、CYBERNETICSCORE、可能有 TWILIGHTCOUNCIL，剧本要做的是
把建筑链补完整 + 持续 train 关键单位 + 升空军武器。

核心 target composition（参考 SC2 wiki）:
  12 Carrier + 3 Tempest + 5 HighTemplar (合 Archon) + 1 Mothership + 2 Observer

关键路径：
  1. 经济基建（永久）：AutoPylon + DistributeWorkers + PlanZoneDefense
  2. 建筑链：
     - VS (Stargate) → 4 个（持续产 Carrier/Tempest）
     - VX (FleetBeacon) → 1 个（Carrier/Tempest 前置）
     - TC (TwilightCouncil) + TA (TemplarArchives) → HT + Storm 前置
  3. 升级：
     - ProtossAirWeapons 1/2/3
     - ProtossAirArmor 1/2/3
     - PsiStorm (TA 完成立刻研)
     - TempestGroundAttackUpgrade (FleetBeacon，Tempest 射程，慢拆 turtle 用)
  4. 单位训练：
     - Carrier 持续 train（target 12）
     - HT 持续 train（target 5，sharpy 自动合 Archon）
     - Tempest 后期补（8+ Carrier 后开始）
     - Mothership 1 个（6+ Carrier 后允许）
     - Observer 2 个（反隐 + 侦察）
  5. 战斗触发：8+ Carrier → VibeCraftZoneAttack 推；之前留家防守

不写早期 supply 节点 build（14 BE / 16 BG 等）—— 假设转入时基础经济已就位。
若转入时缺早期建筑（如 CYBERNETICSCORE 都没），各 Step 的 require 会卡住等先决。
"""

from __future__ import annotations

from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import (
    AutoPylon,
    ChronoUnit,
    ProtossUnit,
    RestorePower,
)
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack import VibeCraftZoneAttack


class Skytoss(KnowledgeBot):  # type: ignore[misc]  # sharpy 无类型,KnowledgeBot=Any
    """Skytoss 后期航母流 — Carrier + Tempest + HT/Archon + Mothership。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Skytoss")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # ---------- 经济基线（持续）----------
            # 80 农民封顶（4 矿满采），AutoPylon 不断补 supply
            ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 70),
            AutoPylon(),

            # ---------- 扩张：3 矿（如果还没开）----------
            # 假设玩家从 IAC 转过来通常已 2-3 矿，留 ActUnit 自动补到 4
            # （没用 expand_now，让玩家用 L4 expansion_override 决定时机）

            # ---------- 气矿：4-6 个 ----------
            BuildGas(6),

            # ---------- 建筑链 ----------
            # Cybernetics Core（前置）—— 若还没有
            Step(
                UnitReady(UnitTypeId.GATEWAY, 1),
                GridBuilding(UnitTypeId.CYBERNETICSCORE, 1),
            ),
            # 第一个 Stargate
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.STARGATE, 1),
            ),
            # Fleet Beacon（Carrier/Tempest/GravitonCatapult 前置）
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                GridBuilding(UnitTypeId.FLEETBEACON, 1),
            ),
            # TwilightCouncil（TA 前置）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),
            # Templar Archives（HT/Storm 前置）
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                GridBuilding(UnitTypeId.TEMPLARARCHIVE, 1),
            ),
            # 补到 4 Stargate（持续 Carrier 产能）
            Step(
                UnitReady(UnitTypeId.FLEETBEACON, 1),
                GridBuilding(UnitTypeId.STARGATE, 4),
            ),

            # ---------- 升级链 ----------
            # ProtossAirWeapons 1/2/3（CYBERNETICSCORE）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                Tech(UpgradeId.PROTOSSAIRWEAPONSLEVEL1),
            ),
            Step(
                UnitReady(UnitTypeId.FLEETBEACON, 1),
                Tech(UpgradeId.PROTOSSAIRWEAPONSLEVEL2),
            ),
            Step(
                UnitReady(UnitTypeId.FLEETBEACON, 1),
                Tech(UpgradeId.PROTOSSAIRWEAPONSLEVEL3),
            ),
            # ProtossAirArmor 1/2/3（CYBERNETICSCORE）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                Tech(UpgradeId.PROTOSSAIRARMORSLEVEL1),
            ),
            Step(
                UnitReady(UnitTypeId.FLEETBEACON, 1),
                Tech(UpgradeId.PROTOSSAIRARMORSLEVEL2),
            ),
            Step(
                UnitReady(UnitTypeId.FLEETBEACON, 1),
                Tech(UpgradeId.PROTOSSAIRARMORSLEVEL3),
            ),
            # Psi Storm（HT 关键技能）
            Step(
                UnitReady(UnitTypeId.TEMPLARARCHIVE, 1),
                Tech(UpgradeId.PSISTORMTECH),
            ),
            # Tempest range upgrade（FleetBeacon，加 Tempest 射程，慢拆 turtle 用）
            Step(
                UnitReady(UnitTypeId.FLEETBEACON, 1),
                Tech(UpgradeId.TEMPESTGROUNDATTACKUPGRADE),
            ),

            # ---------- 单位训练 ----------
            # Observer 2 个（反隐 + 侦察，前置 Robotics）
            Step(
                UnitExists(UnitTypeId.ROBOTICSFACILITY, 1),
                ProtossUnit(UnitTypeId.OBSERVER, 2),
            ),
            # Carrier 持续 train（target 12）
            Step(
                UnitReady(UnitTypeId.FLEETBEACON, 1),
                ProtossUnit(UnitTypeId.CARRIER, 12),
            ),
            # HT 持续 train（target 5，sharpy 自动合 2 HT → Archon）
            Step(
                UnitReady(UnitTypeId.TEMPLARARCHIVE, 1),
                ProtossUnit(UnitTypeId.HIGHTEMPLAR, 5),
            ),
            # Tempest 后期补：6+ Carrier 后开始（反 lategame air）
            Step(
                self._after_n_carriers(6),
                ProtossUnit(UnitTypeId.TEMPEST, 3),
            ),
            # Mothership 1 个：8+ Carrier 后允许（lategame 旗舰）
            Step(
                self._after_n_carriers(8),
                ProtossUnit(UnitTypeId.MOTHERSHIP, 1),
            ),

            # ---------- Carrier chrono（FleetBeacon 完成后持续给所有 VS）----------
            Step(
                UnitReady(UnitTypeId.FLEETBEACON, 1),
                ChronoUnit(UnitTypeId.CARRIER, UnitTypeId.STARGATE),
            ),

            # ---------- 战术 / 维护 / 战斗触发 ----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 8+ Carrier → 出门推；玩家强制 attack 直接绕过
                Step(
                    lambda ai: self._ready_to_push(ai)
                    or getattr(ai.knowledge.vibecraft, "combat_intent_override", None) == "attack",
                    VibeCraftZoneAttack(8),
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _after_n_carriers(n: int) -> Any:
        """返回一个谓词函数：当前 Carrier 数量 >= n。"""
        def predicate(ai: Any) -> bool:
            return ai.units(UnitTypeId.CARRIER).amount >= n
        return predicate

    @staticmethod
    def _ready_to_push(ai: Any) -> bool:
        """出门条件：8+ Carrier ready 即可推（Tempest/Mothership 可后续支援）。

        Carrier 是核心 DPS；HT/Archon 在 free_army 里随大军走，不必等。
        """
        carriers = ai.units(UnitTypeId.CARRIER).amount
        return bool(carriers >= 8)
