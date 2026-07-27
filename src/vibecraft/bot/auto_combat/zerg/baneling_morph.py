"""共享毒爆 morph：前压 + 护蛹（ling_bane 开局 plan 与 build-aware sustain 共用）。

设计真理源：docs/plans/2026-06-16-ling-bane-choreography-design.md。

三道 gate（顺序）：
1. **够狗**：≥6 ling（cocoon 4 + 护卫 ≥2 才有意义）。
2. **护蛹**：ling 群几何中心 8 格内 ≥4 ling（cocoon 在主力中间，有护卫；防 2026-05-23 裸死坑）。
3. **前压**：ling 群已推进过中点（寻路距离 center→enemy < center→own）。未到前沿不变（狗先压出去）。
   **超时兜底（latch）**：护蛹满足但 ≥60s 推不出去（如 forced-defend 沙盒军队不出门）→ latch 回退
   "就地变"，本局之后忽略前压 gate。防永不出爆 + 防效率沙盒回归。
"""

from __future__ import annotations

import logging
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts.zerg import ZergUnit
from sharpy.plans.acts.zerg.morph_units import MorphBaneling

logger = logging.getLogger(__name__)

_MIN_LINGS = 6  # gate 1：够狗
_PROTECT_NEAR = 4  # gate 2：中心附近护卫数
_PROTECT_RADIUS = 8.0  # gate 2：护卫半径
_FORWARD_TIMEOUT_S = 60.0  # gate 3 超时兜底（latch 回退就地变）


class ForwardBanelingMorph(MorphBaneling):  # type: ignore[misc]
    """毒爆「前压 + 护蛹」morph。返回 None 的帧不变蛹（等狗集结 / 等推进）。"""

    def __init__(self, target_count: int) -> None:
        super().__init__(target_count)
        # ability 冗余显式（vendored MorphBaneling 已修成 MORPHTOBANELING_BANELING，
        # 这里再 set 一次防 vendor 被 revert）。
        self.ability_type = AbilityId.MORPHTOBANELING_BANELING
        self._wanted_since: float | None = None  # 护蛹满足、开始"想变"的 game-time
        self._fallback_latched: bool = False  # 超时后 latch：就地变

    def _is_forward(self, center: Point2) -> bool:
        """ling 群中心是否已推进过中点（寻路距离离敌方主基地比离己方近）。"""
        ai = self.ai
        try:
            own = ai.start_location
            enemy = ai.enemy_start_locations[0]
        except Exception:
            return True  # 拿不到出生点 → 不阻塞
        try:
            pm = self.knowledge.pathing_manager
            return float(pm.walk_distance(center, enemy)) < float(pm.walk_distance(center, own))
        except Exception:
            # 寻路失败 → 欧氏直线兜底
            return center.distance_to(enemy) < center.distance_to(own)

    def _morph_target(self) -> Point2 | None:
        ai = self.ai
        lings = ai.units(UnitTypeId.ZERGLING).ready
        # gate 1：够狗
        if lings.amount < _MIN_LINGS:
            self._wanted_since = None
            return None
        cx = sum(u.position.x for u in lings) / lings.amount
        cy = sum(u.position.y for u in lings) / lings.amount
        center = Point2((cx, cy))
        # gate 2：护蛹（中心 8 格内 ≥4 ling）
        near_center = lings.filter(lambda u: u.distance_to(center) < _PROTECT_RADIUS)
        if near_center.amount < _PROTECT_NEAR:
            return None
        # 护蛹满足 → 记"想变"起点，超时 latch 回退就地变
        now = float(ai.time)
        if self._wanted_since is None:
            self._wanted_since = now
        if not self._fallback_latched and now - self._wanted_since >= _FORWARD_TIMEOUT_S:
            self._fallback_latched = True
        # gate 3：前压（latch 后跳过 → 就地变）
        if not self._fallback_latched and not self._is_forward(center):
            return None
        return center

    async def execute(self) -> bool:
        target_pt = self._morph_target()
        if target_pt is None:
            return False  # 没集结好 / 没到前沿，本帧不 morph
        done = self.cache.own(self.result_type).amount
        # 选最靠近群中心的 ling morph → cocoon 在群中，有护卫
        start_units = self.cache.own(self.unit_type).ready.sorted_by_distance_to(target_pt)
        done += self.cache.own(self.cocoon_type).amount
        for u in start_units:
            if u.orders and u.orders[0].ability.id == self.ability_type:
                done += 1
        if done >= self.target_count:
            return True
        for u in start_units:
            if not u.is_ready:
                continue
            if self.knowledge.can_afford(self.ability_type):
                u(self.ability_type, subtract_cost=True, subtract_supply=True)
                self._trace_morph(target_pt)
            else:
                self.knowledge.reserve_costs(self.ability_type)
            done += 1
            if done >= self.target_count:
                return True
        return not start_units

    def _trace_morph(self, pt: Point2) -> None:
        """真局自验：每次真 morph 一只爆虫时打 greppable 日志（在前沿变 vs 家里变）。"""
        try:
            ai = self.ai
            own = ai.start_location
            enemy = ai.enemy_start_locations[0]
            logger.warning(
                "BANETRACE morph t=%.0f at=(%.0f,%.0f) home_dist=%.0f enemy_dist=%.0f fallback=%s",
                float(ai.time),
                pt.x,
                pt.y,
                pt.distance_to(own),
                pt.distance_to(enemy),
                self._fallback_latched,
            )
        except Exception:
            pass


class ForwardBanelingZergUnit(ZergUnit):  # type: ignore[misc]
    """ZergUnit(BANELING) 但 morph 走 ForwardBanelingMorph（前压+护蛹）而非默认 home morph。

    build-aware sustain 对 baneling_morph_mode=forward 的 build 用它（替代裸 ZergUnit(BANELING)
    的 home MorphBaneling）。源兵 ActUnit(ZERGLING, LARVA) 部分不变。
    """

    def __init__(self, to_count: int = 9999, priority: bool = False) -> None:
        super().__init__(UnitTypeId.BANELING, to_count, priority)
        fwd = ForwardBanelingMorph(to_count)
        self.morph_unit = fwd
        self.orders[0] = fwd


def make_baneling_morph(to_count: int, *, mode: str) -> Any:
    """按 build 的 baneling_morph_mode 选 morph act。

    - "forward"：ForwardBanelingZergUnit（前压+护蛹，ling_bane all-in）。
    - 其它（默认 "home"）：裸 ZergUnit(BANELING)（home MorphBaneling，宏观防守预备队）。
    """
    if str(mode).lower() == "forward":
        return ForwardBanelingZergUnit(to_count)
    return ZergUnit(UnitTypeId.BANELING, to_count)
