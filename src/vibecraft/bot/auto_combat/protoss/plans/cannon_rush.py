"""vibecraft 炮塔速攻（Cannon Rush）plan。

战术核心
========
探机提前修 BF（Forge）+ BC（PhotonCannon）在对方 natural 附近，压制矿线：
  - 主炮轰：1:27 前 BC 开始建造，压制 Drone/Probe 采矿
  - 护盾电池（BB）撑炮塔存活，延长压制窗口
  - 后手追猎（Stalker）：折跃完成后前出配合炮塔，钳住对方

关键路径
========
1. 12 探机前出（去对方 natural / main 附近）
2. 家里 BG + BA 同时下，BF 修在前线
3. BF 完成 → 立刻 BC × 2-3，配合 BB × 2 撑
4. 家里 BY → 折跃 → 追猎后手
5. 追猎前出支援，若炮塔压住 → 开二矿延续

Build 节奏（参考 spawningtool.com 111586，PvZ Cannon Rush / Proxy Stalkers）
===========================================================================
  12  探机前出（去对方 natural 旁）
  14  BE（home Pylon）
  16  BF（forward，靠近对方 natural BE）
  16  BA（home Assimilator）
  18  BC x2（forward，压制矿线）
  18  BG（home Gateway）
  18  BE（forward Pylon，给 BC 供电）
  19  BC（第 3 个加强封锁）
  20  BY（home CyberneticsCore）
  20  BG × 2（home，第 2 门）
  21  Stalker @chrono（保家 + 后手）
  22  Warp Gate @chrono
  23  BB × 2（Shield Battery，撑炮塔）
  25  Stalker @chrono
  27  Stalker @chrono
  27  折跃完成 → 追猎前出
  31  BG（第 3 门，提升追猎产能）

注意：真实前线建筑（forward BF / BC）需要 ForwardProxy 类支持。
本 plan 用 GridBuilding 在 home 位置建 BF / BC（sharpy 默认放在家附近的
网格位），前线 proxy 需要 ForwardSupportPylonGateway 类扩展实现（暂无，
留 TODO：未来加 forward_cannon_proxy.py）。
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

from vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack import VibeCraftZoneAttack


class CannonRush(KnowledgeBot):  # type: ignore[misc]
    """炮塔速攻（Cannon Rush）— 速 BF + BC 偷家，追猎后手。PvZ/PvP。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Cannon Rush")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # ---------- chrono ----------
            # 探机 chrono 到 BY 出现
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
                skip_until=UnitExists(UnitTypeId.ASSIMILATOR, 1),
            ),
            # 折跃 chrono（BY 完成后，把能量全给折跃研究）
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ChronoTech(AbilityId.RESEARCH_WARPGATE, UnitTypeId.CYBERNETICSCORE),
            ),
            # ---------- 早期主线（严守顺序）----------
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 12),
                GridBuilding(UnitTypeId.PYLON, 1),  # 14 supply BE
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                # BF（Forge）：家附近先建（TODO 真前线 proxy 需 forward_cannon_proxy）
                GridBuilding(UnitTypeId.FORGE, 1),
                BuildGas(1),
                # BG 同时开
                GridBuilding(UnitTypeId.GATEWAY, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 18),
            ),
            # ---------- BC（Photon Cannon）× 3 —— BF 完成立刻建 ----------
            # sharpy GridBuilding 会找网格位（默认在家附近；前线 proxy 留 TODO）
            Step(UnitReady(UnitTypeId.FORGE, 1), GridBuilding(UnitTypeId.PHOTONCANNON, 3)),
            # ---------- 家里补 BG × 2 + BY ----------
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), GridBuilding(UnitTypeId.GATEWAY, 3)),
            # ---------- 折跃研究 ----------
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), Tech(UpgradeId.WARPGATERESEARCH)),
            # ---------- 护盾电池 × 2（撑炮塔存活）----------
            Step(UnitReady(UnitTypeId.FORGE, 1), GridBuilding(UnitTypeId.SHIELDBATTERY, 2)),
            # ---------- 后续 BC 扩张（把对方矿线完全锁住）----------
            Step(UnitReady(UnitTypeId.FORGE, 1), GridBuilding(UnitTypeId.PHOTONCANNON, 6)),
            # ---------- 二矿延续（炮塔压住后开）----------
            Step(UnitExists(UnitTypeId.PHOTONCANNON, 3), Expand(2)),
            # ---------- 二矿气矿 ----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(2)),
            # ---------- 单位训练 ----------
            # 追猎（折跃好后前出，配合炮塔钳制）
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
                    VibeCraftZoneAttack(6),  # 6 Stalker 出门配合炮塔
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
