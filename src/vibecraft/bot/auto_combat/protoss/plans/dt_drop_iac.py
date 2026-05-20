"""vibecraft 空投隐刀转叉球一波（Stats DT/Archon drop into IAC push）plan。

战术意图（2026-05-19 用户重新定义）
====================================
  VR 早出 Warp Prism 飞前线 → 8 DT 全员从棱镜处 warp-in 骚扰对方矿区 →
  尽量保 DT 活着 → 棱镜接回主力区 → DT 全合 Archon (2 DT = 1 archon → 4 archon) →
  跟家里持续暴的 chargelot 汇合 → 一波 attack

vs vanilla iac_2base 关键差异
==============================
  - **多了 WarpPrism**（VR 唯一产品，不出不朽）
  - DT 用法不同：iac_2base 全 DT 在家 warp + 立刻合 archon；
    本路线 DT 全员去敌方矿区 warp-in 骚扰，活下来再合 Archon
  - 出门 timing 晚 ~30s（要等 DT 撤回 + archon 合）

vs Stats 原版 (spawningtool /68902)
====================================
  - Stats 原版有不朽：4:04/4:51 出 2 不朽 + Archon。本变体**完全不出不朽**
  - Stats 原版 DT 分两波（4 + 4）按 timing 出。本变体 DT 同样 2 波但全去骚扰
  - 出门 timing ~8:00（同 Stats）

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

from vibecraft.bot.auto_combat.protoss.plans.prism_harass import PrismHarassAct
from vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack import VibeCraftZoneAttack
from vibecraft.bot.auto_combat.protoss.plans.warp_dt_at_prism import WarpDTAtPrism


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
            # 2026-05-19 修正：VR 不出不朽,只出 Warp Prism（用户要求）
            # Warp Prism chrono：VR 一好就 chrono Warp Prism，越快出越好（飞前线开棱镜空投）
            Step(
                None,
                ChronoUnit(UnitTypeId.WARPPRISM, UnitTypeId.ROBOTICSFACILITY),
                skip=UnitExists(UnitTypeId.WARPPRISM, 1, include_pending=True),
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
            # 2026-05-20 用户修正:加三矿。原来只 Expand(2),整局两矿,撑不住
            # 8:00 chargelot+archon 一波的产能。5:00 game-time 开三矿(DT 骚扰期间
            # 趁机扩,Expand 内部判断已有 NEXUS 数,够了不会重复开)。
            Step(Time(60 * 5), Expand(3)),

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

            # ---------- 防身（2026-05-20 用户修正：只出 1 追猎，之后直接刷影刀）----------
            # 用户："上来出一个追猎之后就直接刷影刀就行了"。资源全留给 DT 科技 +
            # WarpPrism。哨兵也砍掉(见下方 — 移到 Charge 主力期才出)。
            ProtossUnit(UnitTypeId.STALKER, 1, priority=True),

            # ---------- VR 一好：★ 出 1 个 Warp Prism（不出 Immortal）----------
            # 2026-05-19 修正：用户要求 VR 早出折跃棱镜，飞对方基地附近展开做空投点
            # sharpy MicroWarpPrism 自动处理：transport mode → phasing → 安全位 →
            # 警告位返回 transport。配合 ProtossUnit(DT) 让 DT 从 prism phasing 处 warp-in
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                ActUnit(UnitTypeId.WARPPRISM, UnitTypeId.ROBOTICSFACILITY, 1, priority=True),
            ),

            # ---------- VD 一好：出 8 个 DT（2 批 ×4，~4:38 第 1 批，~5:26 第 2 批）----------
            # 折跃门 warp-in：sharpy 会选最近 power source；如果 Warp Prism 已 phasing
            # 在敌前线，DT 直接在敌方矿区 warp 出来杀农民。否则 fallback warp 在家。
            # 残 DT 回家合 Archon。
            Step(
                UnitReady(UnitTypeId.DARKSHRINE, 1),
                ProtossUnit(UnitTypeId.DARKTEMPLAR, 8, priority=True),
            ),

            # ---------- ★ DT 合 Archon（用户 2026-05-19 spec：延迟合，先全员骚扰）----------
            # 用户要求：8 DT 全部去骚扰，棱镜把残 DT 接回来跟主力汇合后再合 Archon
            # 实现：Archon([DARKTEMPLAR]) 用 Time gate 卡到 7:00，DT 全员保留 ~2 分钟做骚扰
            # 之前是 UnitExists(DT, 2) 立刻合 → 4:40 第一波 DT 出生就被合，骚扰不了
            Step(
                Time(60 * 7),
                Archon([UnitTypeId.DARKTEMPLAR]),
            ),

            # ---------- ★ 棱镜精准骚扰行为（PrismHarassAct）----------
            # 9-状态机：fly_safe → warp 4 DT → load → drop @ enemy main 低地 →
            # hover_wait 保护 + 等 CD → 原地 phase warp 第二波 OR 飞回 safe warp →
            # 8 DT delivered → hover_final → macro_attack → follow_army
            PrismHarassAct(),

            # ---------- ★ DT 在 phasing prism 处 warp（强制；否则 sharpy 默认在家 warp）----------
            # 配合 PrismHarassAct WARPING 状态使用。无 phasing prism 时 yield。
            # cap：dt_trained_count ≥ 8 终止 act。
            WarpDTAtPrism(),

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
            # 2026-05-20 用户修正:哨兵移到 Charge 主力期才出(Time 6:00),前期不出,
            # 资源全给 DT 科技。priority=False — 力场是 nice-to-have,不抢 chargelot 产能。
            Step(
                Time(60 * 6),
                ProtossUnit(UnitTypeId.SENTRY, 2),
            ),

            # ---------- Charge Zealot 主力 ----------
            # 2026-05-20 用户修正:zealot 移到 Dark Shrine 完成后才开始
            # ("出一个追猎后直接刷影刀" — DT 科技/产能优先,chargelot 是 DT 骚扰
            # 之后的主力)。Dark Shrine ~3:14 完成,之后 zealot 持续暴到 8:00 一波。
            # priority=True：DT 出完后必须抢资源暴 chargelot。
            Step(
                UnitReady(UnitTypeId.DARKSHRINE, 1),
                ProtossUnit(UnitTypeId.ZEALOT, 14, priority=True),
            ),

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
        """大军一波 timing（2026-05-19 修正：不要求 Immortal，因为本路线不出不朽）.

        触发条件（任一）：
        - Charge done + 2 Archon ready + army_supply >= 30
        - 8:30 game time 兜底（DT 双波结束 + 残合 archon + chargelot 暴 → 集合反推）
        """
        charge_done = (
            ai.already_pending_upgrade(UpgradeId.CHARGE) >= 1.0
            or UpgradeId.CHARGE in ai.state.upgrades
        )
        if not charge_done:
            return False
        archons = ai.units(UnitTypeId.ARCHON).ready.amount
        if archons < 2:
            return False
        # 兵力够：army_supply >= 30（chargelot 主力 + 2-4 archon + 残 DT，没不朽路线 supply 更低）
        unit_supply = {
            UnitTypeId.ZEALOT: 2, UnitTypeId.STALKER: 2, UnitTypeId.SENTRY: 2,
            UnitTypeId.ARCHON: 4, UnitTypeId.DARKTEMPLAR: 2,
        }
        army_supply = sum(
            ai.units(ut).ready.amount * sup for ut, sup in unit_supply.items()
        )
        if army_supply >= 30:
            return True
        # 时间兜底：8:30 game time（DT 双波 + archon 合 + chargelot 集结）
        return bool(ai.time >= 60 * 8.5)
