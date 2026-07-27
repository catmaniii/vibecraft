"""vibecraft 速隐刀（DT Rush）plan。

战术核心
========
极速 VD（DarkShrine）+ 野水晶折跃，~4:00 首波 DT 直接在敌方家门口偷家：
  - 早期：速 BY → 折跃 → VC → VD 科技线
  - 野水晶：VD 开建时派 1 农民到敌方家门口隐蔽点修 BE（proxy PYLON）
  - 出击：VD 完成后 DT 直接在野水晶折跃，落地即偷家 —— 不走路
  - 仅 1 追猎：攀 DT 科技期 BG 产能富裕，1 追猎兼顾防守 + 火力侦察
  - 转型：刷满几轮 DT 后通知 Director 推荐转常规打法（iac_2base 等）

为什么用野水晶
==============
旧版 DT 从家里折跃，要走 ~25s 才到敌方主基地，期间极易被 scan / 静态反隐
发现并清掉。野水晶（forward proxy PYLON）提前修在敌方家门口的隐蔽点，VD
一好 DT 直接在那折跃、落地即偷家 —— DT timing 提早 ~25s，且 warp 点隐蔽。
选点逻辑复用 4bg 的 ForwardSupportPylonGateway（隐蔽 / 避敌方视野 / 贴地图
边走廊），build_gateway=False 表示只修水晶不修野 BG（DT 在水晶能量区折跃
即可，不需要前线 BG）。

为什么只出 1 追猎
=================
用户反馈（2026-05-21）：旧版出隐刀前先攒了 4 个追猎，严重拖慢偷家 timing。
攀 VD 科技这一段 BG 产能本来就有富裕，出**恰好 1 个**追猎填补空窗：既能
防守早期骚扰，又能火力侦察对手科技走向。多于 1 个就是浪费 timing。

DT 生产为何不用 sharpy ProtossUnit
==================================
折跃研究完成后 sharpy 的 ProtossUnit(DARKTEMPLAR) 会走 WarpUnit 分支，而
WarpUnit 把 warp target 硬重置回家 NEXUS（见 forward_warp.py 模块注释），
DT 会落在家里得走 ~25s。所以 DT 生产完全交给 ForwardWarpStalker —— 它绕过
该 bug，把所有 ready WARPGATE 的 DT 都折跃到野水晶。

Build 节奏（参考 spawningtool 47308 + 野水晶改造）
================================================
  0:14  BE（Pylon）
  0:35  BG（Gateway）
  0:47  BA x2（双气：DT 极度吃气）
  ~1:00 BY（CyberneticsCore）
  ~1:30 research 折跃 @chrono
  ~2:00 VC（TwilightCouncil）
  ~2:36 VD（DarkShrine）→ 同时补 BG 到 4 + 派 1 农民修野水晶
  ~3:47 VD 完成 → 野水晶持续 warp DT @chrono
  ~3:55 **DT 在敌方家门口落地偷家**
  仅 1 追猎（BY 一好即出，防守 + 侦察）
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
    PlanZoneAttack,
    PlanZoneDefense,
    PlanZoneGather,
    SpeedMining,
)

from vibecraft.bot.auto_combat.protoss.plans.dt_raid import DtRaidAct
from vibecraft.bot.auto_combat.protoss.plans.forward_proxy import ForwardSupportPylonGateway
from vibecraft.bot.auto_combat.protoss.plans.forward_warp import ForwardWarpStalker
from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import EmitOpeningCompleteAct
from vibecraft.bot.auto_combat.protoss.plans.phase_events import EmitPhaseEventAct

# 刷满这么多 DT（dt_trained_count，累计永不回退）后通知 Director 推荐转常规打法
_DT_OPENING_DONE_COUNT: int = 8


class DtRush(KnowledgeBot):  # type: ignore[misc]
    """速隐刀（DT Rush）— 野水晶折跃，~4:00 首波 DT 在敌方家门口偷家。"""

    def __init__(self) -> None:
        super().__init__("VibeCraft DT Rush")

    async def create_plan(self) -> BuildOrder:
        # DT 一落地就偷家:force_attack=True + 关 attack_on_advantage ——
        # DT rush 是 all-in,1 个 DT ready 就让它冲进对方家,不等凑数、不看
        # 「是否占优」、也不跟 enemy_total_power 比较。
        #
        # 仅 start_attack_power=1 + attack_on_advantage=False 不够:sharpy
        # 父类 _should_attack 仍走 enemy_total_power 比较 ——
        # `enemy_total_power.power = max(start_attack_power, enemy_total_power.power)`
        # 把阈值钳成敌方总 power(任何敌方早期单位 power 都 > 2 DT),`power.is_enough_for`
        # 永 False → DT 未进 Attacking role → PlanZoneGather 把它当 idle 拉回 home。
        # (2026-05-22 复现:log game_20260523_021648 telemetry 显示 DT 在 t=265
        # 突然从 d_enemy=45 一路退回 d_home=29)
        #
        # force_attack=True 让 _should_attack 跳过父类 power 比较直接 True,
        # DT 被设成 Attacking role,combat 接管,前线 DT 直接冲。
        attack = PlanZoneAttack(1)
        attack.attack_on_advantage = False
        attack.force_attack = True
        return BuildOrder(
            # ---------- chrono ----------
            # 折跃研究前：chrono 探机（BY 出现则停，没气矿前不 chrono）
            Step(
                None,
                ChronoUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS),
                skip=UnitExists(UnitTypeId.CYBERNETICSCORE, 1),
                skip_until=UnitExists(UnitTypeId.ASSIMILATOR, 1),
            ),
            # 折跃 chrono：BY 完成即刻开始，直到折跃 99%
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ChronoTech(AbilityId.RESEARCH_WARPGATE, UnitTypeId.CYBERNETICSCORE),
            ),
            # DT warp-in chrono：VD 完成后全力 chrono DT（加速偷家 timing）
            Step(
                UnitReady(UnitTypeId.DARKSHRINE, 1),
                ChronoUnit(UnitTypeId.DARKTEMPLAR, UnitTypeId.WARPGATE),
            ),
            # ---------- 野水晶：DT 折跃点提前到敌方家门口 ----------
            # VD 一开建就派 1 农民去敌方家门口隐蔽点修 BE（proxy PYLON）。
            # 为什么 gate 在 VD 开建：野水晶必须从自家抽 1 个矿农（不能抽侦察探机
            # —— 会和侦察 manager 抢 worker，野水晶永远修不起来）。抽矿农会掉
            # ~16s 采矿收入；gate 在 VD 开建之后，这笔成本落在 VD 的 150 矿已经
            # 下单之后，不拖慢 DT 科技线。VD 建造 71s、野水晶走位+修建 ~45s，
            # 能赶在 VD 完成、首波 DT 折跃前就位。
            # build_gateway=False：只修水晶不修野 BG —— DT 在水晶能量区折跃即可。
            # 选点逻辑复用 4bg 的 ForwardSupportPylonGateway（隐蔽 / 避视野 / 贴边）。
            Step(
                UnitExists(UnitTypeId.DARKSHRINE, 1),
                ForwardSupportPylonGateway(build_gateway=False),
            ),
            # VD 完成后 DT 全部在野水晶折跃。ForwardWarpStalker 绕过 sharpy WarpUnit
            # 把 warp target 硬重置回家 NEXUS 的 bug。gate DARKSHRINE ready —— 否则
            # 折跃研究完成（~3:00）到 VD 完成（~3:47）之间会空烧 warpgate CD。
            Step(
                UnitReady(UnitTypeId.DARKSHRINE, 1),
                ForwardWarpStalker(UnitTypeId.DARKTEMPLAR),
            ),
            # 刷满几轮 DT 后通知 Director 推荐转常规打法（只发 toast 不强转 plan，
            # swap_plan=False，玩家自己挑时机切）。
            EmitOpeningCompleteAct(self._opening_done),
            # ---------- Phase 事件触发(2026-05-23 用户 spec)----------
            # 用户反馈:野水晶还没修完,supply 阈值就让 forward phase 显示完成 ——
            # 改用事件触发(forward PYLON ready 才算 phase 起)。
            EmitPhaseEventAct(
                "dt_rush_forward_pylon_ready",
                lambda ai: (
                    ai.structures(UnitTypeId.PYLON).ready.exists
                    and any(
                        p.distance_to(ai.enemy_start_locations[0])
                        < p.distance_to(ai.start_location) * 0.7
                        for p in ai.structures(UnitTypeId.PYLON).ready
                    )
                ),
            ),
            # DT 偷家成功:telemetry.enemy_workers_harassed >= 1(common_bot 维护,
            # 跟踪 on_unit_destroyed/on_unit_took_damage 的敌方农民 tag 集合 size)。
            EmitPhaseEventAct(
                "dt_rush_dt_killed_worker",
                lambda ai: len(getattr(ai, "_harassed_worker_tags", set())) >= 1,
            ),
            # ---------- 早期主线（严守顺序）----------
            SequentialList(
                # 13 农下首水晶（原 14 农 → probe 15 先把 supply 顶到 15/15、水晶没好 →
                # 早期卡人口 8.9s）。提前到 13 农给水晶留出建造时间，消早期 block。
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 13),
                GridBuilding(UnitTypeId.PYLON, 1),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 15),
                GridBuilding(UnitTypeId.GATEWAY, 1),
                # DT 极度吃气 → 早期双气
                BuildGas(1),
                BuildGas(2),
                ActUnit(UnitTypeId.PROBE, UnitTypeId.NEXUS, 19),
            ),
            # ---------- BY 一好 → 折跃 + VC + VD 科技线 ----------
            Step(UnitReady(UnitTypeId.GATEWAY, 1), GridBuilding(UnitTypeId.CYBERNETICSCORE, 1)),
            Step(UnitReady(UnitTypeId.CYBERNETICSCORE, 1), Tech(UpgradeId.WARPGATERESEARCH)),
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                GridBuilding(UnitTypeId.TWILIGHTCOUNCIL, 1),
            ),
            # ---------- VC 一好 → VD（DarkShrine，~2:36）----------
            Step(UnitReady(UnitTypeId.TWILIGHTCOUNCIL, 1), GridBuilding(UnitTypeId.DARKSHRINE, 1)),
            # ---------- 补 4 BG（VD 开建后才补，DT 偷家 timing 优先）----------
            # VD 一开建就补到 4 BG：科技建筑优先抢矿，4 BG 完成仍能跟上 DT warp
            # 产能（4 个 WARPGATE 同时折跃 DT）。
            Step(UnitExists(UnitTypeId.DARKSHRINE, 1), GridBuilding(UnitTypeId.GATEWAY, 4)),
            # 二矿：VD **完成后**才开（~3:47），对齐标准 DT Rush 二矿时机。
            # VD 修好 = DT 科技链全部到位，这时开二矿不再拖慢偷家 timing；
            # DT rush 被克时这个二矿是转运营的经济底子。
            Step(UnitReady(UnitTypeId.DARKSHRINE, 1), Expand(2)),
            # ---------- 第三气矿（DT 出兵需要）----------
            Step(UnitExists(UnitTypeId.NEXUS, 2), BuildGas(3)),
            # ---------- 单位训练 ----------
            # 仅 1 追猎：攀 DT 科技期 BG 产能富裕，1 追猎兼顾防守 + 火力侦察。
            # ProtossUnit 计存活数 —— 死了会补回 1 个，不会累积。
            Step(
                UnitReady(UnitTypeId.CYBERNETICSCORE, 1),
                ProtossUnit(UnitTypeId.STALKER, 1, priority=True),
            ),
            # DT 生产完全交给 ForwardWarpStalker（在野水晶折跃）。不用 sharpy
            # ProtossUnit(DARKTEMPLAR) —— 折跃完成后它走 WarpUnit 分支会把 DT
            # target 硬重置回家 NEXUS，DT 落在家里得走 ~25s 才到敌方。
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
                # DT 个体微操(2026-05-23 用户 spec):第一波 DT 不等其他 DT、
                # 第一时间直奔敌方矿区杀农民、忽略路上敌人;被反隐/被攻击则
                # 自己 release 给 sharpy ZoneAttack + VibeCraftMicroDarkTemplar 接管。
                # 必须排在 PlanZoneGather 之前 —— raid DT 被 Reserved,ZoneGather
                # 不会拽;但 act 顺序更前能确保新 DT 同 tick 内就被标 Reserved。
                DtRaidAct(),
                PlanZoneGather(),
                # DT 一落地就出门偷家；玩家强制 attack 直接绕过时机检查
                Step(
                    lambda ai: (
                        self._ready_to_pressure(ai)
                        or getattr(ai.knowledge.vibecraft, "combat_intent_override", None)
                        == "attack"
                    ),
                    attack,
                ),
                PlanFinishEnemy(),
            ),
        )

    @staticmethod
    def _opening_done(ai: Any) -> bool:
        """开局完成：累计刷满几轮 DT → 通知 Director 推荐转常规打法。

        dt_trained_count 是 on_unit_created 递增、永不回退的累计计数。刷满
        _DT_OPENING_DONE_COUNT 个 DT（~3 轮折跃）后，无论偷家成败，后续都该
        转入常规运营 / 作战。这里只触发推荐 toast（swap_plan=False），dt_rush
        plan 继续跑，玩家自己挑时机切（默认转 iac_2base，见 yaml）。
        """
        try:
            count = getattr(ai.knowledge.vibecraft, "dt_trained_count", 0)
        except Exception:
            return False
        return bool(count >= _DT_OPENING_DONE_COUNT)

    @staticmethod
    def _ready_to_pressure(ai: Any) -> bool:
        """触发出门偷家：VD 完成 + 至少 1 DT ready。

        DT 在野水晶折跃，落地即在敌方家门口 —— 1 个 ready 就立刻让它冲进对方
        家偷农民，不凑数。等凑 3 个的话，头 1-2 个折跃出来的 DT 会被
        PlanZoneGather 当 idle 单位拉回家集结，野水晶「落地即偷家」的优势全废。
        """
        # VD 必须 ready（DT 的前置建筑）
        darkshrine_ready = ai.structures(UnitTypeId.DARKSHRINE).ready.exists
        if not darkshrine_ready:
            return False
        # 至少 1 DT ready（.ready 过滤掉 warp 动画中的 DT）
        dt_count = ai.units(UnitTypeId.DARKTEMPLAR).ready.amount
        return bool(dt_count >= 1)
