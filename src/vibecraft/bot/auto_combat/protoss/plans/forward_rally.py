"""ForwardRallyStalker:把战斗单位拉到 forward PYLON 集结,覆盖 sharpy PlanZoneGather
默认"回家集结"。

存在意义
========
4bg 早压一波的关键 timing:warp 出来的 stalker 应该立刻在 forward PYLON 集结,
等够 4 个就直接出门压制。但 sharpy `PlanZoneGather` 默认把闲置兵拉回家附近 —
新 warp 的 stalker 一进游戏就被命令回家,然后等 4 个齐了再走前线 → 浪费 30s+
路上时间 + 出门 timing 整个推后(用户反馈 2026-05-20)。

放在 SequentialList 中 PlanZoneGather 之后、VibeCraftZoneAttack 之前:
- 后发先至覆盖 ZoneGather 的 home rally 命令
- VibeCraftZoneAttack 触发时,attack 命令再次覆盖本 act 的 move,顺利切到攻击
"""

from __future__ import annotations

import logging
from typing import Any

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

logger = logging.getLogger(__name__)

# 距 forward PYLON 这个距离以内视为"已在前线"— 不重复发 move 命令
_RALLY_RADIUS: float = 12.0
# "forward" 判定:距敌方 < 距家 * 此比率(与 ForwardWarpStalker 同步)
_FORWARD_RATIO: float = 0.7


class ForwardRallyStalker(ActBase):  # type: ignore[misc]
    """每 tick 把闲置 STALKER(默认)拉到 forward PYLON 集结。

    可指定其它单位类型;不指定则只处理 STALKER。已在 attack 状态的兵不动 —
    让 VibeCraftZoneAttack 接管。
    """

    def __init__(self, unit_types: tuple[UnitTypeId, ...] = (UnitTypeId.STALKER,)) -> None:
        super().__init__()
        self.unit_types = unit_types

    async def execute(self) -> bool:
        try:
            home = self.ai.start_location
            enemy = self.ai.enemy_start_locations[0]
        except (IndexError, AttributeError):
            return True  # 地图信息不全 → 退出让默认 ZoneGather 接管

        forward_pylon = self._find_forward_pylon(home, enemy)
        if forward_pylon is None:
            return True  # 没前线 PYLON → 退出,让 ZoneGather 默认行为
        target = forward_pylon.position

        moved = 0
        for unit_type in self.unit_types:
            try:
                units = self.ai.units(unit_type)
            except Exception:
                continue
            for u in units:
                # 已在前线 → 不动
                if u.position.distance_to(target) <= _RALLY_RADIUS:
                    continue
                # 已在攻击中(VibeCraftZoneAttack 接管了) → 不抢
                if getattr(u, "is_attacking", False):
                    continue
                u.move(target)
                moved += 1
        if moved > 0:
            logger.debug(
                "ForwardRallyStalker moved %d units to forward (%.1f, %.1f)",
                moved,
                target.x,
                target.y,
            )
        return False

    def _find_forward_pylon(self, home: Point2, enemy: Point2) -> Any:
        try:
            pylons = self.ai.structures(UnitTypeId.PYLON).ready
        except Exception:
            return None
        for py in pylons:
            d_home = py.distance_to(home)
            d_enemy = py.distance_to(enemy)
            if d_enemy < d_home * _FORWARD_RATIO:
                return py
        return None
