"""vibecraft 4 BG 早压 plan(无 VT / 无闪烁)。

sharpy `dummies/protoss/gate4.py` 的 Stalkers4Gate 实际是 4 BG 闪追
(造 VT + 研 BlinkTech + `TechReady(BLINKTECH, 0.9)` 才出门)。
经典纯 4 Gate 压制是不带 VT 不研究闪烁的,折跃好了就拉一波出门。

参考:SC2 wiki Liberty's "4 Gateway Pressure",TeamLiquid 4 Gate Stalker guide。

build order(supply / 动作):
   9  Pylon
  13  Gateway (家里 #1)
  15  Assimilator
  16  CyberneticsCore
  17  Pylon
  20  Assimilator #2(为持续 Stalker 出兵的气)
  21  WarpGateResearch (chrono)
  22  Gateway (家里 #2)
  23  Gateway (家里 #3) —— 2026-05-19 用户修正：家里到 3 BG 即可
  ~3:00 ForwardSupportPylonGateway 启动 → 前线野 BE + 1 野 BG
  持续:Stalker × N(所有 BG，含前线)
  WarpGate 完成 → 一次折跃 4 Stalker(家 3 + 前线 1) → VibeCraftZoneAttack 出门
  总 BG = 4（家 3 + 野 1）

不写战斗逻辑:VibeCraftZoneAttack / PlanZoneDefense / DistributeWorkers
全是 sharpy 自带 Manager,我们只是组装 BuildOrder 触发它们。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder, SequentialList, Step
from sharpy.plans.acts import ActBase, ActUnit, BuildGas, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import (
    AutoPylon,
    ChronoTech,
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
from vibecraft.bot.auto_combat.protoss.plans.forward_rally import (
    ForwardRallyStalker,
)
from vibecraft.bot.auto_combat.protoss.plans.forward_warp import (
    ForwardWarpStalker,
)
from vibecraft.bot.auto_combat.protoss.plans.vibecraft_zone_attack import VibeCraftZoneAttack

logger = logging.getLogger(__name__)


class EmitOpeningCompleteAct(ActBase):  # type: ignore[misc]
    """开局完成条件首次满足时,通知 Director 自动切持续策略 — 一次性。

    用户反馈(2026-05-20):"4bg 的条件都满足后,除了开启进攻压制模式以外,
    宏观策略可以自动切换到一个持续策略"。逻辑:每 tick 调 completion_check(ai),
    True → ai.director.notify_opening_completed(now);之后 act 自身 latch _signaled
    永远 return True 不再 check。Director 端也有自己的 _opening_completed_signaled
    防双重保险。

    Director 不存在(集成测试 / 单元测试)时,silent skip。
    """

    def __init__(self, completion_check: Callable[[Any], bool]) -> None:
        super().__init__()
        self._completion_check = completion_check
        self._signaled = False

    async def execute(self) -> bool:
        if self._signaled:
            return True  # 本 act 任务完成,sharpy 后续 skip
        try:
            done = bool(self._completion_check(self.ai))
        except Exception:
            return False
        if not done:
            return False  # 还没达成,下 tick 再 check
        director = getattr(self.ai, "director", None)
        if director is not None:
            try:
                now = float(self.ai.time)
                triggered = director.notify_opening_completed(now)
                if triggered:
                    logger.info(
                        "opening_completed signaled to director (game_t=%.1f)", now
                    )
            except Exception as exc:
                logger.warning("notify_opening_completed fail: %s", exc)
        self._signaled = True
        return True


class Gate4Pressure(KnowledgeBot):  # type: ignore[misc]  # sharpy 无类型,KnowledgeBot=Any
    """4 BG 早压(无闪烁)— Stalker 折跃 timing 压制。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Gate4 Pressure")

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            # 2026-05-19 用户修正：删除探机 chrono，所有能量留给折跃研究
            # 原因：探机 chrono ~1 次 = 多 1 个探机 ~50 矿，但折跃研究多 1 次
            # chrono = 早 15s 出门 timing，对 4BG 一波价值更高
            # 折跃研究 chrono:BY 出现后所有 chrono 持续给 BY,直到折跃 99% 完成
            Step(
                UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
                ChronoTech(AbilityId.RESEARCH_WARPGATE, UnitTypeId.CYBERNETICSCORE),
                skip=TechReady(UpgradeId.WARPGATERESEARCH, 0.99),
            ),
            # 2026-05-19 用户修正：ForwardWarpStalker 只在折跃完成 + 4 BG 全部 ready 后启用
            # 原因：折跃没好时 forward warpgate 还没 morph 完，提前激活无用且
            # 可能让兵在不对的位置 spawn。等齐了再开门集中刷
            Step(
                self._all_4bg_warpgate_ready,
                ForwardWarpStalker(UnitTypeId.STALKER),
            ),
            # 2026-05-20 用户修正:4bg 完成条件(`_ready_to_pressure`)首次满足时,
            # 除了触发 VibeCraftZoneAttack 进攻,还通知 Director 切持续策略 —
            # 取代 4bg 开局期的宏观角色,让 LLM 后续围绕持续 doctrine 推荐辅助 directive。
            EmitOpeningCompleteAct(self._ready_to_pressure),
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
                    # 2026-05-20 用户修正:GridBuilding(GATEWAY, N) 中 N 是**总数**目标
                    # (含前线野 BG)。之前传 3 → 1 家里 + 1 前线 = 2 → 再补 1 家里 =
                    # 2 家里 + 1 前线 = 3 总 = 用户反馈"家里才 2 BG 就开门了"。
                    # 改 4 → 总数 4 = 3 家里 + 1 前线,符合原意"家 3 + 野 1 = 4"。
                    # 这同时修复下游 Issue:之前 3 BG < _all_4bg_warpgate_ready 阈值
                    # 4 → ForwardWarpStalker 永不启动 → 折跃完成后无兵刷。
                    Step(
                        self._three_bg_at_once,
                        GridBuilding(UnitTypeId.GATEWAY, 4),
                    ),
                    # 2026-05-19 用户修正：折跃完成前**只出 1 追猎**做探路 + 防守
                    # 原本 cap=100 = 暴兵浪费产能；用户要"折跃好了才在前线集中刷兵"
                    # 折跃 ready 后 _can_train_stalker 返回 False，stop home train，
                    # 改由 ForwardWarpStalker 接管在前线 warp（条件：4 BG 全 ready）
                    Step(
                        self._can_train_stalker,
                        ProtossUnit(UnitTypeId.STALKER, 1),
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
                # 2026-05-20 用户修正:warp 出来的 stalker 在前线野 BG 集结,不要被
                # PlanZoneGather 拽回家。Step gate 跟 ForwardWarpStalker 同步:
                # 折跃完成 + 4 BG 全 ready 才启用前线集结(之前 ProtossUnit 阶段的
                # 1 个 stalker 仍走默认 ZoneGather → 当家里防守用)。
                # 放在 PlanZoneGather 之后 → 后发先至覆盖 home rally;放在
                # VibeCraftZoneAttack 之前 → attack 命令再次覆盖本 act 的 move。
                Step(self._all_4bg_warpgate_ready, ForwardRallyStalker()),
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
    def _all_4bg_warpgate_ready(ai: Any) -> bool:
        """ForwardWarpStalker 触发条件（2026-05-19 用户修正）:
        折跃研究完成 + 4 BG 全部 ready（含前线野 BG）→ 集中前线刷兵。

        前线 warp 之前 ProtossUnit(STALKER, 1) 已出过 1 追猎；之后所有兵都
        在前线 spawn，避免兵从家走过去浪费时间。
        """
        from sc2.ids.unit_typeid import UnitTypeId as _U

        warpgate_done = UpgradeId.WARPGATERESEARCH in ai.state.upgrades
        if not warpgate_done:
            return False
        bg_count = ai.structures.of_type({_U.GATEWAY, _U.WARPGATE}).ready.amount
        return bool(bg_count >= 4)

    @staticmethod
    def _can_train_stalker(ai: Any) -> bool:
        """sharpy ProtossUnit(STALKER) 的训练门:

        - 折跃未完成 → True,GATEWAY mode 出 1 个 stalker(防守 + 探路)
        - 折跃完成 + 4 BG 全 ready → False,完全交 ForwardWarpStalker
          (防同帧 race 让兵 spawn 在家;用户反馈"杜绝从家里刷兵")
        - **折跃完成但 4 BG 没集齐** → True,sharpy 兜底继续训练防"哑火"
          (用户反馈 2026-05-20:"BG 折跃变形后没有刷兵"=没法走 forward warp
          就连家里也不训了 → 全卡死;cap=1 保证不暴兵)

        防 Gateway 不 morph:sharpy ProtossUnit train 持续触发会让 Gateway 不空闲
        → 不 morph。所以 4 BG 都 ready 后(意味着 morph 已经完成)立即停 sharpy
        train,留产线给 ForwardWarpStalker warp。
        """
        from sc2.ids.unit_typeid import UnitTypeId as _U

        if not ai.structures(_U.CYBERNETICSCORE).ready.exists:
            return False
        warpgate_done = UpgradeId.WARPGATERESEARCH in ai.state.upgrades
        if not warpgate_done:
            return True  # 折跃没完成 → GATEWAY mode 训 1 个
        # 折跃完成,看 4 BG 是否集齐
        bg_ready = ai.structures.of_type({_U.GATEWAY, _U.WARPGATE}).ready.amount
        # < 4 → sharpy 兜底继续 train(cap=1 防过度);≥ 4 → 让 ForwardWarpStalker 接管
        return bool(bg_ready < 4)

    @staticmethod
    def _three_bg_at_once(ai: Any) -> bool:
        """补 BG 的触发条件:折跃研究 >= 50% + 矿 ≥ 300(或已开造)。

        2026-05-19 用户修正：家里目标 3 BG（之前 4），前线 1 野 BG 由
        ForwardSupportPylonGateway 出，总数仍是 4。

        推迟到折跃过半:前期能量都给 chrono BY,矿用来多补 PYLON,
        折跃过半后矿够 → 一次性下 2 BG,保证它们同时修好同时转 WarpGate,
        刚好和折跃完成 timing 对齐。

        已造 + pending 的 BG ≥ 2 时返回 True,让 GridBuilding 继续推到 3。
        """
        from sc2.ids.unit_typeid import UnitTypeId as _U

        if not ai.structures(_U.CYBERNETICSCORE).ready.exists:
            return False
        current_bg = ai.structures.of_type({_U.GATEWAY, _U.WARPGATE}).amount
        pending_bg = ai.already_pending(_U.GATEWAY)
        # 已开始连下 → 让 GridBuilding 继续推到 3
        if current_bg + pending_bg >= 2:
            return True
        # 折跃过半才允许下 BG(否则前期补 PYLON)
        warp_progress = ai.already_pending_upgrade(UpgradeId.WARPGATERESEARCH)
        if warp_progress < 0.5:
            return False
        # 300 矿 = 2 BG × 150 (一次性下 2 同时修好)
        return bool(ai.minerals >= 300)

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
