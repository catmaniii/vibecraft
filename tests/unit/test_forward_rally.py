"""ForwardRallyStalker 单测：把闲置 STALKER 拉到 forward PYLON 集结。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_VENDOR_SHARPY = Path(__file__).resolve().parents[2] / "vendor" / "sharpy"
if str(_VENDOR_SHARPY) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SHARPY))

pytest.importorskip("sc2.ids.unit_typeid")
pytest.importorskip("sharpy.plans.acts")


class _MockUnits:
    def __init__(self, units):
        self._units = list(units)

    @property
    def ready(self):
        return _MockUnits([u for u in self._units if getattr(u, "is_ready", True)])

    def __iter__(self):
        return iter(self._units)

    def __len__(self):
        return len(self._units)


def _make_pylon(pos, tag=10):
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.position import Point2

    p = MagicMock()
    p.position = Point2(pos)
    p.tag = tag
    p.type_id = UnitTypeId.PYLON
    p.is_ready = True
    p.distance_to = lambda other, _self_pos=Point2(pos): _self_pos.distance_to(
        other.position if hasattr(other, "position") else other
    )
    return p


def _make_stalker(pos, tag, is_attacking=False):
    from sc2.position import Point2

    s = MagicMock()
    s.position = Point2(pos)
    s.tag = tag
    s.is_attacking = is_attacking
    return s


def _make_inst(home=(127, 119), enemy=(48, 28), pylons=(), stalkers=()):
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.position import Point2
    from vibecraft.bot.auto_combat.protoss.plans import forward_rally

    inst = forward_rally.ForwardRallyStalker()
    ai = MagicMock()
    ai.start_location = Point2(home)
    ai.enemy_start_locations = [Point2(enemy)]

    def _structures(type_id):
        if type_id == UnitTypeId.PYLON:
            return _MockUnits(list(pylons))
        return _MockUnits([])

    ai.structures = MagicMock(side_effect=_structures)

    def _units(type_id):
        if type_id == UnitTypeId.STALKER:
            return list(stalkers)
        return []

    ai.units = MagicMock(side_effect=_units)
    inst.ai = ai
    return inst


class TestForwardRallyStalker:
    async def test_no_forward_pylon_returns_true(self):
        """没 forward PYLON → return True 让 sharpy ZoneGather 默认处理。"""
        inst = _make_inst(pylons=[], stalkers=[_make_stalker((127, 119), 1)])
        assert await inst.execute() is True

    async def test_moves_idle_stalker_at_home_to_forward(self):
        """家里 idle stalker 距 forward PYLON > 12 → 发 move 拉过来。"""
        forward_py = _make_pylon((83, 28))
        home_stalker = _make_stalker((127, 119), tag=1)
        inst = _make_inst(pylons=[forward_py], stalkers=[home_stalker])

        result = await inst.execute()
        assert result is False
        home_stalker.move.assert_called_once_with(forward_py.position)

    async def test_does_not_move_stalker_already_at_forward(self):
        """已经在 forward PYLON 12 范围内的 stalker 不再发 move(防命令洪泛)。"""
        forward_py = _make_pylon((83, 28))
        near_stalker = _make_stalker((85, 30), tag=1)  # 距 forward ~2.8 < 12
        inst = _make_inst(pylons=[forward_py], stalkers=[near_stalker])

        await inst.execute()
        near_stalker.move.assert_not_called()

    async def test_skips_attacking_stalker(self):
        """is_attacking=True 的 stalker 跳过(由 VibeCraftZoneAttack 接管)。"""
        forward_py = _make_pylon((83, 28))
        attacking = _make_stalker((127, 119), tag=1, is_attacking=True)
        inst = _make_inst(pylons=[forward_py], stalkers=[attacking])

        await inst.execute()
        attacking.move.assert_not_called()

    async def test_home_pylon_not_treated_as_forward(self):
        """只有家里 PYLON(非 forward) → 视为没 forward,return True。"""
        home_py = _make_pylon((127, 119))  # 距家 0, 距敌方 117 → 远非 forward
        stalker = _make_stalker((127, 119), tag=1)
        inst = _make_inst(pylons=[home_py], stalkers=[stalker])

        assert await inst.execute() is True

    async def test_moves_multiple_stalkers(self):
        """同 tick 把多个家里 stalker 都 move 到 forward。"""
        forward_py = _make_pylon((83, 28))
        s1 = _make_stalker((127, 119), tag=1)
        s2 = _make_stalker((125, 117), tag=2)
        s3 = _make_stalker((85, 30), tag=3)  # 已在 forward 附近
        inst = _make_inst(pylons=[forward_py], stalkers=[s1, s2, s3])

        await inst.execute()
        s1.move.assert_called_once_with(forward_py.position)
        s2.move.assert_called_once_with(forward_py.position)
        s3.move.assert_not_called()
