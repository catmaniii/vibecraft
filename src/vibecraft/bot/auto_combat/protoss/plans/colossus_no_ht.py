"""vibecraft 机械巨像无电兵变体 — 纯 VR+VB 路线，去掉 HT/VT 链路。

与原版 ColossusImmortal 的差异：
- **删除** TC → VT → HT 链路（TemplarArchive / HighTemplar 全部删除）
- **删除** PsiStorm 研究
- **保留** TC（仅用于 Charge 升级）
- **删除** Archon 目标（巨像 AoE 已覆盖，不需要 Archon 补充 splash）
- 叉子上限从 8 升到 10（多补叉子填满 HT 空出的 supply slot）

设计理由：
  Colossus 是 AoE 核心，HT 风暴是奢侈品而非必需。
  Liquipedia 把 Colossus 路线和 HT 路线列为两条对立方向。
  去掉 VT(36s) → 转型窗口缩短 ~60s → 巨像更快上车 timing。

core target composition:
  6 Colossus + 6 Immortal + 12 Stalker + 10 Zealot + 2 Observer

关键路径（相对原版的变更点已标 [CHANGE]）：
  1. 建筑链：VR×2 → VB / VC（[CHANGE] 无 VT）/ 2 BF / 5 BG
  2. 升级：巨像射程 + Charge + 地面攻防 1/2/3；[CHANGE] 不研 PsiStorm
  3. 单位：巨像 6 / 不朽 6 / 追猎 12 / 叉子 10 / Observer 2
     [CHANGE] 无 HT / 无 Archon 目标
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


class ColossusNoHT(KnowledgeBot):  # type: ignore[misc]  # sharpy 无类型,KnowledgeBot=Any
    """机械巨像无电兵 — 巨像 + 不朽 + 冲锋叉；去掉 VT/HT 链路，转型更快。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Colossus-NoHT")
        from vibecraft.bot.auto_combat.intent_gate import AttackGate

        self._attack_gate = AttackGate(self._ready_to_push)

    async def create_plan(self) -> BuildOrder:
        macro = ProtossPersistentMacro(MacroConfig(expansion_cap=3))
        return BuildOrder(
            # ---------- 经济基线 ----------
            *macro.acts(),
            # ---------- 气矿：6 个（3 矿满气）----------
            BuildGas(6),
            # ---------- 建筑链 ----------
            # BY 前置兜底
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
            # VC（Charge 前置；[CHANGE] 不继续建 VT）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),
            # [CHANGE] 不建 TemplarArchive — VT 链路整体删除
            # 2 BF（地面攻防 weapons / armor 并行）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.FORGE, 2)),
            # 5 BG（追猎 + 叉子主体）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.GATEWAY, 5)),
            # ---------- 升级 ----------
            # 巨像射程（VB 完成立刻）
            Step(UnitReady(UnitTypeId.ROBOTICSBAY, 1), Tech(UpgradeId.EXTENDEDTHERMALLANCE)),
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
            # 巨像（核心 AoE）；不朽（硬盾前排）；Observer（反隐）
            Step(UnitReady(UnitTypeId.ROBOTICSBAY, 1), ProtossUnit(UnitTypeId.COLOSSUS, 6)),
            Step(UnitReady(UnitTypeId.ROBOTICSFACILITY, 1), ProtossUnit(UnitTypeId.IMMORTAL, 6)),
            Step(UnitReady(UnitTypeId.ROBOTICSFACILITY, 1), ProtossUnit(UnitTypeId.OBSERVER, 2)),
            # gateway 兵：追猎 + 叉子（[CHANGE] 无 HT，叉子上限从 8 升到 10）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), ProtossUnit(UnitTypeId.STALKER, 12)),
            Step(UnitReady(UnitTypeId.GATEWAY, 1), ProtossUnit(UnitTypeId.ZEALOT, 10)),
            # [CHANGE] 不出 HighTemplar
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
                # 4+ 巨像 → 出门（无电兵版同原版 timing 相当，转型反而更快）
                Step(self._attack_gate, PlanZoneAttack(4)),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_push(ai: Any) -> bool:
        """出门条件：4+ 巨像 ready — 死球核心 AoE 够了就强 timing 推。"""
        return bool(ai.units(UnitTypeId.COLOSSUS).amount >= 4)
