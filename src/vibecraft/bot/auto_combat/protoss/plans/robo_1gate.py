"""vibecraft 1门 Robo 不朽开 plan（标杆稳健开局，万金油应付未知情况）。

Build 概要（标准 SC2 1g Robo Immortal 开局）：
  14 BE → 16 BG + BA → 19 NX(二矿) || 17 BC（**并行**）→ Warpgate research +
  ROBO + TC（**全部 CC 一好的并行触发**）→ 1 不朽 + 1 OB + 持续不朽 +
  Charge 研究 + 5 分钟三矿

退出 timing：
  - 主力 3+ 不朽 ready + 折跃完成 → VibeCraftZoneAttack(4) 准备出门
  - vibecraft 中期切走时 active_recipe flag 切到别处，本 plan 让位

设计差异 vs sharpy MacroRobo
============================
MacroRobo 把 CC、Robo、TC 全塞进**外层 SequentialList**，导致：
  - CC 等 Expand 完成才开始 → Warpgate 研究延后 30s
  - Robo 卡在 TC 之后的 SequentialList → 首个 Immortal 延后 ~1 分钟

本版**早期 critical path 用 SequentialList**（probes/pylon/BG 严守顺序），
**CC/Expand/ROBO/TC 全用 Step(UnitReady(...), act) 并行触发**，timing 与
标准 1g Robo Immortal build 对齐。
"""

from __future__ import annotations

from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step, StepBuildGas
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import (
    AutoPylon,
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
from sharpy.plans.tactics.protoss import PlanHeatObserver

from vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack import VibeCraftZoneAttack


class Robo1GateImmortal(KnowledgeBot):  # type: ignore[misc]  # sharpy 无类型
    """1门 Robo 不朽开 — 稳健运营开局，主力 Immortal + Zealot。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft 1G Robo Immortal")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # ---------- chrono（永久后台，跟其它 sibling 并行）----------
            # 农民 chrono：到 30 农或开始造 gas 后停（早期经济快）
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.PROBE, 30, include_pending=True),
                skip_until=UnitExists(UnitTypeId.ASSIMILATOR, 1),
            ),
            # Immortal chrono：ROBO 一好就持续 chrono 不朽（核心 DPS）
            ChronoUnit(UnitTypeId.IMMORTAL, UnitTypeId.ROBOTICSFACILITY),

            # ---------- 早期 critical path（严守顺序，到 20 农停）----------
            # 后续 CC / Expand / Robo / TC 全 parallel 触发，避免 SequentialList 阻塞
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                GridBuilding(UnitTypeId.PYLON, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 16),
                BuildGas(1),
                GridBuilding(UnitTypeId.GATEWAY, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 20),
            ),

            # ---------- BG 一好的并行触发（CC + Expand 同时启动，不互等）----------
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            Step(UnitReady(UnitTypeId.GATEWAY, 1), Expand(2)),

            # ---------- CC 一好的并行触发（折跃 + ROBO + TC 三件事同时启动）----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), Tech(UpgradeId.WARPGATERESEARCH)),
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.ROBOTICSFACILITY, 1)),
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1)),

            # ---------- 第二气矿（二矿启动后立刻补）----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(2)),

            # ---------- TC 一好就研 Charge ----------
            Step(UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1), Tech(UpgradeId.CHARGE)),

            # ---------- 防身 ----------
            ProtossUnit(UnitTypeId.STALKER, 2, priority=True),

            # ---------- ROBO 一好的训练队列（sequential：1 不朽抢节奏 → OB → 20 不朽）----------
            [
                Step(
                    UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                    ActUnit(UnitTypeId.IMMORTAL, UnitTypeId.ROBOTICSFACILITY, 1, priority=True),
                ),
                Step(
                    UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                    ActUnit(UnitTypeId.OBSERVER, UnitTypeId.ROBOTICSFACILITY, 1, priority=True),
                ),
                Step(
                    UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                    ActUnit(UnitTypeId.IMMORTAL, UnitTypeId.ROBOTICSFACILITY, 20, priority=True),
                ),
            ],

            # ---------- Zealot 持续训练（Charge 完后 sharpy 自动出 Charge Zealot）----------
            ProtossUnit(UnitTypeId.ZEALOT, 100),

            # ---------- 经济持续（sequential pacing）----------
            AutoPylon(),
            [
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 22),
                Step(
                    UnitExists(UnitTypeId.NEXUS, 2),
                    ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 44),
                ),
                StepBuildGas(3, skip=Gas(300)),
                Step(
                    UnitExists(UnitTypeId.NEXUS, 3),
                    ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 56),
                ),
                StepBuildGas(5, skip=Gas(200)),
            ],

            # ---------- 5 分钟开三矿（vibecraft 中期切剧本时通常已转走，留兜底）----------
            Step(Time(60 * 5), Expand(3)),

            # ---------- 后期暴产能（vibecraft 中期切走前通常用不到）----------
            Step(Time(60 * 6), GridBuilding(UnitTypeId.GATEWAY, 4)),
            Step(Time(60 * 7), GridBuilding(UnitTypeId.ROBOTICSFACILITY, 2)),

            # ---------- 战术 / 维护 / 攻击触发（全是 sharpy 自带 Manager）----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanHeatObserver(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 3 不朽 ready → VibeCraftZoneAttack(4)；玩家显式 attack 立即绕过
                Step(
                    lambda ai: self._ready_to_pressure(ai)
                    or getattr(ai.knowledge.vibecraft, "combat_intent_override", None) == "attack",
                    VibeCraftZoneAttack(4),
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_pressure(ai: Any) -> bool:
        """主力出门 timing：3+ 不朽 ready + 折跃完成。

        1 BG Robo 不朽开本质是稳健运营，不强求一波，主力到位再压。
        """
        immortals = ai.units(UnitTypeId.IMMORTAL).ready.amount
        if immortals < 3:
            return False
        warpgate_done = (
            ai.already_pending_upgrade(UpgradeId.WARPGATERESEARCH) >= 1.0
            or UpgradeId.WARPGATERESEARCH in ai.state.upgrades
        )
        return bool(warpgate_done)
