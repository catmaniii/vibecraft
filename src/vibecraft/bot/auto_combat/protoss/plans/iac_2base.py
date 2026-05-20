"""vibecraft 叉球一波（pure Chargelot + Archon timing push）plan。

战术核心（2026-05-19 用户重新定义）
==================================
**最终主力只有 2 个兵种：Chargelot + Archon**（白球用 DT 合，不用 HT）。
追猎仅 1-2 只用于火力侦查 + 防早期偷袭；其它资源全压 DT + Charge + 攻防升级 + 暴叉子。

  - 主力：Charge Zealot × 18-24（叉子主力，切入对方阵地）
  - 溅伤：Archon × 4-6（由 DT 合，每 2 DT = 1 Archon；8 DT 合 4 Archon）
  - 火力侦查：Stalker × 1-2（不当主力，前压试探用）
  - 力场切阵：Sentry × 2（前压时切对方部队 / 守家时挡冲锋）

参考: Stats PvZ 5:34 4HT/6:05 出门 build（但本变体改用 DT 合而非 HT，
所以出门 timing 比 Stats 晚 ~50s，约 7:00；DarkShrine 71s 比 Templar Archive 36s 慢）

关键升级（全部跑完才出门）
==========================
1. WarpgateResearch（必，BY 一好立刻研，~140s）
2. Charge（必，VC 一好立刻研，~100s）
3. ProtossGroundWeaponsLevel1（+1 攻；charge 叉 dps 提升显著；Forge）
4. ProtossGroundArmorsLevel1（+1 防；硬度提升，扛住对面集火）

Build 节奏
==========
  1:25  二矿（natural NX）
  1:35  BY（CyberneticsCore）
  2:10  Warpgate research
  2:38  VC (TwilightCouncil)
  3:00  Charge research（VC 一好立刻研）+ BF (Forge)
  3:14  VD (Dark Shrine)
  3:30  +1 攻 + +1 防（Forge 一好双研）
  4:00  暴 6 BG
  4:38  第一批 DT × 4 出门
  5:26  第二批 DT × 4 出门
  5:30  Archon 开始合（2 → 4 → 6）
  ~7:00 出门 attack（Charge 完 + +1/+1 完 + 4+ Archon + 12+ Chargelot）

设计取舍
========
- 不出 Immortal（不要 VR，所有 gas 都给 DT 和升级）
- 不出 HT 不研 Storm（DT 合 Archon 不需要 HT/TA）
- 不出 Adept / Observer（资源紧缺；对面没隐形单位时 OB 不必要）
- 多 1 个建筑（VD 71s vs HT 路线 36s TA）→ timing 比 Stats 晚 ~50s
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


class IacTwoBase(KnowledgeBot):  # type: ignore[misc]
    """叉球一波（pure Chargelot + Archon）— DT 合 Archon，~7:00 timing 出门"""

    def __init__(self) -> None:
        super().__init__("VibeCraft IAC 2-base")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
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
            # DT chrono：VD 一好后持续 chrono DT，够 8 个停（DT 是 Archon 池子）
            Step(
                UnitReady(UnitTypeId.DARKSHRINE, 1),
                ChronoUnit(UnitTypeId.DARKTEMPLAR, UnitTypeId.GATEWAY),
                skip=UnitExists(UnitTypeId.DARKTEMPLAR, 8, include_pending=True),
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
            # 注：二矿触发故意是 UnitReady(BG)，不是 UnitExists(BG)。实测把它提前
            # 到 BG 一开建就下二矿，二矿早抢 400 矿 → VC/VD/Charge/DT 科技线全被
            # 拖垮（dark_shrine 296→381s），验收 15→11。iac 科技密集，二矿不能再早。
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
            # ---------- VC 一好：立刻研 Charge + chrono + 起 VD + BF ----------
            Step(UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1), Tech(UpgradeId.CHARGE)),
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                ChronoTech(AbilityId.RESEARCH_CHARGE, UnitTypeId.TWILIGHTCOUNCIL),
            ),
            # 黑暗神殿 VD：DT 前置（71s 造时，比 TemplarArchive 36s 慢 → 出门 timing 晚 ~50s）
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                GridBuilding(UnitTypeId.DARKSHRINE, 1),
            ),
            # Forge：用户明确要 BF 升攻防
            Step(UnitExists(UnitTypeId.NEXUS, 2), GridBuilding(UnitTypeId.FORGE, 1)),
            # +1 攻 + +1 防（Forge 一好同时双研）
            Step(UnitReady(UnitTypeId.FORGE, 1), Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1)),
            Step(UnitReady(UnitTypeId.FORGE, 1), Tech(UpgradeId.PROTOSSGROUNDARMORSLEVEL1)),
            # ---------- 防身（少量 Stalker 火力侦查 + 切阵 Sentry，没 Adept / Immortal / Observer）----------
            ProtossUnit(UnitTypeId.STALKER, 2, priority=True),
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.SENTRY, 2, priority=True),
            ),
            # ---------- VD 一好：出 8 个 DT（2 批 ×4，全部进 Archon 池）----------
            Step(
                UnitReady(UnitTypeId.DARKSHRINE, 1),
                ProtossUnit(UnitTypeId.DARKTEMPLAR, 8, priority=True),
            ),
            # ---------- ★ DT 合 Archon（核心！sharpy Archon([DARKTEMPLAR]) 自动）----------
            # 2 DT → 1 Archon；8 DT → 4 Archon。
            Step(
                UnitExists(UnitTypeId.DARKTEMPLAR, 2),
                Archon([UnitTypeId.DARKTEMPLAR]),
            ),
            # ---------- Charge Zealot 主力（target 24，叉球一波的主体）----------
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
            # 不需要 7 BG，因为没 Immortal/不朽，产能瓶颈在 gas（DT/charge 烧气），mineral 给叉子够 6 BG 出
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
                # ★ 出门 timing：Charge 完 + +1/+1 攻防完 + 4+ Archon + 兵力 / 时间双兜底
                Step(
                    lambda ai: (
                        self._ready_to_pressure(ai)
                        or getattr(ai.knowledge.vibecraft, "combat_intent_override", None)
                        == "attack"
                    ),
                    VibeCraftZoneAttack(20),  # 20 supply（chargelot + archon 主力）
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_pressure(ai: Any) -> bool:
        """叉球一波 timing：Charge + +1 攻 + +1 防 + 2+ Archon + 兵力/时间双兜底。

        2026-05-19 重写：用户要求最终组合只有 Chargelot + Archon；攻防升级好了才推。
        - Charge 必完成（chargelot 没 charge = 送菜）
        - +1 ground weapon 完成（叉子 dps 提升显著）
        - +1 ground armor 完成（扛住对面集火）
        - 至少 2 Archon ready（白球溅伤是 IAC 的核心 anti-bio/light）
        - army_supply >= 30 兵力够 OR time >= 7:30 timer 兜底
        """
        # 升级三件套
        upgrades_required = [
            UpgradeId.CHARGE,
            UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1,
            UpgradeId.PROTOSSGROUNDARMORSLEVEL1,
        ]
        for upg in upgrades_required:
            done = ai.already_pending_upgrade(upg) >= 1.0 or upg in ai.state.upgrades
            if not done:
                return False
        # 至少 2 Archon ready（DT 合，溅伤主力）
        archons = ai.units(UnitTypeId.ARCHON).ready.amount
        if archons < 2:
            return False
        # 兵力 / 时间双兜底
        unit_supply = {
            UnitTypeId.ZEALOT: 2,
            UnitTypeId.STALKER: 2,
            UnitTypeId.SENTRY: 2,
            UnitTypeId.ARCHON: 4,
            UnitTypeId.DARKTEMPLAR: 2,
        }
        army_supply = sum(ai.units(ut).ready.amount * sup for ut, sup in unit_supply.items())
        if army_supply >= 30:
            return True
        # 时间兜底：7:30（DarkShrine 71s 比 TA 36s 慢 → Stats 6:05 + ~50s + 容错）
        return bool(ai.time >= 60 * 7.5)
