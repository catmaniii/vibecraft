from typing import List

from sc2.position import Point2

from sc2.ids.effect_id import EffectId
from sc2.units import Units

from sharpy.combat import GenericMicro, Action, CombatUnits
from sc2.ids.ability_id import AbilityId
from sc2.unit import Unit
from sharpy.interfaces.combat_manager import MoveType

# vibecraft: 电兵安全 micro 常量（2026-05-29 iac_2base 叉球一波改造）
# 电兵应在大部队后方、与敌保持安全距离放 Storm，不主动 attack 敌方
_VBC_HT_DANGER_RADIUS: float = 15.0  # 15 格内有敌 → 需要后撤
_VBC_HT_SAFE_RADIUS: float = 20.0  # 后撤目标：距离敌人 20 格（安全放 Storm 距离）
_VBC_HT_STORM_RADIUS: float = 9.0  # 心灵风暴施法范围 9 格（原版用 10，留 1 格余量）
_VBC_HT_MIN_STORM_COUNT: int = 4  # Storm 目标区域最少敌人数才放（避免浪费）
_VBC_HT_FEEDBACK_RADIUS: float = 9.0  # Feedback 施法范围 9 格
_VBC_HT_FEEDBACK_MIN_ENERGY: int = 50  # Feedback 目标最少能量（法力反馈消耗 50，目标需 >= 50）


class MicroHighTemplars(GenericMicro):
    def __init__(self):
        super().__init__()
        self.ordered_storms: List[Point2] = []

    def init_group(
        self,
        rules: "MicroRules",
        group: CombatUnits,
        units: Units,
        enemy_groups: List[CombatUnits],
        move_type: MoveType,
        original_target: Point2,
    ):
        super().init_group(rules, group, units, enemy_groups, move_type, original_target)
        self.ordered_storms.clear()

    def unit_solve_combat(self, unit: Unit, current_command: Action) -> Action:
        # vibecraft: 电兵安全 micro — iac_2base 叉球一波专用逻辑
        # 判定是否启用 vibecraft 电兵安全 micro：
        #   knowledge.vibecraft.ht_safe_micro 为 True 时启用
        #   默认 False（不改变其他 plan 使用 MicroHighTemplars 的行为）
        _vibecraft = getattr(self.knowledge, "vibecraft", None)
        _ht_safe = getattr(_vibecraft, "ht_safe_micro", False)

        if _ht_safe:
            return self._vbc_safe_unit_solve(unit, current_command)

        # vibecraft: 以下为 sharpy 原始逻辑（未修改，其他 plan 走此路径）
        if self.cd_manager.is_ready(unit.tag, AbilityId.PSISTORM_PSISTORM):
            stormable_enemies = self.cache.enemy_in_range(unit.position, 10).not_structure
            storms = self.cache.effects(EffectId.PSISTORMPERSISTENT)

            for storm in storms:
                stormable_enemies = stormable_enemies.further_than(3, storm[0])

            for storm in self.ordered_storms:
                stormable_enemies = stormable_enemies.further_than(3, storm)

            if len(stormable_enemies) > 4:
                center = stormable_enemies.center
                target = stormable_enemies.closest_to(center)
                if len(stormable_enemies.closer_than(3, target.position)) > 3:
                    self.ordered_storms.append(target.position)
                    return Action(target.position, False, AbilityId.PSISTORM_PSISTORM)

        if self.cd_manager.is_ready(unit.tag, AbilityId.FEEDBACK_FEEDBACK):
            # vibecraft: Feedback 能量阈值修正：法力反馈消耗 50 能量，目标需 >= 50 才有意义
            # 原始代码 energy > 74 过于保守（漏掉 50-74 能量段的施法者）
            feedback_enemies = self.cache.enemy_in_range(unit.position, 10).filter(
                lambda u: u.energy >= 50 and not u.is_structure
            )
            if feedback_enemies:
                closest = feedback_enemies.closest_to(unit)
                return Action(closest, False, AbilityId.FEEDBACK_FEEDBACK)

        return super().unit_solve_combat(unit, current_command)

    def _vbc_safe_unit_solve(self, unit: Unit, current_command: Action) -> Action:
        """vibecraft 电兵安全 micro：保持安全距离放 Storm/Feedback，不主动 attack 敌人。

        行为优先级（从高到低）：
          1. Feedback 范围内有法术施法者（enemy energy >= 50）→ 优先放 Feedback（即使有近敌）
             法力反馈是瞬发，释放后无论如何都要后撤，所以放完就走是最优操作。
          2. 近敌威胁（< 15 格）→ 后撤到安全距离（保命）
          3. energy >= 75 + 敌人密集（9 格内 >= 4 个）→ 放 Psi Storm
          4. 默认 → 跟随大部队中心移动（不 attack，不冲前线）

        Feedback 设计说明（2026-05-29 修复）：
          - Feedback 目标 = 敌方有能量的法术单位（energy >= 50；Ghost/Raven/Viper/
            Infestor/Queen/OtherHT/Sentry/Oracle 等），energy=0 普通战斗单位不触发。
          - Feedback 施法范围 9 格；在 _VBC_HT_FEEDBACK_RADIUS 内有目标才 cast。
          - 放完 Feedback 后如仍有近敌，下一帧会走步骤 2 后撤 — 不用在此等。
          - 不设 is_structure 过滤：建筑没有 energy（除 Pylon 等），energy >= 50 已足够筛出法师。
        """
        # vibecraft: 步骤 1 — Feedback（法力反馈优先）：发现法师立刻放，不等退出威胁区
        if self.cd_manager.is_ready(unit.tag, AbilityId.FEEDBACK_FEEDBACK):
            feedback_targets = self.cache.enemy_in_range(
                unit.position, _VBC_HT_FEEDBACK_RADIUS
            ).filter(lambda u: u.energy >= _VBC_HT_FEEDBACK_MIN_ENERGY and not u.is_structure)
            if feedback_targets:
                closest_caster = feedback_targets.closest_to(unit)
                return Action(closest_caster, False, AbilityId.FEEDBACK_FEEDBACK)

        # vibecraft: 步骤 2 — 近敌威胁检测，优先保命后撤（不 attack）
        threats = self.cache.enemy_in_range(unit.position, _VBC_HT_DANGER_RADIUS).not_structure
        if threats.amount > 0:
            # 找最近威胁，朝反方向后撤到安全距离
            closest_threat = threats.closest_to(unit)
            # 后撤方向：从威胁中心往己方方向走，目标 20 格外
            retreat_pos: Point2 = unit.position.towards(closest_threat.position, -5)
            retreat_pos = self.pather.find_weak_influence_ground(retreat_pos, 4)
            return Action(retreat_pos, False)

        # vibecraft: 步骤 3 — 安全距离内放 Psi Storm（15-25 格，敌人密集目标）
        if unit.energy >= 75 and self.cd_manager.is_ready(unit.tag, AbilityId.PSISTORM_PSISTORM):
            stormable_enemies = self.cache.enemy_in_range(
                unit.position, _VBC_HT_STORM_RADIUS + _VBC_HT_DANGER_RADIUS
            ).not_structure
            # 排除已有 Storm 覆盖的区域（避免浪费）
            storms = self.cache.effects(EffectId.PSISTORMPERSISTENT)
            for storm in storms:
                stormable_enemies = stormable_enemies.further_than(3, storm[0])
            for storm in self.ordered_storms:
                stormable_enemies = stormable_enemies.further_than(3, storm)

            if stormable_enemies.amount >= _VBC_HT_MIN_STORM_COUNT:
                center = stormable_enemies.center
                target = stormable_enemies.closest_to(center)
                if (
                    stormable_enemies.closer_than(3, target.position).amount
                    >= _VBC_HT_MIN_STORM_COUNT
                ):
                    self.ordered_storms.append(target.position)
                    return Action(target.position, False, AbilityId.PSISTORM_PSISTORM)

        # vibecraft: 步骤 4 — 默认：跟随大部队中心（不 attack，保持队形）
        # 不调用 super().unit_solve_combat 避免 GenericMicro 触发 attack move
        rally_pos = self.group.center
        if unit.distance_to(rally_pos) > 3:
            pos = self.pather.find_influence_ground_path(unit.position, rally_pos, 4)
            return Action(pos, False)
        # 已在大部队中心附近，原地待命（move 到自身位置避免空闲被 sharpy 乱派）
        return Action(unit.position, False)
