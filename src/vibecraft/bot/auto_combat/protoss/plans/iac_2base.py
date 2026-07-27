"""vibecraft 电兵叉球一波（HT + Chargelot + Archon timing push）plan。

战术核心（2026-05-29 改造 v2：兵比例调整 + 追猎加 kite 主力）
=============================================
**主力四件套：Chargelot(肉盾) + Stalker(kite) + HT(放 Storm) + Archon（HT 合）**。
DT 路线改为 HT 路线：VT(TemplarArchives) 取代 VD(DarkShrine)，
HT 放完 Psi Storm 后立刻两两合成 Archon。

  - 肉盾：Charge Zealot × 24（纯矿，数量减少避免一发 Storm 全清）
  - kite 主力：Stalker × 10（远程消耗 + 风筝，大部队保护电兵放完 Storm）
  - 控场：High Templar × 6（放完 Psi Storm 后合 Archon；6 HT = 3 Archon）
  - 溅伤：Archon × 3（由 HT 合，每 2 HT = 1 Archon；能量耗尽后立刻合）
  - 力场切阵：Sentry × 2（出门前最后才出，等 6 电兵 ready）

兵种角色分工（2026-05-29 v2 细化）
===================================
叉子 = 肉盾（前排吸伤，不要太多，免被一发 Storm 全清）
追猎 = kite 主力（远程，保护电兵在大部队后安全放 Storm + 合白球）
电兵 = 放 Storm + 等合白球（由 MicroHighTemplars 控：不 attack、保持安全距离）
白球 = Storm 放完后与大部队一起冲（溅伤输出）

气矿优先级（用户 2026-05-29 细化）
================================
追猎 10 只(500 气，kite 主力)  →  电兵 6 只(900 气，核心)  →  哨兵 2 只(200 气，出门前最后)
叉子 0 气矿，纯矿补，目标 24 减少数量避免一发 Storm 全清

参考: Stats PvZ #81482 Archon Chargelot timing ~6:05 出门（HT 路线，TA 36s
vs VD 71s → 出门 timing 比旧 DT 版早 ~50s，约 6:00-6:30）

关键升级（气矿优先级从高到低）
================================
1. WarpgateResearch（必，BY 一好立刻研，~140s；100 气）
2. PsiStormTech（必，VT 一好立刻研，~110s；150 气 —— 电兵核心输出）
3. ProtossGroundWeaponsLevel1（+1 攻；100 气 —— 叉子/追猎/电兵 dps 提升）
4. ProtossGroundArmorsLevel1（+1 防；100 气）
5. Charge（后置，叉子 ≥8 后才研；100 气 —— 叉子有了再升，不抢 VT/电兵的气）
6. ProtossGroundWeaponsLevel2（+2 攻；+1 完成 + VC ready 后接；150 气）
7. ProtossGroundArmorsLevel2（+2 防；+1 完成 + VC ready 后接；150 气）
追猎不依赖 BF +1/+1（攻防升级仍开，有 buff，但追猎不等升级就出）

Build 节奏
==========
  1:25  二矿（natural NX）
  1:35  BY（CyberneticsCore）
  2:10  Warpgate research
  2:38  VC (TwilightCouncil)
  3:00  BF (Forge)（VC 同期，不研 Charge，气矿留给 VT）
  3:14  VT (TemplarArchives) — 比 VD 快 35s！
  3:30  +1 攻 + +1 防（Forge 一好双研；+1 完成 + VC ready 后接 +2 攻防；~7:30-8:00 完）
  3:50  VT 完成 → 研 Psi Storm（~110s，~5:40 完）
  4:00  暴 6 BG → 持续刷叉子
  4:26  第一批 电兵 × 2 出门（chrono 加速）
  5:00  第二批 电兵 × 2-4 出门
  5:00+ 叉子 ≥8 → 研 Charge（后置，气矿余量足后再研）
  5:20  6 电兵 ready → 出 1-2 哨兵（出门前最后出）
  5:30  电兵 放完 Psi Storm → 立刻合 Archon
  ~6:00 出门 attack（Charge + Storm + 3 Archon + 12+ Chargelot）

设计取舍
========
- 不出 Immortal（不要 VR，资源给 HT + Storm 升级）
- VT 比 VD 快 35s → 出门 timing 比旧 DT 版早 ~50s
- 6 HT = 3 Archon（用户 2026-05-29 调高）：放 6 次 Storm + 合 3 Archon，
  叉球一波核心输出最大化。哨兵后置门槛同步从 4 HT 升 6 HT。
- 研 Storm（PsiStormTech）：VT 一好立刻研，~5:40 完，出门时 HT 有 Storm 放
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
from sharpy.plans.require import All, Gas, TechReady, Time, UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.protoss.plans.archon_after_storm import ArchonAfterStorm
from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct


class IacTwoBase(KnowledgeBot):  # type: ignore[misc]
    """电兵叉球一波（Chargelot + HT Storm + Archon）— HT 合 Archon，~6:00 timing 出门"""

    def __init__(self) -> None:
        super().__init__("VibeCraft IAC 2-base")
        # 2026-05-28 Issue 4: AttackGate 处理 retreat/defend intent + latch
        from vibecraft.bot.auto_combat.intent_gate import AttackGate

        self._attack_gate = AttackGate(self._ready_to_pressure)

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 开局完成（_ready_to_pressure 首次满足）→ 通知 Director 推荐转持续 doctrine
            EmitOpeningCompleteAct(self._ready_to_pressure),
            # ---------- chrono（永久后台）----------
            # 农民 chrono：早期持续 chrono PROBE，到 ASSIMILATOR 起就停
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
            # Psi Storm chrono：VT 一好后 chrono Storm 研究（加速 ~40s）
            Step(
                UnitReady(UnitTypeId.TEMPLARARCHIVE, 1),
                ChronoTech(AbilityId.RESEARCH_PSISTORM, UnitTypeId.TEMPLARARCHIVE),
                skip=UnitExists(UnitTypeId.TEMPLARARCHIVE, 0),
            ),
            # 攻防升级星空加速（用户 2026-05-22：BF 升攻防优先星空加速）
            Step(
                UnitReady(UnitTypeId.FORGE, 1),
                ChronoTech(AbilityId.FORGERESEARCH_PROTOSSGROUNDWEAPONSLEVEL1, UnitTypeId.FORGE),
            ),
            Step(
                UnitReady(UnitTypeId.FORGE, 2),
                ChronoTech(AbilityId.FORGERESEARCH_PROTOSSGROUNDARMORLEVEL1, UnitTypeId.FORGE),
            ),
            # +2 攻防 chrono：+1 完成后自动接 +2（AbilityId 真实名，见 ability_id.py L299/1066）
            Step(
                None,
                ChronoTech(AbilityId.FORGERESEARCH_PROTOSSGROUNDWEAPONSLEVEL2, UnitTypeId.FORGE),
            ),
            Step(
                None,
                ChronoTech(AbilityId.FORGERESEARCH_PROTOSSGROUNDARMORLEVEL2, UnitTypeId.FORGE),
            ),
            # HT chrono：VT 一好后持续 chrono HT，够 6 个停（HT 放 Storm 后合 Archon）
            # 2026-05-29：6 HT = 3 Archon，叉球一波核心输出最大化
            Step(
                UnitReady(UnitTypeId.TEMPLARARCHIVE, 1),
                ChronoUnit(UnitTypeId.HIGHTEMPLAR, UnitTypeId.GATEWAY),
                skip=UnitExists(UnitTypeId.HIGHTEMPLAR, 6, include_pending=True),
            ),
            # ---------- 早期 critical path（严守顺序，到 16 农停）----------
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 13),
                GridBuilding(UnitTypeId.PYLON, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                GridBuilding(UnitTypeId.GATEWAY, 1),
                BuildGas(1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 16),
            ),
            # ---------- BG 一好的并行触发（NX + BY 同时启动）----------
            Step(UnitReady(UnitTypeId.GATEWAY, 1), Expand(2)),
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            # ---------- 第二气矿 ----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(2)),
            # ---------- BY 一好：研折跃 + 起 VC ----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), Tech(UpgradeId.WARPGATERESEARCH)),
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),
            # ---------- VC 一好：先起 VT + BF，Charge 后置（8 叉子后再研）----------
            # 修复 1（Charge 后置）：Charge 升级气矿 100，优先级低于 VT(200 气)
            # + PsiStorm(150 气) + 电兵(150 气×6)。等 BG 刷过 1-2 轮叉子（≥8 叉子 ready）
            # 才研 Charge，避免和 VT 抢气矿
            Step(
                All(
                    UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                    UnitExists(UnitTypeId.ZEALOT, 8),
                ),
                Tech(UpgradeId.CHARGE),
            ),
            Step(
                All(
                    UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                    UnitExists(UnitTypeId.ZEALOT, 8),
                ),
                ChronoTech(AbilityId.RESEARCH_CHARGE, UnitTypeId.TWILIGHTCOUNCIL),
            ),
            # 圣堂档案 VT：HT 前置（36s 建造，比 VD 71s 快 35s → 出门早 ~50s）
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                GridBuilding(UnitTypeId.TEMPLARARCHIVE, 1),
            ),
            # VT 一好：立刻研 Psi Storm（~110s，配合 chrono ~5:40 完）
            Step(UnitReady(UnitTypeId.TEMPLARARCHIVE, 1), Tech(UpgradeId.PSISTORMTECH)),
            # 2 个 Forge —— 攻 / 防升级并行研究。Time(4:30) gate 晚建
            Step(Time(60 * 4.5), GridBuilding(UnitTypeId.FORGE, 2)),
            # +1 攻 + +1 防 —— Forge 1 占攻、Forge 2 占防，各占一个 → 真并行。
            Step(UnitReady(UnitTypeId.FORGE, 1), Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1)),
            Step(UnitReady(UnitTypeId.FORGE, 2), Tech(UpgradeId.PROTOSSGROUNDARMORSLEVEL1)),
            # +2 攻 + +2 防 —— +1 完成 + VC ready 后接 +2（VC 是 +2/+3 前置）。
            # TechReady(+1) 确认 +1 已完成（already_pending_upgrade >= 1.0），
            # UnitReady(TWILIGHTCOUNCIL, 1) 确认 VC 存在（+2/+3 前置建筑）。
            Step(
                All(
                    TechReady(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1),
                    UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                ),
                Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL2),
            ),
            Step(
                All(
                    TechReady(UpgradeId.PROTOSSGROUNDARMORSLEVEL1),
                    UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                ),
                Tech(UpgradeId.PROTOSSGROUNDARMORSLEVEL2),
            ),
            # ---------- 追猎 10 只（kite 主力，大部队保护电兵放 Storm）----------
            # v2 改造 1（2026-05-29）：追猎从 1 升到 10，承担 kite 主力角色。
            # 远程单位（射程 6）保护电兵在大部队后安全放 Storm + 合白球。
            # 追猎不依赖 +1/+1 升级（攻防升级仍开，有 buff，但不等升级就补）
            # 出兵顺序（2026-05-30 用户要求）：电兵 >= 4 ready 后才开始补追猎。
            # 在这之前气矿优先：BY折跃 → VC → VT → Psi Storm → 电兵 × 4（600 气）。
            # 追猎 10 × 50 = 500 气后置，确保电兵先出够。
            Step(
                UnitExists(UnitTypeId.HIGHTEMPLAR, 4),
                ProtossUnit(UnitTypeId.STALKER, 10, priority=True),
            ),
            # 哨兵后置：出门前最后才出（每只 100 气，早出会拖慢电兵 timing）
            # 修复 2 + 追加 2（2026-05-29 细化）：等 6 电兵 ready 后才允许出哨兵
            # （从 4 升 6，配合 HighTemplar=6 目标），数量限制 2 只（力场切阵够用）
            Step(
                UnitExists(UnitTypeId.HIGHTEMPLAR, 6),
                ProtossUnit(UnitTypeId.SENTRY, 2, priority=True),
            ),
            # ---------- VT 一好：出 6 HT（放 Storm 再合 Archon）----------
            # 追加 2（2026-05-29 细化）：6 HT = 3 Archon，叉球一波核心输出最大化
            Step(
                UnitReady(UnitTypeId.TEMPLARARCHIVE, 1),
                ProtossUnit(UnitTypeId.HIGHTEMPLAR, 6, priority=True),
            ),
            # ---------- ★ 电兵合白球（核心！战场放完心灵风暴后，就地合白球）----------
            # ArchonAfterStorm()：energy_threshold=75（心灵风暴费用）+ require_combat=True
            # 两个条件同时满足才合：
            #   1. energy < 75（放不下下一发心灵风暴）
            #   2. 周围 15 格内有敌方战斗单位（在战场，不在家）
            # fresh 电兵（energy=50）在家待命时不合；
            # 战场放完心灵风暴（energy 0-74）+ 附近有敌 → 就地合白球 ✓
            # 2 电兵 → 1 白球；6 电兵 → 3 白球（叉球一波核心输出）
            ArchonAfterStorm(),
            # ---------- Charge Zealot 肉盾（target 24，减少数量避免一发 Storm 全清）----------
            # v2 改造 1（2026-05-29）：叉子从 40 降到 24。
            # 叉子角色 = 肉盾（前排吸伤），不再是唯一输出主力。
            # 数量减少 → 不会被一发 Storm 全清；kite 由追猎承担。
            # 叉子 0 气矿成本，纯矿补，实际受 6 BG 折跃速度限制
            Step(
                UnitReady(UnitTypeId.GATEWAY, 1), ProtossUnit(UnitTypeId.ZEALOT, 24, priority=True)
            ),
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
            # ---------- 4 分钟暴 6 BG（叉球一波关键产能时机）----------
            Step(Time(60 * 4), GridBuilding(UnitTypeId.GATEWAY, 6)),
            # ---------- 战术 / 维护 / 攻击触发 ----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # ★ 出门 timing：Charge + Storm 好 + 2+ Archon(HT 合) + 时间双兜底
                Step(
                    self._attack_gate,
                    PlanZoneAttack(20),  # 20 supply（chargelot + archon 主力）
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_pressure(ai: Any) -> bool:
        """电兵叉球一波 timing：Charge + Psi Storm 好 + 4 电兵 + 6 追猎 ready + 时间双兜底。

        2026-05-29 改造：HT 路线取代 DT 路线。
        2026-05-30 改造：白球改成战场合(ArchonAfterStorm + require_combat=True),
        家里 0 Archon → 出门前不能等"2 Archon ready"否则死循环
        (不出门 → 电兵不放 Storm → 不合 Archon → 永不出门)。
        改成"4 电兵 + 6 追猎 ready" — 出门后到前线放 Storm energy<75 战场合白球,
        追猎 kite 保护电兵。

        - Charge 必完成（chargelot 没 charge = 送菜）
        - PsiStormTech 必完成（HT 的核心输出价值）
        - 至少 4 电兵 ready（出门后战场放 Storm 再合白球,不在家合）
        - 至少 6 追猎 ready（kite 主力,保护电兵放电）
        - army_supply >= 30 兵力够 OR time >= 7:00 timer 兜底
        """
        # 升级：Charge 必完成
        charge_done = (
            ai.already_pending_upgrade(UpgradeId.CHARGE) >= 1.0
            or UpgradeId.CHARGE in ai.state.upgrades
        )
        if not charge_done:
            return False
        # Psi Storm 必完成（HT 的核心输出）
        storm_done = (
            ai.already_pending_upgrade(UpgradeId.PSISTORMTECH) >= 1.0
            or UpgradeId.PSISTORMTECH in ai.state.upgrades
        )
        if not storm_done:
            return False
        # 至少 4 电兵 + 6 追猎 ready
        # 电兵:出门后战场放 Storm,战场合白球(家里不合,ArchonAfterStorm require_combat=True)
        # 追猎:kite 主力,保护电兵放电,数量够才能扛住敌方上来抢
        hts = ai.units(UnitTypeId.HIGHTEMPLAR).ready.amount
        if hts < 4:
            return False
        stalkers = ai.units(UnitTypeId.STALKER).ready.amount
        if stalkers < 6:
            return False
        # 兵力 / 时间双兜底
        unit_supply = {
            UnitTypeId.ZEALOT: 2,
            UnitTypeId.STALKER: 2,
            UnitTypeId.SENTRY: 2,
            UnitTypeId.ARCHON: 4,
            UnitTypeId.HIGHTEMPLAR: 2,
        }
        army_supply = sum(ai.units(ut).ready.amount * sup for ut, sup in unit_supply.items())
        if army_supply >= 30:
            return True
        # 时间兜底：7:00（HT 路线比 DT 路线快 ~50s，从 7:30 提前到 7:00）
        return bool(ai.time >= 60 * 7.0)
