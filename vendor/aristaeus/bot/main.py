from typing import Optional

from ares import AresBot, Hub, ManagerMediator
from ares.behaviors.macro import Mining
from ares.consts import UnitRole
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.unit import Unit

from bot.managers.cannon_rush_manager import CannonRushManager
from bot.managers.combat_manager import CombatManager
from bot.managers.oracle_manager import OracleManager
from bot.managers.production_manager import ProductionManager


class MyBot(AresBot):
    cannon_rush_manager: CannonRushManager
    combat_manager: CombatManager
    oracle_manager: OracleManager
    production_manager: ProductionManager

    def __init__(self, game_step_override: Optional[int] = None):
        """Initiate custom bot

        Parameters
        ----------
        game_step_override :
            If provided, set the game_step to this value regardless of how it was
            specified elsewhere
        """
        super().__init__(game_step_override)

    async def on_step(self, iteration: int) -> None:
        await super(MyBot, self).on_step(iteration)

        self.register_behavior(Mining())

        if self.cannon_rush_manager.cannon_rush_complete:
            await self.production_manager.update(iteration)

    def register_managers(self) -> None:
        """
        Override the default `register_managers` in Ares, so we can
        add our own managers.
        """
        manager_mediator = ManagerMediator()
        self.cannon_rush_manager = CannonRushManager(
            self, self.config, manager_mediator
        )
        self.combat_manager = CombatManager(self, self.config, manager_mediator)
        self.oracle_manager = OracleManager(self, self.config, manager_mediator)
        # update this one manually (don't add to ares manager hub)
        self.production_manager = ProductionManager(self, self.config, manager_mediator)

        self.manager_hub = Hub(
            self,
            self.config,
            manager_mediator,
            additional_managers=[
                self.cannon_rush_manager,
                self.combat_manager,
                self.oracle_manager,
            ],
        )

        self.manager_hub.init_managers()

    """
    Can use `python-sc2` hooks as usual, but make a call the inherited method in the superclass
    Examples:
    """

    # async def on_start(self) -> None:
    #     await super(MyBot, self).on_start()
    #
    #     # on_start logic here ...
    #
    # async def on_end(self, game_result: Result) -> None:
    #     await super(MyBot, self).on_end(game_result)
    #
    #     # custom on_end logic here ...
    #
    # async def on_building_construction_complete(self, unit: Unit) -> None:
    #     await super(MyBot, self).on_building_construction_complete(unit)
    #
    #     # custom on_building_construction_complete logic here ...
    #
    async def on_unit_created(self, unit: Unit) -> None:
        await super(MyBot, self).on_unit_created(unit)

        if unit.type_id == UnitID.ORACLE:
            self.mediator.assign_role(tag=unit.tag, role=UnitRole.HARASSING)
            return

        # assign all units to ATTACKING role by default
        if unit.type_id != UnitID.PROBE:
            self.mediator.assign_role(tag=unit.tag, role=UnitRole.ATTACKING)

    async def on_unit_destroyed(self, unit_tag: int) -> None:
        await super(MyBot, self).on_unit_destroyed(unit_tag)

        self.cannon_rush_manager.remove_unit(unit_tag)

    async def on_unit_took_damage(self, unit: Unit, amount_damage_taken: float) -> None:
        await super(MyBot, self).on_unit_took_damage(unit, amount_damage_taken)

        self.oracle_manager.on_unit_took_damage(unit)
