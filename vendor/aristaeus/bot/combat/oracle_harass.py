from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from ares import ManagerMediator, UnitTreeQueryType
from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import KeepUnitSafe, PathUnitToTarget, UseAbility
from cython_extensions.combat_utils import cy_pick_enemy_target
from cython_extensions.geometry import cy_distance_to
from cython_extensions.units_utils import cy_closest_to
from ares.dicts.unit_data import UNIT_DATA
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from bot.combat.base_unit import BaseUnit
from bot.behaviors.oracle_kite_forward import OracleKiteForward

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class OracleHarass(BaseUnit):
    """Execute behavior for Oracle harass.

    Called from `OracleManager`

    Parameters
    ----------
    ai : AresBot
        Bot object that will be running the game
    config : Dict[Any, Any]
        Dictionary with the data from the configuration file
    mediator : ManagerMediator
        Used for getting information from managers in Ares.
    min_supply_light_units : float
        Min supply of enemy units before turning weapon on.
    retreat_at_danger_level : float
        If influence at oracle position is this high -> retreat.
    retreat_at_shield_perc : float
        Retreat oracle if shield gets this low.
    turn_pulsar_beam_on_at_energy : float
        Threshold at which oracle can turn it's weapon on.
    """

    ai: "AresBot"
    config: dict
    mediator: ManagerMediator
    min_supply_light_units: float = 4.0
    retreat_at_danger_level: float = 40.0
    retreat_at_shield_perc: float = 0.1
    turn_pulsar_beam_on_at_energy: float = 40.0

    @property
    def safe_spot(self) -> Point2:
        """Get safe spot oracle can retreat to inbetween harass.

        Returns
        -------
        Point2 :
            Safe spot near the middle of the map.
        """
        return self.mediator.find_closest_safe_spot(
            from_pos=self.ai.game_info.map_center, grid=self.mediator.get_air_grid
        )

    def execute(self, units: Units, **kwargs) -> None:
        """Actually execute oracle harass.

        Parameters
        ----------
        units : list[Unit]
            The units we want OracleHarass to control.
        **kwargs :
            See below.

        Keyword Arguments
        -----------------
        oracle_to_weapon_ready : Dict[int, int]
            Key: Oracle tag, Value: frame weapon is ready
        """
        assert (
            "oracle_to_weapon_ready" in kwargs
        ), "No value for oracle_to_weapon_ready was passed into kwargs."

        everything_near_oracles: dict[int, Units] = self.mediator.get_units_in_range(
            start_points=units,
            distances=15,
            query_tree=UnitTreeQueryType.AllEnemy,
            return_as_dict=True,
        )
        air_grid: np.ndarray = self.mediator.get_air_grid
        current_frame: int = self.ai.state.game_loop
        safe_spot: Point2 = self.safe_spot
        oracle_to_weapon_ready: dict[int, int] = kwargs["oracle_to_weapon_ready"]

        for unit in units:
            tag: int = unit.tag
            enemy_near_oracle: Units = everything_near_oracles[tag]
            close_targets: list[Unit] = [
                u for u in enemy_near_oracle if u.is_light and not u.is_flying
            ]
            supply_close_targets = sum(
                UNIT_DATA[t.type_id]["supply"] for t in close_targets
            )
            current_threat_level: float = air_grid[unit.position.rounded]
            shield_perc: float = unit.shield_percentage
            weapon_activated: bool = unit.has_buff(BuffId.ORACLEWEAPON)
            weapon_ready: bool = True
            if tag in oracle_to_weapon_ready:
                weapon_ready = current_frame >= oracle_to_weapon_ready[tag]

            oracle_maneuver: CombatManeuver = CombatManeuver()

            # in danger / low shield / low energy -> retreat
            if (
                shield_perc <= self.retreat_at_shield_perc
                or current_threat_level >= self.retreat_at_danger_level
                or (
                    unit.energy < self.turn_pulsar_beam_on_at_energy
                    and not weapon_activated
                )
            ):
                if weapon_activated:
                    oracle_maneuver.add(
                        UseAbility(AbilityId.BEHAVIOR_PULSARBEAMOFF, unit, None)
                    )
                oracle_maneuver.add(PathUnitToTarget(unit, air_grid, safe_spot, 5.0))
                oracle_maneuver.add(KeepUnitSafe(unit, air_grid))
            # else harass is active
            else:
                # no enemy nearby and weapon is on
                if weapon_activated and len(close_targets) == 0:
                    oracle_maneuver.add(
                        UseAbility(AbilityId.BEHAVIOR_PULSARBEAMOFF, unit, None)
                    )

                # there are enough light enemy units closeby, turn weapon on
                elif (
                    not weapon_activated
                    and supply_close_targets >= self.min_supply_light_units
                ):
                    oracle_maneuver.add(
                        UseAbility(AbilityId.BEHAVIOR_PULSARBEAMON, unit, None)
                    )
                else:
                    # enemy targets are around, handle fight
                    if len(close_targets) > 0 and weapon_activated:
                        oracle_maneuver.add(
                            self._handle_oracle_combat(
                                air_grid, unit, close_targets, weapon_ready
                            )
                        )
                    # no enemy, get to the target safely
                    else:
                        oracle_maneuver.add(
                            PathUnitToTarget(
                                unit, air_grid, self.ai.enemy_start_locations[0], 5.0
                            )
                        )
                        oracle_maneuver.add(KeepUnitSafe(unit, air_grid))

            self.ai.register_behavior(oracle_maneuver)

    def _handle_oracle_combat(
        self,
        air_grid: np.ndarray,
        unit: Unit,
        close_targets: list[Unit],
        weapon_ready: bool,
    ) -> CombatManeuver:
        """We have targets and decided to fight.

        Parameters
        ----------
        air_grid : np.ndarray
            The grid used for pathing and enemy influence.
        unit : Unit
            The oracle we want to control.
        close_targets : List[Unit]
            Units that the oracle can attack.
        weapon_ready : bool
            Is the oracle weapon ready to fire.

        Returns
        -------
        CombatManeuver :
            An `ares-sc2` behavior that can later be registered.
        """
        combat_maneuver: CombatManeuver = CombatManeuver()

        in_attack_range: list[Unit] = [
            u
            for u in close_targets
            if cy_distance_to(unit.position, u.position) < 4.0 + u.radius + unit.radius
        ]

        marines: list[Unit] = [u for u in close_targets if u.type_id == UnitID.MARINE]

        # aggressively attack marines
        if len(marines) > 0:
            enemy_target: Unit = cy_pick_enemy_target(marines)
            combat_maneuver.add(OracleKiteForward(unit, enemy_target, weapon_ready))

        # pick best target from those in range
        elif len(in_attack_range) > 0:
            enemy_target: Unit = cy_pick_enemy_target(in_attack_range)
            combat_maneuver.add(OracleKiteForward(unit, enemy_target, weapon_ready))

        # not yet in range of anything, find the best path to a close target
        else:
            enemy_target: Unit = self._pick_target(unit, close_targets)
            combat_maneuver.add(PathUnitToTarget(unit, air_grid, enemy_target.position))

        return combat_maneuver

    @staticmethod
    def _pick_target(unit: Unit, targets: list[Unit]) -> Unit:
        """If all close targets have same health, pick the closest one.
        Otherwise, pick enemy with the lowest health.

        Parameters
        ----------
        unit : Unit
            The oracle looking for a target.
        targets : list[Unit]
            The targets the oracle can shoot at.

        Returns
        -------
        Unit :
            The thing the oracle should kill.

        """
        target_health: float = targets[0].health + targets[0].shield
        for unit in targets:
            if unit.shield + unit.health != target_health:
                return cy_pick_enemy_target(targets)

        return cy_closest_to(unit.position, targets)
