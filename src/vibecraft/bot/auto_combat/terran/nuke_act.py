"""核弹微操 act — AutoNukeAct。

状态机 IDLE → MOVING → ARMING → COOLDOWN：
- IDLE     : 维持核弹库存；选目标 → MOVING
- MOVING   : Reserved 幽灵移向目标；到射程 → ARMING
- ARMING   : calldown；cast 确认 → 撤退 → COOLDOWN
- COOLDOWN : 停 reserve；冷却后 → IDLE

设计严格按评审后定稿的 9 条执行。
"""

from __future__ import annotations

import contextlib
import logging
import os
from enum import Enum, auto

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit import Unit
from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)

_NUKE_TRACE: bool = bool(os.environ.get("VIBECRAFT_NUKE_TRACE"))

_CAST_RANGE: float = 10.0
_DETECTOR_ABORT_RANGE: float = 11.0
_CLOAK_DETECT_RANGE: float = 11.0
_MOVING_TIMEOUT_S: float = 30.0
_ARMING_TIMEOUT_S: float = 5.0


class _State(Enum):
    IDLE = auto()
    MOVING = auto()
    ARMING = auto()
    COOLDOWN = auto()


class AutoNukeAct(ActBase):  # type: ignore[misc]
    """幽灵核弹自动微操 act — 维持核弹库存 + 目标选择 + 潜入 + calldown + 发射即撤。

    永远 return True（non-blocking），放在 PlanZoneGather 之后、PlanZoneAttack 之前。
    """

    def __init__(
        self,
        nuke_min_cluster: int = 6,
        nuke_safe_radius: float = 9.0,
        run_cooldown_s: float = 10.0,
    ) -> None:
        super().__init__()
        self._nuke_min_cluster = nuke_min_cluster
        self._nuke_safe_radius = nuke_safe_radius
        self._run_cooldown_s = run_cooldown_s

        self._state: _State = _State.IDLE
        self._nuker_tag: int | None = None
        self._target: Point2 | None = None
        self._state_start_time: float = 0.0
        self._cooldown_start: float = 0.0

        self._avail_cache: bool = False
        self._avail_frame: int = -100
        self._target_is_building: bool = False

    # ------------------------------------------------------------------
    # ActBase entry point
    # ------------------------------------------------------------------

    async def execute(self) -> bool:
        try:
            await self._tick()
        except Exception:
            logger.exception("AutoNukeAct._tick error")
        return True

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        if self._state == _State.IDLE:
            await self._tick_idle()
        elif self._state == _State.MOVING:
            await self._tick_moving()
        elif self._state == _State.ARMING:
            await self._tick_arming()
        else:
            self._tick_cooldown()

    async def _tick_idle(self) -> None:
        has_nuke = await self._refresh_avail()
        if not has_nuke:
            self._maybe_build_nuke()
            return

        target = self._pick_target()
        if target is None:
            return

        nuker = self._pick_nuker(target)
        if nuker is None:
            return

        self._target = target
        self._nuker_tag = nuker.tag
        self._reserve(nuker)
        self._state = _State.MOVING
        self._state_start_time = float(self.ai.time)
        if _NUKE_TRACE:
            logger.warning(
                "NUKETRACE moving_start nuker_tag=%d target=(%.1f,%.1f)",
                nuker.tag,
                target.x,
                target.y,
            )

    async def _tick_moving(self) -> None:
        nuker = self._get_nuker()
        if nuker is None or self._target is None:
            self._abort("nuker_lost")
            return

        if self._check_abort(nuker):
            return

        if float(self.ai.time) - self._state_start_time > _MOVING_TIMEOUT_S:
            self._abort("moving_timeout")
            return

        self._reserve(nuker)

        with contextlib.suppress(Exception):
            nuker.move(self._target)

        self._maybe_cloak(nuker)

        with contextlib.suppress(Exception):
            if nuker.distance_to(self._target) <= _CAST_RANGE:
                self._state = _State.ARMING
                self._state_start_time = float(self.ai.time)

    async def _tick_arming(self) -> None:
        nuker = self._get_nuker()
        if nuker is None or self._target is None:
            self._abort("nuker_lost")
            return

        if self._check_abort(nuker):
            return

        if float(self.ai.time) - self._state_start_time > _ARMING_TIMEOUT_S:
            self._abort("arming_timeout")
            return

        self._reserve(nuker)

        with contextlib.suppress(Exception):
            nuker(AbilityId.TACNUKESTRIKE_NUKECALLDOWN, self._target)

        if _NUKE_TRACE:
            logger.warning(
                "NUKETRACE calldown_issued pos=(%.1f,%.1f)",
                self._target.x,
                self._target.y,
            )

        cast_confirmed = False
        with contextlib.suppress(Exception):
            for order in nuker.orders:
                if order.ability.id == AbilityId.TACNUKESTRIKE_NUKECALLDOWN:
                    cast_confirmed = True
                    break

        if cast_confirmed:
            with contextlib.suppress(Exception):
                nuker.move(self.ai.start_location)
            self._stop_reserve()
            self._enter_cooldown()

    def _tick_cooldown(self) -> None:
        if float(self.ai.time) - self._cooldown_start >= self._run_cooldown_s:
            self._state = _State.IDLE

    # ------------------------------------------------------------------
    # 库存维持
    # ------------------------------------------------------------------

    async def _refresh_avail(self) -> bool:
        """节流查 get_available_abilities，每 22 帧更新一次缓存。"""
        frame = self.ai.state.game_loop
        if frame - self._avail_frame < 22:
            return self._avail_cache
        self._avail_frame = frame
        ghosts = list(self.cache.own(UnitTypeId.GHOST).ready)
        if not ghosts:
            self._avail_cache = False
            return False
        try:
            results = await self.ai.get_available_abilities(ghosts)
            for abilities in results:
                if AbilityId.TACNUKESTRIKE_NUKECALLDOWN in abilities:
                    self._avail_cache = True
                    return True
        except Exception:
            pass
        self._avail_cache = False
        return False

    def _maybe_build_nuke(self) -> None:
        with contextlib.suppress(Exception):
            academies = self.cache.own(UnitTypeId.GHOSTACADEMY).ready
            if not academies:
                return
            academy = academies.first
            # 注意:本 sc2 版本对 UnitTypeId.NUKE 调 already_pending()/can_afford() 会抛
            # "Uncaught UnitTypeId: NUKE"(实测 selftest 80 次 ERROR)→ 不能用 NUKE typeid 查。
            # 改:查 GhostAcademy.orders 是否正在造核弹(BUILD_NUKE 在产) + 裸资源判定(核弹 100/100)。
            if any(o.ability.id == AbilityId.BUILD_NUKE for o in academy.orders):
                return
            if self.ai.minerals < 100 or self.ai.vespene < 100:
                return
            academy(AbilityId.BUILD_NUKE)
            if _NUKE_TRACE:
                logger.warning("NUKETRACE build_nuke_issued")

    # ------------------------------------------------------------------
    # 目标选择
    # ------------------------------------------------------------------

    def _pick_target(self) -> Point2 | None:
        """优先敌方建筑（不会跑，14s 必中，detector 也照炸），次选静止兵团簇。

        参考点 = 我方幽灵质心:选离幽灵最近的合格目标,减少潜入路程/暴露。
        """
        ghosts = self.cache.own(UnitTypeId.GHOST).ready
        if not ghosts:
            return None
        ref = ghosts.center

        # 优先:敌方地面建筑（用 enemy_structures —— enemy_units **不含**建筑!）。
        # 建筑不会跑 → 即便基地有 detector(虫族孢子/王虫)也照炸,14s 必中。
        with contextlib.suppress(Exception):
            best: Point2 | None = None
            best_d = float("inf")
            for st in self.ai.enemy_structures:
                if st.is_flying:
                    continue
                pos = st.position
                if self._friendly_count_near(pos, self._nuke_safe_radius) > 0:
                    continue
                d = pos.distance_to(ref)
                if d < best_d:
                    best_d = d
                    best = pos
            if best is not None:
                self._target_is_building = True
                if _NUKE_TRACE:
                    logger.warning(
                        "NUKETRACE target_picked type=building pos=(%.1f,%.1f)", best.x, best.y
                    )
                return best

        # 次选:静止兵团簇（≥ nuke_min_cluster supply 在半径 6 内）。可移动 → 有 detector 会被看见
        # 躲开,故 _check_abort 对非建筑目标保留 detector 中止。
        with contextlib.suppress(Exception):
            seen: set[int] = set()
            best_c: Point2 | None = None
            best_sup = 0
            best_cd = float("inf")
            for enemy in self.ai.enemy_units:
                if enemy.tag in seen or enemy.is_structure:
                    continue
                pos = enemy.position
                cluster = self.ai.enemy_units.closer_than(6.0, pos)
                supply = sum(getattr(u, "supply_cost", 2) for u in cluster)
                seen.update(u.tag for u in cluster)
                if supply < self._nuke_min_cluster:
                    continue
                if self._friendly_count_near(pos, self._nuke_safe_radius) > 0:
                    continue
                d = pos.distance_to(ref)
                if d < best_cd:
                    best_cd = d
                    best_c = pos
                    best_sup = supply
            if best_c is not None:
                self._target_is_building = False
                if _NUKE_TRACE:
                    logger.warning(
                        "NUKETRACE target_picked type=clump pos=(%.1f,%.1f) supply=%d",
                        best_c.x,
                        best_c.y,
                        best_sup,
                    )
                return best_c
        return None

    def _friendly_count_near(self, pos: Point2, radius: float) -> int:
        with contextlib.suppress(Exception):
            return len(self.ai.units.closer_than(radius, pos))
        return 0

    # ------------------------------------------------------------------
    # 幽灵选择
    # ------------------------------------------------------------------

    def _pick_nuker(self, target: Point2) -> Unit | None:
        """选离目标最近、不在 Snipe 引导中的幽灵。"""
        ghosts = self.cache.own(UnitTypeId.GHOST).ready
        best: Unit | None = None
        best_dist = float("inf")
        for ghost in ghosts:
            snipe_channeling = False
            with contextlib.suppress(Exception):
                if ghost.orders and ghost.orders[0].ability.id == AbilityId.EFFECT_GHOSTSNIPE:
                    snipe_channeling = True
            if snipe_channeling:
                continue
            with contextlib.suppress(Exception):
                d = ghost.distance_to(target)
                if d < best_dist:
                    best_dist = d
                    best = ghost
        return best

    # ------------------------------------------------------------------
    # 中止检查
    # ------------------------------------------------------------------

    def _check_abort(self, nuker: Unit) -> bool:
        """True = 已触发中止（已调 _abort）。"""
        intent = getattr(getattr(self.knowledge, "vibecraft", None), "combat_intent_override", None)
        if intent == "retreat":
            self._abort("player_retreat")
            return True

        # detector 中止只对**可移动的兵团簇**目标(会被看见躲开);建筑目标不会跑 → 照炸,
        # 否则虫族每个基地都有孢子/王虫,nuke 永远发不出(实测 selftest 全 abort)。
        if self._target is not None and not self._target_is_building:
            with contextlib.suppress(Exception):
                if any(
                    e.is_detector
                    for e in self.cache.enemy_in_range(self._target, _DETECTOR_ABORT_RANGE)
                ):
                    self._abort("detector_near_target")
                    return True

        return False

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _maybe_cloak(self, nuker: Unit) -> None:
        with contextlib.suppress(Exception):
            if nuker.is_cloaked:
                return
            if any(
                e.is_detector
                for e in self.cache.enemy_in_range(nuker.position, _CLOAK_DETECT_RANGE)
            ):
                return
            nuker(AbilityId.BEHAVIOR_CLOAKON_GHOST)

    def _get_nuker(self) -> Unit | None:
        if self._nuker_tag is None:
            return None
        with contextlib.suppress(Exception):
            return self.ai.units.find_by_tag(self._nuker_tag)
        return None

    def _reserve(self, unit: object) -> None:
        with contextlib.suppress(Exception):
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, unit)

    def _stop_reserve(self) -> None:
        if self._nuker_tag is None:
            return
        ghost = self._get_nuker()
        if ghost is not None:
            with contextlib.suppress(Exception):
                self.knowledge.roles.clear_task(ghost)
        self._nuker_tag = None

    def _abort(self, reason: str) -> None:
        if _NUKE_TRACE:
            logger.warning("NUKETRACE aborted reason=%s", reason)
        self._stop_reserve()
        self._enter_cooldown()

    def _enter_cooldown(self) -> None:
        self._state = _State.COOLDOWN
        self._cooldown_start = float(self.ai.time)
        self._target = None
