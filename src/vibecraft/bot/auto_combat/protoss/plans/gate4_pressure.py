"""vibecraft 4 BG 早压 plan(无 VC / 无闪烁)。

sharpy `dummies/protoss/gate4.py` 的 Stalkers4Gate 实际是 4 BG 闪追
(造 VC + 研 BlinkTech + `TechReady(BLINKTECH, 0.9)` 才出门)。
经典纯 4 Gate 压制是不带 VC 不研究闪烁的,折跃好了就拉一波出门。

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
  WarpGate 完成 → 一次折跃 4 Stalker(家 3 + 前线 1) → PlanZoneAttack 出门
  总 BG = 4（家 3 + 野 1）

不写战斗逻辑:PlanZoneAttack / PlanZoneDefense / DistributeWorkers
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
    PlanZoneAttack,
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

logger = logging.getLogger(__name__)


def _make_force_attack_act(start_attack_power: int) -> PlanZoneAttack:
    """2026-05-24 用户:4bg 一波 all-in,PlanZoneAttack 设 force_attack=True
    跳过 sharpy 父类的 power 比较 — 出门就死也要冲,不让 sharpy 因 enemy_local_power
    高(SCV 在场被高估等)把兵拉回家变 Idle。"""
    act = PlanZoneAttack(start_attack_power)
    act.force_attack = True
    return act


class Gate4StateLogger(ActBase):  # type: ignore[misc]
    """每 N 秒 dump 一次 4bg 关键状态到 log,debug 用。

    输出每个 stalker 的 tag/position/role/orders,以及 BG/WG count + warpgate
    research 进度。用户反馈"神秘力量把追猎引回家"时,从 log 能直接看到 stalker
    的 role 从 Idle/Moving 变成 Defending 的瞬间 + 谁拉的(zone position)。
    """

    def __init__(self, interval_s: float = 5.0) -> None:
        super().__init__()
        self.interval_s = interval_s
        self._last_log_t: float = -1000.0

    async def execute(self) -> bool:
        from sharpy.managers.core.roles import UnitTask

        try:
            now = float(self.ai.time)
        except Exception:
            return True
        if now - self._last_log_t < self.interval_s:
            return True
        self._last_log_t = now

        try:
            stalkers = list(self.ai.units(UnitTypeId.STALKER))
        except Exception:
            stalkers = []

        # BG/WG state
        try:
            bg_total = self.ai.structures.of_type({UnitTypeId.GATEWAY, UnitTypeId.WARPGATE}).amount
            bg_ready = self.ai.structures.of_type(
                {UnitTypeId.GATEWAY, UnitTypeId.WARPGATE}
            ).ready.amount
        except Exception:
            bg_total = bg_ready = 0
        warpgate_done = UpgradeId.WARPGATERESEARCH in self.ai.state.upgrades
        try:
            warp_progress = float(self.ai.already_pending_upgrade(UpgradeId.WARPGATERESEARCH))
        except Exception:
            warp_progress = 0.0

        # gather_point — 通过 IGatherPointSolver interface 拿(直接访问
        # `self.knowledge.gather_point_solver` 不存在,只有 KnowledgeBot 实例上有)
        try:
            from sharpy.interfaces import IGatherPointSolver

            gp = self.knowledge.get_required_manager(IGatherPointSolver).gather_point
            gp_str = f"({gp.x:.1f},{gp.y:.1f})"
        except Exception:
            gp_str = "?"

        # supply / 资源
        try:
            res_str = f"M{self.ai.minerals} G{self.ai.vespene} S{self.ai.supply_used}/{self.ai.supply_cap}"
        except Exception:
            res_str = "?"

        logger.info(
            "4bg-state t=%.1f %s BG=%d/%d WG-research=%.0f%%/%s gather=%s stalkers=%d",
            now,
            res_str,
            bg_ready,
            bg_total,
            warp_progress * 100,
            "done" if warpgate_done else "pending",
            gp_str,
            len(stalkers),
        )

        # 每只 stalker 的 role + position + order
        task_names = {
            UnitTask.Idle: "Idle",
            UnitTask.Moving: "Moving",
            UnitTask.Fighting: "Fighting",
            UnitTask.Defending: "Defending",
            UnitTask.Attacking: "Attacking",
            UnitTask.Reserved: "Reserved",
            UnitTask.Scouting: "Scouting",
        }
        for s in stalkers:
            # 查任务(逐一 is_in_role)。sharpy `Knowledge.roles` 是 UnitRoleManager。
            task_str = "?"
            try:
                for task_id, name in task_names.items():
                    if self.knowledge.roles.is_in_role(task_id, s):
                        task_str = name
                        break
            except Exception:
                pass

            ready = "ready" if s.is_ready else f"warp={s.build_progress:.0%}"
            # orders 摘要
            order_str = "idle"
            try:
                orders = list(getattr(s, "orders", []) or [])
                if orders:
                    o = orders[0]
                    aid = getattr(getattr(o, "ability", None), "id", None)
                    target = getattr(o, "target", None)
                    if hasattr(target, "x") and hasattr(target, "y"):
                        order_str = f"{aid}→({target.x:.0f},{target.y:.0f})"
                    else:
                        order_str = f"{aid}→{target}"
            except Exception:
                pass
            logger.info(
                "  stalker tag=%d pos=(%.1f,%.1f) %s task=%s order=%s",
                s.tag,
                s.position.x,
                s.position.y,
                ready,
                task_str,
                order_str,
            )
        return True


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
                    logger.info("opening_completed signaled to director (game_t=%.1f)", now)
            except Exception as exc:
                logger.warning("notify_opening_completed fail: %s", exc)
        self._signaled = True
        return True


class Gate4Pressure(KnowledgeBot):  # type: ignore[misc]  # sharpy 无类型,KnowledgeBot=Any
    """4 BG 早压(无闪烁)— Stalker 折跃 timing 压制。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft Gate4 Pressure")
        # 2026-06-13 用户实测(多人局"面板显示进攻但部队不动"):主攻 gate 原是裸
        # lambda(_ready_to_pressure or intent==attack)——追猎被打死条件翻 False →
        # Step 返 False → SequentialList 整段停 → PlanZoneAttack/PlanFinishEnemy
        # 全冻,直到玩家手动按进攻。换锁存式 AttackGate(开过就不再关),对齐其余
        # 15 个剧本的既有模式(blink_stalker 等,2026-05-28 Issue 4 同根因)。
        from vibecraft.bot.auto_combat.intent_gate import AttackGate

        self._attack_gate = AttackGate(self._ready_to_pressure)

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
            # 2026-05-20 用户反馈"有钱有CD不刷兵":去掉 Step gate(原来要求 4 BG
            # 全 ready)。ForwardWarpStalker 内部自检 forward PYLON 存在 + WG 列表
            # 非空,任一不满足就 noop 返回 True。直接每 tick 跑,只要 1 个 WG ready
            # 就 warp 1 个,不浪费产能。也消除"前 7s morph 窗口 + 后续 BG 阵亡降至
            # 3 时 gate False 永不 warp" 的死锁。
            ForwardWarpStalker(UnitTypeId.STALKER),
            # 2026-05-20 用户修正:4bg 完成条件(`_ready_to_pressure`)首次满足时,
            # 除了触发 PlanZoneAttack 进攻,还通知 Director 切持续策略 —
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
                    # 2026-05-20 用户反馈"钱和人口都有,刷兵频率不够":pre-warpgate
                    # cap 从 1 提到 3。1 home GATEWAY 32s/stalker × 3 = ~96s 充分
                    # 利用单 GATEWAY 产能 + 双气矿气量,3 个 stalker 等 ForwardRally
                    # 把它们带到 forward 集结,折跃完成后立即与 ForwardWarpStalker
                    # 刷的 4 个汇合 → 7 个 stalker 出门远比 4 个稳。
                    # 折跃完成后 _can_train_stalker 仍然 False,sharpy ProtossUnit
                    # 走 WarpUnit 分支会硬重置到 home nexus(已踩过坑),完全交
                    # ForwardWarpStalker 在 forward 刷。
                    Step(
                        self._can_train_stalker,
                        ProtossUnit(UnitTypeId.STALKER, 3),
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
            # 2026-05-20 用户反馈"神秘力量把追猎引回家":根因 = PlanZoneDefense
            # 的 get_defenders 会从 Idle/Moving/Fighting/**Attacking** 各任务拉
            # 最近的兵当 defender(unit_role_manager.py:142-150),包括正在前往
            # forward / 正在攻击中的 stalker。enemy 1 个 probe scout 在家附近就
            # 触发 → 拉 1-2 个 stalker 回家 → 标 Defending → 不在 free_units →
            # PlanZoneAttack 看不见 → 卡家。
            # 修:前线 PYLON 建好(= 进入 4bg 推进阶段)后 skip PlanZoneDefense,
            # 不再让它抽兵。前期(forward PYLON 没建好之前)正常防守 — 那时还没
            # 攻击意图,enemy 1-2 个 scout 拉 1 个 stalker 守家 OK。
            # ALSO Bot 状态日志:每 5s dump 一次 stalker 角色/位置/订单,debug 用。
            Gate4StateLogger(interval_s=5.0),
            # 战术 / 维护 / 攻击触发(全是 sharpy 自带 Manager)
            SequentialList(
                MineOpenBlockedBase(),
                Step(
                    None,
                    PlanZoneDefense(),
                    skip=lambda ai: Gate4Pressure._forward_pylon_exists(ai),
                ),
                RestorePower(),
                DistributeWorkers(),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanZoneGather(),
                # 2026-05-20 用户反馈"野BG 刷的兵还是回家集结点":根因 = 之前
                # Step gate 让 forward_rally 在 _all_4bg_warpgate_ready False 时返
                # False,SequentialList 停在这,PlanZoneAttack 永远不运行。
                # 而 forward_rally 成功 set 时也曾返 False,同样卡死。
                # 现在 ForwardRallyStalker 内部检测 forward PYLON,有就 set
                # gather_point=forward,没有就 noop;**始终 return True**,
                # 不阻断 SequentialList。直接放,不需要 Step 包装。
                # 这样 pre-warpgate 的 1-3 个 home stalker 也会通过 sharpy
                # PlanZoneGather + gather_point=forward 走到 forward 集结 / 防守
                # (用户反馈"第一个追猎也可以到野bg待命也起防守作用")。
                ForwardRallyStalker(),
                # 4 BG 全部就绪 + 折跃完成 + 4 个 Stalker → 第一波立即压制(火力侦察)
                # PlanZoneAttack(4):4 个就够了,等更多会错过 timing;
                # 出门后会顺便侦察敌方走向科技/造兵情况;
                # PlanZoneAttack 优先读 knowledge.vibecraft 的 attack/intent override;
                # 玩家强制发 tactical_objective(attack) 时绕过 _ready_to_pressure 时机检查
                #
                # 2026-05-24 用户反馈:"4bg 刷的追猎没去对方家里进攻,站野水晶处不动"
                # 根因: sharpy 父类 _should_attack/_should_retreat 用 own_power vs
                # enemy_local_power 比较,11 stalker × 2 power = 22 < enemy 25 →
                # 触发 retreat,之后即使刷出更多兵也卡在"power 不够"状态。
                # 修: force_attack=True — 4bg 一波本质是 all-in,出门就死也要冲,
                # 不该让 sharpy power 启发式拉回家。
                # 2026-06-13:裸 lambda → 锁存式 AttackGate(见 __init__ 注释)。
                # AttackGate 自带 intent override 开门 + 满编兜底,语义覆盖原 lambda。
                Step(self._attack_gate, _make_force_attack_act(4)),
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
    def _forward_pylon_exists(ai: Any) -> bool:
        """前线 proxy PYLON 是否 ready(距敌方 < 距家 * 0.7 视为 forward)。

        与 forward_warp / forward_rally 用同一个判定逻辑,保持一致。
        用于 PlanZoneDefense 的 skip 条件:forward PYLON ready → 进入 4bg 推进
        阶段,defense 关闭防止抽兵。
        """
        try:
            home = ai.start_location
            enemy = ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return False
        try:
            pylons = ai.structures(UnitTypeId.PYLON).ready
        except Exception:
            return False
        for py in pylons:
            d_home = py.distance_to(home)
            d_enemy = py.distance_to(enemy)
            if d_enemy < d_home * 0.7:
                return True
        return False

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
        """sharpy ProtossUnit(STALKER) **只在折跃没完成前**训练(GATEWAY mode)。

        折跃完成后(WARPGATE mode)由 ForwardWarpStalker 完全接管,sharpy ProtossUnit
        不再触发。原因(2026-05-20 用户反馈"家里一直有几个追猎没出门"):
        - sharpy WarpUnit 硬重置 target 到最近 NEXUS(`warp_unit.py:68-73`),所以
          一旦 ProtossUnit 走 WarpUnit 分支,兵就 spawn 在家而不是 gather_point
        - 之前曾加 `bg_ready < 4 → True` 兜底,但攻击中 stalker 阵亡 +
          BG 偶尔被打到 → 触发 sharpy WarpUnit → 在家累积"卡家"兵
        - 折跃完成后,即使 4 BG 没集齐,也宁可不刷(等 ForwardWarpStalker 在
          forward 刷),也不要在家 spawn。
        """
        from sc2.ids.unit_typeid import UnitTypeId as _U

        if not ai.structures(_U.CYBERNETICSCORE).ready.exists:
            return False
        warpgate_done = UpgradeId.WARPGATERESEARCH in ai.state.upgrades
        return not warpgate_done

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
        """触发出门压制:折跃完成 + 至少 3 BG 就绪 + 至少 4 个**完成 warp** 的 Stalker。

        2026-05-20 用户反馈"出门时间可以更早":
        - BG ≥ 3(而不是 4):前 3 个 BG 在 warpgate 完成时已经 morph 好,第 4 个野 BG
          可能 ~5s 后才 morph。等齐 4 BG 才出门会推后 timing。3 BG ready 时
          ForwardWarpStalker 已经能刷 3 个 warp,加上 pre-warpgate cap=3 的 home
          stalker 走到 forward,总数远超 4。
        - Stalker ≥ 4 仍然保留:首波要够分量。`.ready` filter 排除 warp 动画中
          (build_progress < 1.0)的兵,等真正可战斗才触发。
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
        if gate_count < 3:
            return False
        # .ready 过滤掉 warp 动画中的 stalker
        stalker_count: int = ai.units(UnitTypeId.STALKER).ready.amount
        return bool(stalker_count >= 4)
