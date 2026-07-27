"""MergeArchon 通用合白球 act 单测（2026-06-02）。

行为：≥2 个 ready 的指定 templar(DT/HT) → MORPH_ARCHON，无位置/能量限制
（战场 + 家里都合）。<2 → 不合。区别于 MergeArchonAtHome（仅家里 DT）。
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

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
    sys.modules.pop("vibecraft.bot.auto_combat.protoss.plans.merge_archon_at_home", None)
    for name in created:
        sys.modules.pop(name, None)


def _unit(tag: int, pos=(50.0, 50.0)):
    u = MagicMock()
    u.tag = tag
    u.is_idle = False
    u.position = MagicMock(x=pos[0], y=pos[1])
    u.distance_to = lambda other: (
        ((u.position.x - other.position.x) ** 2 + (u.position.y - other.position.y) ** 2) ** 0.5
    )
    return u


class FakeUnits:
    def __init__(self, units):
        self._u = list(units)

    def __iter__(self):
        return iter(self._u)

    @property
    def amount(self):
        return len(self._u)

    def __getitem__(self, i):
        return self._u[i]

    def tags_not_in(self, tags):
        return FakeUnits([u for u in self._u if u.tag not in tags])

    def closest_to(self, unit):
        return min(self._u, key=lambda u: u.distance_to(unit))


def _make_act(templar_type, units):
    from vibecraft.bot.auto_combat.protoss.plans.merge_archon_at_home import MergeArchon

    act = MergeArchon(templar_type)
    bot = MagicMock()
    bot._client._execute = AsyncMock()
    cache = MagicMock()
    cache.own = lambda t: MagicMock(ready=FakeUnits(units) if t == templar_type else FakeUnits([]))
    act.ai = bot
    act.cache = cache
    act.roles = MagicMock()
    return act, bot


@pytest.mark.asyncio
async def test_two_dt_anywhere_morph_archon():
    """2 个 ready DT（不设 townhall=远离家）→ 照样 MORPH_ARCHON（无位置限制）。"""
    from sc2.ids.ability_id import AbilityId
    from sc2.ids.unit_typeid import UnitTypeId

    act, bot = _make_act(UnitTypeId.DARKTEMPLAR, [_unit(1, (10, 10)), _unit(2, (12, 10))])
    await act.execute()
    assert bot._client._execute.await_count == 1
    # 验证下的是 MORPH_ARCHON
    call = bot._client._execute.await_args
    raw = call.kwargs["action"].actions[0].action_raw.unit_command
    assert raw.ability_id == AbilityId.MORPH_ARCHON.value
    assert set(raw.unit_tags) == {1, 2}


@pytest.mark.asyncio
async def test_two_ht_morph_archon():
    """HT 也通用（电兵合白球）。"""
    from sc2.ids.unit_typeid import UnitTypeId

    act, bot = _make_act(UnitTypeId.HIGHTEMPLAR, [_unit(3), _unit(4)])
    await act.execute()
    assert bot._client._execute.await_count == 1


@pytest.mark.asyncio
async def test_single_templar_no_merge():
    """只有 1 个 → 不合。"""
    from sc2.ids.unit_typeid import UnitTypeId

    act, bot = _make_act(UnitTypeId.DARKTEMPLAR, [_unit(5)])
    await act.execute()
    assert bot._client._execute.await_count == 0
