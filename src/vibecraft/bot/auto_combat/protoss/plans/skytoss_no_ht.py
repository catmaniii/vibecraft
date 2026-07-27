"""vibecraft Skytoss 无电兵变体 — PvT Ghost EMP 场景优化。

与原版 Skytoss 的差异：
- **删除** TC → VT → HT 链路（完全跳过 TemplarArchive / HighTemplar）
- **删除** PsiStorm 研究
- **新增** TC → VD → DT × 4（产 4 DT 合 2 Archon 兜底地面 AoE）
- 主力仍是 Carrier × 12 + Tempest × 3 + Mothership

设计理由（PvT Ghost 场景）：
  Ghost EMP 清盾 + 清 HT 能量 → HT 沦为废物 → Skytoss 被大幅削弱。
  删 HT 改 DT 合 Archon：
  - 无能量目标 → EMP 只破盾不废法力
  - VD(71s) 比 VT(36s) 慢 35s，但 Archon 只要 2-3 个，非主力
  - 生产线不浪费在 HT 上 → Carrier 产能更专注

core target composition:
  12 Carrier + 3 Tempest + 4 DT（合 2 Archon）+ 1 Mothership + 2 Observer

关键路径（相对原版的变更点已标 [CHANGE]）：
  1. 经济：AutoPylon + 4 矿
  2. 建筑链：
     - VS → 1 → 并行补到 3 + VX
     - VR（Observer 前置）
     - TC（Charge 升级 + VD 前置）
     - [CHANGE] VD（DarkShrine；替换原版 VT/TemplarArchive）
     - BF（防御 Cannon）
  3. 升级：Air Weapons/Armor 各 3 级并行 + Tempest 射程
     [CHANGE] 不研 PsiStorm
  4. 单位：
     - Carrier × 12（主力 DPS）
     - [CHANGE] DT × 4 → 合 2 Archon（兜底地面 AoE）
     - [CHANGE] 不出 HT
     - Tempest × 3（后期补）
     - Mothership × 1（旗舰）
     - Observer × 2（反隐）
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
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)


class SkytossNoHT(KnowledgeBot):  # type: ignore[misc]  # sharpy 无类型,KnowledgeBot=Any
    """Skytoss 无电兵 — Carrier + Tempest + DT 合 Archon；PvT Ghost EMP 场景优化。

    主要变体（相对原版 Skytoss）：
    - VT/TemplarArchive/HT/PsiStorm 全部删除
    - VD(DarkShrine) → DT × 4 → 合 2 Archon 兜底地面 AoE
    - 主力路线不变：VS/VX/Carrier/Tempest/Mothership
    """

    def __init__(self) -> None:
        super().__init__("VibeCraft Skytoss-NoHT")
        from vibecraft.bot.auto_combat.intent_gate import AttackGate

        self._attack_gate = AttackGate(self._ready_to_push)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # ---------- 经济基线 ----------
            ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 80),
            AutoPylon(),
            # ---------- 扩张：开到 4 矿 ----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), Expand(3)),
            Step(UnitExists(UnitTypeId.NEXUS, 3), Expand(4)),
            # ---------- 气矿：6 个（3 矿满气）----------
            BuildGas(6),
            # ---------- 建筑链 ----------
            # BY 前置兜底
            Step(
                UnitReady(UnitTypeId.GATEWAY, 1),
                GridBuilding(UnitTypeId.CYBERNETICSCORE, 1),
            ),
            # 第一个 VS
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.STARGATE, 1),
            ),
            # VX（Carrier/Tempest 前置）
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                GridBuilding(UnitTypeId.FLEETBEACON, 1),
            ),
            # 补到 3 VS（VX 好后并行）
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                GridBuilding(UnitTypeId.STARGATE, 3),
            ),
            # 2026-06-02 用户:后期 VS 不够 → 4 Carrier 后补到 5 VS(加速爆航母到 12)。
            # 去掉 DT/VD 省下的矿气投入更多航母产能。
            Step(
                self._after_n_carriers(4),
                GridBuilding(UnitTypeId.STARGATE, 5),
            ),
            # VR（Observer 前置）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.ROBOTICSFACILITY, 1),
            ),
            # 2026-06-02 用户:去掉隐刀(DT) —— 删 TwilightCouncil + DarkShrine(它们只是
            # DT 的前置)。skytoss 走纯空军(航母/虚空/风暴/母舰),地面靠 Cannon 守家。
            # BF（Cannon 防御前置）
            Step(
                UnitReady(UnitTypeId.NEXUS, 2),
                GridBuilding(UnitTypeId.FORGE, 1),
            ),
            # Cannon 防御
            Step(
                UnitReady(UnitTypeId.FORGE, 1),
                GridBuilding(UnitTypeId.PHOTONCANNON, 4),
            ),
            # ---------- 升级链 ----------
            # Air Weapons 1→2→3（Weapons 独立 SequentialList）
            SequentialList(
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
            ),
            # Air Armor 1→2→3（Armor 独立 SequentialList，并行于 Weapons）
            SequentialList(
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
            ),
            # Tempest 射程（VX 完成立刻，并行于空军升级）
            Step(
                UnitReady(UnitTypeId.FLEETBEACON, 1),
                Tech(UpgradeId.TEMPESTGROUNDATTACKUPGRADE),
            ),
            # [CHANGE] 不研 PsiStorm（无 VT/TemplarArchive）
            # ---------- 单位训练 ----------
            # Observer 2 个
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                ProtossUnit(UnitTypeId.OBSERVER, 2),
            ),
            # Void Ray 过渡期（VS 好 → VX 完成前守家）
            Step(
                UnitReady(UnitTypeId.STARGATE, 1),
                ProtossUnit(UnitTypeId.VOIDRAY, 3),
                skip=UnitReady(UnitTypeId.FLEETBEACON, 1),
            ),
            # Carrier × 12（主力）
            Step(
                UnitReady(UnitTypeId.FLEETBEACON, 1),
                ProtossUnit(UnitTypeId.CARRIER, 12),
            ),
            # 2026-06-02 用户:去掉隐刀(DT) —— 不再出 DT 合白球,纯空军。
            # Tempest × 3（6+ Carrier 后补）
            Step(
                self._after_n_carriers(6),
                ProtossUnit(UnitTypeId.TEMPEST, 3),
            ),
            # Mothership × 1（8+ Carrier 后）
            Step(
                self._after_n_carriers(8),
                ProtossUnit(UnitTypeId.MOTHERSHIP, 1),
            ),
            # ---------- Chrono ----------
            # 阶段 A：VX 好之前 → chrono Air Weapons 1
            Step(
                None,
                ChronoTech(
                    AbilityId.CYBERNETICSCORERESEARCH_PROTOSSAIRWEAPONSLEVEL1,
                    UnitTypeId.CYBERNETICSCORE,
                ),
                skip=UnitReady(UnitTypeId.FLEETBEACON, 1),
                skip_until=UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
            ),
            # 阶段 B：VX 完成后 → chrono Carrier
            Step(
                UnitReady(UnitTypeId.FLEETBEACON, 1),
                ChronoUnit(UnitTypeId.CARRIER, UnitTypeId.STARGATE),
            ),
            # ---------- 战术 / 维护 / 战斗触发 ----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 8+ Carrier → 出门推
                Step(
                    self._attack_gate,
                    PlanZoneAttack(8),
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _after_n_carriers(n: int) -> Any:
        """返回谓词：当前 Carrier 数量 >= n。"""

        def predicate(ai: Any) -> bool:
            return bool(ai.units(UnitTypeId.CARRIER).amount >= n)

        return predicate

    @staticmethod
    def _ready_to_push(ai: Any) -> bool:
        """出门条件：8+ Carrier ready（纯空军，无 DT/Archon）。"""
        carriers = ai.units(UnitTypeId.CARRIER).amount
        return bool(carriers >= 8)
