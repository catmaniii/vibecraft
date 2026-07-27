"""vibecraft 机械巨像 persistent doctrine plan。

地面死球持续运营：巨像 + 不朽 + 追猎 + 叉子 + HT 风暴。从 robo 系开局
（1g_robo_immortal / iac_2base）转入最顺 —— VR 已就位，转型成本低。

不像开局是固定 supply build，persistent doctrine 是**后期组合驱动**：玩家从
中期开局转过来时已经有 2-3 矿 + CYBERNETICSCORE，剧本要做的是把建筑链补完整
+ 持续 train 关键单位 + 滚地面攻防。

core target composition:
  6 巨像 + 6 不朽 + 12 追猎 + 8 叉子 + 4 HT（部分自动合 Archon）+ 2 Observer

关键路径:
  1. 经济：ProtossPersistentMacro（3 矿 / 满农）
  2. 建筑链：VR×2 → VB（巨像前置）/ VC → VT（HT/风暴）/ 2 BF（地面攻防）/ 5 BG
  3. 升级：巨像射程 + 风暴 + 冲锋 + 地面攻防 1/2/3（weapons / armor 两条并行）
  4. 单位：巨像 6 / 不朽 6 / 追猎 12 / 叉子 8 / HT 4 / Observer 2
  5. chrono：VB 完成后 chrono 巨像（核心 AoE DPS）
  6. 战斗：4+ 巨像 → PlanZoneAttack 强 timing 推

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


class ColossusImmortal(KnowledgeBot):  # type: ignore[misc]  # sharpy 无类型,KnowledgeBot=Any
    """机械巨像 — 巨像 + 不朽 + 追猎 + 叉子 + HT 地面死球持续运营。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Colossus-Immortal")
        # 2026-05-28 Issue 4:AttackGate 处理 retreat/defend intent + latch
        from vibecraft.bot.auto_combat.intent_gate import AttackGate

        self._attack_gate = AttackGate(self._ready_to_push)

    async def create_plan(self) -> BuildOrder:
        # 3 矿稳运营；ProtossPersistentMacro 给 probe chrono + AutoPylon + Expand
        macro = ProtossPersistentMacro(MacroConfig(expansion_cap=3))
        return BuildOrder(
            # ---------- 经济基线（probe + AutoPylon + Expand 3 矿）----------
            *macro.acts(),
            # ---------- 气矿：6 个（3 矿满气，巨像 + HT 吃气）----------
            BuildGas(6),
            # ---------- 建筑链 ----------
            # CC 前置兜底（从无 robo 开局切入时可能缺）
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            # VR ×2（巨像 + 不朽 双线产能）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.ROBOTICSFACILITY, 2),
            ),
            # VB（巨像前置）
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                GridBuilding(UnitTypeId.ROBOTICSBAY, 1),
            ),
            # VC → VT（HT / 风暴 / 冲锋前置）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),
            Step(
                UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1),
                GridBuilding(UnitTypeId.TEMPLARARCHIVE, 1),
            ),
            # 2 BF（地面攻防 weapons / armor 并行研）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.FORGE, 2)),
            # 5 BG（gateway 兵主体：追猎 + 叉子 + HT）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.GATEWAY, 5)),
            # ---------- 升级 ----------
            # 巨像射程（VB 完成立刻）
            Step(UnitReady(UnitTypeId.ROBOTICSBAY, 1), Tech(UpgradeId.EXTENDEDTHERMALLANCE)),
            # 风暴（VT 完成立刻）
            Step(UnitReady(UnitTypeId.TEMPLARARCHIVE, 1), Tech(UpgradeId.PSISTORMTECH)),
            # 冲锋（VC 完成立刻）
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
            # 巨像（核心 AoE）；不朽（硬盾前排）；Observer（反隐）
            Step(UnitReady(UnitTypeId.ROBOTICSBAY, 1), ProtossUnit(UnitTypeId.COLOSSUS, 6)),
            Step(UnitReady(UnitTypeId.ROBOTICSFACILITY, 1), ProtossUnit(UnitTypeId.IMMORTAL, 6)),
            Step(UnitReady(UnitTypeId.ROBOTICSFACILITY, 1), ProtossUnit(UnitTypeId.OBSERVER, 2)),
            # gateway 兵：追猎 + 叉子 + HT（HT 没能量时 sharpy 自动合 Archon）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), ProtossUnit(UnitTypeId.STALKER, 12)),
            Step(UnitReady(UnitTypeId.GATEWAY, 1), ProtossUnit(UnitTypeId.ZEALOT, 8)),
            Step(UnitReady(UnitTypeId.TEMPLARARCHIVE, 1), ProtossUnit(UnitTypeId.HIGHTEMPLAR, 4)),
            # ---------- chrono：VB 完成后 chrono 巨像 ----------
            Step(
                UnitReady(UnitTypeId.ROBOTICSBAY, 1),
                ChronoUnit(UnitTypeId.COLOSSUS, UnitTypeId.ROBOTICSFACILITY),
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
                # 4+ 巨像 → 出门强 timing 推;2026-05-28 Issue 4:AttackGate
                Step(self._attack_gate, PlanZoneAttack(4)),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_push(ai: Any) -> bool:
        """出门条件：4+ 巨像 ready —— 死球核心 AoE 够了就强 timing 推。

        巨像是 DPS 核心；不朽 / 追猎 / HT 在 free_army 里随大军走，不必等齐。
        """
        return bool(ai.units(UnitTypeId.COLOSSUS).amount >= 4)
