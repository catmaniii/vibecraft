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
from vibecraft.bot.auto_combat.protoss.plans.forward_warp import (
    ForwardWarpStalker,
)
from vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack import VibeCraftZoneAttack


class Gate4Pressure(KnowledgeBot):  # type: ignore[misc]  # sharpy 无类型,KnowledgeBot=Any
    """4 BG 早压(无闪烁)— Stalker 折跃 timing 压制。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Gate4 Pressure")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 探机 chrono:仅在 BY 还没造之前用,BY 一出现就停 → 留所有能量给折跃 chrono
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
                skip_until=UnitExists(UnitTypeId.ASSIMILATOR, 1),
            ),
            # 折跃研究 chrono:BY 出现后所有 chrono 持续给 BY,直到折跃 99% 完成
            Step(
                UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
                ChronoTech(AbilityId.RESEARCH_WARPGATE, UnitTypeId.CYBERNETICSCORE),
                skip=TechReady(UpgradeId.WARPGATERESEARCH, 0.99),
            ),
            # 前线 warp:**抢在主线 ProtossUnit(STALKER) 之前 execute** —
            # sharpy BuildOrder.execute 是顺序遍历 orders,sharpy 自带 ProtossUnit
            # 会用所有 ready WARPGATE(含 forward)warp 兵但 target=家 NEXUS。
            # 把 ForwardWarpStalker 放在主线 SequentialList 之前 → 每 step 先抢
            # forward warpgate + mark cooldown_manager.used_ability,sharpy 后续
            # 看到 forward wg in cooldown 就跳过 → 兵真在 forward 区域出生。
            ForwardWarpStalker(UnitTypeId.STALKER),
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
                    # 折跃研究(BY 好了立刻研)
                    Step(
                        UnitReady(UnitTypeId.CYBERNETICSCORE, 1), Tech(UpgradeId.WARPGATERESEARCH)
                    ),
                    # 折跃研究期间补到 4 BG:等 BY 好 + 攒够 450 矿(3 BG 同时下,
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
                ),
            ),
            # 前线支援:**顶层并行**而非嵌在主线 SequentialList 里 —— SequentialList 一直
            # 卡在 22 PROBE 这一步直到达成,导致 forward 实际触发延迟到 ~3min(supply 22+),
            # 那时敌方 scout 满地图巡逻,农民走过去必死(实战 log:2 worker death 终止)。
            # 拎出来 + _forward_ready=1 BG ready → supply ~16(~2:30) 派农民,抢在
            # 敌方 scout 摸到中线前到位 + 修建。
            Step(
                self._forward_ready,
                ForwardSupportPylonGateway(),
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
                # VibeCraftZoneAttack 优先读 knowledge.vibecraft 的 attack/intent override;
                # 玩家强制发 tactical_objective(attack) 时绕过 _ready_to_pressure 时机检查
                Step(
                    lambda ai: (
                        self._ready_to_pressure(ai)
                        or getattr(ai.knowledge.vibecraft, "combat_intent_override", None)
                        == "attack"
                    ),
                    VibeCraftZoneAttack(4),
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _forward_ready(ai: Any) -> bool:
        """ForwardSupport 触发:首个 BG ready 就派农民(supply ~16)。

        历史:
        - v1: BY ready + 4 BG pending (~supply 32) - 太晚,主力都准备出门了
        - v2: BY 开始造 + 总 BG ≥ 3 (~supply 24-26) - 仍太晚,实战 log 显示
          (game_20260518_042334) 农民派出时敌方 scout 已布满地图,走 28s 路过
          敌方视野必死;两次 worker_death 直接触发 D 条件提前终止
        - v3 (当前): 1 BG ready (supply ~16) - 抢在敌方 scout 摸到中线前,
          家里 1 BG + BY 在筑,builder 微紧张但能撑住

        意图:让 forward 农民走在敌方 scout 之前,减少路上交手机会。
        """
        from sc2.ids.unit_typeid import UnitTypeId as _U

        ready_bg = ai.structures.of_type({_U.GATEWAY, _U.WARPGATE}).ready.amount
        return bool(ready_bg >= 1)

    @staticmethod
    def _can_train_stalker(ai: Any) -> bool:
        """sharpy ProtossUnit(STALKER) **只在折跃没完成前**训练(GATEWAY mode)。

        折跃完成后(WARPGATE mode)由 ForwardWarpStalker 完全接管 — 它会把所有
        ready WARPGATE 都 warp 到 forward PYLON 附近。sharpy ProtossUnit 不再
        触发,避免与 ForwardWarpStalker 同帧 race condition 让兵 spawn 在家
        (用户反馈:"前面几个条件都满足的情况下,要杜绝从家里刷兵")。

        BY 未好 / 折跃未完成阶段 → 仍由 sharpy ProtossUnit GATEWAY mode train
        (此阶段没 warpgate,ForwardWarpStalker 直接 return True 让出)。

        防 Gateway 不 morph:sharpy ProtossUnit train 持续触发会让 Gateway 不空闲
        → 不 morph。所以折跃 ready 后立即停 sharpy train,留空闲 Gateway 给
        WarpGate morph(MORPH 后由 ForwardWarpStalker warp)。
        """
        from sc2.ids.unit_typeid import UnitTypeId as _U

        if not ai.structures(_U.CYBERNETICSCORE).ready.exists:
            return False
        warpgate_done = UpgradeId.WARPGATERESEARCH in ai.state.upgrades
        # 折跃完成后 sharpy 不再 train — 把 stalker warp 完全交给 ForwardWarpStalker
        return not warpgate_done

    @staticmethod
    def _three_bg_at_once(ai: Any) -> bool:
        """补 3 BG 的触发条件:折跃研究 >= 50% + 矿 ≥ 450(或已开造)。

        推迟到折跃过半:前期能量都给 chrono BY,矿用来多补 PYLON,
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
