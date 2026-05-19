"""PrismHarassAct: 精准 Warp Prism 棱镜骚扰行为。

dt_drop_iac 专用。控制 1 个专门做骚扰的 Warp Prism：
  - 飞到对方矿区附近的安全位置
  - 切换 phasing 模式给 DT 提供 warp-in power
  - 检测威胁（反空 / 反隐 detector）时切回 transport mode 撤回
  - 残 DT 都死后或棱镜血少时回家
  - 撤回家后切回 transport，继续等下次任务

设计意图
========
sharpy 自带 MicroWarpPrism (combat/protoss/micro_warp_prism.py) 是 reactive 的：
- 只会在 warpgate 准备好时 phase，
- 只在 phasing 状态被威胁时找 safe pos
- 不会主动远征到对方家做骚扰

本 act 补这个 gap，专门处理"主动飞前线骚扰"的 micro 行为。

状态机
======
        ┌──────────────────────────────────────────────────┐
        │ no Prism / no DT spec done → IDLE (etat 0)        │
        └──────────────────────────────────────────────────┘
                            │  Prism ready
                            ▼
        ┌──────────────────────────────────────────────────┐
        │ FLY_TO_ENEMY: 飞向 enemy mineral line（safe pos） │
        └──────────────────────────────────────────────────┘
                            │  arrived (距 target < 6)
                            ▼
        ┌──────────────────────────────────────────────────┐
        │ PHASING: 切 phasing 模式, DT 可 warp_in 这里      │
        └──────────────────────────────────────────────────┘
                            │  threat detected / HP 低
                            ▼
        ┌──────────────────────────────────────────────────┐
        │ RETREAT: 切回 transport, 飞往家附近 safe pos      │
        └──────────────────────────────────────────────────┘
                            │  near home OR no DT alive
                            ▼
        ┌──────────────────────────────────────────────────┐
        │ RETURN_HOME → 等下次任务                          │
        └──────────────────────────────────────────────────┘

注意
====
本 act 只控制 Warp Prism 位置 + transport/phasing 切换。
DT 实际 warp_in 由 sharpy ProtossUnit(DARKTEMPLAR, 8) 处理 —— DT 会选最近 power
source，如果本 prism 正 phasing 且离最近，DT 自动在这 warp。
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)


# 威胁检测半径（敌方反空单位在此半径内 → 撤）
_THREAT_RADIUS: float = 9.0

# Prism 撤退 HP 阈值（百分比；shield + hull 都低于此 → 撤）
_RETREAT_HP_PCT: float = 0.5

# 距离阈值
_ARRIVED_DISTANCE: float = 6.0  # 飞到 target 多近算"到达"
_HOME_DISTANCE: float = 15.0    # 距家多近算"已到家"

# 反空单位列表（一旦 prism 半径内出现 → 撤）
_ANTI_AIR_UNITS: frozenset[UnitTypeId] = frozenset(
    {
        # Zerg
        UnitTypeId.QUEEN, UnitTypeId.MUTALISK, UnitTypeId.HYDRALISK,
        UnitTypeId.SPORECRAWLER, UnitTypeId.CORRUPTOR,
        # Terran
        UnitTypeId.MARINE, UnitTypeId.WIDOWMINE, UnitTypeId.THOR,
        UnitTypeId.VIKINGFIGHTER, UnitTypeId.MISSILETURRET, UnitTypeId.CYCLONE,
        UnitTypeId.BUNKER,  # 装枪兵的碉堡
        # Protoss
        UnitTypeId.STALKER, UnitTypeId.PHOENIX, UnitTypeId.PHOTONCANNON,
        UnitTypeId.SENTRY,  # 守护盾会挡 phoenix lift 但对 prism 不构成致命
        UnitTypeId.ARCHON, UnitTypeId.VOIDRAY, UnitTypeId.TEMPEST,
        UnitTypeId.CARRIER,
    }
)


class PrismState(str, Enum):
    """棱镜骚扰状态机的 5 个状态。"""

    IDLE = "idle"
    FLY_TO_ENEMY = "fly_to_enemy"
    PHASING = "phasing"
    RETREAT = "retreat"
    RETURN_HOME = "return_home"


class PrismHarassAct(ActBase):  # type: ignore[misc]
    """精准棱镜骚扰：dt_drop_iac plan 用，配合 8 DT 全员骚扰对方矿区。

    用法：
        BuildOrder(
            ...,
            PrismHarassAct(),  # 顶层，每 step 执行一次状态机 tick
            ...,
        )

    本 act 假设有 1 个 Warp Prism 专门负责骚扰（plan 里 `ActUnit(WARPPRISM, 1)`
    出的那个）。如有多个 Warp Prism，本 act 控制最高血量的那个。
    """

    def __init__(self) -> None:
        super().__init__()
        self._state: PrismState = PrismState.IDLE
        self._last_state_change_ts: float = 0.0
        # 我家棱镜 tag（跟踪同一只，避免抖）
        self._prism_tag: int | None = None
        # phasing 目标点缓存
        self._target_point: Point2 | None = None

    async def execute(self) -> bool:
        # 找当前棱镜（transport 或 phasing 模式都算）
        prism = self._find_my_prism()
        if prism is None:
            # 没棱镜 → 重置状态，等 plan 里 ActUnit 出一个
            if self._state != PrismState.IDLE:
                self._set_state(PrismState.IDLE)
            return False

        now = self.ai.time
        self._prism_tag = prism.tag

        # 检测威胁（敌方反空单位在 prism 半径内）
        threatened = self._threat_nearby(prism)
        # 检测血量（shield+hp 比例）
        hp_low = self._prism_hp_low(prism)
        # DT 还活着吗
        dt_alive = self._dt_alive_count()

        # 状态机
        if self._state == PrismState.IDLE:
            # 棱镜出来了 → 飞前线
            self._target_point = self._pick_harass_target()
            if self._target_point is not None:
                prism.move(self._target_point)
                self._set_state(PrismState.FLY_TO_ENEMY)
                logger.info(
                    "PrismHarass: IDLE → FLY_TO_ENEMY, target=(%.1f, %.1f)",
                    self._target_point.x, self._target_point.y,
                )

        elif self._state == PrismState.FLY_TO_ENEMY:
            # 飞行中：到达 target → 切 phasing；遇威胁 → 撤
            if threatened or hp_low:
                self._begin_retreat(prism, "fly_threat" if threatened else "fly_hp_low")
            elif self._target_point and prism.distance_to(self._target_point) < _ARRIVED_DISTANCE:
                # 到达 → phase
                if prism.type_id == UnitTypeId.WARPPRISM:
                    prism(AbilityId.MORPH_WARPPRISMPHASINGMODE)
                    self._set_state(PrismState.PHASING)
                    logger.info("PrismHarass: FLY → PHASING at target")
            else:
                # 持续推进（每帧 move 一次防止丢指令）
                if self._target_point is not None:
                    prism.move(self._target_point)

        elif self._state == PrismState.PHASING:
            # phasing：威胁/低血 → 撤；DT 全死且 prism 没事 → 也回家（任务完）
            if threatened or hp_low:
                self._begin_retreat(prism, "phasing_threat" if threatened else "phasing_hp_low")
            elif dt_alive == 0 and now - self._last_state_change_ts > 30:
                # 在 phasing 状态 30s 后 DT 都死了 → 任务结束回家
                self._begin_retreat(prism, "phasing_no_dt")

        elif self._state == PrismState.RETREAT:
            # 撤退中：到家 → RETURN_HOME；中途遇威胁继续撤
            try:
                home = self.ai.start_location
                if prism.distance_to(home) < _HOME_DISTANCE:
                    self._set_state(PrismState.RETURN_HOME)
                    logger.info("PrismHarass: RETREAT → RETURN_HOME (arrived)")
                else:
                    # 持续撤（每帧 move）
                    prism.move(home)
            except Exception:
                pass

        elif self._state == PrismState.RETURN_HOME:
            # 已到家：保持 transport mode，闲置等下次任务
            if prism.type_id == UnitTypeId.WARPPRISMPHASING:
                prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            # 如果有 DT 重新出现（家里 Gateway 再 warp）且没威胁 → 重启循环
            if dt_alive >= 2 and not threatened and not hp_low:
                self._set_state(PrismState.IDLE)

        return False

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _set_state(self, new_state: PrismState) -> None:
        if new_state != self._state:
            logger.debug("PrismHarass state: %s → %s", self._state.value, new_state.value)
            self._state = new_state
            self._last_state_change_ts = self.ai.time

    def _find_my_prism(self) -> Any:
        """找当前控制的 Warp Prism（transport 或 phasing 模式）。"""
        try:
            prisms = self.ai.units.of_type(
                {UnitTypeId.WARPPRISM, UnitTypeId.WARPPRISMPHASING}
            )
        except Exception:
            return None
        if not prisms:
            return None
        # 优先跟踪已知 tag 的棱镜，没了再选血最高的
        if self._prism_tag is not None:
            same = prisms.tags_in([self._prism_tag])
            if same:
                return same[0]
        # 选血量最高的（最新出生的最健康）
        return max(prisms, key=lambda u: u.health + u.shield)

    def _pick_harass_target(self) -> Point2 | None:
        """选骚扰目标位置：对方 natural 矿区附近，离主入口有点距离的 safe pos。

        策略：取 enemy natural 位置，向 enemy main 方向偏 ~5 距离（既能 cover
        对方 worker line，又比直接 enemy_main 安全）。
        """
        try:
            enemy_main = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return None

        # 找对方 natural（最近 expansion 给 enemy_main）
        try:
            enemy_natural = min(
                (
                    p for p in self.ai.expansion_locations_list
                    if p.distance_to(enemy_main) > 5  # 排除 main 自己
                ),
                key=lambda p: p.distance_to(enemy_main),
                default=None,
            )
        except Exception:
            enemy_natural = None

        if enemy_natural is None:
            # fallback：直接用 enemy_main 旁边（高地附近，距 main 12-15）
            return enemy_main

        # 取 natural 矿区的位置；这附近 worker 多
        return enemy_natural

    def _threat_nearby(self, prism: Any) -> bool:
        """prism 半径内有敌方反空单位 → 视为威胁。"""
        try:
            enemies_close = self.ai.enemy_units.closer_than(_THREAT_RADIUS, prism)
        except Exception:
            return False
        for u in enemies_close:
            if u.type_id in _ANTI_AIR_UNITS:
                return True
        # 也检查反空建筑
        try:
            enemy_structs = self.ai.enemy_structures.closer_than(_THREAT_RADIUS, prism)
        except Exception:
            return False
        return any(s.type_id in _ANTI_AIR_UNITS for s in enemy_structs)

    def _prism_hp_low(self, prism: Any) -> bool:
        """prism shield+hp 比例 < 阈值 → 撤。"""
        try:
            total = prism.health + prism.shield
            max_total = prism.health_max + prism.shield_max
            if max_total == 0:
                return False
            return bool((total / max_total) < _RETREAT_HP_PCT)
        except Exception:
            return False

    def _dt_alive_count(self) -> int:
        try:
            return int(self.ai.units(UnitTypeId.DARKTEMPLAR).amount)
        except Exception:
            return 0

    def _begin_retreat(self, prism: Any, reason: str) -> None:
        """切回 transport 模式 + 飞回家。"""
        try:
            home = self.ai.start_location
        except (IndexError, AttributeError):
            return
        # 如果还是 phasing，先切回 transport（transport 速度快）
        if prism.type_id == UnitTypeId.WARPPRISMPHASING:
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
        # 立即 move 回家
        prism.move(home)
        self._set_state(PrismState.RETREAT)
        logger.info("PrismHarass: → RETREAT (%s)", reason)
