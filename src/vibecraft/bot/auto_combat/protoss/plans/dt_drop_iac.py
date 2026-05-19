"""vibecraft 空投隐刀转叉球一波（Stats DT/Archon drop into IAC push）plan。

Stats 韩国职业打法,spawningtool /68902。本质是 IAC 的强化变体:
  DT 早期骚扰 → 残 DT 合 Archon → ~8:00 Immortal + Archon + 叉子一波

vs vanilla IAC 关键差异:
  - 提早 VC (2:38 vs 3:00),为 VD 让路
  - ★ Dark Shrine 3:14 起,DT 4:38 第一批出门
  - Archon 不靠 HT,靠 DT 合(2 DT → 1 Archon),5:29 第一对 Archon
  - 出门 timing ~8:00（vs vanilla 6:30）,因为 archon 暴需要时间
  - 主力 4 BG（vs vanilla 7 BG）,archon 顶单位伤害

Build 节奏（Stats spawningtool 完整）
=====================================
  Supply  Time   Action
  ------------------------------------------------------------
  13      ~0:18  Pylon
  14      ~0:30  Gateway #1
  16      ~0:45  Assimilator #1
  18      ~1:25  Natural Nexus (二矿)
  20      ~1:35  CyberneticsCore (BY)
  21      ~1:45  Pylon #2
  22      ~2:05  Assimilator #2
  24      ~2:10  Warpgate research
  31      ~2:38  ★ Twilight Council  (VC)
  34      ~3:05  ★ Robotics Facility (VR)
  36      ~3:14  ★★ Dark Shrine     (VD)  -- DT 前置
  51      ~4:38  ★★ Dark Templar ×4  -- 首波 DT
  60      ~5:26  ★★ Dark Templar ×4  -- 第 2 批 DT
  68      ~5:29  ★★ Archon ×2 (DT 合)
  68      ~5:38  Forge + Charge research
  76      ~6:04  Gateway ×3 (从 1→3)
  76      ~6:11  Gateway #4 (共 4 BG)
  ~140    ~8:00  ★ 出门 attack（3-4 Immortal + 6 Archon + 满 Zealot）

注意：
  - 不用 7 BG（vanilla IAC 的暴产能）,因为 archon 已经顶住单位伤害,产能 4 BG 够用
  - 不研 PsiStorm（要 TA + HT，~8:00 timing 来不及,且 archon 已经 splash）
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
    Archon,
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


class DtDropIac(KnowledgeBot):  # type: ignore[misc]
    """空投隐刀转叉球一波 — Stats DT/Archon drop into IAC push。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft DT-Drop IAC")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # ---------- chrono（永久后台）----------
            # 农民 chrono：早期持续 chrono PROBE,到 ASSIMILATOR 起就停
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.PROBE, 44, include_pending=True),
                skip_until=UnitExists(UnitTypeId.ASSIMILATOR, 1),
            ),
            # 折跃 chrono：BY 出现后全力 chrono 折跃,99% 停
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ChronoTech(AbilityId.RESEARCH_WARPGATE, UnitTypeId.CYBERNETICSCORE),
                skip_until=UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
            ),
            # Immortal chrono：VR 一好就持续 chrono 不朽,但 cap 4 防爆产
            Step(
                None,
                ChronoUnit(UnitTypeId.IMMORTAL, UnitTypeId.ROBOTICSFACILITY),
                skip=UnitExists(UnitTypeId.IMMORTAL, 4, include_pending=True),
            ),
            # DT chrono：VD 一好后 chrono DT 出门,够 8 个停
            Step(
                UnitReady(UnitTypeId.DARKSHRINE, 1),
                ChronoUnit(UnitTypeId.DARKTEMPLAR, UnitTypeId.GATEWAY),
                skip=UnitExists(UnitTypeId.DARKTEMPLAR, 8, include_pending=True),
            ),

            # ---------- 早期 critical path ----------
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 13),
                GridBuilding(UnitTypeId.PYLON, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                GridBuilding(UnitTypeId.GATEWAY, 1),
                BuildGas(1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 16),
            ),

            # ---------- BG 一好的并行触发（NX + BY）----------
            Step(UnitReady(UnitTypeId.GATEWAY, 1), Expand(2)),
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),

            # ---------- 第二气矿 ----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(2)),

            # ---------- BY 一好：研折跃 + 起 VT（Stats 2:38）----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), Tech(UpgradeId.WARPGATERESEARCH)),
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),

            # ---------- VC 一好：起 VR（3:05）+ Dark Shrine（3:14）----------
            # Stats 关键路径：VC 不研 Charge,先让 VR + VD 并行起
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                GridBuilding(UnitTypeId.ROBOTICSFACILITY, 1),
            ),
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                GridBuilding(UnitTypeId.DARKSHRINE, 1),
            ),

            # ---------- 防身 ----------
            ProtossUnit(UnitTypeId.STALKER, 2, priority=True),
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.ADEPT, 1, priority=True),
            ),

            # ---------- VR 一好：起 2 Immortal（spec 4:04 + 4:51 各一个）----------
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                ActUnit(UnitTypeId.IMMORTAL, UnitTypeId.ROBOTICSFACILITY, 4, priority=True),
            ),

            # ---------- VD 一好：出 8 个 DT（2 批 ×4，5:26 完）----------
            # Stats 关键：4:38 第 1 批，5:26 第 2 批，残的合 Archon
            Step(
                UnitReady(UnitTypeId.DARKSHRINE, 1),
                ProtossUnit(UnitTypeId.DARKTEMPLAR, 8, priority=True),
            ),

            # ---------- ★ DT 合 Archon（核心！）----------
            # sharpy Archon([DARKTEMPLAR]) act 自动找 idle DT 2 个一对一对合
            # 5:29 第一对 → 后续 4 对 / 8 DT → 4 Archon
            Step(
                UnitExists(UnitTypeId.DARKTEMPLAR, 2),
                Archon([UnitTypeId.DARKTEMPLAR]),
            ),

            # ---------- 5:30 起 Forge + 研 Charge + +1 攻 ----------
            # 注意：Charge 走 Twilight Council 不是 Forge,但 +1 攻走 Forge
            Step(Time(60 * 5 + 30), GridBuilding(UnitTypeId.FORGE, 1)),
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                Tech(UpgradeId.CHARGE),
            ),
            # Charge chrono（VC 上 chrono,加速出门 timing）
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                ChronoTech(AbilityId.RESEARCH_CHARGE, UnitTypeId.TWILIGHTCOUNCIL),
            ),
            Step(UnitReady(UnitTypeId.FORGE, 1), Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1)),

            # ---------- Sentry × 2（力场切阵）----------
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.SENTRY, 2, priority=True),
            ),

            # ---------- Charge Zealot 主力 ----------
            # priority=True：必须抢资源（不然会被 DT/Archon/Immortal 挤掉，整局只造 1 个）
            Step(UnitReady(UnitTypeId.GATEWAY, 1), ProtossUnit(UnitTypeId.ZEALOT, 14, priority=True)),

            # ---------- 经济 ----------
            AutoPylon(),
            [
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 22),
                Step(
                    UnitExists(UnitTypeId.NEXUS, 2),
                    ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 44),
                ),
                StepBuildGas(3, skip=Gas(300)),
                StepBuildGas(4, skip=Gas(400)),
            ],

            # ---------- 6:04 暴 4 BG（Stats spec）----------
            Step(Time(60 * 6), GridBuilding(UnitTypeId.GATEWAY, 4)),

            # ---------- 战术 / 维护 / 攻击触发 ----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # ★ DT 一波出门：DT 出齐 ~4:40 就该自动 attack
                # 这条单独的 attack trigger 走玩家显式 override（"DT 偷家"语音）
                Step(
                    lambda ai: (
                        self._dt_harass_ready(ai)
                        or getattr(ai.knowledge.vibecraft, "combat_intent_override", None)
                        == "attack"
                    ),
                    VibeCraftZoneAttack(4),  # 4 个 supply 就推（DT 是 2/个，2 个就够）
                ),
                # ★ IAC 大军一波：Charge + Archon + Immortal 都到位 → 8:00 推
                Step(
                    lambda ai: self._iac_ready_to_pressure(ai),
                    VibeCraftZoneAttack(20),  # 20 supply（Charge 叉主力 + Archon）
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _dt_harass_ready(ai: Any) -> bool:
        """DT 骚扰 timing：第一批 DT (≥3) ready + ~4:40 game time."""
        dt_count = ai.units(UnitTypeId.DARKTEMPLAR).ready.amount
        # 3 DT ready（spec 4 但 ≥3 就够，余 1 个留家防侦察）
        if dt_count < 3:
            return False
        return bool(ai.time >= 60 * 4.5)  # 4:30 起开始骚扰

    @staticmethod
    def _iac_ready_to_pressure(ai: Any) -> bool:
        """IAC 大军 timing：Charge done + 2 Immortal + 2 Archon + 兵力/时间双兜底."""
        charge_done = (
            ai.already_pending_upgrade(UpgradeId.CHARGE) >= 1.0
            or UpgradeId.CHARGE in ai.state.upgrades
        )
        if not charge_done:
            return False
        immortals = ai.units(UnitTypeId.IMMORTAL).ready.amount
        archons = ai.units(UnitTypeId.ARCHON).ready.amount
        if immortals < 2:
            return False
        if archons < 2:
            return False
        # 兵力够：army_supply >= 40（Stats spec ~140 supply 主力，扣 worker 后 ~40+）
        unit_supply = {
            UnitTypeId.ZEALOT: 2, UnitTypeId.STALKER: 2, UnitTypeId.SENTRY: 2,
            UnitTypeId.ADEPT: 2, UnitTypeId.IMMORTAL: 4, UnitTypeId.ARCHON: 4,
            UnitTypeId.DARKTEMPLAR: 2,
        }
        army_supply = sum(
            ai.units(ut).ready.amount * sup for ut, sup in unit_supply.items()
        )
        if army_supply >= 40:
            return True
        # 时间兜底：8:00 game time（Stats spec timing）
        return bool(ai.time >= 60 * 8)
