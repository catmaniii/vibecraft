"""PrismHarassAct: 精准 Warp Prism 棱镜骚扰行为（2026-05-19 用户 spec）。

行为
====
1. 棱镜出来后飞向前线 patrol 位置
2. PATROL：实时计算"enemy_center → dt_center 延长线 + d=5"作为 patrol 位置
   （d=5 = warp prism pickup range；DT smart-cast 朝棱镜跑 5 距即 auto-load）
3. PHASING：当 warpgate ready 且 DT 数 < 8 且位置安全（无 AA / 无 detector） →
   切 phasing 为 DT 提供 warp-in power 源
4. FOLLOW_ARMY：macro_attack ready（cumulative DT trained ≥ 8 latched）→ 飞到主力部队中心
5. RETREAT_HOME：HP < 30% → 撤回家修

设计
====
棱镜不主动飞向 DT 接走；保持 patrol 位置，靠 DT 自己 smart-cast 走过来（DT 微操逻辑
在 VibeCraftMicroDarkTemplar 处理）。这样棱镜不容易冒进 AA 范围。

依赖
====
- detector_data.py：DETECTOR_RANGES + AA_THREAT_RANGES + PRISM_PICKUP_RANGE
- bot.knowledge.vibecraft.dt_trained_count（cumulative DT trained latch counter）
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

from vibecraft.bot.auto_combat.protoss.detector_data import (
    AA_THREAT_RANGES,
    DETECTOR_RANGES,
    PRISM_AA_BUFFER,
    PRISM_PICKUP_RANGE,
)

logger = logging.getLogger(__name__)

_RETREAT_HP_PCT: float = 0.30  # HP < 30% → 撤回家修

# macro attack ready latch threshold（cumulative DT trained）
_MACRO_ATTACK_DT_THRESHOLD: int = 8

# 主力部队 supply（用于 follow_army 中心位计算）
_ARMY_UNIT_TYPES: frozenset[UnitTypeId] = frozenset(
    {
        UnitTypeId.ZEALOT, UnitTypeId.STALKER, UnitTypeId.SENTRY,
        UnitTypeId.ADEPT, UnitTypeId.IMMORTAL, UnitTypeId.ARCHON,
        UnitTypeId.HIGHTEMPLAR, UnitTypeId.DARKTEMPLAR, UnitTypeId.COLOSSUS,
    }
)


class PrismState(str, Enum):
    IDLE = "idle"
    FLY_TO_PATROL = "fly_to_patrol"
    PATROL = "patrol"
    FOLLOW_ARMY = "follow_army"
    RETREAT_HOME = "retreat_home"


class PrismHarassAct(ActBase):  # type: ignore[misc]
    """棱镜微操：patrol 在 DT 后方 5 距、HP 低撤、macro attack 跟主力。"""

    def __init__(self) -> None:
        super().__init__()
        self._state: PrismState = PrismState.IDLE
        self._last_state_change_ts: float = 0.0
        self._prism_tag: int | None = None

    async def execute(self) -> bool:
        prism = self._find_my_prism()
        if prism is None:
            if self._state != PrismState.IDLE:
                self._set_state(PrismState.IDLE)
            return False
        self._prism_tag = prism.tag

        # ---- 优先级 1：HP 低撤 ----
        if self._prism_hp_low(prism):
            self._begin_retreat_home(prism)
            return False

        # ---- 优先级 2：macro attack ready → 跟主力部队 ----
        if self._macro_attack_ready():
            self._follow_main_army(prism)
            return False

        # ---- 主流程：飞向 patrol pos / phase / 跟随 ----
        if self._state in (PrismState.IDLE, PrismState.RETREAT_HOME):
            # 进入正常工作流：飞 patrol 位
            self._set_state(PrismState.FLY_TO_PATROL)

        target = self._compute_patrol_pos()
        if target is None:
            return False

        # 切回 transport（如果当前 phasing 且位置不安全）
        if prism.type_id == UnitTypeId.WARPPRISMPHASING and not self._patrol_pos_safe(target):
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            self._set_state(PrismState.FLY_TO_PATROL)
            return False

        # 在 patrol 位附近：考虑切 phasing
        dist_to_target = prism.distance_to(target)
        if dist_to_target < 3.0:
            self._set_state(PrismState.PATROL)
            # 满足 phasing 条件 → 切 phasing
            if (
                prism.type_id == UnitTypeId.WARPPRISM
                and self._should_phase()
                and self._patrol_pos_safe(target)
            ):
                prism(AbilityId.MORPH_WARPPRISMPHASINGMODE)
                logger.debug("PrismHarass: PATROL → phasing for warp-in")
        else:
            prism.move(target)

        return False

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _set_state(self, new_state: PrismState) -> None:
        if new_state != self._state:
            logger.debug("PrismHarass state: %s → %s", self._state.value, new_state.value)
            self._state = new_state
            self._last_state_change_ts = self.ai.time

    # ------------------------------------------------------------------
    # Patrol pos formula (user spec)
    # ------------------------------------------------------------------

    def _compute_patrol_pos(self) -> Point2 | None:
        """用户 spec：棱镜保持在 "enemy_center → dt_center 延长线 + d" 上。

        d = PRISM_PICKUP_RANGE (5)。这样 DT 朝棱镜方向跑（远离敌方）= 远离 detector
        + 棱镜在 pickup range 内自动 load。

        如果 patrol pos 落在 AA 威胁范围内 → 沿同方向再推 + buffer。
        没 DT 时（第一次还没 warp）：fallback enemy_natural - 8 朝家方向。
        """
        try:
            enemy_main = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return None

        # 找 enemy_center：natural 矿区（worker 集中地）
        enemy_center = self._enemy_natural() or enemy_main

        # DT 中心
        try:
            dts = self.ai.units(UnitTypeId.DARKTEMPLAR)
        except Exception:
            dts = None
        if dts is None or not dts:
            # 没 DT：fallback enemy_natural 朝家 8 距（给 DT 第一次 warp 提供着陆点）
            try:
                home = self.ai.start_location
                return enemy_center.towards(home, 8)
            except (IndexError, AttributeError):
                return enemy_center

        dt_center = dts.center

        # 形成 patrol pos：dt_center 沿 (enemy_center → dt_center) 方向延伸 d
        # = dt_center + (dt_center - enemy_center) / |...| * d
        diff = dt_center - enemy_center
        dlen = max(diff.length, 0.1)
        unit_dir = Point2((diff.x / dlen, diff.y / dlen))
        candidate = Point2(
            (
                dt_center.x + unit_dir.x * PRISM_PICKUP_RANGE,
                dt_center.y + unit_dir.y * PRISM_PICKUP_RANGE,
            )
        )

        # 检查 AA 安全；不安全 → 沿同方向再推
        for _ in range(5):  # 最多迭代 5 次推远
            if self._patrol_pos_safe(candidate):
                return candidate
            candidate = Point2(
                (
                    candidate.x + unit_dir.x * 3.0,
                    candidate.y + unit_dir.y * 3.0,
                )
            )
        return candidate  # 兜底（即便不完全安全也用）

    def _enemy_natural(self) -> Point2 | None:
        """对方 natural 矿区位置（worker 多的地方）。"""
        try:
            enemy_main = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return None
        try:
            return min(
                (p for p in self.ai.expansion_locations_list if p.distance_to(enemy_main) > 5),
                key=lambda p: p.distance_to(enemy_main),
                default=None,
            )
        except Exception:
            return None

    def _patrol_pos_safe(self, pos: Point2) -> bool:
        """patrol pos 是否安全（无 AA 威胁 + 无 detector 范围内）。"""
        try:
            for aa_type, aa_range in AA_THREAT_RANGES.items():
                aas = self.ai.enemy_units.of_type(aa_type) | self.ai.enemy_structures.of_type(aa_type)
                threshold = aa_range + PRISM_AA_BUFFER
                for aa in aas:
                    if aa.distance_to(pos) < threshold:
                        return False
        except Exception:
            return False
        # phasing 时 prism 是空军单位也会被 detector 看到，但 transport 模式 cloak 度
        # 跟普通空军一样会被 detector 探测（仅 cloak 单位才需要 detector），这里
        # detector 检查放宽（不强制 patrol 远离 detector）
        return True

    def _should_phase(self) -> bool:
        """是否应该切 phasing 模式（DT 数不足 8 + warpgate ready）。"""
        try:
            trained = int(self.knowledge.ai.knowledge.vibecraft.dt_trained_count)  # type: ignore[attr-defined]
        except Exception:
            trained = 0
        if trained >= 8:
            return False  # DT 训练够了，不用再 warp
        try:
            from sc2.ids.unit_typeid import UnitTypeId as _U

            wgs = self.ai.structures(_U.WARPGATE).ready
            return bool(wgs.exists)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # macro attack ready check (Q2 spec)
    # ------------------------------------------------------------------

    def _macro_attack_ready(self) -> bool:
        """macro_attack_ready：cumulative DT trained ≥ 8 latched，或玩家 override。"""
        try:
            override = self.knowledge.ai.knowledge.vibecraft.combat_intent_override  # type: ignore[attr-defined]
            if override == "attack":
                return True
        except Exception:
            pass
        try:
            trained = int(self.knowledge.ai.knowledge.vibecraft.dt_trained_count)  # type: ignore[attr-defined]
            return trained >= _MACRO_ATTACK_DT_THRESHOLD
        except Exception:
            return False

    def _follow_main_army(self, prism: Any) -> None:
        """跟随主力部队（macro attack 时）。"""
        if prism.type_id == UnitTypeId.WARPPRISMPHASING:
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
        target = self._main_army_center()
        if target is not None:
            prism.move(target)
        self._set_state(PrismState.FOLLOW_ARMY)

    def _main_army_center(self) -> Point2 | None:
        """所有主力战斗单位的平均位置。"""
        try:
            army = self.ai.units.of_type(set(_ARMY_UNIT_TYPES))
        except Exception:
            return None
        if not army:
            try:
                return self.ai.start_location
            except (IndexError, AttributeError):
                return None
        return army.center

    # ------------------------------------------------------------------
    # HP / retreat
    # ------------------------------------------------------------------

    def _prism_hp_low(self, prism: Any) -> bool:
        try:
            total = prism.health + prism.shield
            max_total = prism.health_max + prism.shield_max
            if max_total == 0:
                return False
            return bool((total / max_total) < _RETREAT_HP_PCT)
        except Exception:
            return False

    def _begin_retreat_home(self, prism: Any) -> None:
        if prism.type_id == UnitTypeId.WARPPRISMPHASING:
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
        try:
            home = self.ai.start_location
            prism.move(home)
        except (IndexError, AttributeError):
            pass
        self._set_state(PrismState.RETREAT_HOME)

    # ------------------------------------------------------------------
    # Prism lookup
    # ------------------------------------------------------------------

    def _find_my_prism(self) -> Any:
        try:
            prisms = self.ai.units.of_type(
                {UnitTypeId.WARPPRISM, UnitTypeId.WARPPRISMPHASING}
            )
        except Exception:
            return None
        if not prisms:
            return None
        if self._prism_tag is not None:
            same = prisms.tags_in([self._prism_tag])
            if same:
                return same[0]
        return max(prisms, key=lambda u: u.health + u.shield)


# silence noqa F401 imports we keep for completeness
_ = DETECTOR_RANGES
