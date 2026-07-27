"""vibecraft 闪追扰袭 persistent doctrine plan。

追猎闪现打游击 + 干扰者球分割，VC 闪现 + VR→VB 干扰者，4 矿快铺，
靠骚扰换矿磨对手。从任意开局（1g_robo / IAC）转入均可——VC 是唯一新建。

核心 target composition:
  20 追猎 + 4 干扰者 + 3 不朽 + 3 Observer

关键路径:
  1. 经济：ProtossPersistentMacro（4 矿快铺）
  2. 建筑链：VC（闪现前置）/ VR → VB（干扰者前置）/ 2 BF（地面攻防）/ 7 BG
  3. 升级：Blink（VC 完成立刻）+ 地面攻防 weapons/armor 两条并行 SequentialList
  4. 单位：追猎 20 / 干扰者 4 / 不朽 3 / Observer 3
  5. chrono：追猎（核心兵种，需要快速凑足 14 出门）
  6. 战斗：14+ 追猎 → PlanZoneAttack 多线骚扰推

不写早期 supply 节点 build（14 BE / 16 BG 等）—— 假设转入时基础经济已就位。
若缺早期建筑（如 CYBERNETICSCORE 都没），各 Step 的 require 会卡住等先决。
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


class BlinkHarass(KnowledgeBot):  # type: ignore[misc]  # sharpy 无类型,KnowledgeBot=Any
    """闪追扰袭 — 追猎闪现 + 干扰者球多线骚扰持续运营。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Blink-Harass")
        # 2026-05-28 Issue 4:AttackGate 处理 retreat/defend intent + latch
        from vibecraft.bot.auto_combat.intent_gate import AttackGate

        self._attack_gate = AttackGate(self._ready_to_push)

    async def create_plan(self) -> BuildOrder:
        # 4 矿快铺；ProtossPersistentMacro 给 probe chrono + AutoPylon + Expand
        macro = ProtossPersistentMacro(MacroConfig(expansion_cap=4))
        return BuildOrder(
            # ---------- 经济基线（probe + AutoPylon + Expand 4 矿）----------
            *macro.acts(),
            # ---------- 气矿：6 个（4 矿满气；追猎 + 干扰者 + 地面攻防吃气）----------
            BuildGas(6),
            # ---------- 建筑链 ----------
            # CC 前置兜底（从无 robo 开局切入时可能缺）
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            # VC（Blink / 闪现前置）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),
            # VR（干扰者 + 不朽 + Observer 前置）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.ROBOTICSFACILITY, 1),
            ),
            # VB（干扰者前置；VR 完成后立刻建）
            Step(
                UnitReady(UnitTypeId.ROBOTICSFACILITY, 1),
                GridBuilding(UnitTypeId.ROBOTICSBAY, 1),
            ),
            # 2 BF（地面攻防 weapons / armor 并行研）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.FORGE, 2)),
            # 7 BG（追猎主体产能；闪追需要快速凑 14+ 追猎）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.GATEWAY, 7)),
            # ---------- 升级 ----------
            # Blink / 闪现（VC 完成立刻研，是整个 doctrine 的核心 tech）
            Step(UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1), Tech(UpgradeId.BLINKTECH)),
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
            # 追猎（核心兵种；CC ready 即开始，7 BG 全力暴）
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), ProtossUnit(UnitTypeId.STALKER, 20)),
            # 干扰者（VB 完成后出；甩球分割敌方聚团）
            Step(UnitReady(UnitTypeId.ROBOTICSBAY, 1), ProtossUnit(UnitTypeId.DISRUPTOR, 4)),
            # 不朽（VR ready 后补；顶前排吃集火，保追猎机动）
            Step(UnitReady(UnitTypeId.ROBOTICSFACILITY, 1), ProtossUnit(UnitTypeId.IMMORTAL, 3)),
            # Observer（反隐；VR ready 立刻）
            Step(UnitReady(UnitTypeId.ROBOTICSFACILITY, 1), ProtossUnit(UnitTypeId.OBSERVER, 3)),
            # ---------- chrono：追猎（核心兵种，需快速凑 14 出门）----------
            ChronoUnit(UnitTypeId.STALKER, UnitTypeId.GATEWAY),
            # ---------- 战术 / 维护 / 战斗触发 ----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 14+ 追猎 → 出门多线骚扰；2026-05-28 Issue 4:AttackGate 含
                # retreat/defend/hold + latch,玩家任意 intent 都让 PlanZoneAttack 跑。
                Step(self._attack_gate, PlanZoneAttack(14)),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_push(ai: Any) -> bool:
        """出门条件：14+ 追猎 ready —— 足够多线骚扰；干扰者 / 不朽随后跟上。

        闪追核心是追猎数量 + Blink，不必等干扰者凑齐；干扰者在 free_army 里随走。
        """
        return bool(ai.units(UnitTypeId.STALKER).amount >= 14)
