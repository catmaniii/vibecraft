"""vibecraft 炮塔速攻（Cannon Rush）plan。

战术核心
========
探机提前修 BF（Forge）+ BC（PhotonCannon）在对方 natural 附近，压制矿线：
  - 主炮轰：BF 尽早完成（< 1:30），BC 立刻跟建压制 Drone/Probe 采矿
  - 护盾电池（BB）在追猎出来后建，撑前线炮塔存活
  - 后手追猎（Stalker）：折跃完成后前出配合炮塔，钳住对方

关键路径
========
1. 12 探机前出（去对方 natural 旁），BF 修在前线（forward_cannon_proxy）
2. 家里 BG（先下，不要 BA —— 炮塔速攻阶段不需要气矿）
3. BF 完成 → 立刻 BC × 2-3（在对方矿线旁，压制核心）
4. 家里 BY → 折跃 → 追猎后手
5. 追猎出来后建 BB × 2 撑前线炮塔
6. 追猎前出支援，若炮塔压住 → 开二矿延续

Build 节奏（参考 spawningtool.com 111586，PvZ Cannon Rush / Proxy Stalkers）
===========================================================================
  12  探机前出（去对方 natural 旁，ForwardCannonProxy 接管）
  14  BE（home Pylon）
  16  BF（forward，ForwardCannonProxy 自动建造）
  18  BC x2（forward，BC 紧跟 BF 完成后建）
  18  BG（home Gateway，**无 BA**—— 炮塔速攻阶段纯矿）
  19  BC（第 3 个加强封锁）
  20  BY（home CyberneticsCore）
  20  BG × 2（共 3 门，追猎产能）
  21  Stalker @chrono（保家 + 后手）
  22  Warp Gate @chrono
  23  BB × 2（追猎出来后才建，撑前线炮塔）
  25  Stalker
  27  折跃完成 → 追猎前出
  31  BG（第 4 门）
"""

from __future__ import annotations

from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import (
    AutoPylon,
    ChronoTech,
    ChronoUnit,
    ProtossUnit,
    RestorePower,
)
from sharpy.plans.require import UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanCancelBuilding,
    PlanFinishEnemy,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.protoss.plans.forward_cannon_proxy import ForwardCannonProxy
from vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack import VibeCraftZoneAttack


class CannonRush(KnowledgeBot):  # type: ignore[misc]
    """炮塔速攻（Cannon Rush）— 速 BF + BC 偷家，追猎后手。PvZ/PvP。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Cannon Rush")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # ---------- 前线 proxy（核心！BF + BC 建在对方 natural 矿线旁）----------
            # ForwardCannonProxy 派 1 探机去对方 natural 附近：
            #   先建 BF（Forge）提供电力，BF 完成后立刻建 BC（PhotonCannon）压矿
            # 这是 cannon rush 存在的必要条件，不在前线就等于无效战术
            ForwardCannonProxy(),
            # ---------- chrono ----------
            # 探机 chrono：开局一直开,到 BY 出现停(下一个 step 折跃 chrono 接管)。
            # 2026-05-20 修 bug:原来有 `skip_until=UnitExists(ASSIMILATOR,1)`,但炮塔
            # 速攻**不建气矿**,ASSIMILATOR 永不存在 → skip_until 永不满足 → Step 永远
            # return True → ChronoUnit 整个 act 从未执行。删掉 skip_until,探机 chrono
            # 从开局就跑(skip=CYBERNETICSCORE 保证 BY 一出现就停)。
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
            ),
            # 折跃 chrono（BY 完成后，把能量全给折跃研究）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ChronoTech(AbilityId.RESEARCH_WARPGATE, UnitTypeId.CYBERNETICSCORE),
            ),
            # ---------- 早期主线（严守顺序）----------
            # 炮塔速攻阶段不需要气矿（BF + BC 都是纯矿建筑）：
            # BA 移除，把矿留给 BF 更快建造
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 12),
                GridBuilding(UnitTypeId.PYLON, 1),  # 14 supply BE（家里）
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                # 家里先下 BG（BF 由 ForwardCannonProxy 在前线负责）
                GridBuilding(UnitTypeId.GATEWAY, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 18),
            ),
            # ---------- 家里补 BY ----------
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            # ---------- 补到 3 门（追猎产能）----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.GATEWAY, 3)),
            # ---------- 折跃研究 ----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), Tech(UpgradeId.WARPGATERESEARCH)),
            # ---------- 护盾电池 × 2（追猎出来后才建，比标准 3:07 对齐）----------
            # 标准 build：BB 在 Stalker 出来后才建（撑前线炮塔）
            # 不在 BF ready 时立刻建（炮塔速攻早期矿要留给 BG/BY）
            Step(
                UnitExists(UnitTypeId.STALKER, 1),
                GridBuilding(UnitTypeId.SHIELDBATTERY, 2),
            ),
            # ---------- 二矿延续（至少 3 BC 压住后开）----------
            Step(UnitExists(UnitTypeId.PHOTONCANNON, 3), Expand(2)),
            # ---------- 二矿气矿 ----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(1)),
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(2)),
            # ---------- 后期补 BG（追猎产能）----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.GATEWAY, 4)),
            # ---------- 单位训练 ----------
            # 追猎（折跃好后前出，配合前线炮塔钳制）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.STALKER, 12),
            ),
            # ---------- 经济 ----------
            AutoPylon(),
            ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 44),
            # ---------- 战术 / 维护 / 攻击触发 ----------
            SequentialList(
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 折跃完成 + 3+ BC + 6 Stalker → 出门配合炮塔
                # 玩家强制 attack 绕过
                Step(
                    lambda ai: (
                        self._ready_to_pressure(ai)
                        or getattr(ai.knowledge.vibecraft, "combat_intent_override", None)
                        == "attack"
                    ),
                    VibeCraftZoneAttack(6),  # 6 Stalker 出门配合前线炮塔
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _ready_to_pressure(ai: Any) -> bool:
        """炮塔速攻出门 timing：折跃完成 + 3 BC + 6 Stalker ready。

        炮塔压住矿线后，追猎前出配合封锁 —— 让对方无法 clear cannon，
        被迫分兵，加速经济崩盘。
        """
        # 折跃完成（追猎折跃比 Gateway 训练快 1.5x）
        warpgate_done = (
            ai.already_pending_upgrade(UpgradeId.WARPGATERESEARCH) >= 1.0
            or UpgradeId.WARPGATERESEARCH in ai.state.upgrades
        )
        if not warpgate_done:
            return False
        # 至少 3 BC ready（炮塔已建成，有威慑力）
        cannon_count = ai.structures(UnitTypeId.PHOTONCANNON).ready.amount
        if cannon_count < 3:
            return False
        # 6 Stalker 出门
        stalker_count = ai.units(UnitTypeId.STALKER).ready.amount
        return bool(stalker_count >= 6)
