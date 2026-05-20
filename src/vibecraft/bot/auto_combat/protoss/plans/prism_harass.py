"""PrismHarassAct: 精准 Warp Prism 棱镜骚扰行为 — 完整 9 状态机（2026-05-19 v2）。

dt_drop_iac 专用。配合 WarpDTAtPrism 实现完整空投流程：
  fly → warp 4 DT → load → drop @ enemy main 低地 →
  [hover_wait 保护 + 等 cd] → [in-place 2nd warp OR back-to-safe] → repeat
  → all 8 DT delivered → hover_final → macro_attack → follow_army

行为决策（用户 2026-05-19 spec）
================================
1. 第一批：飞 safe_pos (enemy_main → home, 20 距) → phase → warp 4 DT → load →
   飞 drop_pos (enemy_main → home, 10 距) → unload → DT 落地 + cloak attack workers
2. CD 等待期间：棱镜 hover 在 formula B 位置（dt_center + 5 朝家方向，
   pickup range 内动态保护已空投 DT；DT 危险时可瞬间 smart-cast 上船）
3. CD ready 时分支判定：
   - 当前 hover_pos 安全（无 detector / AA / DT 没有近期受伤）→ 原地 phase warp
     第二波 DT（最激进，第二波直接在敌方基地 warp 出现）
   - 不安全 → 飞回 safe_pos 保守 warp 第二波
4. 8 DT 全部 delivered → hover_final 标记；等 macro_attack ready → follow_army
   (跟主力部队会合)

依赖
====
- detector_data.py：DETECTOR_RANGES / AA_THREAT_RANGES / PRISM_PICKUP_RANGE
- WarpDTAtPrism act：phasing prism 处 warp DT
- bot.knowledge.vibecraft.dt_trained_count：latch counter (≥8 → all done)
- bot.knowledge.vibecraft.damaged_dts：DT 受伤 ts 记录
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

# 阈值
_RETREAT_HP_PCT: float = 0.30
_MACRO_ATTACK_DT_THRESHOLD: int = 8
_WARPING_TIMEOUT_S: float = 25.0
_LOADING_TIMEOUT_S: float = 5.0
_LOADING_MIN_CARGO: int = 4  # 至少 2 DT (2 supply × 2) 上船才起飞
_DAMAGE_LOOKBACK_S: float = 5.0  # hover 安全判定：近 5s 没 DT 受伤
_HOVER_DAMAGE_GRACE_PCT: float = 0.50  # 不强求 0 受伤：5s 内受伤 DT 比例 < 50% 仍算"安全"
_ARRIVED_DISTANCE: float = 3.0

# 距离配置
_SAFE_POS_OFFSET: float = 20.0  # enemy_main → home 20 距 = warp safe pos
_DROP_POS_OFFSET: float = 10.0  # enemy_main → home 10 距 = drop pos（低地近主基地）

# 主力部队 supply types（follow_army 中心位计算）
_ARMY_UNIT_TYPES: frozenset[UnitTypeId] = frozenset(
    {
        UnitTypeId.ZEALOT, UnitTypeId.STALKER, UnitTypeId.SENTRY,
        UnitTypeId.ADEPT, UnitTypeId.IMMORTAL, UnitTypeId.ARCHON,
        UnitTypeId.HIGHTEMPLAR, UnitTypeId.DARKTEMPLAR, UnitTypeId.COLOSSUS,
    }
)


class PrismState(str, Enum):
    """9-状态机（含 IDLE 起始 + 2 个终止）。"""

    IDLE = "idle"
    FLY_TO_SAFE = "fly_to_safe"   # 飞 safe_pos 准备 warp
    WARPING = "warping"           # phasing 模式 warp 4 DT
    LOADING = "loading"           # transport 模式 DT 上船
    FLY_TO_DROP = "fly_to_drop"   # 载 cargo 飞 drop_pos
    UNLOAD = "unload"             # drop_pos 卸 DT
    HOVER_WAIT = "hover_wait"     # 第 1 批已 deliver，等 CD ready 第 2 批
    HOVER_FINAL = "hover_final"   # 8 DT 全部 delivered，standby
    RETREAT_HOME = "retreat_home" # HP < 30%，飞回家修
    FOLLOW_ARMY = "follow_army"   # macro_attack 触发，跟主力会合


class PrismHarassAct(ActBase):  # type: ignore[misc]
    """完整 9-state 棱镜微操，dt_drop_iac 专用。"""

    def __init__(self) -> None:
        super().__init__()
        self._state: PrismState = PrismState.IDLE
        self._state_entered_ts: float = 0.0
        self._prism_tag: int | None = None
        # WARPING 入口的 DT count baseline（用于 delta 检测 4 DT 已 warp）
        self._dt_baseline: int = 0

    async def execute(self) -> bool:
        prism = self._find_my_prism()
        if prism is None:
            if self._state != PrismState.IDLE:
                self._set_state(PrismState.IDLE)
            return False
        self._prism_tag = prism.tag

        # 2026-05-20 关键修复(用户反馈"棱镜一直被无形的力量拉回家"):
        # sharpy `should_attack(WARPPRISM)` 返回 True(unit_value.py:565 — 棱镜
        # 不是建筑也不是农民)。于是 PlanZoneGather 把棱镜当普通战斗单位,每 tick
        # combat-move 回 home gather_point。PrismHarassAct 发的 move(safe) 被
        # tactics SequentialList 里后跑的 PlanZoneGather 覆盖 → 棱镜永远到不了前线。
        # 修:每 tick 把棱镜标 Reserved task。sharpy PlanZoneGather 只看 roles.idle,
        # PlanZoneAttack 只看 free_units(Idle+Moving),Reserved 一律跳过 →
        # PrismHarassAct 独占棱镜控制权。
        try:
            from sharpy.managers.core.roles import UnitTask

            self.knowledge.roles.set_task(UnitTask.Reserved, prism)
        except Exception:
            pass

        # ---- 优先级 1：HP 低撤 ----
        if self._prism_hp_low(prism):
            if self._state != PrismState.RETREAT_HOME:
                self._do_retreat_home(prism)
            return False

        # ---- 优先级 2：macro_attack ready 且 cargo 空 → follow_army ----
        if (
            self._macro_attack_ready()
            and getattr(prism, "cargo_used", 0) == 0
            and self._state in (PrismState.HOVER_WAIT, PrismState.HOVER_FINAL, PrismState.IDLE)
        ):
            self._do_follow_army(prism)
            return False

        # ---- 初始入场 ----
        if self._state == PrismState.IDLE:
            self._set_state(PrismState.FLY_TO_SAFE)

        # ---- 主状态机分发 ----
        if self._state == PrismState.FLY_TO_SAFE:
            await self._handle_fly_to_safe(prism)
        elif self._state == PrismState.WARPING:
            await self._handle_warping(prism)
        elif self._state == PrismState.LOADING:
            await self._handle_loading(prism)
        elif self._state == PrismState.FLY_TO_DROP:
            await self._handle_fly_to_drop(prism)
        elif self._state == PrismState.UNLOAD:
            await self._handle_unload(prism)
        elif self._state == PrismState.HOVER_WAIT:
            await self._handle_hover_wait(prism)
        elif self._state == PrismState.HOVER_FINAL:
            await self._handle_hover_final(prism)
        # RETREAT_HOME / FOLLOW_ARMY：一次性 move 指令后无需每 tick 处理

        return False

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    async def _handle_fly_to_safe(self, prism: Any) -> None:
        safe = self._compute_safe_pos()
        if safe is None:
            return
        # 切回 transport（如果 phasing）
        if prism.type_id == UnitTypeId.WARPPRISMPHASING:
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            return
        if prism.distance_to(safe) > _ARRIVED_DISTANCE:
            prism.move(safe)
        else:
            self._set_state(PrismState.WARPING)

    async def _handle_warping(self, prism: Any) -> None:
        # 第一次进 WARPING 时切 phasing + 记 baseline
        if prism.type_id == UnitTypeId.WARPPRISM:
            prism(AbilityId.MORPH_WARPPRISMPHASINGMODE)
            try:
                self._dt_baseline = self.ai.units(UnitTypeId.DARKTEMPLAR).amount
            except Exception:
                self._dt_baseline = 0
            return

        # 已 phasing：监控 4 DT 是否 warp 完
        try:
            current_dt = self.ai.units(UnitTypeId.DARKTEMPLAR).amount
        except Exception:
            current_dt = 0
        delta = current_dt - self._dt_baseline
        elapsed = self.ai.time - self._state_entered_ts
        if delta >= 4 or elapsed > _WARPING_TIMEOUT_S:
            logger.info(
                "PrismHarass WARPING done: delta=%d elapsed=%.1fs", delta, elapsed
            )
            self._set_state(PrismState.LOADING)

    async def _handle_loading(self, prism: Any) -> None:
        # 切回 transport 才能装载
        if prism.type_id == UnitTypeId.WARPPRISMPHASING:
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            return

        # 主动 smart-cast 附近所有 DT 上船
        try:
            nearby = self.ai.units(UnitTypeId.DARKTEMPLAR).closer_than(8.0, prism)
            for dt in nearby:
                dt.smart(prism)
        except Exception:
            pass

        cargo = int(getattr(prism, "cargo_used", 0))
        elapsed = self.ai.time - self._state_entered_ts
        # cargo >= 4 (2 DT) AND (满载 8 OR 等够 5s) → 起飞
        if cargo >= _LOADING_MIN_CARGO and (cargo >= 8 or elapsed > _LOADING_TIMEOUT_S):
            logger.info("PrismHarass LOADING done: cargo=%d elapsed=%.1fs", cargo, elapsed)
            self._set_state(PrismState.FLY_TO_DROP)

    async def _handle_fly_to_drop(self, prism: Any) -> None:
        drop = self._compute_drop_pos()
        if drop is None:
            return
        if prism.distance_to(drop) > _ARRIVED_DISTANCE:
            prism.move(drop)
        else:
            self._set_state(PrismState.UNLOAD)

    async def _handle_unload(self, prism: Any) -> None:
        cargo = int(getattr(prism, "cargo_used", 0))
        if cargo > 0:
            prism(AbilityId.UNLOADALLAT_WARPPRISM, prism.position)
            return
        # 卸空 → 判断下一步
        trained = self._dt_trained_count()
        if trained >= _MACRO_ATTACK_DT_THRESHOLD:
            self._set_state(PrismState.HOVER_FINAL)
        else:
            self._set_state(PrismState.HOVER_WAIT)

    async def _handle_hover_wait(self, prism: Any) -> None:
        """第 1 批已 deliver，等 warpgate CD。formula B hover，cd 好就分支。"""
        hover = self._compute_hover_pos()
        if hover is None:
            return
        # 切 transport 确保能移动
        if prism.type_id == UnitTypeId.WARPPRISMPHASING:
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            return
        if prism.distance_to(hover) > _ARRIVED_DISTANCE:
            prism.move(hover)

        # 等 warpgate cd
        if not self._warpgate_ready():
            return

        # cd ready：分支
        if self._is_pos_safe(prism.position):
            # 当前 hover_pos 安全 → 原地 phase warp 第二波（最激进）
            logger.info("PrismHarass HOVER_WAIT: cd ready + safe → phase in place")
            self._set_state(PrismState.WARPING)
        else:
            # 不安全 → 飞回 safe_pos 保守 warp
            logger.info("PrismHarass HOVER_WAIT: cd ready + unsafe → fly back to safe")
            self._set_state(PrismState.FLY_TO_SAFE)

    async def _handle_hover_final(self, prism: Any) -> None:
        """8 DT 全部 delivered，standby。"""
        hover = self._compute_hover_pos()
        if hover is None:
            return
        if prism.type_id == UnitTypeId.WARPPRISMPHASING:
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            return
        if prism.distance_to(hover) > _ARRIVED_DISTANCE:
            prism.move(hover)
        # 等 macro_attack（已在顶部优先级 2 处理）

    # ------------------------------------------------------------------
    # Position computations
    # ------------------------------------------------------------------

    def _compute_safe_pos(self) -> Point2 | None:
        """safe_pos = enemy_main 朝家 20 距。"""
        try:
            enemy = self.ai.enemy_start_locations[0]
            home = self.ai.start_location
            return enemy.towards(home, _SAFE_POS_OFFSET)
        except (IndexError, AttributeError):
            return None

    def _compute_drop_pos(self) -> Point2 | None:
        """drop_pos = enemy_main 朝家 10 距（低地近主基地）。"""
        try:
            enemy = self.ai.enemy_start_locations[0]
            home = self.ai.start_location
            return enemy.towards(home, _DROP_POS_OFFSET)
        except (IndexError, AttributeError):
            return None

    def _compute_hover_pos(self) -> Point2 | None:
        """formula B：dt_center + (dt_center - enemy_main).normalized × pickup_range。"""
        try:
            enemy = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return None
        try:
            dts = self.ai.units(UnitTypeId.DARKTEMPLAR)
        except Exception:
            dts = None
        if not dts:
            # 没 DT 时 fallback drop_pos（保留位置等下一波）
            return self._compute_drop_pos()
        dt_center = dts.center
        diff = dt_center - enemy
        dlen = max(diff.length, 0.1)
        ux, uy = diff.x / dlen, diff.y / dlen
        return Point2(
            (dt_center.x + ux * PRISM_PICKUP_RANGE, dt_center.y + uy * PRISM_PICKUP_RANGE)
        )

    # ------------------------------------------------------------------
    # Safety check (HOVER_WAIT 分支用)
    # ------------------------------------------------------------------

    def _is_pos_safe(self, pos: Point2) -> bool:
        """pos 是否适合原地 phase warp 第二波？

        三条全部满足才算 safe：
        1. 无 detector 在 detection range 内
        2. 无 AA 在 (range + buffer) 内
        3. DT 近期 (5s) 没大面积受伤（暗示有未发现 detector / 偷袭）
        """
        # 1. detector
        try:
            for det_type, det_range in DETECTOR_RANGES.items():
                detectors = self.ai.enemy_units.of_type(det_type) | self.ai.enemy_structures.of_type(det_type)
                for det in detectors:
                    if det.distance_to(pos) < det_range:
                        return False
        except Exception:
            pass

        # 2. AA
        try:
            for aa_type, aa_range in AA_THREAT_RANGES.items():
                aas = self.ai.enemy_units.of_type(aa_type) | self.ai.enemy_structures.of_type(aa_type)
                threshold = aa_range + PRISM_AA_BUFFER
                for aa in aas:
                    if aa.distance_to(pos) < threshold:
                        return False
        except Exception:
            pass

        # 3. DT 近期受伤
        try:
            damaged_dts = self.knowledge.ai.knowledge.vibecraft.damaged_dts
            now = self.ai.time
            recent_count = sum(1 for ts in damaged_dts.values() if now - ts < _DAMAGE_LOOKBACK_S)
            dt_alive = int(self.ai.units(UnitTypeId.DARKTEMPLAR).amount)
            if dt_alive > 0 and (recent_count / dt_alive) > _HOVER_DAMAGE_GRACE_PCT:
                return False
        except Exception:
            pass

        return True

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    def _warpgate_ready(self) -> bool:
        """任一 warpgate cd ready 即返回 True。"""
        try:
            wgs = self.ai.structures(UnitTypeId.WARPGATE).ready
            for wg in wgs:
                try:
                    if self.knowledge.cooldown_manager.is_ready(
                        wg.tag, AbilityId.WARPGATETRAIN_ZEALOT
                    ):
                        return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

    def _prism_hp_low(self, prism: Any) -> bool:
        try:
            total = prism.health + prism.shield
            max_total = prism.health_max + prism.shield_max
            if max_total <= 0:
                return False
            return bool((total / max_total) < _RETREAT_HP_PCT)
        except Exception:
            return False

    def _dt_trained_count(self) -> int:
        try:
            return int(self.knowledge.ai.knowledge.vibecraft.dt_trained_count)
        except Exception:
            return 0

    def _macro_attack_ready(self) -> bool:
        """玩家显式 attack OR cumulative DT trained ≥ 8 latched。"""
        try:
            override = self.knowledge.ai.knowledge.vibecraft.combat_intent_override
            if override == "attack":
                return True
        except Exception:
            pass
        return self._dt_trained_count() >= _MACRO_ATTACK_DT_THRESHOLD

    def _do_retreat_home(self, prism: Any) -> None:
        if prism.type_id == UnitTypeId.WARPPRISMPHASING:
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
        try:
            home = self.ai.start_location
            prism.move(home)
        except (IndexError, AttributeError):
            pass
        self._set_state(PrismState.RETREAT_HOME)

    def _do_follow_army(self, prism: Any) -> None:
        if prism.type_id == UnitTypeId.WARPPRISMPHASING:
            prism(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
        target = self._main_army_center()
        if target is not None:
            prism.move(target)
        self._set_state(PrismState.FOLLOW_ARMY)

    def _main_army_center(self) -> Point2 | None:
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

    def _set_state(self, new_state: PrismState) -> None:
        if new_state != self._state:
            logger.debug(
                "PrismHarass state: %s → %s (t=%.1fs)",
                self._state.value, new_state.value, self.ai.time,
            )
            self._state = new_state
            self._state_entered_ts = self.ai.time
