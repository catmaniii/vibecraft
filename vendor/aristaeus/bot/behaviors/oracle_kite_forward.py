from dataclasses import dataclass
from typing import TYPE_CHECKING

from ares import ManagerMediator
from ares.behaviors.combat.individual import CombatIndividualBehavior
from sc2.unit import Unit

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class OracleKiteForward(CombatIndividualBehavior):
    """Custom behavior to keep oracle moving.

    Attributes
    ----------
    unit: Unit
        The unit to shoot.
    target : Unit
        The unit we want to shoot at.
    weapon_ready : bool
    """

    unit: Unit
    target: Unit
    weapon_ready: bool

    def execute(
        self, ai: "AresBot", config: dict, mediator: ManagerMediator, **kwargs
    ) -> bool:
        """Shoot at the target if possible, else kite back.

        Parameters
        ----------
        ai : AresBot
            Bot object that will be running the game
        config :
            Dictionary with the data from the configuration file
        mediator :
            ManagerMediator used for getting information from other managers.
        **kwargs :
            None

        Returns
        -------
        bool :
            CombatBehavior carried out an action.
        """

        if self.weapon_ready:
            # already targeting something, leave oracle alone
            if type(self.unit.order_target) == int:
                return True
            self.unit.attack(self.target)
        else:
            self.unit.move(self.target.position)

        return True
