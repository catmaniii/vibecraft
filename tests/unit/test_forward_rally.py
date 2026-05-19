"""ForwardRallyStalker 单测:覆盖 sharpy 全局 gather_point 到 forward PYLON。

2026-05-20 重写:旧版用 per-unit move(),被 sharpy PlanZoneGather 的 rally +
combat-move 反复覆盖,stalker 在家/前线之间横跳。新版改用
`gather_point_solver.set_gather_point()`,统一走 sharpy 内置机制。
"""

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


def _make_inst(home=(127, 119), enemy=(48, 28), pylons=()):
    """构造 ForwardRallyStalker + mock ai/knowledge.gather_point_solver。"""
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
    inst.ai = ai

    # knowledge.gather_point_solver mock
    inst.knowledge = MagicMock()
    inst.knowledge.gather_point_solver = MagicMock()
    return inst


class TestForwardRallyStalker:
    async def test_no_forward_pylon_returns_true(self):
        """没 forward PYLON → return True 让 sharpy 默认 home gather。"""
        inst = _make_inst(pylons=[])
        assert await inst.execute() is True
        inst.knowledge.gather_point_solver.set_gather_point.assert_not_called()

    async def test_sets_gather_point_to_forward_pylon(self):
        """forward PYLON 在 → set_gather_point(forward_pylon.position)。"""
        forward_py = _make_pylon((83, 28))
        inst = _make_inst(pylons=[forward_py])

        result = await inst.execute()
        assert result is False
        inst.knowledge.gather_point_solver.set_gather_point.assert_called_once_with(
            forward_py.position
        )

    async def test_home_pylon_not_treated_as_forward(self):
        """只有家里 PYLON(非 forward) → 视为没 forward,return True 不动 gather_point。"""
        home_py = _make_pylon((127, 119))  # 距家 0, 距敌方 117 → 远非 forward
        inst = _make_inst(pylons=[home_py])

        assert await inst.execute() is True
        inst.knowledge.gather_point_solver.set_gather_point.assert_not_called()

    async def test_calls_set_gather_point_every_tick(self):
        """每 tick 都要 set_gather_point(sharpy update 每帧重置 _gather_point_set
        flag,不重 set 会被覆盖回 home)。"""
        forward_py = _make_pylon((83, 28))
        inst = _make_inst(pylons=[forward_py])

        await inst.execute()
        await inst.execute()
        await inst.execute()
        assert inst.knowledge.gather_point_solver.set_gather_point.call_count == 3

    async def test_returns_true_when_no_solver(self):
        """knowledge.gather_point_solver 缺失(老旧 mock / 部分集成测试) → 优雅退出。"""
        forward_py = _make_pylon((83, 28))
        inst = _make_inst(pylons=[forward_py])
        # 删 solver 模拟非完整 knowledge
        del inst.knowledge.gather_point_solver
        # 触发 AttributeError → return True
        result = await inst.execute()
        assert result is True
