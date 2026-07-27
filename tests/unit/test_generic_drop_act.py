"""GenericDropAct state machine 单测(mock prism + units)。"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _fake_sharpy():
    """generic_drop_act 顶层 import 了 sharpy.plans.acts.ActBase —— 注入 fake 让 import 过。"""
    created = []
    for name in (
        "sharpy",
        "sharpy.plans",
        "sharpy.plans.acts",
        "sharpy.managers",
        "sharpy.managers.core",
        "sharpy.managers.core.roles",
    ):
        if name not in sys.modules:
            sys.modules[name] = ModuleType(name)
            created.append(name)
    acts = sys.modules["sharpy.plans.acts"]
    if not hasattr(acts, "ActBase"):
        acts.ActBase = type("ActBase", (), {"__init__": lambda self: None})  # type: ignore[attr-defined]
    roles_mod = sys.modules["sharpy.managers.core.roles"]
    if not hasattr(roles_mod, "UnitTask"):
        roles_mod.UnitTask = type("UnitTask", (), {"Reserved": 1, "Idle": 2})()  # type: ignore[attr-defined]
    yield
    sys.modules.pop("vibecraft.bot.auto_combat.protoss.plans.generic_drop_act", None)
    for name in created:
        sys.modules.pop(name, None)


def _mock_position(x: float, y: float):
    from sc2.position import Point2

    return Point2((x, y))


@pytest.fixture
def drop_target():
    from vibecraft.bot.named_spot import DropTarget

    return DropTarget(
        position=_mock_position(48, 28),
        zone_kind="mineral",
        base_index=0,
        source_spec="enemy_main:mineral",
    )


def _mock_bot_with_prism_and_zealots(prism_pos, zealot_count: int):
    """bot 含 1 棱镜 + N 叉子在家附近。"""
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.position import Point2

    bot = MagicMock()
    bot.start_location = Point2((127, 119))
    bot.time = 100.0
    bot.knowledge.zone_manager.enemy_expansion_zones = []
    bot.game_info.map_center = Point2((80, 80))
    bot.game_info.playable_area = MagicMock(x=0, y=0, width=160, height=160)

    prism = MagicMock()
    prism.type_id = UnitTypeId.WARPPRISM
    prism.position = prism_pos
    prism.cargo_used = 0
    prism.health = 100
    prism.shield = 50
    prism.tag = 1
    prism.distance_to = lambda p: (
        ((prism.position.x - p.x) ** 2 + (prism.position.y - p.y) ** 2) ** 0.5
    )

    zealots = []
    for i in range(zealot_count):
        z = MagicMock()
        z.type_id = UnitTypeId.ZEALOT
        z.position = Point2((127 + i, 119))
        z.tag = 100 + i
        zealots.append(z)

    def _units_of_type(types):
        m = MagicMock()
        result = []
        if UnitTypeId.WARPPRISM in types or UnitTypeId.WARPPRISMPHASING in types:
            result.append(prism)
        m.__iter__ = lambda self: iter(result)
        m.__bool__ = lambda self: len(result) > 0
        m.amount = len(result)
        return m

    # for ai.units(UnitTypeId.ZEALOT) call-style usage
    def _units_call(t):
        if t == UnitTypeId.ZEALOT:
            m = MagicMock()
            m.__iter__ = lambda self: iter(zealots)
            m.__bool__ = lambda self: len(zealots) > 0
            m.amount = len(zealots)
            m.ready = zealots
            return m
        m = MagicMock()
        m.__iter__ = lambda self: iter([])
        m.__bool__ = lambda self: False
        m.amount = 0
        m.ready = []
        return m

    bot.units = _units_call
    # also need of_type for _find_transport
    bot.units.of_type = _units_of_type
    # Make bot.units callable AND have of_type
    units_callable = _units_call
    units_callable.of_type = _units_of_type
    bot.units = units_callable

    return bot, prism, zealots


class TestGenericDropActInit:
    def test_initial_state_idle(self, drop_target):
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.auto_combat.protoss.plans.generic_drop_act import (
            GenericDropAct,
            GenericDropState,
        )

        act = GenericDropAct(
            cargo_unit=UnitTypeId.ZEALOT,
            cargo_count=4,
            transport=UnitTypeId.WARPPRISM,
            drop_target=drop_target,
            after_unload="attack_workers",
        )
        assert act._state == GenericDropState.IDLE

    def test_params_stored(self, drop_target):
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.auto_combat.protoss.plans.generic_drop_act import GenericDropAct

        act = GenericDropAct(
            cargo_unit=UnitTypeId.ZEALOT,
            cargo_count=4,
            transport=UnitTypeId.WARPPRISM,
            drop_target=drop_target,
            after_unload="retreat",
        )
        assert act.cargo_unit == UnitTypeId.ZEALOT
        assert act.cargo_count == 4
        assert act.transport == UnitTypeId.WARPPRISM
        assert act.drop_target is drop_target
        assert act.after_unload == "retreat"
        assert act._waypoints is None
        assert act._wp_idx == 0
        assert act._transport_tag is None
        assert act._loading_since is None


class TestGenericDropActLoadAtHome:
    """LOAD_AT_HOME 阶段:smart-cast cargo 上船,装齐后切 FLY_TO_DROP。"""

    @pytest.mark.asyncio
    async def test_load_smart_cast_zealots_onto_prism(self, drop_target):
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.auto_combat.protoss.plans.generic_drop_act import (
            GenericDropAct,
            GenericDropState,
        )

        bot, prism, zealots = _mock_bot_with_prism_and_zealots(
            prism_pos=_mock_position(127, 119), zealot_count=4
        )
        act = GenericDropAct(
            cargo_unit=UnitTypeId.ZEALOT,
            cargo_count=4,
            transport=UnitTypeId.WARPPRISM,
            drop_target=drop_target,
            after_unload="attack_workers",
        )
        # 手动注入 sharpy ActBase 字段
        act.ai = bot
        act.knowledge = MagicMock()
        act.knowledge.roles.set_task = MagicMock()
        act.cache = MagicMock()

        # _find_transport 需要 of_type to work
        prism_container = MagicMock()
        prism_container.__iter__ = lambda self: iter([prism])
        prism_container.__bool__ = lambda self: True
        prism_container.amount = 1
        bot.units.of_type = lambda types: prism_container

        # 第一帧 execute → 切 LOAD_AT_HOME + smart-cast
        await act.execute()

        # 状态应已转变到 LOAD_AT_HOME (IDLE -> LOAD_AT_HOME)
        assert act._state == GenericDropState.LOAD_AT_HOME

        # 每个 zealot 应被 smart(prism)
        for z in zealots:
            z.smart.assert_called_with(prism)

    @pytest.mark.asyncio
    async def test_transitions_to_fly_when_cargo_full(self, drop_target):
        """cargo_used = cargo_count 且无散落 cargo_unit → 切 FLY_TO_DROP。"""
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.auto_combat.protoss.plans.generic_drop_act import (
            GenericDropAct,
            GenericDropState,
        )

        bot, prism, _zealots = _mock_bot_with_prism_and_zealots(
            prism_pos=_mock_position(127, 119),
            zealot_count=0,  # 0 散落叉子
        )
        # cargo_used = 4(全上船了)
        prism.cargo_used = 4

        act = GenericDropAct(
            cargo_unit=UnitTypeId.ZEALOT,
            cargo_count=4,
            transport=UnitTypeId.WARPPRISM,
            drop_target=drop_target,
        )
        act.ai = bot
        act.ai.time = 200.0  # far from loading_since; simulate loaded state
        act.knowledge = MagicMock()
        act.knowledge.roles.set_task = MagicMock()
        act.cache = MagicMock()
        # set loading_since so timeout check works
        act._loading_since = 100.0

        prism_container = MagicMock()
        prism_container.__iter__ = lambda self: iter([prism])
        prism_container.__bool__ = lambda self: True
        prism_container.amount = 1
        bot.units.of_type = lambda types: prism_container

        # First call: IDLE -> LOAD_AT_HOME
        await act.execute()
        # cargo_used=4, no scattered units, should go to FLY_TO_DROP
        assert act._state == GenericDropState.FLY_TO_DROP


class TestGenericDropActFlyToDrop:
    """FLY_TO_DROP 阶段:用 plan_drop_path 拿 waypoints,顺序飞。"""

    @pytest.mark.asyncio
    async def test_fly_to_drop_moves_prism(self, drop_target):
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.auto_combat.protoss.plans.generic_drop_act import (
            GenericDropAct,
            GenericDropState,
        )

        bot, prism, _ = _mock_bot_with_prism_and_zealots(
            prism_pos=_mock_position(127, 119), zealot_count=0
        )
        prism.cargo_used = 4

        act = GenericDropAct(
            cargo_unit=UnitTypeId.ZEALOT,
            cargo_count=4,
            transport=UnitTypeId.WARPPRISM,
            drop_target=drop_target,
        )
        act.ai = bot
        act.knowledge = MagicMock()
        act._state = GenericDropState.FLY_TO_DROP

        prism_container = MagicMock()
        prism_container.__iter__ = lambda self: iter([prism])
        prism_container.__bool__ = lambda self: True
        prism_container.amount = 1
        bot.units.of_type = lambda types: prism_container

        await act.execute()

        # prism.move 应被调用(飞向 waypoint 或 drop_target)
        prism.move.assert_called()


class TestGenericDropActUnload:
    """UNLOAD 阶段:调 UNLOADALLAT,卸空后切 HOVER_FINAL。"""

    @pytest.mark.asyncio
    async def test_unload_calls_ability(self, drop_target):
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.auto_combat.protoss.plans.generic_drop_act import (
            GenericDropAct,
            GenericDropState,
        )

        bot, prism, _ = _mock_bot_with_prism_and_zealots(
            prism_pos=_mock_position(48, 28), zealot_count=0
        )
        prism.cargo_used = 4

        act = GenericDropAct(
            cargo_unit=UnitTypeId.ZEALOT,
            cargo_count=4,
            transport=UnitTypeId.WARPPRISM,
            drop_target=drop_target,
        )
        act.ai = bot
        act.knowledge = MagicMock()
        act._state = GenericDropState.UNLOAD

        prism_container = MagicMock()
        prism_container.__iter__ = lambda self: iter([prism])
        prism_container.__bool__ = lambda self: True
        prism_container.amount = 1
        bot.units.of_type = lambda types: prism_container

        await act.execute()

        # 应调用了 prism(AbilityId.UNLOADALLAT_WARPPRISM, ...)
        prism.assert_called()

    @pytest.mark.asyncio
    async def test_transitions_to_hover_when_cargo_empty(self, drop_target):
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.auto_combat.protoss.plans.generic_drop_act import (
            GenericDropAct,
            GenericDropState,
        )

        bot, prism, _ = _mock_bot_with_prism_and_zealots(
            prism_pos=_mock_position(48, 28), zealot_count=0
        )
        prism.cargo_used = 0  # 已经空了

        act = GenericDropAct(
            cargo_unit=UnitTypeId.ZEALOT,
            cargo_count=4,
            transport=UnitTypeId.WARPPRISM,
            drop_target=drop_target,
        )
        act.ai = bot
        act.knowledge = MagicMock()
        act._state = GenericDropState.UNLOAD

        prism_container = MagicMock()
        prism_container.__iter__ = lambda self: iter([prism])
        prism_container.__bool__ = lambda self: True
        prism_container.amount = 1
        bot.units.of_type = lambda types: prism_container

        await act.execute()

        assert act._state == GenericDropState.HOVER_FINAL


class TestGenericDropActNoTransport:
    """没有 transport → execute 返回 False 不崩。"""

    @pytest.mark.asyncio
    async def test_no_transport_returns_false(self, drop_target):
        from sc2.ids.unit_typeid import UnitTypeId

        from vibecraft.bot.auto_combat.protoss.plans.generic_drop_act import (
            GenericDropAct,
            GenericDropState,
        )

        bot = MagicMock()
        bot.time = 100.0

        act = GenericDropAct(
            cargo_unit=UnitTypeId.ZEALOT,
            cargo_count=4,
            transport=UnitTypeId.WARPPRISM,
            drop_target=drop_target,
        )
        act.ai = bot
        act.knowledge = MagicMock()

        empty_container = MagicMock()
        empty_container.__iter__ = lambda self: iter([])
        empty_container.__bool__ = lambda self: False
        empty_container.amount = 0
        bot.units.of_type = lambda types: empty_container

        result = await act.execute()

        assert result is False
        assert act._state == GenericDropState.IDLE
