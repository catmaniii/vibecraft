"""vibecraft 叉球一波（IAC 2-base all-in）plan。

战术核心
========
双矿 6:15 timing all-in：
  - 主力：Charge Zealot（叉子）× 14-18 — 切入对方后排
  - 反装甲：Immortal × 2-3
  - 溅伤：Archon × 2-4（HT 自动合）
  - 防空 + 增援：Stalker × 2-4
  - 力场切阵：Sentry × 2
  总 supply ~110-130

关键升级
========
1. WarpgateResearch（必，BY 一好立刻研，~140s）
2. Charge（必，VT 一好立刻研，~100s，5 分钟必出）
3. ProtossGroundWeapons +1（强烈推荐，BF 研，charge 叉 +1 攻提升显著）

Build 节奏（spawningtool.com 标准 IAC 2-base all-in）
=====================================================
  1:25  二矿（natural NX）
  1:35  BY（CyberneticsCore）
  2:10  Warpgate research + Adept x1（保家侦察）
  2:22  VR（Robotics）
  2:25  BB（Shield Battery，防早期骚扰）
  3:25 / 4:05  Immortal × 2
  3:40  VT（TwilightCouncil，**3:40 时机，不在 BY ready 时**）
  4:27  Charge research
  4:35 / 4:56  暴 7 BG（总数）
  **6:15 出门**  Charge 完 + +1 武器研究中

注：IAC 2-base all-in 是短平快，**不用 VA / HT / Storm**（来不及 + 分散资源）

设计差异 vs sharpy macro_stalkers dummy
========================================
之前 yaml 用 sharpy `dummies/protoss/macro_stalkers:MacroStalkers` —— 那个 dummy
是"暴追猎"，跟 IAC（叉光不朽）核心组合完全不同。本 plan 是 vibecraft 自家写的，
按真实 IAC 2-base all-in build 节奏 + vibecraft hook（combat_intent_override / VibeCraftZoneAttack）。
"""

from __future__ import annotations

from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step, StepBuildGas
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import (
    AutoPylon,
    ChronoTech,
    ChronoUnit,
    ProtossUnit,
    RestorePower,
)
from sharpy.plans.require import Gas, Time, UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack import VibeCraftZoneAttack


class IacTwoBase(KnowledgeBot):  # type: ignore[misc]
    """叉球一波（双矿 IAC all-in）— 6:15 timing 出门，Charge Zealot + Immortal + Archon。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft IAC 2-base")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # ---------- chrono（永久后台）----------
            # 农民 chrono：早期持续 chrono PROBE，直到 44 农或开始造 gas2
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.PROBE, 44, include_pending=True),
                skip_until=UnitExists(UnitTypeId.ASSIMILATOR, 1),
            ),
            # 折跃 chrono：BY 出现后所有 chrono 给折跃，到折跃 99% 停
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ChronoTech(AbilityId.RESEARCH_WARPGATE, UnitTypeId.CYBERNETICSCORE),
                skip_until=UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
            ),
            # Immortal chrono：VR 一好就持续 chrono 不朽（核心反装甲）
            ChronoUnit(UnitTypeId.IMMORTAL, UnitTypeId.ROBOTICSFACILITY),
            # ---------- 早期 critical path（严守顺序，到 16 农停）----------
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 13),
                GridBuilding(UnitTypeId.PYLON, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                GridBuilding(UnitTypeId.GATEWAY, 1),
                BuildGas(1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 16),
            ),
            # ---------- BG 一好的并行触发（NX + BY 同时启动，标准 1:25 NX / 1:35 BY）----------
            Step(UnitReady(UnitTypeId.GATEWAY, 1), Expand(2)),
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            # ---------- 第二气矿（二矿启动后立刻补；IAC 用气多）----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(2)),
            # ---------- BY 一好的并行触发（折跃研究 + VR 同时启动）----------
            # VT 不在 BY ready 时建（标准 3:40，过早建 VT 会抢占给 VR 的矿资源）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), Tech(UpgradeId.WARPGATERESEARCH)),
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.ROBOTICSFACILITY, 1),
            ),
            # ---------- BB（Shield Battery，VR ready 后建，防早期骚扰）----------
            # 标准 build 2:25 建 BB，比 VR ready（2:22）稍后，跟 VR ready 触发合理
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                GridBuilding(UnitTypeId.SHIELDBATTERY, 1),
            ),
            # ---------- VT 在 3 分钟建（标准 3:40，不过早抢 VR 矿资源）----------
            # 原版在 BY ready（~2:10）就并行建 VT，150 矿本该给 VR（200 矿）优先
            Step(
                Time(60 * 3),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),
            # ---------- VT 一好立刻研 Charge（关键，5 分钟必出）----------
            Step(UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1), Tech(UpgradeId.CHARGE)),
            # Charge 同时 chrono（VT 上面 chrono，把它升完出门 timing 关键）
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                ChronoTech(AbilityId.RESEARCH_CHARGE, UnitTypeId.TWILIGHTCOUNCIL),
            ),
            # ---------- BF 在 NX2 一好后造（攻防升级前置）----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), GridBuilding(UnitTypeId.FORGE, 1)),
            # +1 攻击（charge 叉 dps 提升显著）
            Step(UnitReady(UnitTypeId.FORGE, 1), Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1)),
            # ---------- 防身 + 保家 ----------
            ProtossUnit(UnitTypeId.STALKER, 2, priority=True),
            # Adept x1（BY ready 后出，保家侦察；标准 2:10）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.ADEPT, 1, priority=True),
            ),
            # ---------- 单位训练队列 ----------
            # VR 一好：先 2 不朽 + 1 OB + 后续补到 3 不朽
            [
                Step(
                    UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                    ActUnit(UnitTypeId.IMMORTAL, UnitTypeId.ROBOTICSFACILITY, 2, priority=True),
                ),
                Step(
                    UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                    ActUnit(UnitTypeId.OBSERVER, UnitTypeId.ROBOTICSFACILITY, 1, priority=True),
                ),
                # 后期补到 3 不朽
                Step(
                    UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                    ActUnit(UnitTypeId.IMMORTAL, UnitTypeId.ROBOTICSFACILITY, 3, priority=True),
                ),
            ],
            # Sentry × 2（力场切阵，标准 build 有 1 个，2 个合理）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.SENTRY, 2, priority=True),
            ),
            # Charge Zealot 主力（target 18，叉球一波的主体）
            # VA / HT / Psi Storm 移除：IAC 2-base all-in 是 6:15 出门，
            # Storm 研究需要 VA+TA 各 60s 共 120s，来不及完成，只会分散资源
            Step(UnitReady(UnitTypeId.GATEWAY, 1), ProtossUnit(UnitTypeId.ZEALOT, 18)),
            # ---------- 经济（sequential pacing）----------
            AutoPylon(),
            [
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 22),
                Step(
                    UnitExists(UnitTypeId.NEXUS, 2), ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 44)
                ),
                StepBuildGas(3, skip=Gas(300)),
                StepBuildGas(4, skip=Gas(400)),
            ],
            # ---------- 4 分钟暴 7 BG（IAC 关键产能时机）----------
            # spawningtool: 4:35 / 4:56 BG → 6:15 出门正好满兵
            Step(Time(60 * 4), GridBuilding(UnitTypeId.GATEWAY, 7)),
            # ---------- 战术 / 维护 / 攻击触发 ----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # **IAC 出门 timing**：Charge 完成 + 7 BG + 主力到位 → 出门压制
                # 玩家显式 attack 立即绕过
                Step(
                    lambda ai: (
                        self._ready_to_pressure(ai)
                        or getattr(ai.knowledge.vibecraft, "combat_intent_override", None)
                        == "attack"
                    ),
                    VibeCraftZoneAttack(12),  # 12 个 supply（叉子主力）就可推
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_pressure(ai: Any) -> bool:
        """IAC 出门 timing：Charge 完成 + 7 BG + 2 不朽 + 12+ 叉子 ready。

        spawningtool 标准 6:15 出门；本判定不写硬 timer，按状态触发更稳。
        """
        # Charge 必完成（IAC 的灵魂，没 charge 等于送菜）
        charge_done = (
            ai.already_pending_upgrade(UpgradeId.CHARGE) >= 1.0
            or UpgradeId.CHARGE in ai.state.upgrades
        )
        if not charge_done:
            return False
        # 7 BG 暴产能就位
        bg_count = ai.structures.of_type({UnitTypeId.GATEWAY, UnitTypeId.WARPGATE}).ready.amount
        if bg_count < 7:
            return False
        # 至少 2 不朽 ready
        immortals = ai.units(UnitTypeId.IMMORTAL).ready.amount
        if immortals < 2:
            return False
        # 至少 12 叉子 ready（主力肉盾）
        zealots = ai.units(UnitTypeId.ZEALOT).ready.amount
        return bool(zealots >= 12)
