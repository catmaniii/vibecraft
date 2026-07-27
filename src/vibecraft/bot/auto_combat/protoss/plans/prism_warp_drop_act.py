"""PrismWarpDropAct: 神族二段空投(style=warp_then_drop)。

状态机(9 states):
  IDLE → FLY_TO_WARP_SPOT → DEPLOY_PHASING → WARP_UNITS →
  WAIT_WARP_COMPLETE → MORPH_TRANSPORT → LOAD_CARGO →
  FLY_TO_FINAL → UNLOAD_FINAL → DONE

行为:
  1. 找到棱镜 → IDLE 切 FLY_TO_WARP_SPOT
  2. FLY_TO_WARP_SPOT: 空船飞到 warp_pos(DropTarget),用 drop_path 规划路径
  3. DEPLOY_PHASING: morph 成 WARPPRISMPHASING 模式
  4. WARP_UNITS: 调 warpgate warp cargo_count 个 cargo_unit(默认 DT)到 phasing 范围
  5. WAIT_WARP_COMPLETE: 等附近 cargo_unit build_progress >= 1.0 数量 >= cargo_count
     兜底超时 60s 防 warp 卡死
  6. MORPH_TRANSPORT: morph 回 WARPPRISM transport 模式
  7. LOAD_CARGO: smart-cast 已 warp 的 cargo_unit 上船
  8. FLY_TO_FINAL: 飞到 final_drop_pos(用 drop_path 规划)
  9. UNLOAD_FINAL: UNLOADALLAT_WARPPRISM → 切 DONE

DT 卸下后微操(cargo_unit=DARKTEMPLAR 时):
  DONE 状态后每 tick 调 DtHarassMicro.tick()。接管离 home > 30 格的 DT:
  - raid state: Reserved + attack 最近敌方农民;无视野 → attack-move 到 enemy_main
  - 被攻击或 detector 在范围内 → released: clear Reserved,交 sharpy 接管

Attack handling:
  DEPLOY_PHASING / WARP_UNITS / WAIT_WARP_COMPLETE 阶段棱镜被打:
  - phasing 中被打 + 无法撑到 warp 完成 → morph transport + 撤退 → RETREAT_HOME
  - RETREAT_HOME 冷却 30s + 到家 → 重置 IDLE

参考:
  src/vibecraft/bot/auto_combat/protoss/plans/dt_micro.py (DtHarassMicro)
  src/vibecraft/bot/auto_combat/protoss/plans/generic_drop_act.py (GenericDropAct)
  src/vibecraft/bot/auto_combat/protoss/plans/warp_dt_at_prism.py (WarpDTAtPrism)
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

from vibecraft.bot.auto_combat.protoss.plans.dt_micro import DtHarassMicro
from vibecraft.bot.auto_combat.protoss.plans.warp_cooldowns import get_warp_cooldown
from vibecraft.bot.drop_path import plan_drop_path
from vibecraft.bot.named_spot import DropTarget

logger = logging.getLogger(__name__)

# ---- 阈值常量 ----
_ARRIVED_DISTANCE: float = 3.0
# WAIT_WARP_COMPLETE 超时兜底(秒)
_WARP_TIMEOUT_S: float = 60.0
# DT 附近距离(判定"在 phasing 范围内")
_PHASING_RANGE: float = 10.0
# LOAD_CARGO: 最低装载数量分数(cargo_count 的 50%)
_LOADING_MIN_FRAC: float = 0.5
# 装载硬超时
_LOADING_HARD_TIMEOUT_S: float = 60.0
# RETREAT_HOME 冷却(撤回后等多久重置 IDLE)
_RETREAT_COOLDOWN_S: float = 30.0
# 撤回到家的安全距离
_RETREAT_HOME_PROXIMITY: float = 15.0
# 棱镜受击记忆时间(秒)
_DAMAGE_MEMORY_S: float = 3.0
# 展开中挨打:折跃进度 > 此值就撑着折完
_WARP_FINISH_BP: float = 0.7
# 展开中挨打:HP 比例 > 此值才撑
_WARP_FINISH_HP_RATIO: float = 0.5
# WG cd 查 warp_cooldowns 表(per-cargo_unit,见 self._warp_cd 初始化)
# FLY_TO_WARP_SPOT stuck 兜底:位置不变超过此时间 → 强制 skip waypoint。
# 防 plan_drop_path 算出不可达点(map 外、被建筑挡)导致永远卡。
_FLY_STUCK_TIMEOUT_S: float = 8.0
# 位移阈值:小于此值算"没动"
_FLY_STUCK_EPSILON: float = 1.5

# WarpPrism 两种形态
_WARPPRISM_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.WARPPRISM, UnitTypeId.WARPPRISMPHASING}
)


class WarpDropState(str, Enum):
    """二段空投状态机。"""

    IDLE = "idle"
    FLY_TO_WARP_SPOT = "fly_to_warp_spot"
    DEPLOY_PHASING = "deploy_phasing"
    WARP_UNITS = "warp_units"
    WAIT_WARP_COMPLETE = "wait_warp_complete"
    MORPH_TRANSPORT = "morph_transport"
    LOAD_CARGO = "load_cargo"
    FLY_TO_FINAL = "fly_to_final"
    UNLOAD_FINAL = "unload_final"
    DONE = "done"
    RETREAT_HOME = "retreat_home"


class PrismWarpDropAct(ActBase):  # type: ignore[misc]
    """神族二段空投:棱镜飞前线 warp 兵 → 装船 → 二段深入。

    默认 cargo_unit=DARKTEMPLAR(DT),也可用其他 warp-gate 单位。

    warp_pos / final_drop_pos 支持两种形式:
      - DropTarget 实例 (已解析的坐标)
      - str spec (如 "enemy_main:ramp_outside"),首次 execute() 时懒解析
        (此时 self.ai 已被 sharpy 注入,可访问 zone_manager)
    """

    def __init__(
        self,
        cargo_unit: UnitTypeId,
        cargo_count: int,
        warp_pos: DropTarget | str,
        final_drop_pos: DropTarget | str,
        after_unload: str = "attack_workers",
    ) -> None:
        super().__init__()
        self.cargo_unit = cargo_unit
        self.cargo_count = cargo_count
        # 本 cargo 的 WG cooldown(LotV Faster,Z/A=20 S/Sen=23 HT/DT=32)
        self._warp_cd: float = get_warp_cooldown(cargo_unit)
        # 支持 str spec 懒解析
        self._warp_pos_raw: DropTarget | str = warp_pos
        self._final_drop_pos_raw: DropTarget | str = final_drop_pos
        self.warp_pos: DropTarget | None = warp_pos if isinstance(warp_pos, DropTarget) else None
        self.final_drop_pos: DropTarget | None = (
            final_drop_pos if isinstance(final_drop_pos, DropTarget) else None
        )
        self.after_unload = after_unload

        # 内部状态
        self._state: WarpDropState = WarpDropState.IDLE
        self._state_entered_ts: float = 0.0
        self._prism_tag: int | None = None

        # FLY_TO_WARP_SPOT path
        self._waypoints: list[Point2] | None = None
        self._wp_idx: int = 0
        # 2026-05-24 stuck 兜底:跟踪上次位置 + 时间,N 秒没动就 skip waypoint
        self._fly_last_pos: tuple[float, float] | None = None
        self._fly_last_progress_ts: float = 0.0

        # FLY_TO_FINAL path
        self._final_waypoints: list[Point2] | None = None
        self._final_wp_idx: int = 0

        # warp tracking
        self._warp_timeout_start: float | None = None

        # load tracking
        self._load_since: float | None = None

        # attack detection
        self._prism_hp_prev: float | None = None
        self._last_damage_ts: float = -1000.0

        # DT raid 微操(cargo_unit=DARKTEMPLAR 时激活)
        self._dt_micro: DtHarassMicro = DtHarassMicro()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def _resolve_lazy_targets(self) -> bool:
        """首次 execute() 时将 str spec 懒解析为 DropTarget。

        Returns False 如果解析失败(on_hold,下 tick 重试)。
        """
        if isinstance(self._warp_pos_raw, str) and self.warp_pos is None:
            from vibecraft.bot.named_spot import NamedSpotRegistry

            reg = NamedSpotRegistry()
            resolved = reg.resolve_drop_target(self._warp_pos_raw, self.ai)
            if resolved is None:
                logger.warning(
                    "PrismWarpDropAct: warp_pos spec '%s' 解析失败,下 tick 重试",
                    self._warp_pos_raw,
                )
                return False
            self.warp_pos = resolved

        if isinstance(self._final_drop_pos_raw, str) and self.final_drop_pos is None:
            from vibecraft.bot.named_spot import NamedSpotRegistry

            reg = NamedSpotRegistry()
            resolved = reg.resolve_drop_target(self._final_drop_pos_raw, self.ai)
            if resolved is None:
                logger.warning(
                    "PrismWarpDropAct: final_drop_pos spec '%s' 解析失败,下 tick 重试",
                    self._final_drop_pos_raw,
                )
                return False
            self.final_drop_pos = resolved

        return True

    async def execute(self) -> bool:
        if self._state == WarpDropState.DONE:
            # DONE 状态:棱镜使命完成。DT raid 微操由下方每 tick 逻辑接管(DARKTEMPLAR)。
            # 保持 False 让 act 留在 plan 继续 tick。
            if self.cargo_unit == UnitTypeId.DARKTEMPLAR:
                self._dt_micro.tick(self.ai, self.knowledge)
            return False

        # 懒解析 str spec → DropTarget (需要 self.ai 已被 sharpy 注入)
        if not self._resolve_lazy_targets():
            return False

        prism = self._find_prism()
        if prism is None:
            # 没有棱镜 → 保持 IDLE 等待;DT micro 继续接管已在敌方的 DT
            if self.cargo_unit == UnitTypeId.DARKTEMPLAR:
                self._dt_micro.tick(self.ai, self.knowledge)
            return False
        self._prism_tag = prism.tag

        # 标 Reserved 防 sharpy 抢走棱镜
        self._reserve(prism)

        # ---- 优先级 0: RETREAT_HOME 冷却 + 到家 → 重置 IDLE ----
        if self._state == WarpDropState.RETREAT_HOME:
            self._handle_retreat_cooldown(prism)
            # DT micro 继续接管敌方基地的 DT(prism 撤退不影响 DT 骚扰)
            if self.cargo_unit == UnitTypeId.DARKTEMPLAR:
                self._dt_micro.tick(self.ai, self.knowledge)
            return False

        # ---- 优先级 1: 棱镜被打 → 撤退(中途 warp 阶段) ----
        attacked = self._update_under_attack(prism)
        if attacked and self._state not in (
            WarpDropState.IDLE,
            WarpDropState.FLY_TO_WARP_SPOT,
            WarpDropState.FLY_TO_FINAL,
            WarpDropState.UNLOAD_FINAL,
            WarpDropState.DONE,
            WarpDropState.RETREAT_HOME,
        ):
            phasing = prism.type_id == UnitTypeId.WARPPRISMPHASING
            # 2026-05-24 修 Bug 2:LOAD_CARGO 半装态(cargo >= 1)不撤退 ——
            # 已装的 DT 留前线值钱,带走比保棱镜更重要;真扛不住下方 FLY_TO_FINAL
            # 飞行时还能再判受攻击。空船时(cargo = 0)正常撤退保棱镜。
            half_loaded = (
                self._state == WarpDropState.LOAD_CARGO
                and int(getattr(prism, "cargo_used", 0)) >= 1
            )
            if phasing and self._can_finish_warp(prism):
                pass  # 撑着折完
            elif half_loaded:
                pass  # 半装态先把货带走,不撤
            else:
                self._do_retreat(prism)
                # DT micro 继续(DT 已在敌方基地独立骚扰)
                if self.cargo_unit == UnitTypeId.DARKTEMPLAR:
                    self._dt_micro.tick(self.ai, self.knowledge)
                return False

        # ---- IDLE → FLY_TO_WARP_SPOT ----
        if self._state == WarpDropState.IDLE:
            self._set_state(WarpDropState.FLY_TO_WARP_SPOT)

        # ---- 主状态分发(chain loop)----
        # 2026-05-24 用户:每个 state handler 切完 state 后同 tick 继续 dispatch
        # 下个 handler,省 tick 累积延迟(原本 9 状态 × 0.36s tick = ~3s)。
        # morph 需要等 SC2 真处理(下帧 prism.type_id 变),无法 chain;但
        # 不依赖 morph 的 transition(到 warp_pos 切 DEPLOY、count 满切
        # WAIT、cargo 满切 FLY_FINAL、到 final 切 UNLOAD、空切 DONE)能省。
        # 最多 9 次 iter(覆盖整个 state machine 一遍),实际多数 tick 只 1 iter。
        for _ in range(9):
            prev_state = self._state
            if self._state == WarpDropState.FLY_TO_WARP_SPOT:
                await self._handle_fly_to_warp_spot(prism)
            elif self._state == WarpDropState.DEPLOY_PHASING:
                await self._handle_deploy_phasing(prism)
            elif self._state == WarpDropState.WARP_UNITS:
                await self._handle_warp_units(prism)
            elif self._state == WarpDropState.WAIT_WARP_COMPLETE:
                await self._handle_wait_warp_complete(prism)
            elif self._state == WarpDropState.MORPH_TRANSPORT:
                await self._handle_morph_transport(prism)
            elif self._state == WarpDropState.LOAD_CARGO:
                await self._handle_load_cargo(prism)
            elif self._state == WarpDropState.FLY_TO_FINAL:
                await self._handle_fly_to_final(prism)
            elif self._state == WarpDropState.UNLOAD_FINAL:
                await self._handle_unload_final(prism)
            else:
                break  # 不在 dispatchable state(如 DONE / RETREAT_HOME)
            if self._state == prev_state:
                break  # 没 transition → 本 tick 工作完

        # ---- DT raid 微操(每 tick;DARKTEMPLAR 专用) ----
        # 接管离 home > 30 的 DT(被 prism 运到或自行到达敌方的);无论 prism
        # 当前状态如何都持续 tick —— 对齐 DTPrismHarass 原行为:
        # _micro_unloaded_dts() 在每 tick 末尾调用,不等 DONE。
        if self.cargo_unit == UnitTypeId.DARKTEMPLAR:
            self._dt_micro.tick(self.ai, self.knowledge)

        return False

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    async def _handle_fly_to_warp_spot(self, prism: Any) -> None:
        """空船飞到 warp_pos,用 drop_path 规划 waypoints。"""
        # WarpPrism 需 transport 模式才能移动
        if prism.type_id == UnitTypeId.WARPPRISMPHASING:
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            return

        # 规划路径(第一次进入)
        if self._waypoints is None:
            self._plan_warp_path(prism)

        if not self._waypoints:
            return

        # 2026-05-24 stuck 兜底:位置 _FLY_STUCK_TIMEOUT_S 秒内位移 < epsilon
        # → 强制 skip 当前 waypoint(防 plan_drop_path 算出不可达点导致永远卡)。
        now = float(self.ai.time)
        cur_pos = (float(prism.position.x), float(prism.position.y))
        if self._fly_last_pos is None:
            self._fly_last_pos = cur_pos
            self._fly_last_progress_ts = now
        else:
            dx_ = cur_pos[0] - self._fly_last_pos[0]
            dy_ = cur_pos[1] - self._fly_last_pos[1]
            if (dx_ * dx_ + dy_ * dy_) ** 0.5 >= _FLY_STUCK_EPSILON:
                # 有显著位移 → 重置 stuck 计时
                self._fly_last_pos = cur_pos
                self._fly_last_progress_ts = now
            elif now - self._fly_last_progress_ts > _FLY_STUCK_TIMEOUT_S:
                # 卡死 → skip 这个 waypoint
                if self._wp_idx < len(self._waypoints) - 1:
                    logger.warning(
                        "PrismWarpDropAct: stuck at (%.1f,%.1f) for %.1fs, skipping wp_idx=%d",
                        cur_pos[0],
                        cur_pos[1],
                        now - self._fly_last_progress_ts,
                        self._wp_idx,
                    )
                    self._wp_idx += 1
                    self._fly_last_pos = cur_pos
                    self._fly_last_progress_ts = now
                else:
                    # 已到最后 waypoint 还卡 → 强制进 DEPLOY_PHASING
                    logger.warning(
                        "PrismWarpDropAct: stuck at last waypoint (%.1f,%.1f) "
                        "for %.1fs, forcing DEPLOY_PHASING",
                        cur_pos[0],
                        cur_pos[1],
                        now - self._fly_last_progress_ts,
                    )
                    self._set_state(WarpDropState.DEPLOY_PHASING)
                    return

        # 走 waypoints(跳过起点 index 0)
        target_idx = min(self._wp_idx + 1, len(self._waypoints) - 1)
        wp = self._waypoints[target_idx]

        if prism.distance_to(wp) > _ARRIVED_DISTANCE:
            prism.move(wp)
            return

        # 到达当前 waypoint → 前进
        if self._wp_idx < len(self._waypoints) - 2:
            self._wp_idx += 1
            return

        # 所有 waypoints 到达 → 飞 warp_pos
        warp_pos = self.warp_pos.position
        if prism.distance_to(warp_pos) > _ARRIVED_DISTANCE:
            prism.move(warp_pos)
        else:
            logger.info("PrismWarpDropAct: arrived at warp_spot %s", self.warp_pos.source_spec)
            self._set_state(WarpDropState.DEPLOY_PHASING)

    async def _handle_deploy_phasing(self, prism: Any) -> None:
        """棱镜 morph 成 WARPPRISMPHASING。"""
        if prism.type_id == UnitTypeId.WARPPRISM:
            prism(AbilityId.MORPH_WARPPRISMPHASINGMODE)
            return
        # 已是 phasing → 切 WARP_UNITS
        logger.info("PrismWarpDropAct: phasing deployed, starting warp")
        self._set_state(WarpDropState.WARP_UNITS)

    async def _handle_warp_units(self, prism: Any) -> None:
        """在 phasing 范围内 warp cargo_count 个 cargo_unit。"""
        if prism.type_id != UnitTypeId.WARPPRISMPHASING:
            # 不是 phasing 状态 → 等
            return

        # 找所有 ready warpgate
        try:
            warpgates = list(self.ai.structures(UnitTypeId.WARPGATE).ready)
        except Exception:
            warpgates = []

        warped = 0
        cm = self.knowledge.cooldown_manager
        for wg in warpgates:
            # 已到 cargo_count → 不再 warp
            try:
                ready_count = self._count_nearby_cargo(prism)
                warping_count = self._count_warping_cargo(prism)
                if ready_count + warping_count >= self.cargo_count:
                    break
            except Exception:
                pass

            if not self.ai.can_afford(self.cargo_unit):
                break

            try:
                if self.ai.supply_left < 2:
                    break
            except Exception:
                pass

            # CD 判定:走 sharpy cd_manager cooldown 模式(查 used_dict + self._warp_cd)。
            # 不用默认模式 — SC2 get_available_abilities 对 WG warp ability 不过滤 cd。
            if not cm.is_ready(wg.tag, AbilityId.WARPGATETRAIN_ZEALOT, cooldown=self._warp_cd):
                continue

            # find_placement near phasing prism
            warp_ability = self._cargo_warp_ability()
            try:
                placement = await self.ai.find_placement(
                    warp_ability,
                    prism.position,
                    placement_step=1,
                    max_distance=6,
                )
            except Exception as exc:
                logger.warning("PrismWarpDropAct find_placement fail: %s", exc)
                continue
            if placement is None:
                continue

            wg.warp_in(self.cargo_unit, placement)
            cm.used_ability(wg.tag, AbilityId.WARPGATETRAIN_ZEALOT)
            warped += 1

        if warped > 0:
            logger.info(
                "PrismWarpDropAct: warped %d %s at phasing prism (%.0f, %.0f)",
                warped,
                self.cargo_unit.name,
                prism.position.x,
                prism.position.y,
            )

        # 2026-05-24 修 Bug 1:状态转换加门控。
        # 原本无条件切 WAIT_WARP_COMPLETE — 没钱/没人口/cd 中导致 0 warped 时
        # 也照样切,然后等 60s 超时空载继续 → 棱镜白来一趟。
        # 改:只有 ready+warping >= cargo_count 才切 WAIT;否则留在 WARP_UNITS
        # 下 tick 重试(_DAMAGE_MEMORY_S + _can_finish_warp 仍兜底处理被打)。
        try:
            total = self._count_nearby_cargo(prism) + self._count_warping_cargo(prism)
        except Exception:
            total = 0
        if total >= self.cargo_count:
            self._set_state(WarpDropState.WAIT_WARP_COMPLETE)
            if self._warp_timeout_start is None:
                self._warp_timeout_start = self.ai.time

    async def _handle_wait_warp_complete(self, prism: Any) -> None:
        """等 phasing prism 附近 cargo_unit build_progress >= 1.0 的数量 >= cargo_count。"""
        # 启动超时计时器
        if self._warp_timeout_start is None:
            self._warp_timeout_start = self.ai.time

        # 超时兜底(防 warp 卡死)
        elapsed = self.ai.time - self._warp_timeout_start
        if elapsed >= _WARP_TIMEOUT_S:
            logger.warning(
                "PrismWarpDropAct: WAIT_WARP_COMPLETE timeout after %.1fs, proceeding",
                elapsed,
            )
            self._set_state(WarpDropState.MORPH_TRANSPORT)
            return

        # 检查 ready count
        ready_count = self._count_nearby_cargo(prism)
        if ready_count >= self.cargo_count:
            logger.info(
                "PrismWarpDropAct: %d %s warp complete, morphing transport",
                ready_count,
                self.cargo_unit.name,
            )
            self._set_state(WarpDropState.MORPH_TRANSPORT)

    async def _handle_morph_transport(self, prism: Any) -> None:
        """morph 回 WARPPRISM transport 模式。"""
        if prism.type_id == UnitTypeId.WARPPRISMPHASING:
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            return
        # 已是 transport → 切 LOAD_CARGO
        logger.info("PrismWarpDropAct: morphed to transport, loading cargo")
        self._set_state(WarpDropState.LOAD_CARGO)

    async def _handle_load_cargo(self, prism: Any) -> None:
        """smart-cast 刚 warp 的 cargo_unit 上船。"""
        if prism.type_id == UnitTypeId.WARPPRISMPHASING:
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            return

        # smart-cast 棱镜附近的 cargo_unit(2026-05-24 修 Bug 3:加距离过滤)。
        # 原本对全图 cargo_unit 调 smart(prism),把家里的 DT 和敌方基地骚扰中
        # 的 DT 一并拉过来 → 浪费 DT、骚扰停摆。
        # 限制在 _PHASING_RANGE * 2 (= 20) 内,只装真正在棱镜附近的 DT(刚 warp
        # 的或附近残留的)。直接用 .position 算距离(unit.distance_to(prism)
        # 在 MagicMock 测试下返回 MagicMock,无法过滤)。
        _LOAD_RANGE_SQ = (_PHASING_RANGE * 2.0) ** 2
        try:
            pp = prism.position
            px, py = float(pp.x), float(pp.y)
        except Exception:
            px = py = 0.0
            _LOAD_RANGE_SQ = float("inf")  # 取不到 prism 位置 → 不过滤(回退原行为)
        try:
            for unit in self.ai.units(self.cargo_unit):
                try:
                    up = unit.position
                    ux, uy = float(up.x), float(up.y)
                    if (ux - px) ** 2 + (uy - py) ** 2 >= _LOAD_RANGE_SQ:
                        continue
                except Exception:
                    continue
                self._reserve(unit)
                unit.smart(prism)
        except Exception:
            pass

        cargo = int(getattr(prism, "cargo_used", 0))
        if cargo > 0 and self._load_since is None:
            self._load_since = self.ai.time

        if cargo <= 0:
            return

        # 起飞:场上无散落 cargo_unit 或硬超时
        try:
            scattered = self.ai.units(self.cargo_unit).amount
        except Exception:
            scattered = 0
        all_aboard = scattered == 0

        timed_out = (
            self._load_since is not None
            and self.ai.time - self._load_since > _LOADING_HARD_TIMEOUT_S
        )
        min_cargo = max(1, int(self.cargo_count * _LOADING_MIN_FRAC))

        if (cargo >= min_cargo and all_aboard) or timed_out:
            logger.info(
                "PrismWarpDropAct LOAD_CARGO done: cargo=%d, flying to final drop",
                cargo,
            )
            self._set_state(WarpDropState.FLY_TO_FINAL)
            self._plan_final_path(prism)

    async def _handle_fly_to_final(self, prism: Any) -> None:
        """飞到 final_drop_pos,用 drop_path 规划路径。"""
        if prism.type_id == UnitTypeId.WARPPRISMPHASING:
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            return

        if self._final_waypoints is None:
            self._plan_final_path(prism)

        if not self._final_waypoints:
            return

        # 走 final waypoints(跳过起点 index 0)
        target_idx = min(self._final_wp_idx + 1, len(self._final_waypoints) - 1)
        wp = self._final_waypoints[target_idx]

        if prism.distance_to(wp) > _ARRIVED_DISTANCE:
            prism.move(wp)
            return

        # 到达 waypoint
        if self._final_wp_idx < len(self._final_waypoints) - 2:
            self._final_wp_idx += 1
            return

        # 走完所有 waypoints → 飞 final_drop_pos
        final_pos = self.final_drop_pos.position
        if prism.distance_to(final_pos) > _ARRIVED_DISTANCE:
            prism.move(final_pos)
        else:
            logger.info(
                "PrismWarpDropAct: arrived at final drop %s",
                self.final_drop_pos.source_spec,
            )
            self._set_state(WarpDropState.UNLOAD_FINAL)

    async def _handle_unload_final(self, prism: Any) -> None:
        """到达 final_drop_pos → 卸下所有 cargo → 切 DONE。"""
        if prism.type_id == UnitTypeId.WARPPRISMPHASING:
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            return

        cargo = int(getattr(prism, "cargo_used", 0))
        if cargo > 0:
            prism(AbilityId.UNLOADALLAT_WARPPRISM, prism.position)
            return

        # cargo 空 → 完成
        logger.info(
            "PrismWarpDropAct: unload complete at %s, after_unload=%s",
            self.final_drop_pos.source_spec,
            self.after_unload,
        )
        self._set_state(WarpDropState.DONE)

    # ------------------------------------------------------------------
    # Attack handling
    # ------------------------------------------------------------------

    def _handle_retreat_cooldown(self, prism: Any) -> None:
        """RETREAT_HOME 冷却到期 + 到家附近 → 重置 IDLE。"""
        cooldown_over = (self.ai.time - self._state_entered_ts) > _RETREAT_COOLDOWN_S
        try:
            at_home = prism.distance_to(self.ai.start_location) < _RETREAT_HOME_PROXIMITY
        except Exception:
            at_home = False
        if cooldown_over and at_home:
            logger.info(
                "PrismWarpDropAct: retreat cooldown done, reset to IDLE (t=%.1f)",
                float(self.ai.time),
            )
            self._set_state(WarpDropState.IDLE)
            self._waypoints = None
            self._wp_idx = 0
            self._final_waypoints = None
            self._final_wp_idx = 0
            self._warp_timeout_start = None
            self._load_since = None

    def _update_under_attack(self, prism: Any) -> bool:
        """HP 下降 → 受攻击;_DAMAGE_MEMORY_S 内持续算受攻击。"""
        try:
            hp = float(prism.health) + float(prism.shield)
        except Exception:
            return False
        if self._prism_hp_prev is not None and hp < self._prism_hp_prev - 0.5:
            self._last_damage_ts = self.ai.time
        self._prism_hp_prev = hp
        return bool((self.ai.time - self._last_damage_ts) < _DAMAGE_MEMORY_S)

    def _can_finish_warp(self, prism: Any) -> bool:
        """展开中挨打:是否值得撑着让正在折跃的单位完成。"""
        warping = self._warping_cargo_near(prism)
        if not warping:
            return False
        try:
            bp = max(float(u.build_progress) for u in warping)
        except Exception:
            return False
        if bp > _WARP_FINISH_BP:
            return True
        return self._prism_hp_ratio(prism) > _WARP_FINISH_HP_RATIO

    def _prism_hp_ratio(self, prism: Any) -> float:
        try:
            mx = float(prism.health_max) + float(prism.shield_max)
            if mx <= 0:
                return 1.0
            return (float(prism.health) + float(prism.shield)) / mx
        except Exception:
            return 1.0

    def _do_retreat(self, prism: Any) -> None:
        """收起 phasing + 飞回家。"""
        if prism.type_id == UnitTypeId.WARPPRISMPHASING:
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
        try:
            home = self.ai.start_location
            prism.move(home)
        except (IndexError, AttributeError):
            pass
        self._set_state(WarpDropState.RETREAT_HOME)

    # ------------------------------------------------------------------
    # Path planning
    # ------------------------------------------------------------------

    def _plan_warp_path(self, prism: Any) -> None:
        """home → warp_pos 路径。"""
        try:
            start = self.ai.start_location
            end = self.warp_pos.position
            self._waypoints = plan_drop_path(start, end, self.ai)
            self._wp_idx = 0
            logger.debug("PrismWarpDropAct: warp path planned (%d waypoints)", len(self._waypoints))
        except Exception as e:
            logger.warning("PrismWarpDropAct _plan_warp_path failed: %s", e)
            try:
                self._waypoints = [self.ai.start_location, self.warp_pos.position]
            except Exception:
                self._waypoints = []
            self._wp_idx = 0

    def _plan_final_path(self, prism: Any) -> None:
        """warp_pos → final_drop_pos 路径。"""
        try:
            start = prism.position
            end = self.final_drop_pos.position
            self._final_waypoints = plan_drop_path(start, end, self.ai)
            self._final_wp_idx = 0
            logger.debug(
                "PrismWarpDropAct: final path planned (%d waypoints)", len(self._final_waypoints)
            )
        except Exception as e:
            logger.warning("PrismWarpDropAct _plan_final_path failed: %s", e)
            try:
                self._final_waypoints = [prism.position, self.final_drop_pos.position]
            except Exception:
                self._final_waypoints = []
            self._final_wp_idx = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_prism(self) -> Any | None:
        """找 WarpPrism(任意形态)。优先 tag 缓存。"""
        try:
            prisms = self.ai.units.of_type(_WARPPRISM_TYPES)
        except Exception:
            return None
        if not prisms:
            return None
        if self._prism_tag is not None:
            try:
                same = prisms.tags_in([self._prism_tag])
                if same:
                    return same[0]
            except Exception:
                pass
        try:
            return max(
                prisms,
                key=lambda u: float(getattr(u, "health", 0)) + float(getattr(u, "shield", 0)),
            )
        except Exception:
            try:
                return next(iter(prisms))
            except StopIteration:
                return None

    def _reserve(self, unit: Any) -> None:
        """标 Reserved 防 sharpy 接管。"""
        try:
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, unit)
        except Exception:
            pass

    def _count_nearby_cargo(self, prism: Any) -> int:
        """在 phasing prism 附近(< _PHASING_RANGE)且 build_progress >= 1.0 的 cargo_unit 数量。"""
        try:
            return sum(
                1
                for u in self.ai.units(self.cargo_unit)
                if float(u.build_progress) >= 1.0 and float(u.distance_to(prism)) < _PHASING_RANGE
            )
        except Exception:
            return 0

    def _count_warping_cargo(self, prism: Any) -> int:
        """在 phasing prism 附近且正在 warp 中的 cargo_unit 数量。"""
        try:
            return sum(
                1
                for u in self.ai.units(self.cargo_unit)
                if float(u.build_progress) < 1.0 and float(u.distance_to(prism)) < _PHASING_RANGE
            )
        except Exception:
            return 0

    def _warping_cargo_near(self, prism: Any) -> list[Any]:
        """正在折跃(build_progress < 1)且在 phasing 范围内的 cargo_unit。"""
        try:
            return [
                u
                for u in self.ai.units(self.cargo_unit)
                if float(u.build_progress) < 1.0 and float(u.distance_to(prism)) < _PHASING_RANGE
            ]
        except Exception:
            return []

    def _cargo_warp_ability(self) -> AbilityId:
        """根据 cargo_unit 返回对应 warp ability。"""
        _WARP_ABILITIES = {
            UnitTypeId.DARKTEMPLAR: AbilityId.WARPGATETRAIN_DARKTEMPLAR,
            UnitTypeId.ZEALOT: AbilityId.WARPGATETRAIN_ZEALOT,
            UnitTypeId.STALKER: AbilityId.WARPGATETRAIN_STALKER,
            UnitTypeId.HIGHTEMPLAR: AbilityId.WARPGATETRAIN_HIGHTEMPLAR,
            UnitTypeId.SENTRY: AbilityId.WARPGATETRAIN_SENTRY,
            # WARPGATETRAIN_ADEPT 在当前 python-sc2 版本不存在，用 Zealot 兜底
        }
        return _WARP_ABILITIES.get(self.cargo_unit, AbilityId.WARPGATETRAIN_ZEALOT)

    def _set_state(self, new_state: WarpDropState) -> None:
        if new_state != self._state:
            logger.debug(
                "PrismWarpDropAct state: %s → %s (t=%.1fs)",
                self._state.value,
                new_state.value,
                getattr(getattr(self, "ai", None), "time", 0.0),
            )
            # 进入 FLY_TO_WARP_SPOT = 新一波,清路径缓存
            if new_state == WarpDropState.FLY_TO_WARP_SPOT:
                self._waypoints = None
                self._wp_idx = 0
            # 进入 FLY_TO_FINAL = 清 final 路径缓存
            if new_state == WarpDropState.FLY_TO_FINAL:
                self._final_waypoints = None
                self._final_wp_idx = 0
            self._state = new_state
            self._state_entered_ts = getattr(getattr(self, "ai", None), "time", 0.0)
