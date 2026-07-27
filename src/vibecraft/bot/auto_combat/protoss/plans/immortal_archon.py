"""vibecraft 不朽白球 persistent doctrine plan。

地面兵种互补持续运营：不朽 + 白球(Archon) + 冲锋叉子 + 哨兵 + 棱镜多线。
从 robo 系开局（1g_robo_immortal / IAC）转入最顺 —— VR 已就位，转型成本低。

不像开局是固定 supply build，persistent doctrine 是**后期组合驱动**：玩家从
中期开局转过来时已经有 2-3 矿 + CYBERNETICSCORE，剧本要做的是把建筑链补完整
+ 持续 train 关键单位 + 滚地面攻防。

core target composition:
  6 不朽 + 6 白球（HT 合）+ 14 叉子 + 4 哨兵 + 1 棱镜 + 2 Observer

关键路径:
  1. 经济：ProtossPersistentMacro（3 矿 / 满农）
  2. 建筑链：VR×1 / VC → VT / 2 BF / 6 BG
  3. 升级：Charge（VC 完成）+ 地面攻防 weapons/armor 两条并行 SequentialList
     （此流派不研风暴，HT 专用于合 Archon）
  4. 单位：不朽 6 / 叉子 14 / 哨兵 4 / HT 6（合 Archon）/ 棱镜 1 / Observer 2
  5. chrono：VR 完成后 chrono 不朽（核心硬盾输出单位）
  6. 战斗：不朽 + Archon 合计 >= 8 → PlanZoneAttack(8)

假定转入时基础经济已就位；若缺早期建筑（如 CYBERNETICSCORE 都没），各 Step
的 require 会卡住等先决。
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


class ImmortalArchon(KnowledgeBot):  # type: ignore[misc]  # sharpy 无类型，KnowledgeBot=Any
    """不朽白球 — 不朽 + 白球(Archon) + 冲锋叉 + 哨兵 + 棱镜多线持续运营。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Immortal-Archon")
        # 2026-05-28 Issue 4:AttackGate 处理 retreat/defend intent + latch
        from vibecraft.bot.auto_combat.intent_gate import AttackGate

        self._attack_gate = AttackGate(self._ready_to_push)
        # 2026-05-28 Issue 4:AttackGate 处理 retreat/defend intent + latch
        from vibecraft.bot.auto_combat.intent_gate import AttackGate

        self._attack_gate = AttackGate(self._ready_to_push)

    async def create_plan(self) -> BuildOrder:
        # 3 矿稳运营；ProtossPersistentMacro 给 probe chrono + AutoPylon + Expand
        macro = ProtossPersistentMacro(MacroConfig(expansion_cap=3))
        return BuildOrder(
            # ---------- 经济基线（probe + AutoPylon + Expand 3 矿）----------
            *macro.acts(),
            # ---------- 气矿：4 个（3 矿 medium gas，不朽 + HT 吃气）----------
            BuildGas(4),
            # ---------- 建筑链 ----------
            # BY 前置兜底（从无 CC 开局切入时可能缺）
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            # VR×1（不朽 + 棱镜 + Observer 产能）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.ROBOTICSFACILITY, 1),
            ),
            # VC（冲锋 + VT 前置）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),
            # VT（HT 前置；HT 没能量时 sharpy 自动合 Archon）
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                GridBuilding(UnitTypeId.TEMPLARARCHIVE, 1),
            ),
            # 2 BF（地面攻防 weapons / armor 并行研）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.FORGE, 2)),
            # 6 BG（gateway 兵主体：叉子 + 哨兵 + HT）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.GATEWAY, 6)),
            # ---------- 升级 ----------
            # Charge（VC 完成立刻研；叉子核心升级）
            Step(UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1), Tech(UpgradeId.CHARGE)),
            # 地面攻防：weapons 在 BF#1、armor 在 BF#2，两条 SequentialList 并行
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
            # gateway 兵：叉子 + 哨兵 + HT（HT 没能量时 sharpy 自动合 Archon）
            Step(UnitReady(UnitTypeId.GATEWAY, 1), ProtossUnit(UnitTypeId.ZEALOT, 14)),
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), ProtossUnit(UnitTypeId.SENTRY, 4)),
            Step(
                UnitReady(UnitTypeId.TEMPLARARCHIVE, 1),
                ProtossUnit(UnitTypeId.HIGHTEMPLAR, 6),
            ),
            # 2026-06-02 用户:补合白球 act —— HT 战场+家里都合(原来没有任何合体,
            # HT 永远堆着不合)。MergeArchon 无脑就近合 ≥2 HT。
            MergeArchon(UnitTypeId.HIGHTEMPLAR),
            # ---------- chrono：VR 完成后 chrono 不朽（核心硬盾 DPS）----------
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
                # 不朽 + Archon 合计 >= 8 → 出门多线推；玩家强制 attack 直接绕过
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

        不朽硬盾 + 白球 AoE 是核心战力；叉子 / 哨兵 / 棱镜在 free_army 里随大军走。
        """
        immortals = ai.units(UnitTypeId.IMMORTAL).amount
        archons = ai.units(UnitTypeId.ARCHON).amount
        return bool(immortals + archons >= 8)
