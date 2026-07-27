import warnings

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.buff_id import BuffId
from sc2.unit import Unit, UnitOrder

from sharpy.plans.acts.act_base import ActBase


class ChronoUnit(ActBase):
    # Use Chronoboost on unit production
    def __init__(self, name: UnitTypeId, from_building: UnitTypeId, count: int = 0):
        """
        Chrono boosts unit production.
        @param name: Unit type for which to chronoboost
        @param from_building: Which building to chrono
        @param count: Amount of times to cast chronoboost, use 0 for infinite
        """
        assert name is not None and isinstance(name, UnitTypeId)
        assert from_building is not None and isinstance(from_building, UnitTypeId)

        self.unit_type = name
        self.from_building = from_building
        self.count = count
        self.casted = 0
        super().__init__()

    async def start(self, knowledge: "Knowledge"):
        await super().start(knowledge)
        unit = self.ai._game_data.units[self.unit_type.value]
        self.creation_ability = unit.creation_ability.id

    async def execute(self) -> bool:
        if self.count > 0 and self.casted >= self.count:
            return True

        # vibecraft: 偷矿成长期 Nexus 的能量预留给它自我加速产农民（StealthCellManager
        # cast_chrono_on_nexus 自我加速）。bot 全局 ChronoUnit 既不拿它当**能量源**，也不拿它
        # 当**加速目标**（否则用主矿能量把偷矿基地先 boost 了 → 偷矿 Nexus 自己能量永远用不上、
        # 闲置，玩家观察"星空要塞没用过"）。满采后 Manager 移出预留 → 能量释放回公共池。
        _vc_reserved = getattr(
            getattr(self.knowledge, "vibecraft", None),
            "stealth_chrono_reserved_tags",
            set(),
        )
        for target in self.cache.own(self.from_building).ready:  # type: Unit
            if target.tag in _vc_reserved:
                continue  # 偷矿基地的生产只由它自己的能量加速，bot 不碰
            if target.orders and target.orders[0].ability.id == self.creation_ability:
                # boost here!
                if not target.has_buff(BuffId.CHRONOBOOSTENERGYCOST):
                    for nexus in self.cache.own(UnitTypeId.NEXUS):
                        if nexus.tag in _vc_reserved:
                            continue  # 预留给偷矿自我加速，不当能量源
                        if self.cd_manager.is_ready(nexus.tag, AbilityId.EFFECT_CHRONOBOOSTENERGYCOST):
                            if nexus(AbilityId.EFFECT_CHRONOBOOSTENERGYCOST, target):
                                self.print(f"Chrono {self.creation_ability.name}")
                                self.casted += 1
                                return True
        return True  # Never block
