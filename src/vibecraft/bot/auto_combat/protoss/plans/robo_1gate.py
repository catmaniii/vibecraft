"""vibecraft 1门 Robo 不朽开 plan（标杆稳健开局，万金油应付未知情况）。

参考 sharpy `dummies/protoss/robo.py:MacroRobo`（基线 build 正确），但 fork 一份让
两件事跑通：
  1. 读 `knowledge.vibecraft.combat_intent_override`，玩家手动 attack/defend 可绕过
     默认时机
  2. 出门用 VibeCraftZoneAttack 而不是裸 PlanZoneAttack（统一战斗 act 行为）

Build 概要（典型 SC2 1 BG Robo 神族开局）：
  14 PROBE → BE → 16 PROBE → gas → BG → 20 PROBE → 二矿 → BC →
  21 PROBE → gas2 → 22 PROBE → BE → [autopylon 阶段]
  -- 2 Stalker 防身 + Tech(WarpGate) +
  -- TC → ROBO → Charge 研究 +
  -- 1 Immortal priority + 1 OB priority + 持续 Immortal 直到 ~20 +
  -- 5 min 三矿 + ZEALOT 持续 train（target 100）+
  -- 后期补 4 BG + 2 Robo

退出 timing：
  - 主力 3+ Immortal 时 VibeCraftZoneAttack(4) 准备出门
  - vibecraft 中期切到 iac_2base 时 active_recipe flag 切走，本 plan 让位

不像 4bg（committed 一波），1g_robo_immortal 是"稳一手运营开局"，目的是给中期
任何剧本（IAC / Skytoss / 其它）留好科技 + 经济基础。
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
            # 农民 chrono：早期持续 chrono PROBE，直到 30 农或开始造 gas（基础经济快）
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.PROBE, 30, include_pending=True),
                skip_until=UnitExists(UnitTypeId.ASSIMILATOR, 1),
            ),
            # Immortal chrono：Robo 一好就持续 chrono 不朽（核心 DPS）
            ChronoUnit(UnitTypeId.IMMORTAL, UnitTypeId.ROBOTICSFACILITY),

            # ---------- build order 主线 ----------
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                GridBuilding(UnitTypeId.BELON, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 16),
                BuildGas(1),
                GridBuilding(UnitTypeId.GATEWAY, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 20),
                Expand(2),  # 走双矿（标准 1 BG Robo 开局必扩）
                GridBuilding(UnitTypeId.CYBERNETICSCORE, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 21),
                BuildGas(2),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 22),
                GridBuilding(UnitTypeId.BELON, 1),

                # 进入"折跃 + 科技 + 暴 Immortal"阶段
                BuildOrder(
                    AutoPylon(),
                    # 2 Stalker 防身（priority=True 抢 BG 队列前），防 1-2 颗虫族小狗 / 人族探机骚扰
                    ProtossUnit(UnitTypeId.STALKER, 2, priority=True),
                    # 折跃研究（CYBERNETICSCORE 一好立刻研）
                    Tech(UpgradeId.WARPGATERESEARCH),
                    # 持续补农 + 气矿（到 5 气够 100 zealot 经济）
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
                    # 科技建筑：TC（Charge 前置）+ ROBO + Charge 研究
                    SequentialList(
                        [
                            Step(
                                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
                            ),
                            GridBuilding(UnitTypeId.ROBOTICSFACILITY, 1),
                            Step(
                                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                                Tech(UpgradeId.CHARGE),
                            ),
                        ]
                    ),
                    # 单位训练：1 Immortal priority → 1 OB priority → 持续 20 Immortal
                    [
                        ActUnit(
                            UnitTypeId.IMMORTAL, UnitTypeId.ROBOTICSFACILITY, 1, priority=True
                        ),
                        ActUnit(
                            UnitTypeId.OBSERVER, UnitTypeId.ROBOTICSFACILITY, 1, priority=True
                        ),
                        ActUnit(
                            UnitTypeId.IMMORTAL, UnitTypeId.ROBOTICSFACILITY, 20, priority=True
                        ),
                    ],
                    # 5 分钟开三矿（vibecraft 中期切剧本时通常已转走，但留兜底）
                    Step(Time(60 * 5), Expand(3)),
                    # 持续 train Zealot 当主力肉盾（Charge 完后 100 个 buffer）
                    [ProtossUnit(UnitTypeId.ZEALOT, 100)],
                    # 后期补 BG 暴产能 + 第 2 Robo（vibecraft 中期切走前通常用不到）
                    [
                        GridBuilding(UnitTypeId.GATEWAY, 4),
                        StepBuildGas(4, skip=Gas(200)),
                        GridBuilding(UnitTypeId.ROBOTICSFACILITY, 2),
                    ],
                ),
            ),

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
                # 3 不朽 ready → VibeCraftZoneAttack(4)；
                # 玩家显式 tactical_objective(attack) 立即绕过时机检查
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
