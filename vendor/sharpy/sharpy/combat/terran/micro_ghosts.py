# vibecraft: 2026-06-18 新增 Ghost 主动技能 micro(原 sharpy 无)
import logging
import os
from typing import Optional

from sc2.ids.ability_id import AbilityId
from sc2.unit import Unit
from sc2.units import Units
from sharpy.combat import MicroStep, Action, MoveType


class MicroGhosts(MicroStep):
    def __init__(self):
        super().__init__()

    def group_solve_combat(self, units: Units, current_command: Action) -> Action:
        return current_command

    def unit_solve_combat(self, unit: Unit, current_command: Action) -> Action:
        # 撤退中不施法，跟队即可
        if self.move_type in {MoveType.PanicRetreat, MoveType.DefensiveRetreat}:
            return current_command

        # 不在战斗（最近敌 >14 格）→ 跟队
        closest = self.closest_units.get(unit.tag)
        if not closest or closest.distance_to(unit) > 14:
            return current_command

        # 已在引导 Snipe（约 2s 定身技）→ 不打断自己
        if unit.orders and unit.orders[0].ability.id == AbilityId.EFFECT_GHOSTSNIPE:
            return current_command

        energy = unit.energy

        if energy >= 75:
            # --- EMP 优先（AoE，费 75，砸护盾/能量）---
            if self.cd_manager.is_ready(unit.tag, AbilityId.EMP_EMP):
                best_score = 0
                best_target: Optional[Unit] = None
                for enemy in self.enemies_near_by:
                    if enemy.distance_to(unit) > 10:
                        continue
                    # 含护盾或高能量 caster 才值得 EMP
                    if enemy.shield > 0 or enemy.energy > 50:
                        cluster = self.cache.enemy_in_range(enemy.position, 1.5).amount
                        if cluster > best_score:
                            best_score = cluster
                            best_target = enemy
                if best_target is not None:
                    if os.environ.get("VIBECRAFT_CASTER_TRACE"):
                        logging.getLogger(__name__).warning(
                            "CASTERTRACE ghost emp tag=%d e=%.0f", unit.tag, unit.energy
                        )
                    return Action(best_target.position, False, AbilityId.EMP_EMP)

            # EMP 无合格目标 → 试 Snipe
            if self.cd_manager.is_ready(unit.tag, AbilityId.EFFECT_GHOSTSNIPE):
                target = self._best_snipe_target(unit)
                if target is not None:
                    if os.environ.get("VIBECRAFT_CASTER_TRACE"):
                        logging.getLogger(__name__).warning(
                            "CASTERTRACE ghost snipe tag=%d e=%.0f", unit.tag, unit.energy
                        )
                    return Action(target, False, AbilityId.EFFECT_GHOSTSNIPE)

        elif energy >= 50:
            # --- 只 Snipe（费 50，只打生物）---
            if self.cd_manager.is_ready(unit.tag, AbilityId.EFFECT_GHOSTSNIPE):
                target = self._best_snipe_target(unit)
                if target is not None:
                    if os.environ.get("VIBECRAFT_CASTER_TRACE"):
                        logging.getLogger(__name__).warning(
                            "CASTERTRACE ghost snipe tag=%d e=%.0f", unit.tag, unit.energy
                        )
                    return Action(target, False, AbilityId.EFFECT_GHOSTSNIPE)

        else:
            # --- energy < 50：Cloak 保命 ---
            if (
                self.cd_manager.is_ready(unit.tag, AbilityId.BEHAVIOR_CLOAKON_GHOST)
                and not unit.is_cloaked
                and self.enemies_near_by
            ):
                # 附近有敌方探测器时 cloak 无意义，跳过
                has_detector = any(
                    e.is_detector
                    for e in self.cache.enemy_in_range(unit.position, 11)
                )
                if not has_detector:
                    if os.environ.get("VIBECRAFT_CASTER_TRACE"):
                        logging.getLogger(__name__).warning(
                            "CASTERTRACE ghost cloak tag=%d e=%.0f", unit.tag, unit.energy
                        )
                    return Action(None, False, AbilityId.BEHAVIOR_CLOAKON_GHOST)

        # 所有路径未 cast → 脆皮后撤
        return self.stay_safe(unit)

    def _best_snipe_target(self, unit: Unit) -> Optional[Unit]:
        """优先高价值生物，tag shuffler 防同帧多鬼锁同一目标。"""
        shuffler = unit.tag % 10
        best_score = 0.0
        target: Optional[Unit] = None
        enemy: Unit
        for enemy in self.enemies_near_by:
            if enemy.distance_to(unit) >= 10:
                continue
            if not enemy.is_biological or enemy.is_structure:
                continue
            score = (
                enemy.health
                + self.unit_values.power(enemy) * 50
                + enemy.tag % (shuffler + 2)
            )
            if score > best_score:
                best_score = score
                target = enemy
        return target

    def stay_safe(self, unit: Unit) -> Action:
        """Ghost 脆皮后撤到低地面影响力点。"""
        pos = self.pather.find_weak_influence_ground(unit.position, 5)
        return Action(pos, False)
