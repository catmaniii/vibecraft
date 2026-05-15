from dataclasses import dataclass
from typing import TYPE_CHECKING

from ares import ManagerMediator, UnitTreeQueryType
from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import PathUnitToTarget, UseAbility
from cython_extensions.units_utils import cy_closest_to
from ares.dicts.unit_data import UNIT_DATA
from sc2.ids.ability_id import AbilityId
from sc2.units import Units

from bot.combat.base_unit import BaseUnit

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class OracleScout(BaseUnit):
    """Execute behavior for Oracle scout.

    Called from `OracleManager`

    Parameters
    ----------
    ai : AresBot
        Bot object that will be running the game
    config : Dict[Any, Any]
        Dictionary with the data from the configuration file
    mediator : ManagerMediator
        Used for getting information from managers in Ares.
    min_supply_for_revelation : float
        Min close enemy supply to use revelation
    """

    ai: "AresBot"
    config: dict
    mediator: ManagerMediator
    min_supply_for_revelation: float = 4.0

    def execute(self, units: Units, **kwargs) -> None:
        """Execute oracle scout.

        Parameters
        ----------
        units : list[Unit]
            The units we want OracleHarass to control.
        **kwargs :
            See below.

        Keyword Arguments
        -----------------
        scout_target : Point2
            Target on the map that oracle should scout.
        """
        assert (
            "scout_target" in kwargs
        ), "No value for scout_target was passed into kwargs."
        everything_near_oracles: dict[int, Units] = self.mediator.get_units_in_range(
            start_points=units,
            distances=15,
            query_tree=UnitTreeQueryType.AllEnemy,
            return_as_dict=True,
        )

        for unit in units:
            scout_maneuver: CombatManeuver = CombatManeuver()
            enemy_near_oracle: Units = everything_near_oracles[unit.tag]
            supply_close_targets = sum(
                UNIT_DATA[t.type_id]["supply"] for t in enemy_near_oracle
            )

            if (
                AbilityId.ORACLEREVELATION_ORACLEREVELATION in unit.abilities
                and supply_close_targets > self.min_supply_for_revelation
            ):
                scout_maneuver.add(
                    UseAbility(
                        AbilityId.ORACLEREVELATION_ORACLEREVELATION,
                        unit,
                        cy_closest_to(unit.position, enemy_near_oracle).position,
                    )
                )
            else:
                scout_maneuver.add(
                    PathUnitToTarget(
                        unit, self.mediator.get_air_grid, kwargs["scout_target"]
                    )
                )

            self.ai.register_behavior(scout_maneuver)
