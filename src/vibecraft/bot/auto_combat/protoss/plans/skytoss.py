"""vibecraft Skytoss 后期航母流 plan。

不像 4bg 是固定开局 build，Skytoss 是**后期组合驱动**：玩家从 IAC 或 1g_robo 中
期转过来时已经有 2-3 矿、CYBERNETICSCORE、可能有 TWILIGHTCOUNCIL，剧本要做的是
把建筑链补完整 + 持续 train 关键单位 + 升空军武器。

核心 target composition（参考 SC2 wiki + Liquipedia Skytoss 战术）:
  12 Carrier + 3 Tempest + 5 HighTemplar (合 Archon) + 1 Mothership + 2 Observer

关键路径（修复版 v2：加扩张 + Robo + 4 VS 并行 + 升级顺序）：
  1. 经济：AutoPylon + 3 矿 4 矿（Skytoss 至少 3-4 矿才撑得住 Carrier 产能）
  2. 防御：Forge + 二三矿各 2-3 PhotonCannon（Carrier mass 期间防骚扰）
  3. 建筑链：
     - VS (Stargate) → 1 个先建 → 跟 FleetBeacon 并行补到 4（不等 FB 完成）
     - VX (FleetBeacon) → 1 个（Carrier/Tempest 前置）
     - VR (Robotics) → 1 个（Observer 反隐前置；从 4bg 切的人没 Robo 必须自己造）
     - TC (TwilightCouncil) + TA (TemplarArchive) → HT + Storm 前置
  4. 升级（**严格顺序**，CC 一次研一个）：
     - SequentialList: AirWeapons 1 → AirArmor 1 → AirWeapons 2 → AirArmor 2 → AirWeapons 3 → AirArmor 3
     - 并行：Tech(PsiStorm) + Tech(TempestGroundAttack)
  5. 单位训练：
     - Carrier 持续 train（target 12）
     - HT 持续 train（target 5，sharpy 自动合 Archon）
     - Tempest 后期补（6+ Carrier 后开始）
     - Mothership 1 个（8+ Carrier 后允许）
     - Observer 2 个（Robo 完成后立即）
  6. Chrono：
     - 早期 chrono Air Weapons 1（FB 没完之前 Nexus 能量别浪费）
     - FB 完成后切到 chrono Carrier
  7. 战斗触发：8+ Carrier → VibeCraftZoneAttack 推；之前留家防守

不写早期 supply 节点 build（14 BE / 16 BG 等）—— 假设转入时基础经济已就位。
若转入时缺早期建筑（如 CYBERNETICSCORE 都没），各 Step 的 require 会卡住等先决。
"""

from __future__ import annotations

from typing import Any

from sc2.ids.ability_id import AbilityId
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
            # ---------- 经济基线 ----------
            ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 80),
            AutoPylon(),

            # ---------- 扩张：自动开到 4 矿（Skytoss 撑 12 Carrier 产能必需）----------
            # 切入时假设已有 2 矿；不主动开 3 矿会卡气矿。
            # Expand 内部判断已有数量，不会重复开。
            Step(UnitExists(UnitTypeId.NEXUS, 2), Expand(3)),
            Step(UnitExists(UnitTypeId.NEXUS, 3), Expand(4)),

            # ---------- 气矿：6 个（3 矿满气）----------
            BuildGas(6),

            # ---------- 建筑链 ----------
            # CC（前置，若还没有）
            Step(
                UnitReady(UnitTypeId.GATEWAY, 1),
                GridBuilding(UnitTypeId.CYBERNETICSCORE, 1),
            ),
            # 第一个 VS
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.STARGATE, 1),
            ),
            # **并行**：FleetBeacon 跟 VS 2-4 同时建（不等 FB 完成才补 VS）
            # 1 VS 一好就启动两条并行线
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                GridBuilding(UnitTypeId.FLEETBEACON, 1),
            ),
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                GridBuilding(UnitTypeId.STARGATE, 4),  # 补到 4 VS
            ),
            # VR（Observer 前置；从 4bg / 单 VS 切的人没 Robo 必须自己造）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.ROBOTICSFACILITY, 1),
            ),
            # TC（TA 前置）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),
            # TA（HT/Storm 前置）
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                GridBuilding(UnitTypeId.TEMPLARARCHIVE, 1),
            ),
            # Forge（地面攻防 + Photon Cannon 防御前置）
            Step(
                UnitReady(UnitTypeId.NEXUS, 2),
                GridBuilding(UnitTypeId.FORGE, 1),
            ),

            # ---------- 防御：二三矿各 2-3 PhotonCannon（防 Mutalisk / Liberator / DT）----------
            # Forge ready + 二矿 ready 时开始造 Cannon
            Step(
                UnitReady(UnitTypeId.FORGE, 1),
                GridBuilding(UnitTypeId.PHOTONCANNON, 4),  # 主二三矿各 1-2 个
            ),

            # ---------- 升级链 ----------
            # **关键**：空军升级用 SequentialList 强制 1→2→3 顺序
            # CC 一次只研一个，并行的 Tech() 会乱抢；SequentialList 确保
            # 武器 1 → 护甲 1 → 武器 2 → 护甲 2 → 武器 3 → 护甲 3
            SequentialList(
                Step(
                    UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                    Tech(UpgradeId.PROTOSSAIRWEAPONSLEVEL1),
                ),
                Step(
                    UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                    Tech(UpgradeId.PROTOSSAIRARMORSLEVEL1),
                ),
                Step(
                    UnitReady(UnitTypeId.FLEETBEACON, 1),
                    Tech(UpgradeId.PROTOSSAIRWEAPONSLEVEL2),
                ),
                Step(
                    UnitReady(UnitTypeId.FLEETBEACON, 1),
                    Tech(UpgradeId.PROTOSSAIRARMORSLEVEL2),
                ),
                Step(
                    UnitReady(UnitTypeId.FLEETBEACON, 1),
                    Tech(UpgradeId.PROTOSSAIRWEAPONSLEVEL3),
                ),
                Step(
                    UnitReady(UnitTypeId.FLEETBEACON, 1),
                    Tech(UpgradeId.PROTOSSAIRARMORSLEVEL3),
                ),
            ),
            # Psi Storm（TA 完成立刻研，并行于空军升级 —— TA 不挤 CC）
            Step(
                UnitReady(UnitTypeId.TEMPLARARCHIVE, 1),
                Tech(UpgradeId.PSISTORMTECH),
            ),
            # Tempest 射程升级（FleetBeacon，并行 —— FB 也不挤 CC）
            Step(
                UnitReady(UnitTypeId.FLEETBEACON, 1),
                Tech(UpgradeId.TEMPESTGROUNDATTACKUPGRADE),
            ),

            # ---------- 单位训练 ----------
            # Observer 2 个（Robo 完成立刻）
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
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

            # ---------- Chrono：分两阶段 ----------
            # 阶段 A：FleetBeacon 没好之前 → chrono Air Weapons 1（让升级抢 timing）
            Step(
                None,
                ChronoTech(
                    AbilityId.CYBERNETICSCORERESEARCH_PROTOSSAIRWEAPONSLEVEL1,
                    UnitTypeId.CYBERNETICSCORE,
                ),
                skip=UnitReady(UnitTypeId.FLEETBEACON, 1),
                skip_until=UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
            ),
            # 阶段 B：FleetBeacon 完成后 → chrono Carrier（核心 DPS 单位）
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
