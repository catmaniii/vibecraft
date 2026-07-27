from abc import ABC, abstractmethod

from sc2.constants import EQUIVALENTS_FOR_TECH_PROGRESS
from sc2.data import Race
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit import Unit

from sharpy.general.component import Component
from sharpy.interfaces import ILostUnitsManager
from sharpy.managers.core.roles import UnitTask

build_commands = {
    # Protoss
    AbilityId.PROTOSSBUILD_NEXUS,
    AbilityId.PROTOSSBUILD_PYLON,
    AbilityId.PROTOSSBUILD_GATEWAY,
    AbilityId.PROTOSSBUILD_ASSIMILATOR,
    AbilityId.PROTOSSBUILD_CYBERNETICSCORE,
    AbilityId.PROTOSSBUILD_FORGE,
    AbilityId.PROTOSSBUILD_PHOTONCANNON,
    AbilityId.BUILD_SHIELDBATTERY,
    AbilityId.PROTOSSBUILD_STARGATE,
    AbilityId.PROTOSSBUILD_FLEETBEACON,
    AbilityId.PROTOSSBUILD_TWILIGHTCOUNCIL,
    AbilityId.PROTOSSBUILD_TEMPLARARCHIVE,
    AbilityId.PROTOSSBUILD_DARKSHRINE,
    AbilityId.PROTOSSBUILD_ROBOTICSFACILITY,
    AbilityId.PROTOSSBUILD_ROBOTICSBAY,
    # Terran
    AbilityId.TERRANBUILD_COMMANDCENTER,
    AbilityId.TERRANBUILD_SUPPLYDEPOT,
    AbilityId.TERRANBUILD_BARRACKS,
    AbilityId.TERRANBUILD_REFINERY,
    AbilityId.TERRANBUILD_ENGINEERINGBAY,
    AbilityId.TERRANBUILD_FACTORY,
    AbilityId.TERRANBUILD_ARMORY,
    AbilityId.TERRANBUILD_MISSILETURRET,
    AbilityId.TERRANBUILD_BUNKER,
    AbilityId.TERRANBUILD_SENSORTOWER,
    AbilityId.TERRANBUILD_GHOSTACADEMY,
    AbilityId.TERRANBUILD_STARPORT,
    AbilityId.TERRANBUILD_FUSIONCORE,
    # Zerg
    AbilityId.ZERGBUILD_BANELINGNEST,
    AbilityId.ZERGBUILD_EVOLUTIONCHAMBER,
    AbilityId.ZERGBUILD_EXTRACTOR,
    AbilityId.ZERGBUILD_HATCHERY,
    AbilityId.ZERGBUILD_HYDRALISKDEN,
    AbilityId.ZERGBUILD_INFESTATIONPIT,
    AbilityId.ZERGBUILD_NYDUSNETWORK,
    AbilityId.ZERGBUILD_ROACHWARREN,
    AbilityId.ZERGBUILD_SPAWNINGPOOL,
    AbilityId.ZERGBUILD_SPINECRAWLER,
    AbilityId.ZERGBUILD_SPIRE,
    AbilityId.ZERGBUILD_SPORECRAWLER,
    AbilityId.ZERGBUILD_ULTRALISKCAVERN,
}


class ActBase(Component, ABC):
    lost_units_manager: ILostUnitsManager

    async def debug_draw(self):
        if self.debug:
            await self.debug_actions()

    async def start(self, knowledge: "Knowledge"):
        await super().start(knowledge)
        self.lost_units_manager = self.knowledge.get_required_manager(ILostUnitsManager)

    async def debug_actions(self):
        pass

    @abstractmethod
    async def execute(self) -> bool:
        """Return True when the act is complete and execution can continue to the next act.
        Return False if you want to block execution and not continue to the next act."""
        pass

    def pending_build(self, unit_type: UnitTypeId) -> float:
        """Only counts buildings that are commanded to be built, not ready builds are not included"""
        return self.get_count(unit_type) - self.get_count(unit_type, include_pending=False)

    def pending_building_positions(self, unit_type: UnitTypeId) -> list[Point2]:
        """Returns positions of buildings of the specified type that have either been ordered to be built by a worker
        or are currently being built."""
        positions: list[Point2] = list()
        creation_ability: AbilityId = self.ai._game_data.units[unit_type.value].creation_ability

        # Workers ordered to build
        for worker in self.ai.workers:  # type: Unit
            for order in worker.orders:  # type: UnitOrder
                if order.ability.id == creation_ability.id:
                    p2: Point2 = Point2.from_proto(order.target)
                    positions.append(p2)

        # Already building structures
        # Avoid counting structures twice for Terran SCVs.
        if self.knowledge.my_race != Race.Terran:
            pending_buildings: list[Point2] = list(
                map(lambda structure: structure.position, self.cache.own(unit_type).structure.not_ready)
            )
            positions.extend(pending_buildings)

        return positions

    def unit_pending_count(self, unit_type: UnitTypeId) -> float:
        return self.ai.already_pending(unit_type)

    def building_progress(self, pre_type: UnitTypeId):
        percentage = 0

        if pre_type == UnitTypeId.SUPPLYDEPOT:
            types = [UnitTypeId.SUPPLYDEPOT, UnitTypeId.SUPPLYDEPOTDROP, UnitTypeId.SUPPLYDEPOTLOWERED]
        else:
            types = pre_type

        for unit in self.cache.own(types):
            if unit.is_ready:
                return 0
            percentage = max(percentage, unit.build_progress)

        if percentage == 0:
            return 1000

        return self.unit_values.build_time(pre_type) * (1 - percentage)

    def has_build_order(self, worker: Unit):
        if worker.orders:
            for orders in worker.orders:
                if orders.ability.id in build_commands:
                    return True
        return False

    def get_ordered_count(self, unit_type: UnitTypeId):
        return self.get_count(unit_type, include_pending=True) - self.get_count(unit_type, include_pending=False)

    def get_count(
        self, unit_type: UnitTypeId, include_pending=True, include_killed=False, include_not_ready: bool = True
    ) -> int:
        """Calculates how many buildings there are already, including pending structures."""
        # vibecraft: Gateway/Warpgate/Hatchery/Lair/Hive/CC/OC/PF/Spire/GreaterSpire 同质化
        # 防止 GATEWAY→WARPGATE 升级后 count=0 导致 GridBuilding plan 重复触发造新 BG。
        # 覆盖 EQUIVALENTS_FOR_TECH_PROGRESS 已有逻辑的所有 building-chain 组合。
        _VBC_EQUIVALENTS = {
            UnitTypeId.GATEWAY: [UnitTypeId.GATEWAY, UnitTypeId.WARPGATE],
            UnitTypeId.HATCHERY: [UnitTypeId.HATCHERY, UnitTypeId.LAIR, UnitTypeId.HIVE],
            UnitTypeId.LAIR: [UnitTypeId.LAIR, UnitTypeId.HIVE],
            UnitTypeId.COMMANDCENTER: [
                UnitTypeId.COMMANDCENTER,
                UnitTypeId.ORBITALCOMMAND,
                UnitTypeId.PLANETARYFORTRESS,
            ],
            UnitTypeId.SPIRE: [UnitTypeId.SPIRE, UnitTypeId.GREATERSPIRE],
        }
        types_to_count = _VBC_EQUIVALENTS.get(unit_type, [unit_type])

        count = 0

        if include_not_ready and include_pending:
            count += sum(self.unit_pending_count(t) for t in types_to_count)
            count += sum(self.cache.own(t).ready.amount for t in types_to_count)
        elif include_not_ready and not include_pending:
            count += sum(self.cache.own(t).amount for t in types_to_count)
        elif not include_not_ready and include_pending:
            count += sum(self.unit_pending_count(t) for t in types_to_count)
            count += sum(
                self.cache.own(t).amount - self.cache.own(t).not_ready.amount
                for t in types_to_count
            )
        else:
            # Only and only ready
            count += sum(self.cache.own(t).ready.amount for t in types_to_count)

        # related_count 仅在 unit_type 不被 _VBC_EQUIVALENTS 覆盖时调用,
        # 防止等价体被双重计数(上面 sum 已经覆盖了 _VBC_EQUIVALENTS 里的所有类型)。
        if unit_type not in _VBC_EQUIVALENTS:
            count = self.related_count(count, unit_type)

        if include_killed:
            count += self.lost_units_manager.own_lost_type(unit_type, real_type=False)
            related = EQUIVALENTS_FOR_TECH_PROGRESS.get(unit_type, None)
            if related:
                for related_type in related:
                    count += self.lost_units_manager.own_lost_type(related_type, real_type=False)

        return count

    def related_count(self, count, unit_type):
        if unit_type in EQUIVALENTS_FOR_TECH_PROGRESS:
            count += self.cache.own(EQUIVALENTS_FOR_TECH_PROGRESS[unit_type]).amount
        return count

    def get_worker_builder(
        self, position: Point2, priority_tag: int | None, only_roles: list[UnitTask] | None = None
    ) -> Unit | None:
        """
        Gets best worker to build in the selected location.
        Priorities:
        1. Existing worker with the current priority_tag
        2. For Protoss, other builders, long distance Proxy builders should be in UnitTask.Reserved
        3. Idle workers
        4. Workers returning to base from building
        5. Workers mining minerals
        6. Workers mining gas (Pulling workers out of mining gas messes up timings for optimal harvesting)

        @param position: location on where we want to build something
        @param priority_tag: Worker tag that has been used here before
        @param only_roles: If worker tag is not in these roles, then pick up another worker.
        Useful if another act kidnaps worker
        @return: Worker if one was found
        """

        worker: Unit | None = None
        if priority_tag is not None:
            worker: Unit = self.cache.by_tag(priority_tag)
            if worker is None or worker.is_constructing_scv or self.roles.unit_role(worker) != UnitTask.Building:
                # Worker is probably dead or it is already building something else.
                worker = None

        if (
            only_roles
            and worker is not None
            and (self.roles.unit_role(worker) not in only_roles or worker.is_constructing_scv)
        ):
            # Worker is probably taken by some other act or it is already building something else.
            worker = None

        if worker is None:
            workers = self.ai.workers.filter(
                lambda w: not w.has_buff(BuffId.ORACLESTASISTRAPTARGET) and not w.is_constructing_scv
            ).sorted_by_distance_to(position)
            if not workers:
                return None

            def sort_method(unit: Unit):
                role = self.roles.unit_role(unit)
                # if self.knowledge.my_race == Race.Protoss and role == UnitTask.Building:
                #     return 0

                if role == UnitTask.Idle:
                    return 1

                if role == UnitTask.Gathering:
                    if unit.is_gathering and isinstance(unit.order_target, int):
                        target = self.cache.by_tag(unit.order_target)
                        if target and target.is_mineral_field:
                            return 2
                        return 4
                    if unit.is_carrying_vespene:
                        return 5
                    if unit.is_carrying_minerals:
                        return 3
                    return 3
                return 10

            workers.sort(key=sort_method)

            worker = workers.first

        return worker

    async def build(
        self,
        type_id: UnitTypeId,
        near: Point2,
        max_distance: int = 20,
        build_worker: Unit | None = None,
        random_alternative: bool = True,
        placement_step: int = 2,
    ) -> bool:
        if build_worker is None:
            build_worker = self.get_worker_builder(near, 0)

        pos = await self.ai.find_placement(
            type_id,
            near,
            max_distance=max_distance,
            random_alternative=random_alternative,
            placement_step=placement_step,
        )
        if pos and build_worker:
            build_worker.build(type_id, pos)
            return True
        return False
