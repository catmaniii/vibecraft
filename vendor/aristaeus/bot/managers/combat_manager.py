from itertools import cycle
from typing import TYPE_CHECKING

from ares import ManagerMediator
from ares.consts import UnitRole
from ares.managers.manager import Manager
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2

from bot.combat.base_unit import BaseUnit
from bot.combat.tempest_offensive import TempestOffensive

if TYPE_CHECKING:
    from ares import AresBot


class CombatManager(Manager):
    def __init__(
        self,
        ai: "AresBot",
        config: dict,
        mediator: ManagerMediator,
    ) -> None:
        """Handle all main combat logic.

        This manager is incharge of all the main offensive units.
        Combat classes should be called as needed to execute unit control.

        Parameters
        ----------
        ai :
            Bot object that will be running the game
        config :
            Dictionary with the data from the configuration file
        mediator :
            ManagerMediator used for getting information from other managers.
        """
        super().__init__(ai, config, mediator)
        self.expansions_generator = None
        self.current_base_target: Point2 = self.ai.enemy_start_locations[0]
        self.tempest_offensive: BaseUnit = TempestOffensive(ai, config, mediator)

    @property
    def attack_target(self) -> Point2:
        """Quick attack target implementation, improve this later."""
        if self.ai.enemy_structures:
            return self.ai.enemy_structures.closest_to(self.ai.start_location).position
        else:
            # cycle through base locations
            if self.ai.is_visible(self.current_base_target):
                if not self.expansions_generator:
                    base_locations: list[Point2] = [
                        i for i in self.ai.expansion_locations_list
                    ]
                    self.expansions_generator = cycle(base_locations)

                self.current_base_target = next(self.expansions_generator)

            return self.current_base_target

    async def update(self, iteration: int) -> None:
        """This is only currently required to execute tempest micro.

        Parameters
        ----------
        iteration
        """
        if offensive_tempests := self.manager_mediator.get_units_from_role(
            role=UnitRole.ATTACKING, unit_type=UnitID.TEMPEST
        ):
            self.tempest_offensive.execute(
                offensive_tempests, attack_target=self.attack_target
            )
