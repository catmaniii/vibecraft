"""Handle Cannon Rush tasks."""

from typing import Dict, Set, TYPE_CHECKING, Any, Union, List, Optional

import numpy as np
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from ares.behaviors.combat import CombatManeuver
from ares.consts import ManagerName, ManagerRequestType, UnitRole, UnitTreeQueryType
from cython_extensions.units_utils import cy_closest_to, cy_sorted_by_distance_to
from ares.managers.manager import Manager
from ares.managers.manager_mediator import IManagerMediator, ManagerMediator
from bot.tools.cannon_placement import CannonPlacement

from bot.consts import (
    BLOCKING,
    DESIRABILITY_KERNEL,
    FINAL_PLACEMENT,
    INVALID_BLOCK,
    LOCATION,
    POINTS,
    SCORE,
    TYPE_ID,
    WEIGHT,
)
from ares.behaviors.combat.individual import (
    KeepUnitSafe,
    PathUnitToTarget,
    AttackTarget,
    AMove,
)

from ares.managers.path_manager import MapData

if TYPE_CHECKING:
    from ares import AresBot


class CannonRushManager(Manager, IManagerMediator):
    """Handle cannon rush tasks."""

    map_data: MapData
    cannon_placement: CannonPlacement

    def __init__(
        self,
        ai: "AresBot",
        config: Dict,
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
        super(CannonRushManager, self).__init__(ai, config, mediator)

        self.manager_requests_dict = {
            "RegisterCannonRushWorker": lambda kwargs: self.register_cannon_rush_worker(
                **kwargs
            )
        }
        self.cannon_rush_worker_tags: Set[int] = set()
        self.high_ground_pylon_established: bool = False
        self.initial_cannon_placed: bool = False
        self.custom_build_order_complete: bool = False
        self.enemy_main_height: int = self.ai.get_terrain_height(
            self.ai.enemy_start_locations[0]
        )

        # cannon worker roles
        self.cannon_placers = UnitRole.CONTROL_GROUP_ONE
        self.chaos_probes = UnitRole.CONTROL_GROUP_TWO

    async def initialise(self) -> None:
        self.map_data: MapData = self.manager_mediator.get_map_data_object
        self.cannon_placement: CannonPlacement = CannonPlacement(
            self.ai, self.map_data, self.manager_mediator
        )

    def manager_request(
        self,
        receiver: ManagerName,
        request: ManagerRequestType,
        reason: str = None,
        **kwargs,
    ) -> Any:
        """To be implemented by managers that inherit from IManagerMediator interface.

        Parameters
        ----------
        receiver :
            The Manager the request is being sent to.
        request :
            The Manager that made the request
        reason :
            Why the Manager has made the request
        kwargs :
            If the ManagerRequest is calling a function, that function's keyword
            arguments go here.

        Returns
        -------

        """
        return self.manager_requests_dict[request](kwargs)

    async def update(self, _iteration: int) -> None:
        """Update cannon rush status, tasks, and objectives.

        Parameters
        ----------
        _iteration :
            The current game iteration.

        Returns
        -------

        """
        return
        # don't do anything if the build order is still running
        if not self.ai.build_order_runner.build_completed:
            return

        if not self.custom_build_order_complete:
            self.custom_build_order_complete = self.run_custom_build_order()
            grid = self.manager_mediator.get_ground_avoidance_grid
            for probe_tag in self.cannon_rush_worker_tags:
                probe = self.ai.unit_tag_dict[probe_tag]
                # only control the probe if it doesn't have an order, i.e. it's building
                if probe.orders:
                    continue
                maneuver = self.create_path_if_safe_maneuver(
                    unit=probe,
                    grid=grid,
                    target=self.cannon_placement.initial_cannon,
                )
                self.ai.register_behavior(maneuver)
            return

        # update cannon-based calculations
        self.cannon_placement.update()

        # cancel any pylons we don't need
        self.cancel_pylons(self.cannon_placement.initial_cannon)

        # get the units we're rushing with
        worker_units: List[Unit] = self.get_cannon_workers()

        # protocol for getting the first cannon placed
        if not self.initial_cannon_placed:
            self.initial_cannon_placed = self.secure_initial_cannon()
        else:
            self.cause_chaos()

    def _keep_workers_safe(self, units: Union[Units, List[Unit]]):
        grid = self.manager_mediator.get_ground_avoidance_grid
        for probe in units:
            maneuver = self.create_path_if_safe_maneuver(
                unit=probe,
                grid=grid,
                target=self.cannon_placement.initial_cannon,
            )
            self.ai.register_behavior(maneuver)

    def place_building(
        self, next_building: Dict, worker_units: Union[List[Unit], Units]
    ) -> int:
        """Place the next building.

        Parameters
        ----------
        next_building : Dict
            Dictionary containing information about the next building to place.
        worker_units : Units
            Available workers.

        Returns
        -------
        int :
            The tag of the worker that was used.

        """
        sorted_workers = cy_sorted_by_distance_to(
            units=worker_units,
            position=self.cannon_placement.initial_cannon
            if next_building[FINAL_PLACEMENT]
            else next_building[LOCATION],
        )
        used_worker = sorted_workers[0]

        if (
            used_worker.position == self.cannon_placement.initial_cannon
            or self.worker_on_correct_side_of_wall(
                used_worker,
                self.cannon_placement.initial_cannon,
                next_building[LOCATION],
            )
        ):
            # we either don't need to worry about walling ourselves out OR we're on the
            # correct side of the wall
            self.manager_mediator.build_with_specific_worker(
                worker=used_worker,
                structure_type=next_building[TYPE_ID],
                pos=next_building[LOCATION],
                assign_role=False,
            )
        else:
            # move so that we're on the correct side of the wall
            used_worker.move(self.cannon_placement.initial_cannon)
        return used_worker.tag

    def cancel_pylons(self, cannon_location: Point2) -> None:
        """Cancel unnecessary Pylons once one of them has finished.

        Parameters
        ----------
        cannon_location : Point2
            The cannon we're potentially canceling Pylons for.

        Returns
        -------

        """
        # ensure that the cannon position is powered
        if not self.ai.state.psionic_matrix.covers(cannon_location):
            return
        # currently all units, but will get filtered to just pylons
        if nearby_pylons := self.manager_mediator.get_units_in_range(
            start_points=[cannon_location],
            distances=7,
            query_tree=UnitTreeQueryType.AllOwn,
        ):
            nearby_enemies: Dict[int, Units] = self.manager_mediator.get_units_in_range(
                start_points=nearby_pylons[0].filter(
                    lambda u: u.type_id == UnitID.PYLON
                    and self.ai.get_terrain_height(u.position) != self.enemy_main_height
                ),
                distances=5,
                query_tree=UnitTreeQueryType.AllEnemy,
                return_as_dict=True,
            )
            for pylon_tag in nearby_enemies:
                if nearby_enemies[pylon_tag]:
                    continue
                else:
                    pylon = self.ai.unit_tag_dict[pylon_tag]
                    if pylon.build_progress > 0.9:
                        pylon(AbilityId.CANCEL)

    def register_cannon_rush_worker(self, tag: int) -> None:
        """Register a worker as a cannon rush worker.

        Parameters
        ----------
        tag : int
            The tag of the worker to register.

        Returns
        -------

        """
        self.cannon_rush_worker_tags.add(tag)

    def remove_unit(self, unit_tag: int) -> None:
        """Remove a dead unit from tracking.

        Parameters
        ----------
        unit_tag :
            Tag to be removed

        Returns
        -------

        """
        if unit_tag in self.cannon_rush_worker_tags:
            self.cannon_rush_worker_tags.remove(unit_tag)

    def secure_initial_cannon(self) -> bool:
        """Place the first cannon that will be used as our anchor for the cannon rush.

        Parameters
        ----------

        Returns
        -------
        bool :
            Whether this step should be considered completed.

        """
        units_near_placement = self.manager_mediator.get_units_in_range(
            start_points=[self.cannon_placement.initial_cannon],
            distances=2,
            query_tree=UnitTreeQueryType.AllOwn,
        )[0]
        initial_cannons = units_near_placement.filter(
            lambda u: u.type_id == UnitID.PHOTONCANNON
        )

        # there's some type of cannon
        if initial_cannons.amount != 0:
            self.defend_pending_cannon()
            return False

        # no cannons have been placed, time to fix that
        next_building = self.cannon_placement.next_building

        used_worker_tags: Set[int] = set()
        if cannon_workers := self.manager_mediator.get_units_from_role(
            role=self.cannon_placers
        ):
            # place something if we should
            if next_building and self.ai.minerals > 100:
                used_worker_tags.add(self.place_building(next_building, cannon_workers))

        self.cause_chaos()

        # keep unordered probes safe
        self._keep_workers_safe(
            [p for p in cannon_workers if p.tag not in used_worker_tags]
        )

        return False

    def defend_pending_cannon(self) -> None:
        """A cannon has been started but it isn't finished; defend it.

        Parameters
        ----------

        Returns
        -------

        """
        # if cannon placers exist, place Pylons and Cannons near the initial point
        if cannon_placers := self.manager_mediator.get_units_from_role(
            role=self.cannon_placers
        ):
            structure_id: UnitID = UnitID.PHOTONCANNON
            pylons_near_cannon = self.get_pylons_near_point(
                point=self.cannon_placement.initial_cannon,
                distance=7.0,
            )
            healthy_pylons = [
                p for p in pylons_near_cannon if p.health_percentage >= 0.75
            ]
            if not healthy_pylons:
                structure_id = UnitID.PYLON
            locations = np.array(list(self.cannon_placement.invalid_blocks))
            for placer in cannon_placers:
                # do one thing at a time
                if placer.orders:
                    continue
                target_position = Point2(
                    locations[
                        np.argmin(
                            np.sum(
                                (locations - placer.position) ** 2,
                                axis=1,
                            )
                        )
                    ]
                )
                self.manager_mediator.build_with_specific_worker(
                    worker=placer,
                    structure_type=structure_id,
                    pos=target_position,
                    assign_role=False,
                )
        self.cause_chaos()

    def cause_chaos(self) -> None:
        """Cause some chaos in their main.

        Parameters
        ----------

        Returns
        -------

        """
        if chaos_probes := self.manager_mediator.get_units_from_role(
            role=self.chaos_probes,
        ):
            high_ground_target = (
                self.cannon_placement.get_high_ground_point_near_cannon(
                    self.cannon_placement.initial_cannon
                )
            )
            grid = self.manager_mediator.get_ground_avoidance_grid
            building_placed: bool = False
            for probe in chaos_probes:
                if not building_placed and high_ground_target:
                    structure_id = UnitID.PYLON
                    # can't use python-sc2's psionic_matrix.covers because it doesn't
                    # check the height of the pylon
                    if possible_pylons := self.get_pylons_near_point(
                        point=high_ground_target, distance=6.0
                    ):
                        if (
                            possible_pylons.filter(
                                lambda u: u.build_progress == 1.0
                                and self.ai.get_terrain_height(u)
                                == self.enemy_main_height
                                and u.health_percentage >= 0.75
                            ).amount
                            >= 0
                        ):
                            structure_id = UnitID.PHOTONCANNON
                    self.manager_mediator.build_with_specific_worker(
                        worker=probe,
                        structure_type=structure_id,
                        pos=high_ground_target,
                        assign_role=False,
                    )
                    building_placed = True
                    continue
                maneuver: CombatManeuver = CombatManeuver()
                maneuver.add(KeepUnitSafe(unit=probe, grid=grid))
                maneuver.add(
                    AMove(
                        unit=probe,
                        target=high_ground_target.towards_with_random_angle(
                            self.cannon_placement.initial_cannon, distance=3
                        )
                        if high_ground_target
                        else self.ai.enemy_start_locations[0],
                    )
                )
                self.ai.register_behavior(maneuver)

    def get_cannon_workers(self, amount: int = 2) -> Union[Units, List[Unit]]:
        """Get workers for the cannon rush.

        Returns
        -------
        List[Unit] :
            The probes we're rushing with.

        """
        worker_units = [self.ai.unit_tag_dict[t] for t in self.cannon_rush_worker_tags]

        if len(worker_units) < amount:
            # figure out which role this worker needs
            if len(worker_units) < 1:
                target_role = self.cannon_placers
            else:
                target_role = self.chaos_probes
            # steal any idle ones
            if (
                available_workers := self.manager_mediator.get_units_from_role(
                    role=UnitRole.GATHERING
                )
                .filter(lambda u: not u.is_carrying_resource)
                .take(amount - len(worker_units))
            ):
                self.cannon_rush_worker_tags |= available_workers.tags
                worker_units.extend(available_workers)
                self.manager_mediator.batch_assign_role(
                    tags=available_workers.tags,
                    role=target_role,
                )
                for worker in available_workers:
                    self.manager_mediator.remove_worker_from_mineral(
                        worker_tag=worker.tag
                    )
                return worker_units

        return worker_units

    @property
    def cannon_rush_complete(self) -> bool:
        # TODO: rework this as needed
        return True
        # is the cannon done?
        # if len(self.manager_mediator.get_own_structures_dict[UnitID.PHOTONCANNON]) < 1:
        #     return False
        # # enemies near cannon
        # near_cannon_enemies = self.manager_mediator.get_units_in_range(
        #     start_points=[self.cannon_placement.initial_cannon],
        #     distances=7,
        #     query_tree=UnitTreeQueryType.AllEnemy,
        # )[0]
        # if near_cannon_enemies:
        #     return False
        # return True

    def run_custom_build_order(self) -> bool:
        """Run the build order from here rather than the BuildOrderRunner.

        Parameters
        ----------

        Returns
        -------
        bool :
            Whether the build order is completed.

        """
        structure_dict = self.manager_mediator.get_own_structures_dict
        # if the forge is started, we're done with the build order
        if UnitID.FORGE in structure_dict:
            return True

        # get our first pylon at home
        if len(structure_dict[UnitID.PYLON]) == 0:
            if self.ai.minerals >= 25:
                self.build_structure_at_home_ramp(
                    structure_type=UnitID.PYLON,
                    location=list(self.ai.main_base_ramp.corner_depots)[0],
                    amount=1,
                    take_nexus_probe=True,
                )
        else:
            # build workers up to 16
            if self.ai.supply_workers + self.ai.already_pending(UnitID.PROBE) < 16:
                if nexus := structure_dict[UnitID.NEXUS]:
                    if self.ai.minerals >= 50 and nexus[0].is_idle:
                        self.ai.train(UnitID.PROBE)
            # then build the forge
            else:
                if self.ai.minerals >= 75:
                    self.build_structure_at_home_ramp(
                        structure_type=UnitID.FORGE,
                        location=self.ai.main_base_ramp.barracks_in_middle,
                        amount=2,
                    )
        return False

    def build_structure_at_home_ramp(
        self,
        structure_type: UnitID,
        location: Point2,
        amount: int,
        take_nexus_probe: bool = False,
    ) -> None:
        """Construct buildings at the home ramp.

        Parameters
        ----------
        structure_type : UnitID
            Which structure to build.
        location : Point2
            Where to build the structure.
        amount : int
            How many cannon workers we need at this stage.
        take_nexus_probe : bool
            Whether to take the Probe nearest the Nexus, which hopefully just finished

        Returns
        -------

        """
        closest_worker: Optional[Unit] = None
        if take_nexus_probe:
            closest_worker = cy_closest_to(
                self.ai.townhalls.first.position, self.ai.workers
            )
        if worker_container := self.get_cannon_workers(amount=amount):
            closest_worker = cy_closest_to(location, worker_container)
        if closest_worker:
            self.manager_mediator.build_with_specific_worker(
                worker=closest_worker,
                structure_type=structure_type,
                pos=location,
                assign_role=False,
            )

    @staticmethod
    def create_path_if_safe_maneuver(
        unit: Unit, grid: np.ndarray, target: Point2
    ) -> CombatManeuver:
        """CombatManeuver for Probes to moving towards the cannon location if safe.

        Parameters
        ----------
        unit : Unit
            The Probe being micro'd.
        grid : np.ndarray
            Grid to use for pathing.
        target : Point2
            Where the Probe is going.

        Returns
        -------
        CombatManeuver :
            The completed maneuver.

        """
        probe_maneuver: CombatManeuver = CombatManeuver()
        probe_maneuver.add(KeepUnitSafe(unit=unit, grid=grid))
        probe_maneuver.add(PathUnitToTarget(unit=unit, grid=grid, target=target))
        return probe_maneuver

    @staticmethod
    def worker_on_correct_side_of_wall(
        worker: Unit, interior_point: Point2, final_placement: Point2
    ) -> bool:
        """Figure out if the worker is on the correct side of the wall.

        Parameters
        ----------
        worker : Unit
            The worker being checked.
        interior_point : Point2
            Point that's inside the walled off area.
        final_placement : Point2
            Where the last building is going.

        Returns
        -------
        bool :
            Whether the Probe is on the correct side of the wall.

        """
        # see if the worker is in the rectangle with a diagonal of the interior point
        # and final placement
        worker_x, worker_y = worker.position.x, worker.position.y

        x_offset = 0.5 if interior_point.x == final_placement.x else 0.0
        y_offset = 0.5 if interior_point.y == final_placement.y else 0.0

        x_one, x_two = interior_point.x + x_offset, final_placement.x - x_offset
        y_one, y_two = interior_point.y + y_offset, final_placement.y - y_offset

        if x_one > x_two:
            if not x_two < worker_x < x_one:
                return False
        else:
            if not x_one < worker_x < x_two:
                return False

        if y_one > y_two:
            if not y_two < worker_y < y_one:
                return False
        else:
            if not y_one < worker_y < y_two:
                return False

        return True

    def get_pylons_near_point(self, point: Point2, distance: float = 7.0) -> Units:
        """Get Pylons within distance of the point.

        Parameters
        ----------
        point : Point2
            Where the cannon is.
        distance : float
            How far to search for Pylons.

        Returns
        -------
        Units :
            The Pylons near the Cannon.

        """
        if nearby_units := self.manager_mediator.get_units_in_range(
            start_points=[point],
            distances=distance,
            query_tree=UnitTreeQueryType.AllOwn,
        ):
            return Units(
                [u for u in nearby_units[0] if u.type_id == UnitID.PHOTONCANNON],
                self.ai,
            )
