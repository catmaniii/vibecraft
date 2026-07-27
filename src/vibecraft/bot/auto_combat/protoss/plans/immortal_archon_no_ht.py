"""vibecraft 不朽白球隐刀合球变体 — DT 合 Archon，去掉 HT/VT 链路。

与原版 ImmortalArchon 的差异：
- **删除** TC → VT → HT 链路（TemplarArchive / HighTemplar 全部删除）
- **新增** TC → VD → DT × 8 → Archon × 4（VD = DarkShrine，每 2 DT 合 1 Archon）
- **删除** PsiStorm 研究
- Archon 来源完全改为 DT 合球

设计理由：
  iac_2base 本身就是 DT 偷家开局，转 persistent_immortal_archon_no_ht 路线
  最自然 —— VD 已在建或刚好需要，DT 合 Archon 无缝延续。
  HT 合 Archon 与 DT 合 Archon 的最终产物一致（白球 AoE），差异：
    - VT(36s) 比 VD(71s) 快 35s，但 HT 需要 gas 出法力值才有价值
    - DT 直接合 Archon，对 Ghost EMP 免疫（Archon 无魔法值，EMP 只破盾）
    - 与 iac_2base 开局路线一致，无额外建筑分叉

core target composition:
  6 Immortal + 4 Archon（DT 合）+ 14 Zealot + 4 Sentry + 1 WarpPrism + 2 Observer

关键路径（相对原版的变更点已标 [CHANGE]）：
  1. 建筑链：VR×1 / VC / [CHANGE] VD（代替 VT）/ 2 BF / 6 BG
  2. 升级：Charge（VC 完成）+ 地面攻防 1/2/3
     [CHANGE] 不研 PsiStorm
  3. 单位：不朽 6 / 叉子 14 / 哨兵 4 / [CHANGE] DT 8（合 Archon 4）/ 棱镜 1 / Observer 2
     [CHANGE] 无 HT
"""

from __future__ import annotations

from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import BuildGas, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import ChronoUnit, ProtossUnit, RestorePower
from sharpy.plans.require import UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.persistent_macro import MacroConfig, ProtossPersistentMacro
from vibecraft.bot.auto_combat.protoss.plans.merge_archon_at_home import MergeArchon


class ImmortalArchonNoHT(KnowledgeBot):  # type: ignore[misc]  # sharpy 无类型，KnowledgeBot=Any
    """不朽白球隐刀合球 — 不朽 + DT 合 Archon + 冲锋叉 + 哨兵 + 棱镜。

    主要变体（相对原版 ImmortalArchon）：
    - VT/TemplarArchive/HT/PsiStorm 全部删除
    - VD(DarkShrine) → DT × 8 → Archon × 4
    - 与 iac_2base 开局路线一致（DT 开局 → DT 合球后期）
    """

    def __init__(self) -> None:
        super().__init__("VibeCraft Immortal-Archon-NoHT")
        from vibecraft.bot.auto_combat.intent_gate import AttackGate

        self._attack_gate = AttackGate(self._ready_to_push)

    async def create_plan(self) -> BuildOrder:
        macro = ProtossPersistentMacro(MacroConfig(expansion_cap=3))
        return BuildOrder(
            # ---------- 经济基线 ----------
            *macro.acts(),
            # ---------- 气矿：4 个（medium gas；不朽 + DT 吃气）----------
            BuildGas(4),
            # ---------- 建筑链 ----------
            # BY 前置兜底
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            # VR×1（不朽 + 棱镜 + Observer 产能）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.ROBOTICSFACILITY, 1),
            ),
            # VC（Charge + VD 前置）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),
            # [CHANGE] VD（DarkShrine；替换 TemplarArchive）
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                GridBuilding(UnitTypeId.DARKSHRINE, 1),
            ),
            # 2 BF（地面攻防 weapons / armor 并行）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.FORGE, 2)),
            # 6 BG（叉子 + 哨兵 + DT 主体）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.GATEWAY, 6)),
            # ---------- 升级 ----------
            # Charge（VC 完成立刻；叉子核心）
            Step(UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1), Tech(UpgradeId.CHARGE)),
            # [CHANGE] 不研 PsiStorm（无 TemplarArchive）
            # 地面攻防：weapons BF#1 / armor BF#2，两条 SequentialList 并行
            SequentialList(
                Step(UnitReady(UnitTypeId.FORGE, 1), Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1)),
                Step(UnitReady(UnitTypeId.FORGE, 1), Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL2)),
                Step(UnitReady(UnitTypeId.FORGE, 1), Tech(UpgradeId.PROTOSSGROUNDWEAPONSLEVEL3)),
            ),
            SequentialList(
                Step(UnitReady(UnitTypeId.FORGE, 2), Tech(UpgradeId.PROTOSSGROUNDARMORSLEVEL1)),
                Step(UnitReady(UnitTypeId.FORGE, 2), Tech(UpgradeId.PROTOSSGROUNDARMORSLEVEL2)),
                Step(UnitReady(UnitTypeId.FORGE, 2), Tech(UpgradeId.PROTOSSGROUNDARMORSLEVEL3)),
            ),
            # ---------- 单位训练 ----------
            # 不朽（核心硬盾前排）；Observer（反隐）；棱镜（多线运兵）
            Step(UnitReady(UnitTypeId.ROBOTICSFACILITY, 1), ProtossUnit(UnitTypeId.IMMORTAL, 6)),
            Step(UnitReady(UnitTypeId.ROBOTICSFACILITY, 1), ProtossUnit(UnitTypeId.OBSERVER, 2)),
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                ProtossUnit(UnitTypeId.WARPPRISM, 1),
            ),
            # gateway 兵：叉子 + 哨兵
            Step(UnitReady(UnitTypeId.GATEWAY, 1), ProtossUnit(UnitTypeId.ZEALOT, 14)),
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), ProtossUnit(UnitTypeId.SENTRY, 4)),
            # DT × 8 → 合 Archon × 4（DT 合白球；2026-06-02 补合体 act）
            Step(
                UnitReady(UnitTypeId.DARKSHRINE, 1),
                ProtossUnit(UnitTypeId.DARKTEMPLAR, 8),
            ),
            # 2026-06-02 用户:补合白球 act —— DT 战场+家里都合(原来没有任何合体,
            # DT 永远堆着不合)。MergeArchon 无脑就近合 ≥2 DT。
            MergeArchon(UnitTypeId.DARKTEMPLAR),
            # [CHANGE] 不出 HighTemplar
            # ---------- chrono：VR 完成后 chrono 不朽 ----------
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                ChronoUnit(UnitTypeId.IMMORTAL, UnitTypeId.ROBOTICSFACILITY),
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
                # 不朽 + Archon 合计 >= 8 → 出门
                Step(
                    self._attack_gate,
                    PlanZoneAttack(8),
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_push(ai: Any) -> bool:
        """出门条件：不朽 + Archon 合计 >= 8。

        DT 合的 Archon 与 HT 合的 Archon 战斗力完全相同；
        叉子 / 哨兵 / 棱镜在 free_army 里随大军走，不必等齐。
        """
        immortals = ai.units(UnitTypeId.IMMORTAL).amount
        archons = ai.units(UnitTypeId.ARCHON).amount
        return bool(immortals + archons >= 8)
