"""PhoenixSquadAct: squad-aware 凤凰控制 act。

取代旧 PhoenixHarassAct（旧 act 留作 dead code 可回退）。

行为（每 tick）
===============
1. wave gating — 第一波必须攒 wave_threshold(默认 5)凤凰才 launch；
   未 launch 时凤凰 Reserved + stay home。
2. wave launch 那一刻 → 调 director.notify_phoenix_harass_started(开始时间, 截止时间)
   一次，让 Director 创建"凤凰骚扰"持久指令卡（玩家可见可×）。
3. 每 tick 读 knowledge.vibecraft.phoenix_harass_active：
   - True  → Reserve 全部凤凰 + 调 PhoenixSquadMicro 骚扰 micro。
   - False → 释放 Reserved（clear_task=Idle），sharpy free_units 自动把凤凰
            纳入主力 PlanZoneAttack/Defense → 凤凰归队打团。本 act 不再下指令。
   Director 在玩家点×卡片 / 到硬性截止时间时 set False（一次性 latch）。

2026-05-30：骚扰=持久指令卡 —— 玩家可随时×让凤凰归队；硬性截止时间兜底。
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

from vibecraft.bot.auto_combat.protoss.phoenix_squad_micro import (
    _ARRIVE_DIST,
    _STATIC_RAID_MAX,
    LIFT_ENERGY,
    PhoenixSquadMicro,
)
from vibecraft.bot.drop_path import air_path_ground_frac, harass_stage_point, plan_air_path
from vibecraft.bot.terrain_harass import (
    build_enemy_highground_cells,
    find_mineback_pocket,
    path_highground_frac,
    plan_lowground_path,
)

logger = logging.getLogger(__name__)

# greppable 走位 trace 开关（真局自验矿后侧切路径几何用；默认关，环境变量开）。
# 用法：设 VIBECRAFT_PHOENIX_TRACE=1 跑真局 → grep game log 的 PHOENIXPATH / PHOENIXTRACE 行。
_PHOENIX_TRACE: bool = bool(os.environ.get("VIBECRAFT_PHOENIX_TRACE"))
_TRACE_GAP_S: float = 1.5  # 每帧 trace 节流间隔（game-seconds）

# 敌方农民类型（召回判定排除：骚扰农民不算"大部队来攻"；矿区评分数农民也用）。
_WORKER_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.SCV, UnitTypeId.PROBE, UnitTypeId.DRONE, UnitTypeId.MULE}
)
# 第一波 launch 默认门槛（攒 5 才一起出门，防"出一个去一个被吃掉"）。
_DEFAULT_WAVE_THRESHOLD: int = 5
# 默认骚扰硬性时长（game-seconds，从 launch 起算）。到点 Director 自动收卡归队。
_DEFAULT_HARASS_DURATION: float = 300.0

# ── 矿后侧切接近 / 目标矿选取（rule 4，借鉴 BC GroupHarassAct，2026-07-20）──────────
# 矿线背基地侧偏移 = 矿后锚点（凤凰在此空盘旋，地面军够不到；air 单位比 BC 略靠后一点）。
_BEHIND_OFFSET: float = 2.0
_WORKERS_RADIUS: float = 12.0  # 矿区农民计数半径（评分分子）
_AA_RADIUS: float = 14.0  # 对空 DPS 评分半径（矿线中心）
_ARMY_SCAN: float = (
    22.0  # 以**候选矿**为心:圈内漫游对空军 > 凤凰数 → 避开该矿(iter4/F129，实测最优 33%)
)
_ARMY_DODGE_R: float = 24.0  # 以**squad**为心:附近这么大范围的**能对空部队**判"能不能打过"(_can_beat_aa),打不过就退藏(I49)
_DODGE_HOLD_S: float = 25.0  # dodge 滞回:一旦判打不过、藏悬崖口袋 → 锁定藏这么久不再切(打断'退→再approach→被抓'的 oscillate,保存凤凰,I49 突破)
_ARMY_AVOID_MIN: int = 4  # 敌方漫游对空军 ≥ 这么多 → 其中心也当路线避障(迂回绕开军队去矿后悬崖,D86/用户 2026-07-26 迂回拉扯)
# 护盾容差(F124/D90):凤凰 60 护盾脱战会回,能扛一波火力不掉血 → 即便抬不完全部对空,护盾还能多顶
# 这么多个对空单位的火力而无损失。判"能不能打过"时在"能抬数"上加这个容差(用户 2026-07-26 强调护盾)。
_SHIELD_TANK_MARGIN: int = 2
# 抬完后的剩余输出火力(F124/用户 2026-07-26):抬人的凤凰在 channel graviton beam 时**不能开火**,
# 所以判"能不能打过"还要求**抬完后剩下的凤凰 ≥ 这么多**,才有足够 DPS 打死被抬的对空 + 杀农民。
_MIN_FREE_SHOOTERS: int = 2
_W_AA: float = 8.0  # 对空 DPS 评分权重（score = workers - _W_AA × aa_dps）
_ZONE_DWELL_S: float = 8.0  # 换目标矿最小停留（切换滞回）
_ZONE_SWITCH_RATIO: float = 1.3  # 换矿所需评分领先比例
_APPROACH_ADVANCE: float = 6.0  # 接近路径 waypoint 推进判定（squad_center 距当前点 < 此 → 下一点）
# 打不过的矿"根本别去"预判 + 退安全待命（2026-07-22 用户："要么打要么走,别杵军队旁"）。
# harassable = 静态防空 ≤ 预算 且 可抬对空 < 凤凰数（与 micro can_fight D89/D90 同口径，不去打不过的矿）。
_HOLD_STANDOFF: float = (
    38.0  # 无可打矿时的安全待命点：敌方主基朝己方外推 N 格（出敌军射程、待机等窗口）
)
# 未 launch 时重发"去集结点"的最小间隔(秒)。不每帧发 —— 每帧硬发会把凤凰钉死在集结点上。
_RALLY_REISSUE_COOLDOWN_S: float = 4.0
# 未 launch 时家门口有这么多敌方战斗单位 → 凤凰交还 sharpy 防守(与召回开关解耦,见
# _home_needs_defense_prelaunch)
_PRELAUNCH_DEFEND_MIN_ENEMIES: int = 2
# 兵力打散重整（2026-07-22 用户："打散到打不动就退敌方高地外安全集结点重整，不回家，更快再一起去"）。
_REGROUP_FLOOR: int = 3  # squad 有效凤凰 < 此数 → 打散了、打不动 → 退安全点重整
_REGROUP_RESUME: int = 5  # 重整到 >= 此数（滞回）→ 攒够一波，恢复出击
_REGROUP_SAFE_R: float = 13.0  # 重整点"安全"判据:此半径内无敌方对空威胁（AA 射程 ~6-7 + buffer）
# 静态防空建筑（评分累计 air_dps）
_STATIC_AA_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.SPORECRAWLER, UnitTypeId.MISSILETURRET, UnitTypeId.PHOTONCANNON}
)


class PhoenixSquadAct(ActBase):  # type: ignore[misc]
    """Squad-aware 凤凰控制 act — 抱团 + 永动 kite + 智能 lift + 骚扰持久指令卡。

    取代旧 PhoenixHarassAct（旧 act 留作 dead code 可回退）。
    骚扰中所有凤凰 Reserved（sharpy 默认 micro 不接管）；玩家×卡片或到截止
    时间后 Director set phoenix_harass_active=False → 本 act 释放凤凰归队主力。
    """

    def __init__(
        self,
        release_after: float | None = None,
        bail_hp_ratio: float = 0.3,
        recover_hp_ratio: float = 0.6,
        wave_threshold: int = _DEFAULT_WAVE_THRESHOLD,
        harass_duration: float = _DEFAULT_HARASS_DURATION,
        recall_threshold: int = 0,
        recall_radius: float = 30.0,
    ) -> None:
        """
        release_after   : game-seconds；到点放手归队（保留兜底，一般用 harass_duration）。
        bail_hp_ratio   : 血量(HP+护盾)比例低于此值 → 全撤回家。
        recover_hp_ratio: 已撤退的凤凰血量回到此值以上才重新出击（回血滞回）。
        wave_threshold  : 第一波 launch 凤凰数下限（默认 5）。未达此数前凤凰 stay home。
        harass_duration : 骚扰硬性时长（game-seconds，从 launch 起算）。Director
                          据此设卡片截止时间；到点自动收卡 + 凤凰归队。玩家可提前×。
        recall_threshold: 敌方大部队来攻召回门槛（2026-07-19 用户）。我方基地
                          recall_radius 内敌方战斗单位（非建筑非农民）≥ 此数 →
                          释放凤凰归队防守/参战。0 = 关闭（不按威胁召回）。
        recall_radius   : 算"逼近我方基地"的半径（默认 30）。
        """
        super().__init__()
        self._release_after = release_after
        self._wave_threshold = int(wave_threshold)
        self._harass_duration = float(harass_duration)
        self._recall_threshold = int(recall_threshold)
        self._recall_radius = float(recall_radius)
        self._wave_launched: bool = False
        # 未 launch 时的集结点(星门旁)+ 每只凤凰上次被下集结指令的时刻(节流,不每帧发)
        self._home_rally: object | None = None
        self._rally_move_at: dict[int, float] = {}
        self._harass_notified: bool = False  # 是否已通知 Director 创建骚扰卡
        self._micro = PhoenixSquadMicro(
            bail_hp_ratio=bail_hp_ratio,
            recover_hp_ratio=recover_hp_ratio,
        )
        # ── 矿后侧切接近（rule 4）缓存 ─────────────────────────────────────────
        self._zone_rank: int | None = None  # 当前选定敌方矿区 rank（切换滞回）
        self._zone_since: float = 0.0
        self._approach_path: list[Any] = []  # plan_avoid_path 算出的避主基/矿后侧切路径
        self._approach_key: str = ""  # 路径对应锚点 key（换矿才重算，一次锁定）
        self._approach_idx: int = 0
        self._last_trace_t: float = -999.0  # 走位 trace 节流
        self._last_target_reason: str = (
            "?"  # 上次目标决策(ok/all_defended/no_zones/regroup)供 trace
        )
        self._regrouping: bool = False  # 打散重整 latch（滞回 _REGROUP_FLOOR→_REGROUP_RESUME）
        self._regroup_pt: Point2 | None = None  # 缓存的重整安全点（锁定不每帧漂；不安全时才重算）
        self._dodge_until: float = (
            0.0  # dodge 滞回:藏悬崖口袋锁到这个 game_time,期间不再切(保存凤凰,I49)
        )
        # ── 安全集结点（D71/terrain_harass：各敌方矿的矿后悬崖口袋，静态，按局缓存）─────────
        self._safe_points: list[Point2] = []  # 各矿矿后安全口袋（地面够不到，terrain_harass 算）
        self._safe_points_key: str = ""  # 缓存 key（已知敌方矿集合变了才重算）
        # ── 低地路由（F122 真解）：敌方高地代价栅格（terrain 静态，一局一算，D79）─────────
        self._highground_cells: frozenset[tuple[int, int]] | None = None
        self._approach_router: str = "snap"  # 上次 approach 用的选路器（lowground/snap），供 trace
        # ── 归队后"抬地防守"（用户 2026-07-26：不裸交 sharpy 退却，凤凰持续抬敌方地面关键单位）──
        # 评审①⑥：区分「自动超时/deadline」(→永久 latch lift-defend)与「玩家×早收」(→真 release 归还)。
        self._lift_defend: bool = False  # 自动超时/release_after 到点 → latch,此后持续抬地防守
        self._harass_deadline: float | None = (
            None  # launch 时算的硬性截止(= launch + harass_duration),区分玩家×
        )

    async def execute(self) -> bool:
        try:
            phoenixes = self.ai.units(UnitTypeId.PHOENIX).ready
        except Exception:
            return True
        if not phoenixes:
            return True

        # 归队后"抬地防守"（用户 2026-07-26）：凤凰打不到地面,归队后被 sharpy 退却控制=不抬发呆送掉
        # （80% 损失在归队后）→ 保留控制持续抬敌方地面关键单位。评审①⑥ 区分三种"结束"语义：
        #   ①自动超时(release_after / harass deadline 到点) → **永久 latch** lift-defend(harass 窗口真结束)。
        #   ②玩家×卡片早收(harass_active False 但 deadline 没到) → **真 release 归还**(尊重玩家显式收回)。
        #   ③召回(敌军扑家) → **临时** lift-defend 防守(不 latch,威胁清了恢复骚扰)。
        now = float(self.ai.time)
        _no_ld = bool(os.environ.get("VIBECRAFT_PHOENIX_NO_LIFTDEFEND"))  # A/B 开关:回退旧 release
        # 已 latch → 持续 lift-defend
        if self._lift_defend:
            if _no_ld:
                for u in phoenixes:
                    self._release(u)
                return True
            return self._run_lift_defend(phoenixes)
        # ① 自动超时 → latch lift-defend
        deadline_passed = (self._release_after is not None and now >= self._release_after) or (
            self._harass_deadline is not None and now >= self._harass_deadline
        )
        if deadline_passed:
            self._lift_defend = True
            if _no_ld:
                for u in phoenixes:
                    self._release(u)
                return True
            return self._run_lift_defend(phoenixes)
        # ② 玩家×早收(已 launch、deadline 没到、卡片被置 inactive) → 真 release 归还给玩家/主力
        if self._wave_launched and not self._harass_active():
            for u in phoenixes:
                self._release(u)
            return True
        # ③ 召回:敌军扑家 → 临时 lift-defend 防守(不 latch;敌退了下帧恢复骚扰)
        if self._recall_threshold > 0 and self._enemy_attacking_home():
            if _no_ld:
                for u in phoenixes:
                    self._release(u)
                return True
            return self._run_lift_defend(phoenixes)

        # wave gating — 第一波必须攒够才 launch
        if not self._wave_launched:
            if phoenixes.amount >= self._wave_threshold:
                self._wave_launched = True
                logger.warning(
                    "PhoenixSquad wave LAUNCHED (count=%d >= threshold=%d)",
                    phoenixes.amount,
                    self._wave_threshold,
                )
                # launch 那一刻通知 Director 创建骚扰持久指令卡
                self._notify_harass_started()
            else:
                # 未 launch — 在**星门旁**待命(2026-07-27 用户:"凤凰的集结点也不要放到主基地
                # 上面,机场出来在哪就在那个位置集结")。三点:
                #   ① 集结点 = 星门(产出处),不是主基地 —— 新凤凰一出来就在集结点上,不用先飞回家;
                #   ② **不每帧硬发移动**:每帧发等于把凤凰钉死在点上,别的行为(躲、还手)全被覆盖;
                #   ③ 家里挨打时**交还 sharpy 防守** —— 同坑道集结那条:被 Reserved 的单位
                #      PlanZoneDefense 拿不到,一直 Reserved 就是"看着家被打却不参与"。
                rally = self._home_rally_point()
                home_threat = self._home_needs_defense_prelaunch()
                for u in phoenixes:
                    if home_threat:
                        self._release(u)
                        continue
                    self._reserve(u)
                    with contextlib.suppress(Exception):
                        idle = not getattr(u, "orders", None)
                        last = self._rally_move_at.get(u.tag, -999.0)
                        if idle or now - last >= _RALLY_REISSUE_COOLDOWN_S:
                            u.move(rally)
                            self._rally_move_at[u.tag] = now
                return True

        # （骚扰收卡/时限/召回已由上方 lift-defend latch 统一捕获，不再单独 release 归 sharpy）

        # Reserved 全部凤凰
        for u in phoenixes:
            self._reserve(u)

        # rule 4：算矿后锚点 + 矿后侧切接近路径（避开敌方主基，从矿背后切入）
        harass_anchor, approach_wp = self._harass_anchor_and_approach(phoenixes, now)

        # 调 helper（矿后锚点 + 接近 waypoint + 安全悬崖口袋:flee 退到它,心法 D81/D67）
        actions = self._micro.solve_squad(
            phoenixes, harass_anchor, approach_wp, self.ai, self._safe_gather_points()
        )

        # 走位 trace（真局自验矿后侧切路径几何）
        if _PHOENIX_TRACE:
            self._trace_tick(phoenixes, harass_anchor, approach_wp, actions, now)

        # 执行 actions（move / attack / lift）
        phoenix_by_tag = {u.tag: u for u in phoenixes}
        for tag, (action_type, target) in actions.items():
            unit = phoenix_by_tag.get(tag)
            if unit is None or target is None:
                continue
            with contextlib.suppress(Exception):
                if action_type == "move":
                    unit.move(target)
                elif action_type == "attack":
                    unit.attack(target)
                elif action_type == "lift":
                    unit(AbilityId.GRAVITONBEAM_GRAVITONBEAM, target)

        return True

    def _run_lift_defend(self, phoenixes: Any) -> bool:
        """归队后抬地防守（用户 2026-07-26）：凤凰保留 Reserved（不交 sharpy 退却），持续抬敌方地面
        关键单位集火。修 80% 凤凰死在归队后主力退却里不抬送掉。执行 micro 返回的 move/attack/lift。"""
        for u in phoenixes:
            self._reserve(u)
        # 评审④:不传敌方矿后口袋(那会让凤凰飞去敌方基地回盾)——lift-defend 内部用**己方侧**养能量/回盾点
        actions = self._micro.solve_lift_defend(phoenixes, self.ai)
        if _PHOENIX_TRACE:
            with contextlib.suppress(Exception):
                n_lift = sum(1 for a, _ in actions.values() if a == "lift")
                n_atk = sum(1 for a, _ in actions.values() if a == "attack")
                n_move = sum(1 for a, _ in actions.values() if a == "move")
                logger.warning(
                    "PHOENIXDEFEND t=%.0f n_ph=%d lift=%d atk=%d move=%d",
                    float(self.ai.time),
                    len(list(phoenixes)),
                    n_lift,
                    n_atk,
                    n_move,
                )
        phoenix_by_tag = {u.tag: u for u in phoenixes}
        for tag, (action_type, target) in actions.items():
            unit = phoenix_by_tag.get(tag)
            if unit is None or target is None:
                continue
            with contextlib.suppress(Exception):
                if action_type == "move":
                    unit.move(target)
                elif action_type == "attack":
                    unit.attack(target)
                elif action_type == "lift":
                    unit(AbilityId.GRAVITONBEAM_GRAVITONBEAM, target)
        return True

    # ------------------------------------------------------------------
    # 矿后侧切接近（rule 4，借鉴 BC GroupHarassAct，凤凰版）
    # ------------------------------------------------------------------

    def _harass_anchor_and_approach(self, phoenixes: Any, now: float) -> tuple[Any, Any]:
        """算 (骚扰锚点 harass_anchor, 当前接近 waypoint approach_wp)。

        **要么打要么走(2026-07-22 用户)**:去之前就判"这个矿去不去得起"——
        - 有**能打**的矿(对空 <= 凤凰总数×gate) → 锚点=其矿后点,矿后侧切接近 → 到了 fight;
        - **所有矿都打不过**(军队/炮台镇着) → 锚点=**安全待命点**(退出敌军射程等窗口),**绝不
          飞到军队跟前又不打又不走**(旧版最坏选择);
        - **没侦察到矿区** → 兜底奔敌方主基揭视野/找机会。
        """
        try:
            squad_center = phoenixes.center
        except Exception:
            squad_center = self._enemy_main()
        try:
            ready = phoenixes.filter(lambda p: p.is_ready)
            n_ph = len(ready)
            n_energy = len(ready.filter(lambda p: float(p.energy) >= LIFT_ENERGY))
        except Exception:
            n_ph = 0
            n_energy = 0

        # D79 预计算精神:已知敌方矿后早点算好安全集结点(悬崖口袋),缓存后每帧零开销(触发 PHOENIXSAFE trace)
        self._safe_gather_points()

        # 打散重整（用户 2026-07-22）：兵力打散到打不动 → 退**敌方高地外的安全集结点**重整
        # （不回家，攒够一波更快再一起去骚扰）。滞回：< FLOOR 进重整，>= RESUME 出重整。
        if self._regrouping:
            if n_ph >= _REGROUP_RESUME:
                self._regrouping = False
                self._regroup_pt = None  # 退出重整,清缓存
        elif n_ph < _REGROUP_FLOOR:
            self._regrouping = True
        if self._regrouping:
            self._last_target_reason = "regroup"
            rp = self._regroup_point()
            return rp, rp

        # 躲避大军 / 自保(D93 得分目标:减损失=提得分):squad 附近 _ARMY_DODGE_R 内的**能对空部队**,
        # 用 _can_beat_aa 判(能量抬capacity+护盾+剩余火力)——打不过就退安全点自保、别飞进去喂兵;
        # 打得过才继续 harass。dodge + **滞回藏住**(I49 突破):判打不过 → 藏悬崖口袋并锁定藏
        # _DODGE_HOLD_S 秒;期间(now<_dodge_until)一律继续藏、不再切,打断'退→再approach→被 blink 追猎抓'
        # 的 oscillate(F137/F138)。到点且附近打得过才恢复出击。
        # 注(F142/I50):曾试"去掉滞回、纯拉扯到开矿"(D95/D96),但路线仍穿高地(F140,vis 55%)→ 暴露拉扯
        # 送死、得分腰斩(-3.5→-14.1),已回退。'安全多矿拉扯'(D94)须先有低地路由(F122)做前提,再叠加。
        n_aa_squad = self._mobile_aa_near(squad_center, _ARMY_DODGE_R)
        if not self._can_beat_aa(n_aa_squad, n_ph, n_energy):
            self._dodge_until = now + _DODGE_HOLD_S  # 见打不过 → (续期)藏住
        if now < self._dodge_until:
            self._last_target_reason = "dodge_army"
            dp = self._dodge_hold_point(
                squad_center
            )  # 地面够不到+出建筑视野的悬崖口袋(不回家/不暴露)
            return dp, dp

        geom, reason = self._pick_harass_geom(now, squad_center, n_ph, n_energy)
        self._last_target_reason = reason
        enemy_main_c = self._enemy_main_center()

        if reason == "all_defended":
            # 所有矿被镇 → 退概隐安全点回盾等窗口(不退暴露中场干等暴露行踪)。
            rp = self._regroup_point()
            return rp, rp
        if geom is None:
            # 未侦察到矿区 → 兜底奔敌方主基揭视野
            anchor = self._enemy_main()
            return anchor, anchor

        behind, zone_center, mineral_line = geom
        approach_wp = self._approach_waypoint(
            squad_center, behind, zone_center, mineral_line, enemy_main_c
        )
        return behind, approach_wp

    def _pick_harass_geom(
        self, now: float, squad_center: Point2, n_ph: int, n_energy: int
    ) -> tuple[tuple[Point2, Point2, Point2] | None, str]:
        """选**能打**的目标矿区几何,返回 (geom | None, reason)。

        reason: "ok"(选到能打的矿) / "all_defended"(有矿但都打不过) / "no_zones"(没侦察到矿区)。

        **harassable 预判**(D89/D90):只在"静态防空≤预算 且 可抬对空<凤凰数"的矿里挑,打不过的矿不选。
        **到达门**:当前目标矿仍能打 且 squad 没抵达 → 锁死不切(防接近途中横跳追移动靶);
        当前矿变得打不过 → 解锁重挑。
        """
        cands: list[tuple[float, bool, int, tuple[Point2, Point2, Point2]]] = []
        for rank in (0, 1):
            zone = self._enemy_zone_by_rank(rank)
            geom = self._harass_geom(zone)
            if geom is None:
                continue
            _, _, ml = geom
            # 与 micro _squad_can_fight 同口径(D89/D90):静态防空 ≤ 预算(可护盾硬闯)且 可抬对空 < 凤凰数
            # (有富余抬清)→ 该矿去得起。旧 `aa<=n_ph*0.5` 太严 → 有几个女王的矿一律不去(不去矿区的根)。
            n_static, n_liftable = self._aa_split_near(ml)
            # iter4/F129(以矿为心避军,实测最优 33%):以该矿为心 _ARMY_SCAN(22)圈内的**能对空部队**,
            # 用 _can_beat_aa 判(图谱 D89/D90/F124:抬得光[能量]+护盾扛得住 才算打得过)——打得过才去、
            # 打不过(飞进去必喂)才避。不再拍脑袋 `> 凤凰数`(没算能量/护盾,用户 2026-07-26)。
            n_army = self._mobile_aa_near(ml, _ARMY_SCAN)
            ok = (
                n_static <= _STATIC_RAID_MAX
                and (n_liftable == 0 or n_liftable < max(n_ph, 1))
                and self._can_beat_aa(n_army, n_ph, n_energy)
            )
            score = float(self._workers_near(ml)) - _W_AA * self._aa_dps_near(ml)
            cands.append((score, ok, rank, geom))
        if not cands:
            return None, "no_zones"

        # **对凤凰而言"defended"= 抬不掉的静态防空太多 / 可抬对空多到没富余**——不是有地面军(凤凰不能被
        # 地面打,地面军再多也 snipe 农民,D83 反向审查)。可抬对空(女王)有富余就抬清、静态少可硬闯(D89/D90)。
        harassable = [(s, ok, r, g) for (s, ok, r, g) in cands if ok]
        if not harassable:
            return None, "all_defended"  # 都打不过 → 退安全口袋(bail/flee→口袋保命)

        cur = self._zone_rank
        # 到达门:当前目标矿"仍能打"且 squad 没抵达 → 锁死不切(防横跳)
        if cur is not None:
            cur_locked = next((g for (_, _, r, g) in harassable if r == cur), None)
            if cur_locked is not None:
                with contextlib.suppress(Exception):
                    if squad_center.distance_to(cur_locked[0]) > _ARRIVE_DIST:
                        return cur_locked, "ok"

        harassable.sort(key=lambda x: x[0], reverse=True)
        best_score, _, best_rank, best_geom = harassable[0]
        if cur is None or all(r != cur for (_, _, r, _) in harassable):
            self._zone_rank, self._zone_since = best_rank, now
            return best_geom, "ok"
        if best_rank != cur and now - self._zone_since >= _ZONE_DWELL_S:
            cur_score = next((s for (s, _, r, _) in harassable if r == cur), -1e9)
            if best_score > cur_score * _ZONE_SWITCH_RATIO:
                self._zone_rank, self._zone_since = best_rank, now
                return best_geom, "ok"
        return next((g for (_, _, r, g) in harassable if r == cur), best_geom), "ok"

    def _approach_waypoint(
        self,
        squad_center: Point2,
        behind: Point2,
        zone_center: Point2,
        mineral_line: Point2,
        enemy_main_c: Point2 | None,
    ) -> Point2:
        """矿后侧切接近路径的当前 waypoint（一次锁定缓存，换矿才重算，CLAUDE.md 强规则）。

        **绕矿后切入(2026-07-25 用户强调)**：避开**所有**敌方基地中心**含目标矿自己**——这样
        plan_avoid_path 会把路径**绕到目标矿背后(矿后)**再切入，而不是从主二矿中间直穿进去。
        (末段 stage→矿后点是短跳，dive 进矿后。)空军贴对方高地边缘、地面够不到处保命；主矿↔
        二矿迁移也各绕各自矿后、溜边走，不穿中间。参考大件/棱镜引刀空投贴边路线。
        """
        # 缓存 key 含**量化的敌军中心**(÷12):敌军移动 >~12 格才重算路径(迂回随军队机动更新,
        # 但量化避免每帧 jitter,兼顾"目标点一次锁定"实现纪律与"随敌军迂回"D86)。
        army_c = self._enemy_aa_army_center()
        akey = f"{int(army_c.x) // 12},{int(army_c.y) // 12}" if army_c is not None else "-"
        key = f"{behind.x:.0f},{behind.y:.0f}|{akey}"
        if key != self._approach_key or not self._approach_path:
            path: list[Any] = []
            router = "snap"
            with contextlib.suppress(Exception):
                pa = self.ai.game_info.playable_area
                # 极性不自己猜(评审 F1)：传 ai.in_pathing_grid 回调,其"True=地面可走"已被
                # proxy/nydus 等真机功能验证。取不到 → plan_air_path 内回退纯几何 plan_avoid_path。
                is_pathable = getattr(self.ai, "in_pathing_grid", None)
                # D71/D72:接近目标 = 矿后**悬崖口袋**(地面够不到的安全矿后,terrain_harass 靠悬崖算),
                # 替代原 harass_stage_point(只是几何外推、可能仍在地面可达处)。平坦矿无悬崖 → 回退 stage。
                pocket = find_mineback_pocket(
                    zone_center, mineral_line, self.ai.get_terrain_height, pa
                )
                stage = harass_stage_point(mineral_line, zone_center, pa)
                approach_target = pocket if pocket is not None else stage
                # **主选路:低地 A*(F122 真解)** —— 按 terrain_height 走低地(敌高地台面以外),叠加
                # 静态防空/漫游军动态惩罚(评审必改②)。走低地=敌看不到、地面够不到 → 不再穿高地暴露
                # (修 F140/F142 vis 55%)。超 max_expand/max_detour → None → 回退 snap 版 plan_air_path。
                hg = self._get_highground_cells()
                lg = None
                if hg:
                    lg = plan_lowground_path(
                        squad_center,
                        approach_target,
                        hg,
                        pa,
                        avoid_pts=self._lowground_avoid_pts(),
                    )
                if lg is not None:
                    path = lg
                    router = "lowground"
                else:
                    # 回退:低地路由超限/无高地栅格 → snap 版(局部贴崖,2026-07-25 方案)。
                    avoid = self._avoid_enemy_centers()
                    path = plan_air_path(squad_center, approach_target, avoid, is_pathable, pa)
                if approach_target.distance_to(behind) > 1.0:
                    path.append(behind)
            self._approach_path = path or [behind]
            self._approach_key = key
            self._approach_idx = 0
            self._approach_router = router
            if _PHOENIX_TRACE:
                # 三件套(评审 F9,别单看 air_frac)：n_wp + air_frac(路径落地面可走格比例,越低=越贴
                # 悬崖走、D60 生效) vs 直线 straight_frac + detour(路径长/直线长,>1.4 就是绕大圈=病态)。
                em = enemy_main_c
                is_pathable = getattr(self.ai, "in_pathing_grid", None)
                wp = self._approach_path
                gfrac = air_path_ground_frac(wp, is_pathable)
                straight_frac = air_path_ground_frac([squad_center, behind], is_pathable)
                # 评审⑧ 主指标:路径落**敌高地格**比例(hg_frac,直接对应低地路由目标) vs 直线 hg_straight
                hgc = self._get_highground_cells()
                hg_frac = path_highground_frac(wp, hgc)
                hg_straight = path_highground_frac([squad_center, behind], hgc)
                plen = sum(wp[i].distance_to(wp[i + 1]) for i in range(len(wp) - 1))
                slen = squad_center.distance_to(behind) or 1.0
                wps_s = " ".join(f"({p.x:.0f},{p.y:.0f})" for p in wp)
                logger.warning(
                    "PHOENIXPATH router=%s key=%s n_wp=%d main=(%.0f,%.0f) behind=(%.0f,%.0f) "
                    "hg_frac=%.2f hg_straight=%.2f air_frac=%.2f straight_frac=%.2f detour=%.2f wps=%s",
                    self._approach_router,
                    key,
                    len(wp),
                    (em.x if em is not None else -1.0),
                    (em.y if em is not None else -1.0),
                    behind.x,
                    behind.y,
                    hg_frac,
                    hg_straight,
                    gfrac,
                    straight_frac,
                    plen / slen,
                    wps_s,
                )
        wps = self._approach_path
        idx = self._approach_idx
        with contextlib.suppress(Exception):
            while idx < len(wps) - 1 and squad_center.distance_to(wps[idx]) < _APPROACH_ADVANCE:
                idx += 1
        self._approach_idx = idx
        return wps[min(idx, len(wps) - 1)]

    def _enemy_zone_by_rank(self, rank: int) -> Any:
        """敌方第 rank 个 expansion（0=主矿/1=二矿），按距敌方 start_location 距离排序（确定性）。"""
        with contextlib.suppress(Exception):
            zm = self.zone_manager
            start = getattr(zm, "enemy_start_location", None)
            if start is None:
                start = self.ai.enemy_start_locations[0]
            zones = sorted(zm.expansion_zones, key=lambda z: z.center_location.distance_to(start))
            if 0 <= rank < len(zones):
                return zones[rank]
        return None

    def _harass_geom(self, zone: Any) -> tuple[Point2, Point2, Point2] | None:
        """(矿后锚点, 矿区中心=基地, 矿线中心)。矿后锚点=矿线背基地侧偏移，air 盘旋处地面够不到。"""
        if zone is None:
            return None
        with contextlib.suppress(Exception):
            th = zone.center_location
            ml = zone.mineral_line_center
            d = ml - th
            dn = d.normalized if d.length >= 0.1 else Point2((1.0, 0.0))
            behind = Point2((ml.x + dn.x * _BEHIND_OFFSET, ml.y + dn.y * _BEHIND_OFFSET))
            return (behind, th, ml)
        return None

    def _workers_near(self, mineral_line: Point2) -> int:
        with contextlib.suppress(Exception):
            return sum(
                1
                for u in self.ai.enemy_units
                if u.type_id in _WORKER_TYPES and u.distance_to(mineral_line) < _WORKERS_RADIUS
            )
        return 0

    def _aa_dps_near(self, mineral_line: Point2) -> float:
        total = 0.0
        with contextlib.suppress(Exception):
            for u in self.ai.enemy_units:
                if u.distance_to(mineral_line) > _AA_RADIUS:
                    continue
                if (
                    getattr(u, "can_attack_air", False)
                    or getattr(u, "type_id", None) in _STATIC_AA_TYPES
                ):
                    total += float(getattr(u, "air_dps", 0.0))
        return total

    def _aa_count_near(self, mineral_line: Point2) -> int:
        """该矿矿线附近可打空军的敌方非农民单位 + 静态防空 数量(旧接口,harassable 预判用)。"""
        n_static, n_liftable = self._aa_split_near(mineral_line)
        return n_static + n_liftable

    @staticmethod
    def _can_beat_aa(n_aa: int, n_ph: int, n_energy: int) -> bool:
        """凤凰这波'能不能打过'这些**能对空**敌军(图谱 D89 抬capacity + F124/D90 护盾 + 剩余火力)。

        不是拍脑袋数量硬比,而是按真实机制两关都过:
        - **① 压制得住**:N 只带能量(≥50)凤凰能同时抬起 N 个对空(F124),抬光 → 零对空火力 → 零损失
          (D89);抬不完的靠 60 护盾多扛 _SHIELD_TANK_MARGIN 个火力不掉血(F124/D90)。
          即 `能抬数 + 护盾容差 ≥ 对空数`。
        - **② 抬完还有输出火力**(用户 2026-07-26):抬人的凤凰 channel graviton beam 时**不开火**,
          抬掉 `min(能抬数, 对空数)` 个要占这么多凤凰当抬手 → 剩下的凤凰要 ≥ _MIN_FREE_SHOOTERS,
          才有足够 DPS 打死被抬的对空 + 杀农民。
        n_aa=0 恒 True。两关任一不过 = 打不过 = 该避/躲。
        """
        if n_aa <= 0:
            return True
        # ① 压制:能抬光 或 抬不完的护盾扛得住
        if (n_energy + _SHIELD_TANK_MARGIN) < n_aa:
            return False
        # ② 剩余输出火力:抬手(min 能抬数,对空数)在 channel 不开火,剩下的要够
        lifters = min(n_energy, n_aa)
        return (n_ph - lifters) >= _MIN_FREE_SHOOTERS

    def _enemy_aa_army_center(self) -> Point2 | None:
        """敌方**漫游对空军**(能打空的非农民非建筑单位)的中心 —— 够多(≥_ARMY_AVOID_MIN)才返回,
        给接近路线当避障中心(迂回绕开军队去矿后悬崖,D86/用户 2026-07-26 迂回拉扯);不够多 → None。"""
        with contextlib.suppress(Exception):
            aa = [
                u
                for u in self.ai.enemy_units
                if getattr(u, "can_attack_air", False)
                and not getattr(u, "is_structure", False)
                and getattr(u, "type_id", None) not in _WORKER_TYPES
            ]
            if len(aa) >= _ARMY_AVOID_MIN:
                sx = sum(float(u.position.x) for u in aa) / len(aa)
                sy = sum(float(u.position.y) for u in aa) / len(aa)
                return Point2((sx, sy))
        return None

    def _mobile_aa_near(self, mineral_line: Point2, radius: float) -> int:
        """矿附近 radius 内的**漫游对空军**数(能打空的敌方非农民、非建筑单位)——旧②前以矿为心版。"""
        n = 0
        with contextlib.suppress(Exception):
            for u in self.ai.enemy_units:
                if getattr(u, "is_structure", False):
                    continue
                if getattr(u, "type_id", None) in _WORKER_TYPES:
                    continue
                if not getattr(u, "can_attack_air", False):
                    continue
                if u.distance_to(mineral_line) <= radius:
                    n += 1
        return n

    def _aa_split_near(self, mineral_line: Point2) -> tuple[int, int]:
        """该矿附近对空拆成 (静态防空数, 可抬对空数)——D89/D90 harassable 预判分别判。

        - 静态防空(孢子/光子炮/导弹塔,is_structure 或 _STATIC_AA_TYPES,抬不掉)→ n_static。
        - 可抬对空(女王/枪兵等非建筑地面对空,GRAVITONBEAM 抬得起)→ n_liftable。
        """
        n_static = n_liftable = 0
        with contextlib.suppress(Exception):
            for u in self.ai.enemy_units:
                if getattr(u, "type_id", None) in _WORKER_TYPES:
                    continue
                if u.distance_to(mineral_line) > _AA_RADIUS:
                    continue
                tid = getattr(u, "type_id", None)
                is_static = getattr(u, "is_structure", False) or tid in _STATIC_AA_TYPES
                if not (getattr(u, "can_attack_air", False) or tid in _STATIC_AA_TYPES):
                    continue
                if is_static:
                    n_static += 1
                else:
                    n_liftable += 1
        return n_static, n_liftable

    def _hold_point(self, enemy_main_c: Point2 | None) -> Point2:
        """安全待命点:敌方主基朝己方 start 外推 _HOLD_STANDOFF(出敌军射程,等下手窗口)。"""
        with contextlib.suppress(Exception):
            em = enemy_main_c if enemy_main_c is not None else self.ai.enemy_start_locations[0]
            return em.towards(self.ai.start_location, _HOLD_STANDOFF)
        with contextlib.suppress(Exception):
            return self.ai.start_location
        return enemy_main_c if enemy_main_c is not None else Point2((0.0, 0.0))

    def _dodge_hold_point(self, squad_center: Point2) -> Point2:
        """躲避大军/自保的 hold 点(用户 2026-07-26):**离敌方矿最近的、地面够不到(矿后悬崖)+
        出敌建筑视野**的安全点——躲进这种点 = 敌地面军够不着 + 建筑看不到你(不会追过来),
        凤凰藏这儿等下手窗口,而不是退回家(浪费位置)或退到还在敌军够得着的暴露口袋(I47 证伪:
        退矿后口袋照样被 blink 追猎打死)。

        候选 = 各敌方矿的**矿后悬崖口袋**(_safe_gather_points,地面 BFS 够不到,F114);优先取
        **出敌建筑视野**的(_in_enemy_building_vision False);再取离 squad 最近的(就近躲、不跑远、
        随时能再切进矿区)。都取不到 → 兜底 _regroup_point。
        """
        with contextlib.suppress(Exception):
            pts = self._safe_gather_points()
            if pts:
                hidden = [p for p in pts if not self._in_enemy_building_vision(p)]
                cand = hidden if hidden else pts
                return min(cand, key=lambda p: squad_center.distance_to(p))
        return self._regroup_point()

    def _safe_gather_points(self) -> list[Point2]:
        """各已知敌方矿的**矿后悬崖口袋**（地面够不到=安全集结点,D71/terrain_harass）。

        判定基=矿后 terrain_height 突降(悬崖,F114 真机证);平坦矿无悬崖 → 回退用矿后锚点 behind。
        静态,按已知敌方矿集合缓存(矿集合变了才重算)。返回一组候选点,调用方按情况选。
        """
        geoms: list[tuple[Point2, Point2, Point2]] = []
        key_parts: list[str] = []
        for rank in (0, 1, 2):
            geom = self._harass_geom(self._enemy_zone_by_rank(rank))
            if geom is None:
                continue
            geoms.append(geom)
            key_parts.append(f"{geom[1].x:.0f},{geom[1].y:.0f}")
        key = "|".join(key_parts)
        if key and key == self._safe_points_key and self._safe_points:
            return self._safe_points
        pts: list[Point2] = []
        with contextlib.suppress(Exception):
            pa = self.ai.game_info.playable_area
            th_fn = self.ai.get_terrain_height
            for behind, th, ml in geoms:
                pocket = find_mineback_pocket(th, ml, th_fn, pa)
                pts.append(pocket if pocket is not None else behind)  # 平坦矿回退矿后锚点
        if pts:
            self._safe_points = pts
            self._safe_points_key = key
            if _PHOENIX_TRACE:
                with contextlib.suppress(Exception):
                    th_fn = self.ai.get_terrain_height
                    parts = []
                    for (_behind, th, ml), p in zip(geoms, pts, strict=False):
                        parts.append(
                            f"矿({th.x:.0f},{th.y:.0f} h{th_fn(ml):.0f})→口袋({p.x:.0f},{p.y:.0f} h{th_fn(p):.0f})"
                        )
                    logger.warning("PHOENIXSAFE n=%d %s", len(pts), " ".join(parts))
        return pts

    def _regroup_point(self) -> Point2:
        """打散重整的**真安全**集结点(D71：各矿矿后悬崖口袋里离敌近且不挨打的;地面够不到、不回家)。

        优先选 _safe_gather_points(矿后悬崖口袋,terrain_harass 靠悬崖算)里不被对空威胁、离敌方主基
        最近的;无口袋/全被威胁 → 回退原'从矿后外侧朝家退到刚好安全'逻辑。**缓存锁定**:缓存点仍安全
        就复用(不每帧漂),变得不安全才重算(目标坐标锁定纪律)。
        """
        # 缓存点仍安全 → 复用(锁定)
        if self._regroup_pt is not None and not self._aa_threatens(self._regroup_pt):
            return self._regroup_pt

        # D71:优先各矿矿后悬崖口袋(安全集结点)里不挨打、离敌方主基最近的(尽量近敌持续压制)
        with contextlib.suppress(Exception):
            safe = [p for p in self._safe_gather_points() if not self._aa_threatens(p)]
            if safe:
                em = self._enemy_main_center()
                if em is not None:
                    safe.sort(key=lambda p: p.distance_to(em))
                self._regroup_pt = safe[0]
                return safe[0]

        # 回退:无悬崖口袋/全被威胁 → 从敌方主矿矿后外侧起朝家退到刚好安全(未侦察 → 敌方主基)
        base_opt: Point2 | None = None
        zone = self._enemy_zone_by_rank(0)
        geom = self._harass_geom(zone)
        if geom is not None:
            _behind, zone_center, mineral_line = geom
            with contextlib.suppress(Exception):
                base_opt = harass_stage_point(
                    mineral_line, zone_center, self.ai.game_info.playable_area
                )
        if base_opt is None:
            base_opt = self._enemy_main_center()
        if base_opt is None:
            with contextlib.suppress(Exception):
                base_opt = self.ai.enemy_start_locations[0]
        if base_opt is None:
            with contextlib.suppress(Exception):
                return self.ai.start_location
            return Point2((0.0, 0.0))
        base: Point2 = base_opt

        # 从 base 朝己方 start 退,找第一个无对空威胁的点(尽量近敌,只退到刚好安全)
        chosen = base
        with contextlib.suppress(Exception):
            start = self.ai.start_location
            span = base.distance_to(start)
            for frac in (0.0, 0.15, 0.3, 0.45, 0.6):
                pt = base.towards(start, span * frac)
                if not self._aa_threatens(pt):
                    chosen = pt
                    break
            else:
                chosen = base.towards(start, span * 0.6)  # 都不安全 → 退 60%(仍不回家)
        self._regroup_pt = chosen
        return chosen

    def _aa_threatens(self, pt: Point2) -> bool:
        """pt 的 _REGROUP_SAFE_R 半径内有敌方对空威胁(能打空非农民单位 + 静态防空)→ 不安全。"""
        with contextlib.suppress(Exception):
            for u in self.ai.enemy_units:
                if getattr(u, "type_id", None) in _WORKER_TYPES:
                    continue
                if not (
                    getattr(u, "can_attack_air", False)
                    or getattr(u, "type_id", None) in _STATIC_AA_TYPES
                ):
                    continue
                if u.distance_to(pt) <= _REGROUP_SAFE_R:
                    return True
        return False

    def _in_enemy_building_vision(self, pt: Point2) -> bool:
        """pt 是否在敌方(已知)静态建筑视野内(会被发现→敌增援)。建筑视野 ~9 + buffer;含 enemy_start(必知)。"""
        with contextlib.suppress(Exception):
            for b in self.ai.enemy_structures:
                if b.distance_to(pt) <= 11.0:
                    return True
        with contextlib.suppress(Exception):
            if self.ai.enemy_start_locations[0].distance_to(pt) <= 11.0:
                return True
        return False

    def _enemy_main_center(self) -> Point2 | None:
        """敌方主基地矿区中心（接近路径避障中心）。取不到 → enemy_start。"""
        zone = self._enemy_zone_by_rank(0)
        if zone is not None:
            with contextlib.suppress(Exception):
                return zone.center_location
        return self._enemy_main()

    def _avoid_enemy_centers(self) -> list[Point2]:
        """接近路径要避开的中心 = 敌方矿区中心(rank 0/1/2，含目标矿) + **静态防空位置**。

        含目标矿 → plan_avoid_path 绕到目标矿背后(矿后)再切入，不从中间直穿(用户 2026-07-25)。
        **D65(用户 2026-07-26)**：凤凰不能对地、抬不动建筑(F108)，静态防空(炮塔/孢子/光子炮)是
        搞不定的硬墙 → 也当避障中心，接近绕开、不从其火力网穿。末段 stage→矿后点短跳不受此 avoid 影响。
        """
        centers: list[Point2] = []
        for rank in (0, 1, 2):
            zone = self._enemy_zone_by_rank(rank)
            if zone is None:
                continue
            with contextlib.suppress(Exception):
                centers.append(zone.center_location)
        # D65：静态防空(凤凰搞不定的硬墙)也当避障中心，接近绕开火力网
        with contextlib.suppress(Exception):
            for u in self.ai.enemy_units:
                if getattr(u, "type_id", None) in _STATIC_AA_TYPES:
                    centers.append(u.position)
        # 迂回拉扯(D86/用户 2026-07-26):**漫游对空军**够多也当避障中心 → 路线绕开军队、走矿后地面
        # 够不到处去悬崖口袋,而不是直穿军队被 blink 追猎抓(F138 一 engage 就死的洞)。
        army_c = self._enemy_aa_army_center()
        if army_c is not None:
            centers.append(army_c)
        if not centers:
            em = self._enemy_main_center()
            if em is not None:
                centers.append(em)
        return centers

    def _get_highground_cells(self) -> frozenset[tuple[int, int]]:
        """敌方高地代价栅格（F122 低地路由，terrain 静态一局一算、缓存）。取不到 → 空集(低地路由禁用)。

        env `VIBECRAFT_PHOENIX_NO_LOWGROUND=1` → 恒空集 = 禁用低地路由、回退 snap(受控 A/B 用)。
        """
        if os.environ.get("VIBECRAFT_PHOENIX_NO_LOWGROUND"):
            return frozenset()
        if self._highground_cells is not None:
            return self._highground_cells
        cells: frozenset[tuple[int, int]] = frozenset()
        with contextlib.suppress(Exception):
            pa = self.ai.game_info.playable_area
            enemy_start = self.ai.enemy_start_locations[0]
            my_start = self.ai.start_location
            cells = build_enemy_highground_cells(
                self.ai.get_terrain_height, pa, enemy_start, my_start
            )
        self._highground_cells = cells
        return cells

    def _lowground_avoid_pts(self) -> list[Point2]:
        """低地路由的动态惩罚点 = 静态防空位置 + 漫游对空军中心(D65/D86)——高地栅格不含 AA/军队,
        叠加这层避免凤凰从炮台/追猎头上低地穿(评审必改②)。基地中心已被高地栅格覆盖,不重复放。"""
        pts: list[Point2] = []
        with contextlib.suppress(Exception):
            for u in self.ai.enemy_units:
                if getattr(u, "type_id", None) in _STATIC_AA_TYPES:
                    pts.append(u.position)
        army_c = self._enemy_aa_army_center()
        if army_c is not None:
            pts.append(army_c)
        return pts

    def _trace_tick(
        self,
        phoenixes: Any,
        harass_anchor: Any,
        approach_wp: Any,
        actions: dict[Any, Any],
        now: float,
    ) -> None:
        """每帧走位 trace（节流）：posture + squad 位置 + 到敌方主基距离 + 动作分布。

        矿后侧切验证判据（grep PHOENIXTRACE + PHOENIXPATH）：
        - PHOENIXPATH：n_wp>=3（plan_avoid_path 插了避障拐点=绕开主基）；
        - PHOENIXTRACE：approach 阶段 squad→敌方主基距离(dmain)不塌到很小（没直穿主基），
          最终 posture 到 fight/flee（抵达矿后区）。
        """
        if now - self._last_trace_t < _TRACE_GAP_S:
            return
        self._last_trace_t = now
        with contextlib.suppress(Exception):
            squad_center = phoenixes.center
            em = self._enemy_main_center()
            dmain = squad_center.distance_to(em) if em is not None else -1.0
            danchor = squad_center.distance_to(harass_anchor) if harass_anchor is not None else -1.0
            posture = getattr(self._micro, "_last_posture", "?")
            n_lifted = getattr(self._micro, "_last_n_lifted", 0)  # 被抬集合大小（D59 验证）
            n_lift = sum(1 for a, _ in actions.values() if a == "lift")
            n_atk = sum(1 for a, _ in actions.values() if a == "attack")
            n_move = sum(1 for a, _ in actions.values() if a == "move")
            vis = 1 if self._in_enemy_building_vision(squad_center) else 0  # 被敌建筑视野发现?
            logger.warning(
                "PHOENIXTRACE t=%.0f tgt=%s posture=%s n_ph=%d squad=(%.0f,%.0f) aim=(%.0f,%.0f) "
                "dmain=%.1f danchor=%.1f vis=%d lifted=%d lift=%d atk=%d move=%d",
                now,
                getattr(self, "_last_target_reason", "?"),
                posture,
                len(list(phoenixes)),
                squad_center.x,
                squad_center.y,
                (approach_wp.x if approach_wp is not None else -1.0),
                (approach_wp.y if approach_wp is not None else -1.0),
                dmain,
                danchor,
                vis,
                n_lifted,
                n_lift,
                n_atk,
                n_move,
            )

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _home_threat_count(self) -> int:
        """我方任一基地 recall_radius 内的敌方**战斗单位**数（非建筑、非农民）。

        农民/建筑不算"大部队"——敌方农民路过或建筑在视野里不该被当成打家。
        """
        try:
            townhalls = self.ai.townhalls
            if not townhalls:
                return 0
            r = self._recall_radius
            n = 0
            for e in self.ai.enemy_units:
                if e.is_structure:
                    continue
                if e.type_id in _WORKER_TYPES:
                    continue
                if any(e.distance_to(th) < r for th in townhalls):
                    n += 1
            return n
        except Exception:
            return 0

    def _enemy_attacking_home(self) -> bool:
        """敌方大部队逼近我方基地 → True（该召回凤凰防守/参战）。

        判据：`_home_threat_count()` ≥ recall_threshold。recall_threshold=0 时本判据不启用
        （调用点自己带 `> 0` 的前置判断）。
        """
        return self._home_threat_count() >= self._recall_threshold

    def _home_needs_defense_prelaunch(self) -> bool:
        """未 launch 期间要不要把凤凰交还防守：家门口有 ≥N 个敌方战斗单位。

        **不复用 recall_threshold**：那个是"骚扰中途要不要召回"的开关，玩家可以关掉（=0）；
        而"还没出门就被打家"跟召回开关无关，两者阈值也不该绑定（recall=0 会退化成"任何一个
        侦查兵靠近就放手"）。故用独立常量。
        """
        return self._home_threat_count() >= _PRELAUNCH_DEFEND_MIN_ENEMIES

    def _harass_active(self) -> bool:
        """读 knowledge.vibecraft.phoenix_harass_active（默认 True，取不到也按 True）。"""
        try:
            return bool(self.knowledge.vibecraft.phoenix_harass_active)
        except Exception:
            return True

    def _notify_harass_started(self) -> None:
        """通知 Director 创建凤凰骚扰持久指令卡（带硬性截止时间）。失败静默。"""
        director = getattr(self.ai, "director", None)
        if director is None:
            return
        notify = getattr(director, "notify_phoenix_harass_started", None)
        if notify is None:
            return
        with contextlib.suppress(Exception):
            now = float(self.ai.time)
            self._harass_deadline = (
                now + self._harass_duration
            )  # 存硬性截止,区分"自动超时"vs"玩家×早收"
            notify(now, now + self._harass_duration)
            self._harass_notified = True
            logger.warning(
                "phoenix_harass notified director (start=%.1f deadline=%.1f)",
                now,
                now + self._harass_duration,
            )

    def _home_rally_point(self) -> object:
        """未 launch 时的集结点 = **星门旁**（凤凰产出处），不是主基地。

        取"离我方 start_location 最近的那座星门"——`.first` 之类依赖 Units 帧间顺序的取法非
        确定性、目标点会跳变（CLAUDE.md 强规则）；closest_to(固定锚点) 同输入每帧同输出。
        算一次缓存住；星门还没建好（或被拆光）才兜底 start_location。
        """
        if self._home_rally is not None:
            return self._home_rally
        with contextlib.suppress(Exception):
            from sc2.ids.unit_typeid import UnitTypeId as _UTI

            gates = self.ai.structures(_UTI.STARGATE).ready
            if gates:
                self._home_rally = gates.closest_to(self.ai.start_location).position
                with contextlib.suppress(Exception):
                    logger.info(
                        "PHOENIXRALLY 集结点=星门(%.0f,%.0f) 离主基 %.1f 格",
                        self._home_rally.x,
                        self._home_rally.y,
                        self._home_rally.distance_to(self.ai.start_location),
                    )
                return self._home_rally
        with contextlib.suppress(Exception):
            logger.info("PHOENIXRALLY 没有星门,集结点兜底主基地")
        return self.ai.start_location

    def _reserve(self, unit: object) -> None:
        """标 Reserved —— 每 tick 重设，独占控制权。"""
        with contextlib.suppress(Exception):
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, unit)

    def _release(self, unit: object) -> None:
        """释放 Reserved（clear_task → Idle）→ sharpy free_units 接管，凤凰归队主力。"""
        with contextlib.suppress(Exception):
            self.knowledge.roles.clear_task(unit)

    def _enemy_main(self) -> object:
        """对方主基地位置；取不到时返回 None。"""
        try:
            return self.ai.enemy_start_locations[0]
        except Exception:
            return None
