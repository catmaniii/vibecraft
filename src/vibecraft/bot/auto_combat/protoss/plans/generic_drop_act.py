"""GenericDropAct: 通用单段空投(style=simple)。

状态机:IDLE → LOAD_AT_HOME → FLY_TO_DROP → UNLOAD → HOVER_FINAL

- LOAD_AT_HOME: smart-cast cargo_unit 上船(cargo_count 个),到位或超时切下一态
- FLY_TO_DROP: 用 drop_path.plan_drop_path 拿 waypoint list,顺序飞
- UNLOAD: 卸下 cargo → 切 HOVER_FINAL
- HOVER_FINAL: 飞回主力球待命(或按 after_unload 行为)

参数化 cargo_unit/transport/target/after_unload:
1. 不写死 DT/WarpPrism
2. 不做 warp 第二波(那是 PrismWarpDropAct)
3. FLY_TO_DROP 用 plan_drop_path 规划路径
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

from vibecraft.bot.drop_path import plan_drop_path
from vibecraft.bot.named_spot import DropTarget

logger = logging.getLogger(__name__)

# ---- 阈值常量 ----
_ARRIVED_DISTANCE: float = 3.0
# 至少装这么多才起飞(通用 simple drop 用 cargo_count / 2 动态算,但保留最小值)
_LOADING_MIN_CARGO_FRAC: float = 0.5  # cargo_count 的 50% 以上就可以起飞
# 硬超时:装了 cargo 后等这么久没全上船也起飞(防单个单位卡住)
_LOADING_HARD_TIMEOUT_S: float = 60.0
# HOVER_FINAL:飞回主力球附近距离阈值
_HOVER_RALLY_DIST: float = 8.0

# WarpPrism 两种形态
_WARPPRISM_TYPES: frozenset[UnitTypeId] = frozenset(
    {UnitTypeId.WARPPRISM, UnitTypeId.WARPPRISMPHASING}
)

# 主力球单位类型(不含 DT 避免位置偏移)
_MAIN_BALL_TYPES: frozenset[UnitTypeId] = frozenset(
    {
        UnitTypeId.ZEALOT,
        UnitTypeId.STALKER,
        UnitTypeId.SENTRY,
        UnitTypeId.ADEPT,
        UnitTypeId.IMMORTAL,
        UnitTypeId.ARCHON,
        UnitTypeId.HIGHTEMPLAR,
        UnitTypeId.COLOSSUS,
    }
)


class GenericDropState(str, Enum):
    """通用空投状态机。"""

    IDLE = "idle"
    LOAD_AT_HOME = "load_at_home"
    FLY_TO_DROP = "fly_to_drop"
    UNLOAD = "unload"
    HOVER_FINAL = "hover_final"


class GenericDropAct(ActBase):  # type: ignore[misc]
    """通用单段空投(style=simple)。

    支持任意 cargo_unit(叉子/Marine 等) + 任意 transport(WarpPrism/Medivac)。
    神族棱镜使用 WarpPrism / WarpPrismPhasing 两种 type_id,调用对应 AbilityId;
    其他 transport 只调 move + UNLOADALL。
    """

    def __init__(
        self,
        cargo_unit: UnitTypeId,
        cargo_count: int,
        transport: UnitTypeId,
        drop_target: DropTarget,
        after_unload: str = "attack_workers",
    ) -> None:
        super().__init__()
        self.cargo_unit = cargo_unit
        self.cargo_count = cargo_count
        self.transport = transport
        self.drop_target = drop_target
        self.after_unload = after_unload

        # 内部状态
        self._state: GenericDropState = GenericDropState.IDLE
        self._state_entered_ts: float = 0.0
        self._transport_tag: int | None = None
        self._loading_since: float | None = None
        self._waypoints: list[Point2] | None = None
        self._wp_idx: int = 0

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def execute(self) -> bool:
        transport = self._find_transport()
        if transport is None:
            return False
        self._transport_tag = transport.tag

        # 标 Reserved 防 sharpy 把 transport 抢走
        self._reserve_transport(transport)

        # IDLE → 自动切 LOAD_AT_HOME
        if self._state == GenericDropState.IDLE:
            self._set_state(GenericDropState.LOAD_AT_HOME)

        if self._state == GenericDropState.LOAD_AT_HOME:
            await self._handle_load_at_home(transport)
        elif self._state == GenericDropState.FLY_TO_DROP:
            await self._handle_fly_to_drop(transport)
        elif self._state == GenericDropState.UNLOAD:
            await self._handle_unload(transport)
        elif self._state == GenericDropState.HOVER_FINAL:
            await self._handle_hover_final(transport)

        return False

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    async def _handle_load_at_home(self, transport: Any) -> None:
        """家里把 cargo_unit smart-cast 上船,装齐(或超时)后飞往目标。"""
        # WarpPrism:确保 transport 模式才能装载
        if self._is_warpprism_phasing(transport):
            transport(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            return

        # smart-cast 所有 cargo_unit 上船(并标 Reserved 防微操覆盖上船指令)
        try:
            for unit in self.ai.units(self.cargo_unit):
                self._reserve_unit(unit)
                unit.smart(transport)
        except Exception:
            pass

        cargo = int(getattr(transport, "cargo_used", 0))
        if cargo > 0 and self._loading_since is None:
            self._loading_since = self.ai.time

        if cargo <= 0:
            return

        # 起飞判定:场上 cargo_unit 数量为 0(全上船了)
        try:
            scattered = self.ai.units(self.cargo_unit).amount
        except Exception:
            scattered = 0
        all_aboard = scattered == 0

        # 硬超时:装了人但迟迟没装满
        timed_out = (
            self._loading_since is not None
            and self.ai.time - self._loading_since > _LOADING_HARD_TIMEOUT_S
        )

        # 动态最低起飞数:cargo_count 的 50% 以上
        min_cargo = max(1, int(self.cargo_count * _LOADING_MIN_CARGO_FRAC))

        if (cargo >= min_cargo and all_aboard) or timed_out:
            logger.info(
                "GenericDropAct LOAD_AT_HOME done: cargo=%d cargo_unit=%s",
                cargo,
                self.cargo_unit.name,
            )
            self._set_state(GenericDropState.FLY_TO_DROP)
            self._plan_path(transport)

    async def _handle_fly_to_drop(self, transport: Any) -> None:
        """沿 waypoints 飞向 drop_target.position。"""
        # WarpPrism:切回 transport 模式
        if self._is_warpprism_phasing(transport):
            transport(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            return

        # 第一次进入 / waypoints 未规划 → 立刻规划
        if self._waypoints is None:
            self._plan_path(transport)

        # 走 waypoints
        if self._waypoints and self._wp_idx < len(self._waypoints) - 1:
            # waypoints 包含起点和终点;跳过第一个(起点 = home),飞后续 waypoints
            wp = self._waypoints[self._wp_idx + 1]  # skip A (index 0)
            if transport.distance_to(wp) > _ARRIVED_DISTANCE:
                transport.move(wp)
                return
            self._wp_idx += 1
            return

        # waypoints 走完 → 飞 drop_target.position
        drop_pos = self.drop_target.position
        if transport.distance_to(drop_pos) > _ARRIVED_DISTANCE:
            transport.move(drop_pos)
        else:
            self._set_state(GenericDropState.UNLOAD)

    async def _handle_unload(self, transport: Any) -> None:
        """到达 drop_target → 卸下所有 cargo。"""
        # WarpPrism:切 transport 模式卸货
        if self._is_warpprism_phasing(transport):
            transport(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            return

        cargo = int(getattr(transport, "cargo_used", 0))
        if cargo > 0:
            # WarpPrism 用 UNLOADALLAT_WARPPRISM,其他 transport 用通用 UNLOADALL
            if self._is_warpprism(transport):
                transport(AbilityId.UNLOADALLAT_WARPPRISM, transport.position)
            else:
                transport(AbilityId.UNLOADALL, transport.position)
            return

        # cargo 空 → 完成卸货
        logger.info(
            "GenericDropAct UNLOAD done at %s after_unload=%s",
            self.drop_target.source_spec,
            self.after_unload,
        )
        self._set_state(GenericDropState.HOVER_FINAL)

    async def _handle_hover_final(self, transport: Any) -> None:
        """卸货完成,飞回主力球待命(HOVER_FINAL 是准终态)。"""
        # WarpPrism:切 transport 模式
        if self._is_warpprism_phasing(transport):
            transport(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            return

        rally = self._main_army_center()
        if rally is not None and transport.distance_to(rally) > _HOVER_RALLY_DIST:
            transport.move(rally)

    # ------------------------------------------------------------------
    # Path planning
    # ------------------------------------------------------------------

    def _plan_path(self, transport: Any) -> None:
        """调 plan_drop_path 规划 home → drop_target 路径,缓存到 _waypoints。"""
        try:
            start = self.ai.start_location
            end = self.drop_target.position
            self._waypoints = plan_drop_path(start, end, self.ai)
            self._wp_idx = 0
            logger.debug("GenericDropAct path planned: %d waypoints", len(self._waypoints))
        except Exception as e:
            logger.warning("GenericDropAct _plan_path failed: %s", e)
            # 兜底:直线飞
            try:
                self._waypoints = [self.ai.start_location, self.drop_target.position]
            except Exception:
                self._waypoints = []
            self._wp_idx = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_transport(self) -> Any | None:
        """找 transport 单位。优先用缓存 tag,找不到取任意一个。"""
        # 对 WarpPrism 同时找两种形态
        transport_types = self._transport_type_set()
        try:
            transports = self.ai.units.of_type(transport_types)
        except Exception:
            return None
        if not transports:
            return None
        if self._transport_tag is not None:
            try:
                same = transports.tags_in([self._transport_tag])
                if same:
                    return same[0]
            except Exception:
                pass
        # 取血量最多的
        try:
            return max(
                transports,
                key=lambda u: float(getattr(u, "health", 0)) + float(getattr(u, "shield", 0)),
            )
        except Exception:
            try:
                return next(iter(transports))
            except StopIteration:
                return None

    def _transport_type_set(self) -> frozenset[UnitTypeId]:
        """WarpPrism 时同时包含 WARPPRISMPHASING。"""
        if self.transport == UnitTypeId.WARPPRISM:
            return _WARPPRISM_TYPES
        return frozenset({self.transport})

    def _is_warpprism(self, transport: Any) -> bool:
        return getattr(transport, "type_id", None) in _WARPPRISM_TYPES

    def _is_warpprism_phasing(self, transport: Any) -> bool:
        return getattr(transport, "type_id", None) == UnitTypeId.WARPPRISMPHASING

    def _reserve_transport(self, transport: Any) -> None:
        """防 sharpy plan 把 transport 抢走。"""
        try:
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, transport)
        except Exception:
            pass

    def _reserve_unit(self, unit: Any) -> None:
        """防 sharpy 微操覆盖上船指令。"""
        try:
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, unit)
        except Exception:
            pass

    def _main_army_center(self) -> Any | None:
        """主力球重心;没主力球时退回主基地。"""
        try:
            army = self.ai.units.of_type(set(_MAIN_BALL_TYPES))
        except Exception:
            return None
        if not army:
            try:
                return self.ai.start_location
            except (IndexError, AttributeError):
                return None
        try:
            return army.center
        except Exception:
            return None

    def _set_state(self, new_state: GenericDropState) -> None:
        if new_state != self._state:
            logger.debug(
                "GenericDropAct state: %s → %s (t=%.1fs)",
                self._state.value,
                new_state.value,
                getattr(getattr(self, "ai", None), "time", 0.0),
            )
            # 重置 waypoints(新装载周期重新规划路径)
            if new_state == GenericDropState.LOAD_AT_HOME:
                self._waypoints = None
                self._wp_idx = 0
            self._state = new_state
            self._state_entered_ts = getattr(getattr(self, "ai", None), "time", 0.0)
