"""ChargelotArchonProducer 单测。

行为:
- 2 home DT → MORPH_ARCHON pair(merge 帧不 train 让位 morph)
- < 2 home DT + 资源够 → train DT + train zealot
- < 2 home DT + 气不够 → 只 train zealot
- 前线 DT 不动 → 不合并
- zealot cap 60 满 → 不 train zealot
- home DT 每 tick set Reserved
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _fake_sharpy():
    created = []
    for name in (
        "sharpy",
        "sharpy.plans",
        "sharpy.plans.acts",
        "sharpy.plans.acts.act_base",
        "sharpy.managers",
        "sharpy.managers.core",
        "sharpy.managers.core.roles",
    ):
        if name not in sys.modules:
            sys.modules[name] = ModuleType(name)
            created.append(name)
    acts_base = sys.modules["sharpy.plans.acts.act_base"]
    if not hasattr(acts_base, "ActBase"):
        acts_base.ActBase = type("ActBase", (), {"__init__": lambda self: None})  # type: ignore[attr-defined]
    roles_mod = sys.modules["sharpy.managers.core.roles"]
    if not hasattr(roles_mod, "UnitTask"):

        class _UnitTask:
            Reserved = 8

        roles_mod.UnitTask = _UnitTask  # type: ignore[attr-defined]
    yield
    sys.modules.pop("vibecraft.bot.auto_combat.protoss.plans.chargelot_archon_producer", None)
    for name in created:
        sys.modules.pop(name, None)


def _make_unit(tag: int, pos: tuple[float, float], type_id):
    class Unit:
        pass

    u = Unit()
    u.tag = tag
    u.type_id = type_id
    u.position = MagicMock(x=pos[0], y=pos[1])
    u.is_idle = True
    u.distance_to = lambda other: (
        ((u.position.x - other.position.x) ** 2 + (u.position.y - other.position.y) ** 2) ** 0.5
    )
    return u


def _make_townhall(pos: tuple[float, float]):
    th = MagicMock()
    th.position = MagicMock(x=pos[0], y=pos[1])
    return th


def _make_act_ready(
    dts: list,
    zealots: int = 0,
    home_pos: tuple[float, float] = (50, 30),
    minerals: int = 500,
    vespene: int = 500,
):
    from sc2.ids.unit_typeid import UnitTypeId

    from vibecraft.bot.auto_combat.protoss.plans.chargelot_archon_producer import (
        ChargelotArchonProducer,
    )

    act = ChargelotArchonProducer()
    bot = MagicMock()
    bot.townhalls = [_make_townhall(home_pos)]
    bot.minerals = minerals
    bot.vespene = vespene
    bot.do = MagicMock()
    bot.train = MagicMock(return_value=0)
    # bot.units(ZEALOT).amount + bot.already_pending(ZEALOT)
    z_units = MagicMock()
    z_units.amount = zealots

    def _units(t):
        if t == UnitTypeId.ZEALOT:
            return z_units
        return MagicMock(amount=0)

    bot.units = _units
    bot.already_pending = MagicMock(return_value=0)

    class FakeUnits:
        def __init__(self, units):
            self._units = list(units)

        def __iter__(self):
            return iter(self._units)

        def __len__(self):
            return len(self._units)

        @property
        def amount(self):
            return len(self._units)

    cache = MagicMock()
    cache.own = lambda t: MagicMock(
        ready=FakeUnits(dts) if t == UnitTypeId.DARKTEMPLAR else FakeUnits([])
    )

    roles = MagicMock()
    roles.set_task = MagicMock()
    roles.clear_task = MagicMock()

    act.ai = bot
    act.cache = cache
    act.roles = roles
    return act, bot, roles


class TestChargelotArchonProducer:
    @pytest.mark.asyncio
    async def test_two_home_dts_morph_archon(self):
        """2 home DT → MORPH_ARCHON(merge 帧不 train zealot)。"""
        from sc2.ids.ability_id import AbilityId
        from sc2.ids.unit_typeid import UnitTypeId

        dts = [
            _make_unit(101, (55, 32), UnitTypeId.DARKTEMPLAR),
            _make_unit(102, (56, 31), UnitTypeId.DARKTEMPLAR),
        ]
        act, bot, _ = _make_act_ready(dts, zealots=10, home_pos=(50, 30))
        await act.execute()
        # 2 个 MORPH_ARCHON UnitCommand
        assert bot.do.call_count == 2
        for call in bot.do.call_args_list:
            assert call.args[0].ability == AbilityId.MORPH_ARCHON
        # merge 帧不 train(让位 morph)
        bot.train.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_home_dt_trains_dt_and_zealot(self):
        """没 home DT + 资源充足 → train 1 DT + 1 zealot。"""
        from sc2.ids.unit_typeid import UnitTypeId

        act, bot, _ = _make_act_ready(
            [],
            zealots=0,
            minerals=500,
            vespene=500,
        )
        await act.execute()
        # 没 morph
        bot.do.assert_not_called()
        # train DT + train zealot 各 1 次
        assert bot.train.call_count == 2
        types = [c.args[0] for c in bot.train.call_args_list]
        assert UnitTypeId.DARKTEMPLAR in types
        assert UnitTypeId.ZEALOT in types

    @pytest.mark.asyncio
    async def test_no_vespene_only_trains_zealot(self):
        """没 home DT + 气矿不足(< 125)→ 只 train zealot 不 train DT。"""
        from sc2.ids.unit_typeid import UnitTypeId

        act, bot, _ = _make_act_ready(
            [],
            zealots=0,
            minerals=500,
            vespene=50,
        )
        await act.execute()
        bot.do.assert_not_called()
        # 只 train zealot 1 次
        assert bot.train.call_count == 1
        assert bot.train.call_args.args[0] == UnitTypeId.ZEALOT

    @pytest.mark.asyncio
    async def test_front_dt_not_merged(self):
        """前线 DT(距 home 100+) 不算 home DT → train DT + zealot。"""
        from sc2.ids.unit_typeid import UnitTypeId

        dts = [
            _make_unit(101, (130, 120), UnitTypeId.DARKTEMPLAR),
            _make_unit(102, (132, 122), UnitTypeId.DARKTEMPLAR),
        ]
        act, bot, _ = _make_act_ready(
            dts,
            zealots=0,
            home_pos=(50, 30),
            minerals=500,
            vespene=500,
        )
        await act.execute()
        # 没合(前线 DT 不算 home)
        bot.do.assert_not_called()
        # 但仍 train DT(补 home DT) + zealot
        assert bot.train.call_count == 2

    @pytest.mark.asyncio
    async def test_zealot_cap_full_no_zealot_train(self):
        """zealot 已达 cap 60 + 没 home DT → 只 train DT 不 train zealot。"""
        from sc2.ids.unit_typeid import UnitTypeId

        act, bot, _ = _make_act_ready(
            [],
            zealots=60,
            minerals=500,
            vespene=500,
        )
        await act.execute()
        bot.do.assert_not_called()
        # 只 train DT
        assert bot.train.call_count == 1
        assert bot.train.call_args.args[0] == UnitTypeId.DARKTEMPLAR

    @pytest.mark.asyncio
    async def test_home_dt_set_reserved(self):
        """home DT 每 tick 标 Reserved 防 sharpy 派前线。"""
        from sc2.ids.unit_typeid import UnitTypeId

        dts = [_make_unit(101, (55, 32), UnitTypeId.DARKTEMPLAR)]
        act, _bot, roles = _make_act_ready(dts, home_pos=(50, 30))
        await act.execute()
        roles.set_task.assert_called()
        called_tags = [getattr(c.args[1], "tag", None) for c in roles.set_task.call_args_list]
        assert 101 in called_tags
