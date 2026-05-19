"""VibeCraftMicroDarkTemplar: DT 智能微操替代 sharpy 默认 MicroZerglings。

行为（用户 2026-05-19 spec）
============================
被检测 OR 被攻击 → 往安全方向跑 / 棱镜方向跑
否则 → 默认 melee（focus fire workers，从父类 MicroZerglings 继承）

通用于所有 DT 场景（dt_rush / dt_drop_iac / 任何含 DT 的剧本）。dt_rush 也享受
"被探测自动撤退保留 DT"的好处（之前 4 DT 探测后愣头青 attack 全死）。

触发条件
========
1. 任一已知敌方 detector 距 DT < DETECTOR_RANGES[type] - DT_DETECTOR_BUFFER
   (i.e., DT 即将进入或已在检测范围内 → 视为危险)
2. DT 在最近 2 秒内被攻击（damaged_dts dict 由 bot.on_unit_took_damage 更新）

撤退选址
========
- 优先：最近的 transport-mode Warp Prism → DT smart-cast 朝棱镜跑
  （DT < 5 距时 sharpy 自动装载；不需要棱镜飞过来）
- 次选：远离 detector 的安全方向（vector sum of "排斥" 向量）
- 兜底：主基地方向

依赖
====
- detector_data.py：DETECTOR_RANGES + PRISM_PICKUP_RANGE + DT_DETECTOR_BUFFER
- bot.knowledge.vibecraft.damaged_dts：{dt_tag: last_damage_timestamp}
  在 common_bot.on_start 初始化，common_bot.on_unit_took_damage 更新
"""

from __future__ import annotations

import logging
from typing import ClassVar

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit import Unit
from sharpy.combat import Action
from sharpy.combat.micro_step import MicroStep

from vibecraft.bot.auto_combat.protoss.detector_data import (
    DETECTOR_RANGES,
    DT_DETECTOR_BUFFER,
)

logger = logging.getLogger(__name__)

# 受伤后保持"危险"状态的秒数（2s 后清记忆，可以继续 melee）
_DAMAGE_MEMORY_SECONDS: float = 2.0


class VibeCraftMicroDarkTemplar(MicroStep):  # type: ignore[misc]
    """DT 智能微操：被检测/攻击 → 棱镜或安全位；否则默认 melee。"""

    # 父类期望的 prio_dict（参考 MicroZerglings）—— 留空让 melee_focus_fire 用默认
    prio_dict: ClassVar[dict[UnitTypeId, int]] = {}

    def unit_solve_combat(self, unit: Unit, current_command: Action) -> Action:
        # ---- 检测危险 ----
        if self._in_danger(unit):
            return self._retreat_action(unit, current_command)
        # ---- 默认 melee（用 sharpy 自带 melee_focus_fire 复刻 MicroZerglings 行为）----
        return self._default_melee(unit, current_command)

    # ------------------------------------------------------------------
    # 危险检测
    # ------------------------------------------------------------------

    def _in_danger(self, unit: Unit) -> bool:
        """DT 是否处于"该撤"状态：被探测 OR 最近 2s 受过伤。"""
        # 1. 检查敌方 detector 在 detection range 内
        try:
            for det_type, det_range in DETECTOR_RANGES.items():
                detectors = self.cache.enemy(det_type)
                if not detectors:
                    continue
                # det_range - buffer：detector 还差 ~1 距才看到 DT 时就开撤
                threshold = det_range - DT_DETECTOR_BUFFER
                for det in detectors:
                    if det.distance_to(unit) < threshold:
                        return True
        except Exception:
            pass

        # 2. 检查最近 2s 受过伤
        try:
            damaged_dts = self.knowledge.ai.knowledge.vibecraft.damaged_dts  # type: ignore[attr-defined]
            last_damage = damaged_dts.get(unit.tag, -1000.0)
            if (self.knowledge.ai.time - last_damage) < _DAMAGE_MEMORY_SECONDS:
                return True
        except (AttributeError, KeyError, Exception):
            pass

        return False

    # ------------------------------------------------------------------
    # 撤退动作
    # ------------------------------------------------------------------

    def _retreat_action(self, unit: Unit, current_command: Action) -> Action:
        """优先朝最近 transport 棱镜跑（smart-cast 自动装载），否则朝安全位跑。"""
        prism = self._find_transport_prism(unit)
        if prism is not None:
            # Action(prism, False) = smart-cast 棱镜 = DT 走过去；
            # 距 prism < PRISM_PICKUP_RANGE (5) 时 sc2 自动 load
            logger.debug("DT %d retreat to prism %d", unit.tag, prism.tag)
            return Action(prism, False)

        # 没棱镜：朝远离 detector 的方向跑
        safe = self._safe_pos_outside_detectors(unit)
        return Action(safe, False)

    def _find_transport_prism(self, unit: Unit) -> Unit | None:
        """最近的 transport-mode（非 phasing）Warp Prism。"""
        try:
            prisms = self.knowledge.unit_cache.own(UnitTypeId.WARPPRISM)
        except Exception:
            return None
        if not prisms:
            return None
        return prisms.closest_to(unit)

    def _safe_pos_outside_detectors(self, unit: Unit) -> Point2:
        """计算远离 detector + 朝家方向的安全位（vector sum 推力）。"""
        try:
            home = self.knowledge.ai.start_location
        except (IndexError, AttributeError):
            return unit.position  # 兜底原地

        # 排斥向量：所有 detector 朝 unit 方向加权（距离越近权重越高）
        push_x, push_y = 0.0, 0.0
        try:
            for det_type, det_range in DETECTOR_RANGES.items():
                detectors = self.cache.enemy(det_type)
                for det in detectors:
                    diff = unit.position - det.position
                    d = max(diff.length, 0.1)
                    if d < det_range + 3:
                        # 越近权重越高
                        weight = (det_range + 3 - d) / (det_range + 3)
                        push_x += (diff.x / d) * weight * 5.0
                        push_y += (diff.y / d) * weight * 5.0
        except Exception:
            pass

        # 兜底方向：朝家
        home_dir = home - unit.position
        home_norm = max(home_dir.length, 0.1)
        push_x += (home_dir.x / home_norm) * 3.0
        push_y += (home_dir.y / home_norm) * 3.0

        return Point2((unit.position.x + push_x, unit.position.y + push_y))

    # ------------------------------------------------------------------
    # 默认 melee（复刻 sharpy MicroZerglings 行为）
    # ------------------------------------------------------------------

    def _default_melee(self, unit: Unit, current_command: Action) -> Action:
        """没威胁时跑 melee_focus_fire（DT 撞 worker / 弱单位）。"""
        try:
            enemies = self.cache.enemy_in_range(
                unit.position, unit.radius + unit.ground_range + 1
            ).filter(
                lambda u: not u.is_flying
                and u.type_id not in self.unit_values.combat_ignore
            )
            if enemies:
                current_command = Action(enemies.center, True)
                return self.melee_focus_fire(unit, current_command, self.prio_dict)
        except Exception:
            pass
        return current_command
