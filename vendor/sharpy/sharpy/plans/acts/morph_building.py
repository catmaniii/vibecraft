from typing import Set

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit
from .act_base import ActBase


class MorphBuilding(ActBase):
    def __init__(self, building_type: UnitTypeId, ability_type: AbilityId, result_type: UnitTypeId, target_count: int):
        super().__init__()
        self.target_count = target_count
        self.result_type = result_type
        self.ability_type = ability_type
        self.building_type = building_type

    async def execute(self) -> bool:
        target_count = self.cache.own(self.result_type).amount
        start_buildings = self.cache.own(self.building_type).ready.sorted_by_distance_to(
            self.zone_manager.own_main_zone.center_location
        )

        ignore_tags: Set[int] = set()
        for target in start_buildings:  # type: Unit
            if target.orders and target.orders[0].ability.id == self.ability_type:
                target_count += 1
                ignore_tags.add((target.tag))

        if target_count >= self.target_count:
            return True

        for target in start_buildings:
            if target.is_ready and target.tag not in ignore_tags:
                # vibecraft(#582): 只对**空闲** building 尝试/预留升级。CC 正在造 SCV(有 orders)
                # 时根本无法升轨道，若仍 reserve_costs/subtract_cost 会**白占 150 矿**每帧、
                # 叠加其它 priority 预留把科技链(工厂/星港)饿死(真机 bc_rush 工厂晚 85s 的根因)。
                # 忙则跳过：等 CC 空出来(农民满采/产兵间隙)自然升，绝不为升不了的升级占矿。
                if target.orders:
                    continue
                if self.knowledge.can_afford(self.ability_type):
                    target(self.ability_type, subtract_cost=True)
                else:
                    self.knowledge.reserve_costs(self.ability_type)

                target_count += 1

                if target_count >= self.target_count:
                    return True
        if start_buildings:
            return False
        return True
