"""BC 骚扰微操执行器 — GroupHarassAct.

重构（2026-06-29 #580）：从"每艘读 per-tag claim 各自骚扰"改成
"读群、健康分状态机统一调度整组"。
重构（2026-07-04 #583 跟进）：posture 从全体 alive 改成只算 joined squad，
治"家里新出大件把前排拽回家"。DIVE/RETREAT 两态，squad 成员 RETREAT 时脱队回家养血。

群信息来自 `knowledge.vibecraft.bc_harass_groups`（director 每 tick 发布的
`[{"did": str, "tags": set[int], "target": str|None, "target_count": int|None}]`）。
每个 group 独立跑健康分状态机（RETREAT ↔ DIVE posture），统一决策目标矿，统一调度群内所有 BC。

旧 `bc_harass_claims` (per-tag map) 已废，不再读。

已验证微操整段搬运、不改数值（opus E3 / CLAUDE.md）：
  跳跃阈值 _jump_hp_threshold / AoE 闪避 _dodge_spot / 爆发跳 / 回血门 _RECOVER_HP_RATIO /
  修理 _ensure_repair / home 锚点 _get_home_anchor / sweep/贴农民/风筝 _nearby_worker_center/
  _nearby_threat / _raid_move_point 的 far/near 直飞逻辑 —— 全是 #561/#557/#580 真局验过的。

控制权语义（CLAUDE.md 规则 2）：群内 BC 是被 claim 的独占单位，全军命令不影响它。
要停某艘 → ❌ 骚扰 claim（director 释放 → 归队主力）；要停全部 → ❌ 骚扰 claim。

目标锁定规则（CLAUDE.md 强规则）：矿区 anchor/home 锚点一次锁定缓存，不每帧漂移。

环境变量：
  VIBECRAFT_BCRAID_TRACE=1  → 输出 greppable trace 行
                              (flyout/posture/zone_switch/jump_home/jump_burst/dodge/healing_hold/regroup)
"""

from __future__ import annotations

import contextlib
import logging
import os
from math import sin
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.effect_id import EffectId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

from vibecraft.bot.drop_path import harass_stage_point, plan_avoid_path
from vibecraft.bot.unit_kind import is_worker as _is_worker

logger = logging.getLogger(__name__)

_BCRAID_TRACE: bool = bool(os.environ.get("VIBECRAFT_BCRAID_TRACE"))

# ── 已验证微操数值（#561/#557 真局验过，不改）─────────────────────────────────────
_JUMP_FLOOR_RATIO: float = 0.09  # 保底：血量 ≤ 9% 必跳
_JUMP_SAFETY_S: float = 6.5  # 提前量：6.5s
_JUMP_THREAT_RADIUS: float = 13.0  # 估算 incoming DPS 的搜敌半径
_RECOVER_HP_RATIO: float = 0.95  # 回血门：满血 95% 才重新出击
_RAID_DWELL_S: float = 10.0  # patrol fallback 矿区停留时间
_BURST_DROP_RATIO: float = 0.18  # 爆发集火判定：一帧掉超过 18% 满血 → 立即跳
_ENGAGE_RADIUS: float = 7.0  # 锚点(矿线) ≤ 此值 = 已到达
_APPROACH_DIRECT_FROM_ZONE: float = 24.0  # 离目标矿区中心 ≤ 此值 → 直飞扎进矿线
_WORKERS_RADIUS: float = 12.0  # 判定矿区有无农民的搜索半径
_SWEEP_AMPLITUDE: float = 3.5  # 沿矿线轴线来回扫的幅度
_SWEEP_OMEGA: float = 1.4  # 来回扫频率
_WORKER_SEEK_RADIUS: float = 13.0  # 以矿线锚点为圆心、贴农民的搜索半径
_KITE_THREAT_RADIUS: float = 10.0  # 风筝：搜对空威胁的半径
_KITE_BACK: float = 3.0  # 风筝：向远离威胁方向偏的格数

# ── P1 威胁规避常量（§3.6，#580 新增）─────────────────────────────────────────
_P1_THREAT_RADIUS: float = 16.0  # P1：搜附近对空威胁的半径
_P1_THREAT_DPS_FLOOR: float = 20.0  # P1：威胁累计 DPS 超此 → 规避（单门卫/炮塔约 15-17 DPS）
_P1_FLEE_DIST: float = 12.0  # P1：精确射程无法算出时的保守 fallback 移动距离（格）
_P1_FLEE_RANGE_BUFFER: float = 2.0  # P1 精确出射程：max(air_range) + 此 buffer 作为 flee_dist
_P1_CHEAP_KILL_ISOLATION_RADIUS: float = 10.0  # cheap-kill 孤立判定：AA 建筑周围此半径内无 army/AA
_P1_CHEAP_KILL_BUDGET_RATIO: float = 0.5  # cheap-kill 可承受伤害：群均血 × 此比例

# ── P3 矿後锚点（§3.6，#580 新增）─────────────────────────────────────────────
_BEHIND_MINERAL_OFFSET: float = (
    0.5  # 矿线背基地侧偏移（几乎贴矿线，大件射程6罩住整条矿线农民，#581 2026-07-03）
)
# 巡逻"已抵达当前矿"判定半径：任一 BC 进此半径才开始计驻留 / 才允许轮换到下一个矿
_PATROL_ARRIVE_RADIUS: float = _APPROACH_DIRECT_FROM_ZONE  # = 24.0（进 airspace 即算到达）

# ── 健康分状态机常量（§3，#580 新增）──────────────────────────────────────────────
_SALLY_HP: float = 0.95  # fit 出击血线（满血才算可出击）
_RETURN_BAR: float = 0.40  # 还能战血线（滞回低门）
_POSTURE_MIN_DWELL_S: float = 4.0  # posture 翻转后最小停留（防极限环）
_ABS_RETREAT_FLOOR: int = 2  # able_sq 绝对数 < 此 → 强制 RETREAT（仅 squad≥2 时生效）

# ── 群体协同移动常量（#583，2026-07-04 群路径 + 集结点）─────────────────────────
_RALLY_RADIUS: float = 5.0  # 集结点收紧判定半径（非 healing 且 joined 的 BC 都进此范围才放行）
_RALLY_TIMEOUT_S: float = 4.0  # 集结点等待超时兜底（防掉队卡整群）

# ── 单状态机常量（#587 重写）────────────────────────────────────────────────────
_FALLBACK_STAGE_OUT: float = (
    10.0  # 矿线未侦察时的兜底集结点：敌方主基朝己方外推 N 格（第一艘立刻出门揭视野，绝不杵家）
)
_HOME_STOP_RADIUS: float = 6.0  # HEAL 到家判定：离 home < N → hold_position 停住让 SCV 修
_GATHER_WINDOW_S: float = (
    2.5  # STAGE→DIVE 集结超时：到 stage 等超过此秒也放行（短一点，防增援在敌火下 loiter 磨死）
)

# ── 在家修理常量（#583，BcHomeRepairAct）────────────────────────────────────────
_HOME_REPAIR_RADIUS: float = 15.0  # 在家判定：离任一己方 townhall < N 格

# ── 安全矿评分 / 切换滞回（§4，#580 新增）─────────────────────────────────────────
_W_AA: float = 8.0  # anti-air DPS 权重
_AA_RADIUS: float = 14.0  # 防空评分搜索半径（矿线中心半径）
_ZONE_SWITCH_MIN_DWELL_S: float = 8.0  # 矿区切换最小停留
_ZONE_SWITCH_SCORE_RATIO: float = 1.3  # 切换矿区所需评分领先比例

# 静态防空建筑（评分时累计其 air_dps）
_STATIC_AA_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.SPORECRAWLER, UnitTypeId.MISSILETURRET, UnitTypeId.PHOTONCANNON}
)

# 指定矿区字符串 → 敌方 expansion 排名（按距敌方 start_location 距离：0=主矿,1=二矿,2=三矿）
_RANK_BY_SPEC: dict[str, int] = {
    "main": 0,
    "natural": 1,
    "third": 2,
    "enemy_main": 0,
    "enemy_natural": 1,
    "enemy_third": 2,
}
# 巡逻圈覆盖的敌方矿区排名
_PATROL_RANKS: tuple[int, ...] = (0, 1, 2)

# 危险 AoE effect 集合 + 各自有效半径
_DANGER_EFFECTS: frozenset[EffectId] = frozenset(
    {
        EffectId.RAVAGERCORROSIVEBILECP,
        EffectId.PSISTORMPERSISTENT,
        EffectId.NUKEPERSISTENT,
        EffectId.LIBERATORTARGETMORPHPERSISTENT,
        EffectId.LURKERMP,
        EffectId.BLINDINGCLOUDCP,
    }
)
_EFFECT_RADIUS: dict[EffectId, float] = {
    EffectId.RAVAGERCORROSIVEBILECP: 2.5,
    EffectId.PSISTORMPERSISTENT: 3.5,
    EffectId.NUKEPERSISTENT: 9.0,
    EffectId.LIBERATORTARGETMORPHPERSISTENT: 6.0,
    EffectId.LURKERMP: 1.0,
    EffectId.BLINDINGCLOUDCP: 3.5,
}


class GroupHarassAct(ActBase):  # type: ignore[misc]
    """BC 骚扰微操执行器：读 bc_harass_groups，健康分状态机统一调度整组。

    每帧 execute() 返回 True（non-blocking），放 tactics SequentialList 的
    PlanZoneGather 之后、PlanZoneAttack 之前。

    RETREAT：squad 成员脱队回家养血，未入队健康 BC 自主赶前方 stage；
    DIVE：squad 成员沿共享路径扎矿骚扰农民，至 jump 阈值才跳回（省 CD）。

    posture 只从 joined squad（已到 stage 的 BC）计算，家里新出的 BC 永远不影响
    前排 squad 的 DIVE/RETREAT 决策（治"新兵拽回前排"bug #583 跟进）。
    """

    def __init__(
        self,
        jump_floor_ratio: float = _JUMP_FLOOR_RATIO,
        jump_safety_s: float = _JUMP_SAFETY_S,
        recover_hp_ratio: float = _RECOVER_HP_RATIO,
        raid_dwell_s: float = _RAID_DWELL_S,
    ) -> None:
        super().__init__()
        self._jump_floor_ratio = jump_floor_ratio
        self._jump_safety_s = jump_safety_s
        self._recover_hp_ratio = recover_hp_ratio
        self._raid_dwell_s = raid_dwell_s

        # ── per-BC 单一状态机（#587 推倒重写）───────────────────────────────
        # 每艘 BC 恰好一个状态：STAGE(奔赴/待命集结点) / DIVE(扎矿骚扰) / HEAL(脱离养血)
        # 删掉旧的 _joined_tags/_healing_tags/_approach_arrived/_approach_wp_idx/_rally_since/
        # _group_posture 等一堆纠缠 latch —— 那是"改 4 次坏 4 次"的乱麻根源。
        self._state: dict[int, str] = {}  # tag → "STAGE" / "DIVE" / "HEAL"
        self._state_since: dict[int, float] = {}  # tag → 进入当前状态时刻（per-BC gather 超时）
        self._last_hp: dict[int, float] = {}  # tag → 上帧 hp（爆发掉血检测）
        self._healed_stopped: set[int] = (
            set()
        )  # HEAL 到家已发过 hold_position 的 BC（只发一次，防每帧重发漂移）
        self._home_anchor: Point2 | None = None
        # per-BC 微操辅助（sweep 轴线 + 矿区中心供 _raid_move_point 判 far/near + trace 去重）
        self._sweep_axis_by_tag: dict[int, Point2] = {}
        self._zone_center_by_tag: dict[int, Point2] = {}
        self._last_flyout_by_tag: dict[int, tuple[float, float]] = {}
        # STAGE 奔集结点路径：**(tag, stage_key) → 路径，从 BC 当前位置算**（#587 修"去一半被拉回"：
        # 换矿 stage_key 变 → 从当前位置重算继续前进，绝不飞回家）。一次锁定缓存（CLAUDE.md）。
        self._stage_path: dict[tuple[int, str], list] = {}
        self._stage_idx: dict[tuple[int, str], int] = {}  # (tag, stage_key) → per-BC 路径进度

        # ── per-group 状态（did 键）─────────────────────────────────────────
        # auto picker：当前选定矿区 rank + 选定时间（切换滞回）
        self._group_zone: dict[str, int] = {}
        self._group_zone_since: dict[str, float] = {}
        # patrol fallback：所有已知矿 score <= 0 时的巡逻状态
        self._group_patrol_rank: dict[str, int] = {}
        self._group_patrol_since: dict[str, float] = {}
        # committed stage（只随真正切矿更新，不追 picker 瞬时值 —— 必修5 确定性）
        self._stage_pt: dict[str, Point2] = {}  # did → 当前 stage 集结点
        self._stage_key: dict[str, str] = {}  # did → stage 对应 target_key（判是否切矿）

    # ------------------------------------------------------------------
    # ActBase entry point
    # ------------------------------------------------------------------

    async def execute(self) -> bool:
        try:
            self._tick()
        except Exception:
            logger.exception("GroupHarassAct._tick error")
        return True

    # ------------------------------------------------------------------
    # 主循环（同步）
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        # director 每 tick 发布的群列表
        groups: list[dict] = (
            getattr(getattr(self.knowledge, "vibecraft", None), "bc_harass_groups", None) or []
        )

        all_bcs = {bc.tag: bc for bc in self.cache.own(UnitTypeId.BATTLECRUISER).ready}

        # 汇总所有 group 的 tag 和 did，用于剪切过期状态
        # live_dids 提前算（_approach_wps 按 did 剪，_rally_since 按 did 剪）
        live_dids: set[str] = {g["did"] for g in groups}
        all_group_tags: set[int] = set()
        for g in groups:
            all_group_tags.update(g.get("tags") or set())
        live = all_group_tags & set(all_bcs.keys())

        # 剪切 per-BC 过期状态（dead / released BC）
        self._state = {t: v for t, v in self._state.items() if t in live}
        self._state_since = {t: v for t, v in self._state_since.items() if t in live}
        self._healed_stopped &= live
        self._last_hp = {t: v for t, v in self._last_hp.items() if t in live}
        self._sweep_axis_by_tag = {t: v for t, v in self._sweep_axis_by_tag.items() if t in live}
        self._zone_center_by_tag = {t: v for t, v in self._zone_center_by_tag.items() if t in live}
        self._last_flyout_by_tag = {t: v for t, v in self._last_flyout_by_tag.items() if t in live}
        # STAGE 路径/进度缓存剪切（都按 tag 剪，#587 路径改 per-(tag,stage_key)）
        self._stage_path = {k: v for k, v in self._stage_path.items() if k[0] in live}
        self._stage_idx = {k: v for k, v in self._stage_idx.items() if k[0] in live}

        # 剪切 per-group 过期状态（已废弃的 did）
        for cache_dict in (
            self._group_zone,
            self._group_zone_since,
            self._group_patrol_rank,
            self._group_patrol_since,
            self._stage_pt,
            self._stage_key,
        ):
            for d in list(cache_dict):
                if d not in live_dids:
                    del cache_dict[d]

        if not groups:
            return

        home = self._get_home_anchor()
        now = float(self.ai.time)

        for group in groups:
            self._tick_group(group, all_bcs, home, now)

    # ------------------------------------------------------------------
    # 群调度（每群每帧）
    # ------------------------------------------------------------------

    def _tick_group(
        self,
        group: dict,
        all_bcs: dict[int, Any],
        home: Point2 | None,
        now: float,
    ) -> None:
        did: str = group["did"]
        tags: set[int] = group.get("tags") or set()

        alive = [all_bcs[tag] for tag in tags if tag in all_bcs]
        if not alive:
            # 群空：清 per-group 状态，等新 BC 入伍
            for d in (
                self._group_zone,
                self._group_zone_since,
                self._group_patrol_rank,
                self._group_patrol_since,
                self._stage_pt,
                self._stage_key,
            ):
                d.pop(did, None)
            return

        # ── 目标矿区（总是计算，未入队 BC 也需要用来赶 stage）──────────────
        target_rank = self._pick_group_zone(group, did, now, alive)
        target_anchor: Point2 | None = None
        zone_center: Point2 | None = None
        sweep_axis: Point2 | None = None
        harass_ml: Point2 | None = None  # 目标矿矿线中心（算场外集结点用）
        enemy_main_c: Point2 | None = None  # 敌方主基地中心（接近时避障绕它）
        if target_rank is not None:
            zone = self._enemy_zone_by_rank(target_rank)
            geom = self._harass_geom(zone)
            if geom is not None:
                target_anchor, sweep_axis, zone_center = geom
                harass_ml = getattr(zone, "mineral_line_center", None)
        _emz = self._enemy_zone_by_rank(0)
        enemy_main_c = getattr(_emz, "center_location", None) if _emz is not None else None

        # ── 集结点 stage（必修1：矿线未知也有兜底，绝不因未侦察杵家）──────────
        stage = self._stage_for_group(did, harass_ml, zone_center, enemy_main_c)

        # ── 每帧 recruit-state + reserve（必修4）──────────────────────────────
        # director 是唯一 recruiter（已每 tick 把新 BC 放进 group.tags）；act 只对没状态的赋 STAGE，
        # 且每帧 _reserve 独占（防 sharpy PlanZoneGather 把 idle 空军拉回家）。
        for bc in alive:
            self._reserve(bc)
            if bc.tag not in self._state:
                self._state[bc.tag] = "STAGE"
                self._state_since[bc.tag] = now

        # ── HEAL 触发（纯自身血量/爆发，必修6：绝不"群不利就整队 HEAL"）──────────
        # **状态感知**（#587 修"去一半被拉回"）：
        #   DIVE(矿线暴露)→ 预测性早跳(under-fire 自适应阈值、省 CD) 或 爆发；
        #   STAGE(赶路去安全 stage)→ **只在真残血(<_RETURN_BAR) 或爆发才回**，别一挨火就被
        #     预测阈值拉回家（推过去到安全 stage，不半路折返）。
        for bc in alive:
            tag = bc.tag
            prev_hp = self._last_hp.get(tag, float(bc.health))
            self._last_hp[tag] = float(bc.health)
            st = self._state.get(tag)
            if st == "HEAL":
                continue
            hp_max = float(getattr(bc, "health_max", 550.0)) or 550.0
            drop = prev_hp - bc.health
            burst = drop > _BURST_DROP_RATIO * hp_max
            if st == "DIVE":
                trigger = burst or bc.health <= self._jump_hp_threshold(bc)
            else:  # STAGE：赶路**推过去**——只真残血(<40%)才回，**不因单帧重击(burst)折返**
                # （大件速度快、能穿过拦路火力到达安全 stage/矿线；一挨重击就退=半路被打回、永远到不了）
                trigger = bc.health_percentage <= _RETURN_BAR
            if trigger:
                self._state[tag] = "HEAL"
                self._state_since[tag] = now
                self._healed_stopped.discard(tag)
                self._stage_idx = {k: v for k, v in self._stage_idx.items() if k[0] != tag}
                if _BCRAID_TRACE:
                    logger.warning(
                        "BCRAIDTRACE to_heal tag=%d from=%s hp=%.2f drop=%.0f",
                        tag,
                        st,
                        bc.health_percentage,
                        drop,
                    )

        # ── 群决策：STAGE→DIVE commit（唯一群级逻辑；只读 n_ready、只写 STAGE→DIVE，
        #    对 DIVE 中的 BC 零写入 = 前排绝不被拉偏的根本保证，必修6）────────────
        n_dive = sum(1 for bc in alive if self._state.get(bc.tag) == "DIVE")
        ready = [
            bc
            for bc in alive
            if self._state.get(bc.tag) == "STAGE"
            and stage is not None
            and bc.distance_to(stage) < _RALLY_RADIUS
        ]
        commit_min = 1 if n_dive == 0 else 2  # 无前排→1(第一艘及时) / 有前排→2(增援等伴一起走)
        timed_out = any(
            now - self._state_since.get(bc.tag, now) >= _GATHER_WINDOW_S for bc in ready
        )
        if ready and (len(ready) >= commit_min or timed_out):
            for bc in ready:
                self._state[bc.tag] = "DIVE"
                self._state_since[bc.tag] = now
            if _BCRAID_TRACE:
                logger.warning(
                    "BCRAIDTRACE commit group=%s n=%d had_dive=%d timeout=%d",
                    did[:8],
                    len(ready),
                    n_dive,
                    int(timed_out),
                )

        # ── P1 cheap-kill 预检（DIVE 用，群级每帧一次）──────────────
        dive_cohort = [bc for bc in alive if self._state.get(bc.tag) == "DIVE"]
        cheap_kill_target: Any | None = None
        if target_anchor is not None and dive_cohort:
            fit_dive = [bc for bc in dive_cohort if bc.health_percentage >= _SALLY_HP]
            cheap_kill_target = self._p1_aa_cheap_kill(alive, fit_dive)

        # ── per-BC 行为（按单一状态分派 STAGE/DIVE/HEAL）───────────────────────
        for bc in alive:
            tag = bc.tag
            # 目标矿区中心 / 轴线写进 per-tag cache（供 near-micro sweep 用）
            if zone_center is not None:
                self._zone_center_by_tag[tag] = zone_center
            if sweep_axis is not None:
                self._sweep_axis_by_tag[tag] = sweep_axis

            # AoE 闪避（任意状态最高优先；_reserve 已在上面每帧做过）
            safe_pt = self._dodge_spot(bc)
            if safe_pt is not None:
                with contextlib.suppress(Exception):
                    bc.move(safe_pt)
                continue

            state = self._state.get(tag, "STAGE")
            if state == "HEAL":
                self._heal_step(bc, home, stage, now)
            elif state == "DIVE":
                self._dive_step(
                    bc,
                    target_anchor,
                    harass_ml,
                    zone_center,
                    enemy_main_c,
                    stage,
                    home,
                    did,
                    now,
                    dive_cohort,
                    cheap_kill_target,
                )
            else:  # STAGE
                self._stage_step(bc, stage, enemy_main_c, did, home, now)

    # ------------------------------------------------------------------
    # 单状态机 helper（#587 重写）：STAGE / DIVE / HEAL
    # ------------------------------------------------------------------

    def _stage_for_group(
        self,
        did: str,
        harass_ml: Point2 | None,
        zone_center: Point2 | None,
        enemy_main_c: Point2 | None,
    ) -> Point2 | None:
        """集结点（敌方主基外安全点）。一次锁定、只随真正切矿更新（必修1+5）。

        矿线已知 → `harass_stage_point`（矿背外侧开阔地）；
        矿线未知（未侦察）→ **兜底**：敌方主基/出生点朝己方基地方向外推 _FALLBACK_STAGE_OUT 格，
        让第一艘 BC 立刻出门奔敌方主基外待命/揭视野，**绝不因未侦察杵家**。
        """
        if harass_ml is not None and zone_center is not None:
            key = f"ml:{harass_ml.x:.0f},{harass_ml.y:.0f}"
        else:
            key = "fallback"
        # 未切矿 → 返回缓存（锁定，别每帧重算漂移）
        if self._stage_key.get(did) == key and did in self._stage_pt:
            return self._stage_pt[did]
        stage: Point2 | None = None
        with contextlib.suppress(Exception):
            pa = getattr(getattr(self.ai, "game_info", None), "playable_area", None)
            if harass_ml is not None and zone_center is not None:
                stage = harass_stage_point(harass_ml, zone_center, pa)
            else:
                em = enemy_main_c
                if em is None:
                    em = self.ai.enemy_start_locations[0]
                stage = em.towards(self.ai.start_location, _FALLBACK_STAGE_OUT)
        if stage is not None:
            self._stage_pt[did] = stage
            self._stage_key[did] = key
            if _BCRAID_TRACE:
                logger.warning(
                    "BCRAIDTRACE stage group=%s key=%s pt=(%.1f,%.1f)",
                    did[:8],
                    key,
                    stage.x,
                    stage.y,
                )
        return self._stage_pt.get(did)

    def _stage_step(
        self,
        bc: Any,
        stage: Point2 | None,
        enemy_main_c: Point2 | None,
        did: str,
        home: Point2 | None,
        now: float,
    ) -> None:
        """STAGE：奔集结点（绕敌方主基、基本直线，不走中路也不绕整圈）；到了待命。"""
        if stage is None:
            return
        if bc.distance_to(stage) < _RALLY_RADIUS:
            with contextlib.suppress(Exception):
                bc.move(stage)  # 到位待命（幂等）
            return
        # 未到 → plan_avoid_path：基本直奔 stage，只绕开敌方主基（贴其高地边缘），一次锁定缓存
        wp = self._stage_wp(bc, stage, enemy_main_c, did)
        with contextlib.suppress(Exception):
            bc.move(wp)
        if _BCRAID_TRACE:
            _dm = bc.distance_to(enemy_main_c) if enemy_main_c is not None else -1.0
            logger.warning(
                "BCRAIDPATH tag=%d t=%.1f bc=(%.1f,%.1f) aim=(%.1f,%.1f) hp=%.2f state=STAGE "
                "dstage=%.1f dmain=%.1f",
                bc.tag,
                now,
                bc.position.x,
                bc.position.y,
                wp.x,
                wp.y,
                bc.health_percentage,
                bc.distance_to(stage),
                _dm,
            )

    def _stage_wp(self, bc: Any, stage: Point2, enemy_main_c: Point2 | None, did: str) -> Point2:
        """奔 stage 的当前 waypoint（plan_avoid_path：基本直线、只绕敌方主基，不绕整圈）。

        **路径按 (tag, stage_key) 缓存、从 BC 当前位置算**（#587 修"去一半被拉回"）：
        换矿 → stage_key 变 → 从 BC **当前位置**重算奔新 stage → **继续前进、绝不飞回家**。
        （旧版按 (did,stage_key) 从 home 算 + idx 归 0 → 换矿时半路 BC 被塞回"路径第0点=家" → 飞回家。）
        用户 2026-07-04：plan_edge_path 绕整圈太绕 → 改基本直奔、只绕主基。
        """
        stage_key = self._stage_key.get(did, "")
        key = (bc.tag, stage_key)
        wps = self._stage_path.get(key)
        if wps is None:
            with contextlib.suppress(Exception):
                pa = self.ai.game_info.playable_area
                avoid = [enemy_main_c] if enemy_main_c is not None else []
                wps = plan_avoid_path(bc.position, stage, avoid, pa)
            if not wps:
                wps = [stage]
            self._stage_path[key] = wps
        idx = self._stage_idx.get(key, 0)
        while idx < len(wps) - 1 and bc.distance_to(wps[idx]) < 5.0:
            idx += 1
        self._stage_idx[key] = idx
        return wps[min(idx, len(wps) - 1)]

    def _dive_step(
        self,
        bc: Any,
        behind: Point2 | None,
        harass_ml: Point2 | None,
        zone_center: Point2 | None,
        enemy_main_c: Point2 | None,
        stage: Point2 | None,
        home: Point2 | None,
        did: str,
        now: float,
        dive_cohort: list,
        cheap_kill_target: Any | None,
    ) -> None:
        """DIVE：短程直飞矿后点（当前位置起，不走 home-path 全 approach，必修2）+ 矿后微操。"""
        tag = bc.tag
        # 无目标矿（未揭开/全 score<=0/切矿间隙）→ 去 stage 揭视野，**绝不回家**（必修2）
        if behind is None:
            tgt = stage if stage is not None else home
            if tgt is not None:
                with contextlib.suppress(Exception):
                    bc.move(tgt)
            return
        # P1 cheap-kill：孤立静态防空 + 群火够秒 → 集火解锁矿线
        if cheap_kill_target is not None:
            with contextlib.suppress(Exception):
                bc.move(cheap_kill_target.position)
            return
        # P1 精确射程规避：打不过 → 出所有威胁射程外
        flee_pt = self._p1_threat_flee(bc)
        if flee_pt is not None:
            with contextlib.suppress(Exception):
                bc.move(flee_pt)
            return
        # 未到矿后点 → 短程直飞（当前位置直奔 behind，不重算 home-path）
        if bc.distance_to(behind) > _ENGAGE_RADIUS:
            with contextlib.suppress(Exception):
                bc.move(behind)
            self._dive_trace(bc, behind, now)
            return
        # 到位 → near-micro：贴农民质心 + 风筝 + sweep（不停微动别静立送靶）
        base = self._nearby_worker_center(bc, behind) or behind
        threat = self._nearby_threat(bc)
        mode = "move" if threat is not None else "attack"
        if threat is not None:
            with contextlib.suppress(Exception):
                base = base.towards(threat, -_KITE_BACK)
        axis = self._sweep_axis_by_tag.get(tag)
        if axis is not None:
            off = _SWEEP_AMPLITUDE * sin(now * _SWEEP_OMEGA + (tag % 8) * 0.785)
            aim = Point2((base.x + axis.x * off, base.y + axis.y * off))
        else:
            aim = base
        with contextlib.suppress(Exception):
            if mode == "attack":
                bc.attack(aim)
            else:
                bc.move(aim)
        self._dive_trace(bc, aim, now)

    def _dive_trace(self, bc: Any, aim: Point2, now: float) -> None:
        """DIVE 自验 trace：BC 位置 + aim + 到三条敌矿矿线距离（外部终态判据）。"""
        if not _BCRAID_TRACE:
            return
        with contextlib.suppress(Exception):
            dists = []
            for _rk in (0, 1, 2):
                _z = self._enemy_zone_by_rank(_rk)
                _ml = getattr(_z, "mineral_line_center", None) if _z is not None else None
                dists.append(bc.distance_to(_ml) if _ml is not None else 999.0)
            _mz = self._enemy_zone_by_rank(0)
            _mc = getattr(_mz, "center_location", None) if _mz is not None else None
            logger.warning(
                "BCRAIDPATH tag=%d t=%.1f bc=(%.1f,%.1f) aim=(%.1f,%.1f) hp=%.2f state=DIVE "
                "dmain=%.1f d0=%.1f d1=%.1f d2=%.1f",
                bc.tag,
                now,
                bc.position.x,
                bc.position.y,
                aim.x,
                aim.y,
                bc.health_percentage,
                bc.distance_to(_mc) if _mc is not None else -1.0,
                dists[0],
                dists[1],
                dists[2],
            )

    def _heal_step(
        self,
        bc: Any,
        home: Point2 | None,
        stage: Point2 | None,
        now: float,
    ) -> None:
        """HEAL：跳CD好→传送回家；CD没好→退 stage 等CD（不走中路，必修3）；到家 hold_position
        停住让 SCV 修（只发一次，必修3）；修满≥recover→归队 STAGE。"""
        tag = bc.tag
        if bc.health_percentage >= self._recover_hp_ratio:
            self._state[tag] = "STAGE"
            self._state_since[tag] = now
            self._healed_stopped.discard(tag)
            if _BCRAID_TRACE:
                logger.warning(
                    "BCRAIDTRACE heal_done tag=%d hp=%.2f ->STAGE", tag, bc.health_percentage
                )
            return
        if home is None:
            return
        if bc.distance_to(home) < _HOME_STOP_RADIUS:
            # 到家：发一次 hold_position 停住，之后每帧不发指令（Reserved→别人不碰=静止，让 SCV 修）
            if tag not in self._healed_stopped:
                with contextlib.suppress(Exception):
                    bc.hold_position()
                self._healed_stopped.add(tag)
                if _BCRAID_TRACE:
                    logger.warning(
                        "BCRAIDTRACE heal_stop tag=%d hp=%.2f", tag, bc.health_percentage
                    )
            return
        self._healed_stopped.discard(tag)
        if self.cd_manager.is_ready(tag, AbilityId.EFFECT_TACTICALJUMP):
            with contextlib.suppress(Exception):
                self.cd_manager.used_ability(tag, AbilityId.EFFECT_TACTICALJUMP)
                bc(AbilityId.EFFECT_TACTICALJUMP, home)
            if _BCRAID_TRACE:
                logger.warning(
                    "BCRAIDTRACE heal_jump tag=%d ->home hp=%.2f", tag, bc.health_percentage
                )
        else:
            # 跳 CD 没好 → 退到 stage 安全点等 CD（绝不 move(home) 穿中路 71s 送死）
            tgt = stage if stage is not None else home
            with contextlib.suppress(Exception):
                bc.move(tgt)
            if _BCRAID_TRACE:
                logger.warning(
                    "BCRAIDTRACE heal_wait_cd tag=%d ->stage hp=%.2f", tag, bc.health_percentage
                )

    # ------------------------------------------------------------------
    # 目标矿区选取（per-group，带切换滞回）
    # ------------------------------------------------------------------

    def _pick_group_zone(self, group: dict, did: str, now: float, alive: list[Any]) -> int | None:
        """选定该 group 这帧要骚扰的矿区 rank。

        group.target 固定矿 → 锁死该矿，不跑评分。
        target=None → 安全矿评分 picker + 切换滞回；全 score<=0 → patrol fallback。
        """
        target_str = group.get("target")
        if target_str in _RANK_BY_SPEC:
            return _RANK_BY_SPEC[target_str]

        # ── 安全矿评分（auto）───────────────────────────────────────────
        scored: list[tuple[float, int]] = []
        for rank in _PATROL_RANKS:
            zone = self._enemy_zone_by_rank(rank)
            if zone is None:
                continue
            workers = self._count_workers_near(zone)
            aa_dps = self._anti_air_dps_near(zone)
            score = float(workers) - _W_AA * aa_dps
            scored.append((score, rank))

        if not scored or all(s <= 0.0 for s, _ in scored):
            # 所有已知矿 score<=0：未侦察/农民藏起来 → patrol fallback 揭视野
            return self._patrol_fallback_rank(did, now, alive)

        best_score, best_rank = max(scored, key=lambda x: x[0])

        # 切换滞回：当前矿不切除非领先 1.3x 且停留够 8s
        cur_rank = self._group_zone.get(did)
        if cur_rank is None:
            self._group_zone[did] = best_rank
            self._group_zone_since[did] = now
            return best_rank

        cur_score = next((s for s, r in scored if r == cur_rank), -999.0)
        time_on_zone = now - self._group_zone_since.get(did, 0.0)
        if (
            best_score > cur_score * _ZONE_SWITCH_SCORE_RATIO
            and time_on_zone >= _ZONE_SWITCH_MIN_DWELL_S
        ):
            if _BCRAID_TRACE and best_rank != cur_rank:
                logger.warning(
                    "BCRAIDTRACE zone_switch group=%s rank %d->%d score %.1f->%.1f",
                    did[:8],
                    cur_rank,
                    best_rank,
                    cur_score,
                    best_score,
                )
            self._group_zone[did] = best_rank
            self._group_zone_since[did] = now

        return self._group_zone[did]

    def _patrol_fallback_rank(self, did: str, now: float, alive: list[Any]) -> int | None:
        """所有已知矿 score<=0 的兜底：按 rank 轮换揭视野，不在家发呆。

        **到达门（#580 修，2026-07-02）**：只有当群里任一 BC 真正抵达当前巡逻矿的
        airspace（≤ _PATROL_ARRIVE_RADIUS）后，才开始计驻留、才允许轮换到下一个矿。
        BC 还在贴边途中就**锁死当前目标不轮换**——否则每次换 rank → edge-path target_key
        变 → 贴边路径从远角从头重算，横穿全图 100+s 永远走不完就被换目标，BC 在自己
        这侧三个角之间打转、从没接近敌方（真局 trace 确诊的"到不了对方矿后"根因）。
        """
        rank = self._group_patrol_rank.get(did)
        if rank is None:
            rank = _PATROL_RANKS[0]
            self._group_patrol_rank[did] = rank
            self._group_patrol_since[did] = now
            return rank

        zone = self._enemy_zone_by_rank(rank)
        if zone is None:
            # 该 rank 无矿区 → 前进到下一个
            new_rank = self._next_rank(rank)
            self._group_patrol_rank[did] = new_rank
            self._group_patrol_since[did] = now
            return new_rank

        # 到达判定：任一 BC 进入该矿 airspace 才算"到"
        arrived = False
        with contextlib.suppress(Exception):
            zc = zone.center_location
            arrived = any(bc.distance_to(zc) <= _PATROL_ARRIVE_RADIUS for bc in alive)

        if not arrived:
            # 还在贴边途中：锁死目标不轮换 + 重置驻留钟（驻留只计"到矿后"时间）
            self._group_patrol_since[did] = now
            return rank

        # 已抵达且驻留满 _raid_dwell_s（该矿确无农民）→ 巡逻到下一个矿揭视野
        dwell = now - self._group_patrol_since.get(did, now)
        if dwell >= self._raid_dwell_s:
            new_rank = self._next_rank(rank)
            self._group_patrol_rank[did] = new_rank
            self._group_patrol_since[did] = now
            if _BCRAID_TRACE:
                logger.warning(
                    "BCRAIDTRACE patrol_fallback group=%s rank %d->%d (dwell=%.0f arrived)",
                    did[:8],
                    rank,
                    new_rank,
                    dwell,
                )
            return new_rank

        return rank

    def _next_rank(self, rank: int) -> int:
        """巡逻圈下一个排名（主→二→三→主…），跳过不存在的矿区。"""
        ranks = _PATROL_RANKS
        try:
            idx = ranks.index(rank)
        except ValueError:
            idx = 0
        for step in range(1, len(ranks) + 1):
            nxt = ranks[(idx + step) % len(ranks)]
            if self._enemy_zone_by_rank(nxt) is not None:
                return nxt
        return rank

    # ------------------------------------------------------------------
    # 防空评分辅助（§4，#580 新增）
    # ------------------------------------------------------------------

    def _anti_air_dps_near(self, zone: Any) -> float:
        """zone 矿线中心半径 _AA_RADIUS 内可见敌方能打空军单位 air_dps 之和 + 静态防空。"""
        total = 0.0
        with contextlib.suppress(Exception):
            ml = zone.mineral_line_center
            for u in self.ai.enemy_units:
                if u.distance_to(ml) > _AA_RADIUS:
                    continue
                if (
                    getattr(u, "can_attack_air", False)
                    or getattr(u, "type_id", None) in _STATIC_AA_TYPES
                ):
                    total += float(getattr(u, "air_dps", 0.0))
        return total

    def _count_workers_near(self, zone: Any) -> int:
        """zone 矿线附近可见工人数（评分分子）。"""
        with contextlib.suppress(Exception):
            ml = zone.mineral_line_center
            return sum(
                1
                for u in self.ai.enemy_units
                if _is_worker(u) and u.distance_to(ml) < _WORKERS_RADIUS
            )
        return 0

    # ------------------------------------------------------------------
    # 矿区几何（#561 验证，不改）
    # ------------------------------------------------------------------

    def _harass_geom(self, zone: Any | None) -> tuple[Point2, Point2, Point2] | None:
        """返回 (锚点=矿後, 沿矿线轴线单位向量, 矿区中心=基地)。

        锚点 = mineral_line_center 背基地侧偏移 _BEHIND_MINERAL_OFFSET 格（矿後，§3.6 P3）；
        矿体位于基地和 BC 之间 → 用矿体挡地面单位 = 自保几何。
        轴线 = 垂直于"基地→矿线"（沿矿线展开）。
        第三个返回值 = 矿区中心(基地)，供 _raid_move_point 判"是否已抵近本矿 airspace"。
        """
        if zone is None:
            return None
        with contextlib.suppress(Exception):
            th = zone.center_location
            c = zone.mineral_line_center
            d = c - th
            dn = d.normalized if d.length >= 0.1 else Point2((1.0, 0.0))
            axis = Point2((-dn.y, dn.x))  # 垂直 = 沿矿线
            # 矿後锚点：矿线再往背基地方向偏 _BEHIND_MINERAL_OFFSET 格
            behind = Point2(
                (c.x + dn.x * _BEHIND_MINERAL_OFFSET, c.y + dn.y * _BEHIND_MINERAL_OFFSET)
            )
            return (behind, axis, th)
        return None

    def _enemy_zone_by_rank(self, rank: int) -> Any | None:
        """敌方第 rank 个 expansion（0=主矿/1=二矿/2=三矿），按距敌方 start_location 距离排序。

        确定性：距离排序（同输入每帧同输出，不用 .first / is_enemys 这类帧间不稳来源）。
        """
        with contextlib.suppress(Exception):
            zm = self.zone_manager
            start = getattr(zm, "enemy_start_location", None)
            if start is None:
                start = self.ai.enemy_start_locations[0]
            zones = sorted(zm.expansion_zones, key=lambda z: z.center_location.distance_to(start))
            if 0 <= rank < len(zones):
                return zones[rank]
        return None

    def _zone_has_workers(self, zone: Any) -> bool:
        with contextlib.suppress(Exception):
            ml = zone.mineral_line_center
            return any(
                _is_worker(u) and u.distance_to(ml) < _WORKERS_RADIUS for u in self.ai.enemy_units
            )
        return False

    # ------------------------------------------------------------------
    # 跳跃阈值（#561/#557 验证，不改）
    # ------------------------------------------------------------------

    def _jump_hp_threshold(self, bc: Any) -> float:
        """自适应跳跃血量阈值（绝对 HP）。

        floor = 9% × 满血(保底必跳)；
        incoming = 当前能打到这艘 BC 的敌方对空单位 DPS 之和；
        jump_hp = clamp(incoming × safety_s, floor, 满血)。
        """
        hp_max = float(getattr(bc, "health_max", 550.0)) or 550.0
        floor = self._jump_floor_ratio * hp_max
        incoming = 0.0
        with contextlib.suppress(Exception):
            for e in self.ai.all_enemy_units.closer_than(_JUMP_THREAT_RADIUS, bc.position):
                if not getattr(e, "can_attack_air", False):
                    continue
                if e.target_in_range(bc, bonus_distance=3.5):
                    incoming += float(getattr(e, "air_dps", 0.0))
        raw = incoming * self._jump_safety_s
        return max(floor, min(raw, hp_max))

    # ------------------------------------------------------------------
    # AoE 闪避（#561 验证，不改）
    # ------------------------------------------------------------------

    def _dodge_spot(self, bc: Any) -> Point2 | None:
        """检测危险 AoE effect；若 BC 在危险区返回安全点，否则 None。"""
        nearest_danger: Point2 | None = None
        nearest_dist = float("inf")
        nearest_eff_id: EffectId | None = None
        with contextlib.suppress(Exception):
            bc_r = float(getattr(bc, "radius", 1.0))
            for eff in self.ai.state.effects:
                if eff.id not in _DANGER_EFFECTS:
                    continue
                eff_radius = _EFFECT_RADIUS.get(eff.id, 3.0)
                danger_r = eff_radius + bc_r + 2.0
                for pos in eff.positions:
                    d = bc.distance_to(pos)
                    if d <= danger_r and d < nearest_dist:
                        nearest_dist = d
                        nearest_danger = pos
                        nearest_eff_id = eff.id
        if nearest_danger is None:
            return None
        if _BCRAID_TRACE:
            logger.warning(
                "BCRAIDTRACE dodge tag=%d eff=%s dist=%.1f", bc.tag, nearest_eff_id, nearest_dist
            )
        return bc.position.towards(nearest_danger, -5.0)

    # ------------------------------------------------------------------
    # P1 威胁规避（§3.6，#580 新增；补 1/2 细化见下方）
    # ------------------------------------------------------------------

    def _p1_aa_cheap_kill(self, group_bcs: list[Any], fit_bcs: list[Any]) -> Any | None:
        """P1 补 1：cheap-kill 预检 —— 群内 fit BC 附近孤立的静态防空建筑且群能快杀 → 返回目标。

        cheap_kill(building) 成立条件（同时满足）：
        ① 孤立：建筑 _P1_CHEAP_KILL_ISOLATION_RADIUS 内无敌方军队 / 无其它 AA 接力。
        ② kill viability：
               kill_time = building.health / group_ground_dps
               kill_time × building.air_dps < avg_bc_hp × _P1_CHEAP_KILL_BUDGET_RATIO

        成立 → 返回该建筑（所有群 BC 集火，move 到建筑位置，打掉解锁矿线）。
        否则 → None（走精确射程规避）。

        用游戏实时值（building.health / air_dps, bc.ground_dps），不硬编 SC2 数字。
        群级别预检，每帧 _tick_group 调用一次，避免 per-BC 重复扫描。
        """
        if not fit_bcs:
            return None

        group_ground_dps = max(
            sum(float(getattr(bc, "ground_dps", 0.0)) for bc in fit_bcs),
            1.0,
        )
        avg_bc_hp = sum(float(getattr(bc, "health", 550.0)) for bc in fit_bcs) / len(fit_bcs)
        hp_budget = avg_bc_hp * _P1_CHEAP_KILL_BUDGET_RATIO

        with contextlib.suppress(Exception):
            for building in self.ai.enemy_units:
                if getattr(building, "type_id", None) not in _STATIC_AA_TYPES:
                    continue
                # 建筑必须在某艘 fit BC 的 P1 威胁半径内（否则不影响群的当前骚扰）
                if not any(bc.distance_to(building) <= _P1_THREAT_RADIUS for bc in fit_bcs):
                    continue
                # ① 孤立检查：isolation 半径内无 army（can_attack_air）也无其它 AA 建筑
                isolated = True
                for other in self.ai.enemy_units:
                    if other is building:
                        continue
                    if other.distance_to(building) > _P1_CHEAP_KILL_ISOLATION_RADIUS:
                        continue
                    if not _is_worker(other) and (
                        getattr(other, "can_attack_air", False)
                        or getattr(other, "type_id", None) in _STATIC_AA_TYPES
                    ):
                        isolated = False
                        break
                if not isolated:
                    continue
                # ② kill viability：群击杀期间承受的 AA 伤害 < 可接受血量预算
                building_hp = float(getattr(building, "health", 100.0))
                building_air_dps = float(getattr(building, "air_dps", 0.0))
                kill_time = building_hp / group_ground_dps
                damage_taken = building_air_dps * kill_time
                if damage_taken < hp_budget:
                    return building  # cheap kill 成立 → 整群集火它
        return None

    def _p1_threat_flee(self, bc: Any) -> Point2 | None:
        """P1 补 2：精确射程规避。评估附近对空威胁 DPS；超阈值 → 返回精确出射程外的规避点。

        flee_dist = max(in-range 威胁的 air_range) + _P1_FLEE_RANGE_BUFFER
        （避免过躲 / 欠躲；air_range 取不到则退回保守 fallback _P1_FLEE_DIST）。

        威胁 = 能打空的敌方战斗单位 + 静态防空建筑，不含工人。
        轻威胁（DPS < floor）不触发，继续 P2/P3。
        """
        threats: list[Any] = []
        with contextlib.suppress(Exception):
            for u in self.ai.enemy_units:
                if bc.distance_to(u) > _P1_THREAT_RADIUS:
                    continue
                if _is_worker(u):
                    continue
                if (
                    getattr(u, "can_attack_air", False)
                    or getattr(u, "type_id", None) in _STATIC_AA_TYPES
                ):
                    threats.append(u)

        if not threats:
            return None

        total_dps = sum(float(getattr(u, "air_dps", 0.0)) for u in threats)
        if total_dps < _P1_THREAT_DPS_FLOOR:
            return None

        # 威胁质心（规避目标：远离它）
        tx = sum(float(u.position.x) for u in threats) / len(threats)
        ty = sum(float(u.position.y) for u in threats) / len(threats)
        threat_center = Point2((tx, ty))

        # 精确出射程：flee_dist = max(in-range 威胁的 air_range) + buffer（§3.6 补 2）
        # 取不到 air_range 则退回保守 fallback；避免过躲（太远不打农民）/ 欠躲（固定 12 还在范围内）
        max_range = max(
            (float(getattr(u, "air_range", 0.0)) for u in threats),
            default=0.0,
        )
        flee_dist = (max_range + _P1_FLEE_RANGE_BUFFER) if max_range > 0.0 else _P1_FLEE_DIST
        flee_pt = bc.position.towards(threat_center, -flee_dist)

        if _BCRAID_TRACE:
            dist_to_threat = bc.distance_to(threat_center)
            logger.warning(
                "BCRAIDTRACE threat_avoid tag=%d dist_to_threat=%.1f fleeing=1 dps=%.1f n_threats=%d",
                bc.tag,
                dist_to_threat,
                total_dps,
                len(threats),
            )

        return flee_pt

    def _nearby_worker_center(self, bc: Any, anchor: Point2) -> Point2 | None:
        """以 anchor(矿线锚点) 为圆心、_WORKER_SEEK_RADIUS 内的敌方农民质心。

        以锚点(不是 BC)为圆心 → 农民沿矿线逃也一路跟，不会"逃出 BC 半径就缩回"。
        """
        with contextlib.suppress(Exception):
            ws = [
                u
                for u in self.ai.enemy_units
                if _is_worker(u) and anchor.distance_to(u.position) < _WORKER_SEEK_RADIUS
            ]
            if ws:
                n = len(ws)
                return Point2(
                    (sum(u.position.x for u in ws) / n, sum(u.position.y for u in ws) / n)
                )
        return None

    def _nearby_threat(self, bc: Any) -> Point2 | None:
        """_KITE_THREAT_RADIUS 内最近的、能打空军的敌方机动战斗单位位置（风筝躲它）。"""
        with contextlib.suppress(Exception):
            threats = [
                u
                for u in self.ai.enemy_units
                if not _is_worker(u)
                and getattr(u, "can_attack_air", False)
                and bc.distance_to(u) < _KITE_THREAT_RADIUS
            ]
            if threats:
                return min(threats, key=lambda u: bc.distance_to(u)).position
        return None

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _get_home_anchor(self) -> Point2 | None:
        """BC Jump 回家落点（一次锁定，不每帧重算 —— CLAUDE.md 强规则）。

        落点选主基台地上（矿和基地之间），农民来回采矿时够得着、好修。
        """
        if self._home_anchor is not None:
            return self._home_anchor
        with contextlib.suppress(Exception):
            zones = self.zone_manager.our_zones_with_minerals
            if zones:
                z = zones[0]
                self._home_anchor = z.center_location.towards(z.behind_mineral_position_center, 4.0)
                return self._home_anchor
        return None

    def _ensure_repair(self, bc: Any) -> None:
        """确保至少 3 个 SCV 在修这艘回血中的 BC（修得快）。"""
        if getattr(bc, "health_percentage", 1.0) >= 0.99:
            return
        with contextlib.suppress(Exception):
            scvs = self.cache.own(UnitTypeId.SCV)
            if not scvs:
                return
            repairing_this = sum(
                1
                for w in scvs
                if getattr(w, "is_repairing", False) and getattr(w, "order_target", None) == bc.tag
            )
            need = 3 - repairing_this
            if need <= 0:
                return
            free = [w for w in scvs if not getattr(w, "is_repairing", False)]
            free.sort(key=lambda w: w.distance_to(bc))
            for w in free[:need]:
                with contextlib.suppress(Exception):
                    w.repair(bc)

    def _reserve(self, unit: object) -> None:
        """每帧重设 Reserved，独占控制权（照 PhoenixSquadAct 范式）。"""
        with contextlib.suppress(Exception):
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, unit)

    def _release(self, unit: object) -> None:
        """释放 Reserved → Idle，sharpy free_units 接管，BC 归队主力。"""
        with contextlib.suppress(Exception):
            self.knowledge.roles.clear_task(unit)


# ──────────────────────────────────────────────────────────────────────────────
# 在家修理执行器（#583，单一归属：GroupHarassAct 不再调 _ensure_repair）
# ──────────────────────────────────────────────────────────────────────────────


class BcHomeRepairAct(ActBase):  # type: ignore[misc]
    """残血在家 BC 修理执行器：对所有 ready BC，若残血（<0.95）且在家（离任一 townhall
    < _HOME_REPAIR_RADIUS），自动召 ≤3 个空闲 SCV 来修；满血（≥0.99）不派。
    non-blocking（return True），放 tactics SequentialList。

    单一归属：GroupHarassAct 里 healing/STAGING 分支已删 _ensure_repair 调用，
    修理路径只走此 act，避免同帧两个系统同时抢农民（MUST-FIX ③，#583 评审 P0）。

    "修完回采矿"：SCV repair 完自然 idle，sharpy DistributeWorkers/SpeedMining 下帧收回。
    若真局发现 SCV 修完滞留，再显式还 role（当前先靠自然收回）。

    UNVERIFIED（需真机核对）：SCV.repair(unit) / is_repairing / order_target 返回 tag(int)。
    真机验收：telemetry 确认 BC 血量回升 + 修完 SCV 回采矿（gas/mineral workers 恢复）。

    Note（#583 MUST-FIX D）：释放的残血 BC 是否自己回家由真局验；
    若 BC 被 sharpy 主力 plan 带出去战斗不回家，则此 act 不会修它（在家判定 <N 格不满足），
    需后续补 move(home) 显式引导才能触发修理。
    """

    async def execute(self) -> bool:
        try:
            self._do_repair()
        except Exception:
            logger.exception("BcHomeRepairAct._do_repair error")
        return True

    def _do_repair(self) -> None:
        bcs = list(self.cache.own(UnitTypeId.BATTLECRUISER).ready)
        if not bcs:
            return
        scvs = list(self.cache.own(UnitTypeId.SCV))
        if not scvs:
            return
        # 己方 townhall 位置（用于"在家"判定）
        townhall_positions: list[Any] = []
        with contextlib.suppress(Exception):
            townhall_positions = [th.position for th in self.ai.townhalls]
        if not townhall_positions:
            return

        for bc in bcs:
            if bc.health_percentage >= 0.99:
                continue
            # 在家判定：离任一己方 townhall < _HOME_REPAIR_RADIUS
            at_home = False
            with contextlib.suppress(Exception):
                at_home = any(
                    bc.distance_to(pos) < _HOME_REPAIR_RADIUS for pos in townhall_positions
                )
            if not at_home:
                continue
            # 统计已在修此 BC 的 SCV（判重，防同帧重复派）
            with contextlib.suppress(Exception):
                repairing_this = sum(
                    1
                    for w in scvs
                    if getattr(w, "is_repairing", False)
                    and getattr(w, "order_target", None) == bc.tag
                )
                need = 3 - repairing_this
                if need <= 0:
                    continue
                free = [w for w in scvs if not getattr(w, "is_repairing", False)]
                free.sort(key=lambda w: w.distance_to(bc))
                for w in free[:need]:
                    with contextlib.suppress(Exception):
                        w.repair(bc)


# 向后兼容别名（引用旧类名的地方可逐步迁移）
BcRaidSquadAct = GroupHarassAct
