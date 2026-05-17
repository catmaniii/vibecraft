"""vibecraft 4 BG 早压 plan(无 VT / 无闪烁)。

sharpy `dummies/protoss/gate4.py` 的 Stalkers4Gate 实际是 4 BG 闪追
(造 VT + 研 BlinkTech + `TechReady(BLINKTECH, 0.9)` 才出门)。
经典纯 4 Gate 压制是不带 VT 不研究闪烁的,折跃好了就拉一波出门。

参考:SC2 wiki Liberty's "4 Gateway Pressure",TeamLiquid 4 Gate Stalker guide。

build order(supply / 动作):
   9  Pylon
  13  Gateway
  15  Assimilator
  16  CyberneticsCore
  17  Pylon
  20  Assimilator #2(为持续 Stalker 出兵的气)
  21  WarpGateResearch (chrono)
  22  Gateway #2
  23  Gateway #3
  24  Gateway #4 + Pylon
  持续:Stalker × N(所有 BG)
  WarpGate 完成 → 一次折跃 4 Stalker → VibeCraftZoneAttack 出门

不写战斗逻辑:VibeCraftZoneAttack / PlanZoneDefense / DistributeWorkers
全是 sharpy 自带 Manager,我们只是组装 BuildOrder 触发它们。
"""

from __future__ import annotations

from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActUnit, BuildGas, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import (
    AutoPylon,
    ChronoTech,
    ChronoUnit,
    ProtossUnit,
    RestorePower,
)
from sharpy.plans.require import TechReady, UnitExists, UnitReady
from sharpy.plans.tactics import (
    DistributeWorkers,
    PlanFinishEnemy,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.protoss.plans.forward_proxy import (
    ForwardSupportPylonGateway,
)
from vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack import VibeCraftZoneAttack


class Gate4Pressure(KnowledgeBot):  # type: ignore[misc]  # sharpy 无类型,KnowledgeBot=Any
    """4 BG 早压(无闪烁)— Stalker 折跃 timing 压制。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Gate4 Pressure")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 探机 chrono:仅在 BC 还没造之前用,BC 一出现就停 → 留所有能量给折跃 chrono
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
                skip_until=UnitExists(UnitTypeId.ASSIMILATOR, 1),
            ),
            # 折跃研究 chrono:BC 出现后所有 chrono 持续给 BC,直到折跃 99% 完成
            Step(
                UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
                ChronoTech(AbilityId.RESEARCH_WARPGATE, UnitTypeId.CYBERNETICSCORE),
                skip=TechReady(UpgradeId.WARPGATERESEARCH, 0.99),
            ),
            # build order 主线
            SequentialList(
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 14),
                GridBuilding(UnitTypeId.PYLON, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 16),
                GridBuilding(UnitTypeId.GATEWAY, 1),
                BuildGas(1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 17),
                GridBuilding(UnitTypeId.PYLON, 2),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 19),
                Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
                BuildGas(2),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 22),
                # 进入"折跃 + 3 补 BG"阶段
                BuildOrder(
                    AutoPylon(),  # 房子自动补
                    # 折跃研究(BC 好了立刻研)
                    Step(
                        UnitReady(UnitTypeId.CYBERNETICSCORE, 1), Tech(UpgradeId.WARPGATERESEARCH)
                    ),
                    # 折跃研究期间补到 4 BG:等 BC 好 + 攒够 450 矿(3 BG 同时下,
                    # 保证它们同时修好同时升折跃)。攒够后 GridBuilding 一个 step 内尽量下满。
                    Step(
                        self._three_bg_at_once,
                        GridBuilding(UnitTypeId.GATEWAY, 4),
                    ),
                    # 持续 Stalker:折跃完成后若仍有 BG 未 morph 成 WarpGate,
                    # 暂停训练让它们 morph(WarpGate 训练效率更高,且压制 timing 关键)
                    Step(
                        self._can_train_stalker,
                        ProtossUnit(UnitTypeId.STALKER, 100),
                    ),
                    # 前线支援:3 BG 已开始造之后,派 1 农民到前线安全位置修 PY+BG
                    # 时机考虑:补 3 BG 这一步先(massing),农民再出去(forward),
                    # 让 forward 跟折跃完成 timing 对齐,折跃一好前线 BG 也接近完成
                    Step(
                        self._forward_ready,
                        ForwardSupportPylonGateway(),
                    ),
                ),
            ),
            # 战术 / 维护 / 攻击触发(全是 sharpy 自带 Manager)
            SequentialList(
                MineOpenBlockedBase(),
                PlanZoneDefense(),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 4 BG 全部就绪 + 折跃完成 + 4 个 Stalker → 第一波立即压制(火力侦察)
                # VibeCraftZoneAttack(4):4 个就够了,等更多会错过 timing;
                # 出门后会顺便侦察敌方走向科技/造兵情况;
                # VibeCraftZoneAttack 优先读 knowledge.vibecraft 的 attack/intent override
                Step(
                    self._ready_to_pressure,
                    VibeCraftZoneAttack(4),
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _forward_ready(ai: Any) -> bool:
        """ForwardSupport 触发:BC 完成 + 3 BG 已开始造(总 BG≥4 含 pending)。

        意图:补 3 BG 是 massing 阶段,这之后才派农民去前线 — 否则前期
        家里 builder 紧张,影响主基地 build order。
        """
        from sc2.ids.unit_typeid import UnitTypeId as _U

        if not ai.structures(_U.CYBERNETICSCORE).ready.exists:
            return False
        current_bg = ai.structures.of_type({_U.GATEWAY, _U.WARPGATE}).amount
        pending_bg = ai.already_pending(_U.GATEWAY)
        return bool(current_bg + pending_bg >= 4)

    @staticmethod
    def _can_train_stalker(ai: Any) -> bool:
        """折跃完成后,若仍有 ready Gateway(还没 morph 到 WarpGate)→ 暂停训练。

        SC2 引擎:WarpGate 研究完后,空闲 Gateway 会自动 MORPH_WARPGATE。
        若我们一直 train Stalker,Gateway 始终在生产 → 永远不空闲 → 不 morph。
        策略:折跃完成后等所有 BG 都转完(ready GATEWAY 数 = 0)再继续训练。

        BC 未好 / 折跃未完成阶段 → 正常训练(走 BG 训练)。
        """
        from sc2.ids.unit_typeid import UnitTypeId as _U

        if not ai.structures(_U.CYBERNETICSCORE).ready.exists:
            return False
        warpgate_done = UpgradeId.WARPGATERESEARCH in ai.state.upgrades
        if not warpgate_done:
            return True  # 折跃前正常训练
        # 折跃完成后:还有 ready Gateway 没 morph → 等
        ready_bg = ai.structures(_U.GATEWAY).ready.amount
        return bool(ready_bg == 0)

    @staticmethod
    def _three_bg_at_once(ai: Any) -> bool:
        """补 3 BG 的触发条件:折跃研究 >= 50% + 矿 ≥ 450(或已开造)。

        推迟到折跃过半:前期能量都给 chrono BC,矿用来多补 PYLON,
        折跃过半后矿够 → 一次性下 3 BG,保证它们同时修好同时转 WarpGate,
        刚好和折跃完成 timing 对齐。

        已造 + pending 的 BG ≥ 2 时返回 True,让 GridBuilding 继续推到 4。
        """
        from sc2.ids.unit_typeid import UnitTypeId as _U

        if not ai.structures(_U.CYBERNETICSCORE).ready.exists:
            return False
        current_bg = ai.structures.of_type({_U.GATEWAY, _U.WARPGATE}).amount
        pending_bg = ai.already_pending(_U.GATEWAY)
        # 已开始三连下 → 让 GridBuilding 继续推
        if current_bg + pending_bg >= 2:
            return True
        # 折跃过半才允许下 3 BG(否则前期补 PYLON)
        warp_progress = ai.already_pending_upgrade(UpgradeId.WARPGATERESEARCH)
        if warp_progress < 0.5:
            return False
        return bool(ai.minerals >= 450)

    @staticmethod
    def _ready_to_pressure(ai: Any) -> bool:
        """触发出门压制:折跃完成 + 至少 4 BG 就绪 + 至少 6 个 Stalker。

        Step 把第一个参数当作 Callable[ai]->bool 用,等价 RequireCustom。
        """
        warpgate_done: bool = (
            ai.already_pending_upgrade(UpgradeId.WARPGATERESEARCH) >= 1.0
            or UpgradeId.WARPGATERESEARCH in ai.state.upgrades
        )
        if not warpgate_done:
            return False
        gate_count: int = ai.structures.of_type(
            {UnitTypeId.GATEWAY, UnitTypeId.WARPGATE}
        ).ready.amount
        if gate_count < 4:
            return False
        stalker_count: int = ai.units(UnitTypeId.STALKER).amount
        return bool(stalker_count >= 4)  # 第一波 4 个就出门 + 火力侦察
