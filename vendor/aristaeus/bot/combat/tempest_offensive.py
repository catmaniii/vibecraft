from dataclasses import dataclass
from typing import TYPE_CHECKING

from ares import ManagerMediator, UnitTreeQueryType
from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import PathUnitToTarget, StutterUnitBack
from cython_extensions.combat_utils import cy_pick_enemy_target
from cython_extensions.units_utils import cy_in_attack_range
from sc2.unit import Unit
from sc2.units import Units

from bot.combat.base_unit import BaseUnit

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class TempestOffensive(BaseUnit):
    """Execute behavior for Tempest offensive attack.

    Called from `CombatManager`

    Parameters
    ----------
    ai : AresBot
        Bot object that will be running the game
    config : Dict[Any, Any]
        Dictionary with the data from the configuration file
    mediator : ManagerMediator
        Used for getting information from managers in Ares.
    """

    ai: "AresBot"
    config: dict
    mediator: ManagerMediator

    def execute(self, units: Units, **kwargs) -> None:
        """Actually execute tempest attack.

        Parameters
        ----------
        units : list[Unit]
            The units we want OracleHarass to control.
        **kwargs :
            See below.

        Keyword Arguments
        -----------------
        attack_target : Point2
            Point on the map Tempest should head towards.
        """

        assert (
            "attack_target" in kwargs
        ), "No value for scout_target was passed into kwargs."
        everything_near_tempests: dict[int, Units] = self.mediator.get_units_in_range(
            start_points=units,
            distances=15,
            query_tree=UnitTreeQueryType.AllEnemy,
            return_as_dict=True,
        )

        for unit in units:
            offensive_maneuver: CombatManeuver = CombatManeuver()

            enemy_near_tempest: Units = everything_near_tempests[unit.tag].filter(
                lambda u: not u.is_memory
            )

            in_attack_range: list[Unit] = cy_in_attack_range(unit, enemy_near_tempest)

            if len(in_attack_range) > 0:
                target: Unit = cy_pick_enemy_target(in_attack_range)
                offensive_maneuver.add(
                    StutterUnitBack(unit, target, True, self.mediator.get_air_grid)
                )

            elif enemy_near_tempest:
                target: Unit = cy_pick_enemy_target(enemy_near_tempest)
                offensive_maneuver.add(
                    StutterUnitBack(unit, target, True, self.mediator.get_air_grid)
                )
            else:
                offensive_maneuver.add(
                    PathUnitToTarget(
                        unit, self.mediator.get_air_grid, kwargs["attack_target"]
                    )
                )

            self.ai.register_behavior(offensive_maneuver)
