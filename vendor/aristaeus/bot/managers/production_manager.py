from typing import TYPE_CHECKING

from sc2.unit import Unit

from ares.behaviors.macro import SpawnController, BuildStructure, AutoSupply
from ares.behaviors.macro.macro_plan import MacroPlan
from ares.consts import UnitRole
from cython_extensions.general_utils import cy_unit_pending
from cython_extensions.units_utils import cy_closest_to
from ares.managers.manager import Manager
from ares.managers.manager_mediator import ManagerMediator
from sc2.dicts.upgrade_researched_from import UPGRADE_RESEARCHED_FROM
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.ids.upgrade_id import UpgradeId
from sc2.units import Units

if TYPE_CHECKING:
    from ares import AresBot

# we always want one of each
CORE_STRUCTURES: list[UnitID] = [
    UnitID.GATEWAY,
    UnitID.CYBERNETICSCORE,
    UnitID.STARGATE,
]

DESIRED_UPGRADES: list[UpgradeId] = [
    UpgradeId.TEMPESTGROUNDATTACKUPGRADE,
    UpgradeId.PROTOSSAIRARMORSLEVEL1,
    UpgradeId.PROTOSSAIRARMORSLEVEL2,
]


class ProductionManager(Manager):
    def __init__(
        self,
        ai: "AresBot",
        config: dict,
        mediator: ManagerMediator,
    ) -> None:
        """Set up the manager.

        Parameters
        ----------
        ai :
            Bot object that will be running the game
        config :
            Dictionary with the data from the configuration file
        mediator :
            ManagerMediator used for getting information from other managers.

        Returns
        -------

        """
        super().__init__(ai, config, mediator)

        self._built_single_oracle: bool = False
        self._built_extra_production_pylon: bool = False
        # can use a single chrono for the oracle
        self._oracle_chrono: bool = False

    async def update(self, iteration: int) -> None:
        """Handle production.

        TODO: Add `AutoSupply` when that feature is ready in ares

        Parameters
        ----------
        iteration :
            The game iteration.
        """

        if not self._built_extra_production_pylon:
            self.ai.register_behavior(
                BuildStructure(self.ai.start_location, UnitID.PYLON)
            )
            self._built_extra_production_pylon = True

        # use ares-sc2 macro behaviors for building pylons and units
        macro_plan: MacroPlan = MacroPlan()
        macro_plan.add(AutoSupply(base_location=self.ai.start_location))
        macro_plan.add(
            SpawnController(
                army_composition_dict={
                    UnitID.TEMPEST: {"proportion": 1.0, "priority": 0},
                }
            )
        )
        self.ai.register_behavior(macro_plan)

        # custom behavior for all other production, using ares-sc2 to help
        building_counter: dict[UnitID, int] = self.manager_mediator.get_building_counter
        structures_dict: dict[
            UnitID, list[Unit]
        ] = self.manager_mediator.get_own_structures_dict

        self._build_probes(self.ai.ready_townhalls)
        await self._build_tempest_rush_structures(building_counter, structures_dict)
        self._chrono_structures()
        self._research_upgrades()

        # one off task to build an oracle
        if not self._built_single_oracle:
            if (
                self.ai.can_afford(UnitID.ORACLE)
                and len(structures_dict[UnitID.FLEETBEACON]) > 0
                and self.ai.structures.filter(
                    lambda u: u.type_id == UnitID.STARGATE and u.is_ready and u.is_idle
                )
            ):
                self.ai.train(UnitID.ORACLE)
                self._built_single_oracle = True

    def _structure_present_or_pending(self, structure_type: UnitID) -> bool:
        return (
            len(self.manager_mediator.get_own_structures_dict[structure_type]) > 0
            or self.manager_mediator.get_building_counter[structure_type] > 0
        )

    async def _build_core_structure(self, structure_id: UnitID) -> None:
        """Here to prevent repeated logic building core structures.

        Parameters
        ----------
        structure_id : UnitTypeId
            What we want to build
        """
        if (
            not self._structure_present_or_pending(structure_id)
            and self.ai.tech_requirement_progress(structure_id) >= 1.0
        ):
            self.ai.register_behavior(
                BuildStructure(self.ai.start_location, structure_id)
            )

    def _build_probes(self, ready_townhalls: Units) -> None:
        """Add probes.

        Parameters
        ----------
        ready_townhalls : Units
            Current ready nexuses we can train from.
        """
        if (
            self.ai.supply_workers < 22
            and self.ai.can_afford(UnitID.PROBE)
            and self.ai.supply_left > 0
        ):
            if idle_ths := ready_townhalls.idle:
                for nexus in idle_ths:
                    nexus.train(UnitID.PROBE)

    async def _build_tempest_rush_structures(
        self,
        building_counter: dict[UnitID, int],
        structures_dict: dict[UnitID, list[Unit]],
    ) -> None:
        """Build everything we need towards Tempest tech.

        building_counter : Dict[UnitTypeId, int]
            What is currently pending in the building tracker
        structures_dict : Dict[UnitTypeId, Units]
            Data structure of current buildings.
        """
        max_gas_buildings = 2 if UnitID.GATEWAY in structures_dict else 1
        if (
            self.ai.gas_buildings.amount < max_gas_buildings
            and self.ai.can_afford(UnitID.ASSIMILATOR)
            and building_counter[UnitID.ASSIMILATOR] == 0
        ):
            if worker := self.ai.mediator.select_worker(
                target_position=self.ai.start_location
            ):
                geysers: Units = self.ai.vespene_geyser.filter(
                    lambda vg: not self.ai.gas_buildings.closer_than(2, vg)
                )
                self.ai.mediator.build_with_specific_worker(
                    worker=worker,
                    structure_type=UnitID.ASSIMILATOR,
                    pos=cy_closest_to(self.ai.start_location, geysers),
                )
                self.ai.mediator.assign_role(tag=worker.tag, role=UnitRole.BUILDING)

        ready_pylons: list[Unit] = [
            p for p in structures_dict[UnitID.PYLON] if p.is_ready
        ]
        if not ready_pylons:
            return

        for core_structure_id in CORE_STRUCTURES:
            await self._build_core_structure(core_structure_id)

        # add fleetbeacon separate, since `tech_requirement_progress` doesn't work
        if not self._structure_present_or_pending(UnitID.FLEETBEACON) and [
            s for s in structures_dict[UnitID.STARGATE] if s.is_ready
        ]:
            await self._build_core_structure(UnitID.FLEETBEACON)

    def _chrono_structures(self):
        """Decide what to chrono."""
        stargates: list[Unit] = self.manager_mediator.get_own_structures_dict[
            UnitID.STARGATE
        ]
        for nexus in self.ai.townhalls:
            if nexus.energy >= 50:
                non_idle_stargates = [
                    s
                    for s in stargates
                    if not s.is_idle
                    and not s.has_buff(BuffId.CHRONOBOOSTENERGYCOST)
                    and s.type_id == UnitID.STARGATE
                ]
                if len(non_idle_stargates) > 0:
                    if cy_unit_pending(self.ai, UnitID.TEMPEST):
                        nexus(
                            AbilityId.EFFECT_CHRONOBOOSTENERGYCOST,
                            non_idle_stargates[0],
                        )
                        return
                    if not self._oracle_chrono:
                        nexus(
                            AbilityId.EFFECT_CHRONOBOOSTENERGYCOST,
                            non_idle_stargates[0],
                        )
                        self._oracle_chrono = True

    def _research_upgrades(self):
        """Decide what to research."""
        # only get upgrades if stargate is already building a tempest
        if cy_unit_pending(self.ai, UnitID.TEMPEST) == 0:
            return

        structure_dict: dict[
            UnitID, list[Unit]
        ] = self.manager_mediator.get_own_structures_dict
        for upgrade_id in DESIRED_UPGRADES:
            researched_from: UnitID = UPGRADE_RESEARCHED_FROM[upgrade_id]
            cost = self.ai.calculate_cost(upgrade_id)
            # ensure there is always nearly enough for a tempest
            # before spending all the banked vespene
            if self.ai.vespene - cost.vespene < 160:
                continue
            if (
                self.ai.can_afford(upgrade_id)
                and len([s for s in structure_dict[researched_from] if s.is_idle]) > 0
            ):
                if self.ai.research(upgrade_id):
                    return
