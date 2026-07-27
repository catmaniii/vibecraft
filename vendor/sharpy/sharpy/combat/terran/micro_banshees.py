# vibecraft: 2026-06-18 新增 Banshee 主动技能 micro(原 sharpy 无)
import logging
import os

from sc2.ids.ability_id import AbilityId
from sc2.unit import Unit
from sharpy.combat import GenericMicro, Action


class MicroBanshees(GenericMicro):
    def __init__(self):
        super().__init__()

    def unit_solve_combat(self, unit: Unit, current_command: Action) -> Action:
        # Cloak 保命：接敌 + 未被探测 + 未已隐形 + 能量 >30（保留撑一会的底线）
        if (
            self.cd_manager.is_ready(unit.tag, AbilityId.BEHAVIOR_CLOAKON_BANSHEE)
            and not unit.is_cloaked
            and unit.energy > 30
            and self.enemies_near_by
        ):
            # 附近有敌方探测器时隐形无意义，跳过
            has_detector = any(
                e.is_detector
                for e in self.cache.enemy_in_range(unit.position, 11)
            )
            if not has_detector:
                if os.environ.get("VIBECRAFT_CASTER_TRACE"):
                    logging.getLogger(__name__).warning(
                        "CASTERTRACE banshee cloak tag=%d e=%.0f", unit.tag, unit.energy
                    )
                return Action(None, False, AbilityId.BEHAVIOR_CLOAKON_BANSHEE)

        # 其余走 GenericMicro 普攻（女妖打地面）
        return super().unit_solve_combat(unit, current_command)
