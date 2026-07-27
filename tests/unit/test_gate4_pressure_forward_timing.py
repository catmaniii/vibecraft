"""Gate4Pressure._forward_ready timing 单测。

历史:
- v1: BY ready + 4 BG pending(~supply 32) - 太晚,主力都准备出门了
- v2: BY pending + 3 BG(~supply 24-26) - 仍太晚,实战 log
  (game_20260518_042334) 农民派出时敌方 scout 满地图,2 worker_death 终止
- v3(当前): 1 BG ready(~supply 16) - 早派,抢在敌方 scout 摸到中线前到位

直接 mock ai 调 _forward_ready，不走真 sharpy 路径。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# sharpy path（forward_proxy/gate4_pressure 顶 import sharpy）
_VENDOR_SHARPY = Path(__file__).resolve().parents[2] / "vendor" / "sharpy"
if str(_VENDOR_SHARPY) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SHARPY))

pytest.importorskip("sc2.ids.unit_typeid")
pytest.importorskip("sharpy.plans.acts")


def _make_ai(bg_ready_amount: int = 0, bg_pending: int = 0):
    """构造 mock ai：GATEWAY ready 数 + pending（v3 只看 ready BG 数）。"""
    from sc2.ids.unit_typeid import UnitTypeId

    ai = MagicMock()

    bg_collection = MagicMock()
    bg_collection.amount = bg_ready_amount + bg_pending
    bg_collection.ready = MagicMock(amount=bg_ready_amount)

    def _of_type(types):
        if UnitTypeId.GATEWAY in types:
            return bg_collection
        return MagicMock(amount=0, ready=MagicMock(amount=0))

    ai.structures = MagicMock()
    ai.structures.of_type = _of_type

    def _already_pending(unit_type):
        if unit_type == UnitTypeId.GATEWAY:
            return bg_pending
        return 0

    ai.already_pending = _already_pending
    return ai


def _forward_ready(ai):
    from vibecraft.bot.auto_combat.protoss.plans.gate4_pressure import Gate4Pressure

    return Gate4Pressure._forward_ready(ai)


class TestForwardReady:
    def test_false_when_no_bg(self):
        """没 BG → 不触发。"""
        assert _forward_ready(_make_ai()) is False

    def test_false_when_bg_pending_only(self):
        """BG 还在筑（pending）→ 不触发，必须 ready 才派。"""
        assert _forward_ready(_make_ai(bg_pending=1)) is False

    def test_true_when_1_bg_ready(self):
        """v3 触发线：1 BG ready → 派农民出去（supply ~16）。"""
        assert _forward_ready(_make_ai(bg_ready_amount=1)) is True

    def test_true_when_multiple_bg_ready(self):
        """多个 BG ready 也触发（向上兼容旧条件）。"""
        assert _forward_ready(_make_ai(bg_ready_amount=4)) is True


class TestOffAttackAxis:
    """_off_attack_axis: pos 到 own_main→enemy_main 直线的垂直距离。

    实战 log 选 (112.5, 119.5) 距 own→enemy 主轴线很近 → 农民走直线必死。
    新评分项偏好"离主轴远"的点（地图边缘绕路）。
    """

    def _make_proxy_with_locations(self, own=(48, 28), enemy=(112, 120)):
        from sc2.position import Point2

        from vibecraft.bot.auto_combat.protoss.plans import forward_proxy

        inst = forward_proxy.ForwardSupportPylonGateway()
        ai = MagicMock()
        ai.start_location = Point2(own)
        ai.enemy_start_locations = [Point2(enemy)]
        inst.ai = ai
        return inst

    def test_on_axis_returns_zero(self):
        """在自家→敌家直线上的点 → 偏离 = 0。"""
        from sc2.position import Point2

        inst = self._make_proxy_with_locations(own=(0, 0), enemy=(100, 0))
        # 主轴是 x 轴，y=0 的点在主轴上
        assert inst._off_attack_axis(Point2((50, 0))) == pytest.approx(0.0, abs=0.01)

    def test_off_axis_returns_perpendicular_distance(self):
        """偏离主轴 → 返回垂直距离。"""
        from sc2.position import Point2

        inst = self._make_proxy_with_locations(own=(0, 0), enemy=(100, 0))
        # 主轴 x 轴上 (50, 0)，垂直偏 20 → off=20
        assert inst._off_attack_axis(Point2((50, 20))) == pytest.approx(20.0, abs=0.01)

    def test_edge_proxy_scores_higher_than_on_axis(self):
        """两个候选点：一个在主轴上，一个在地图边缘 → 边缘的 off_attack_axis 更大。"""
        from sc2.position import Point2

        inst = self._make_proxy_with_locations(own=(48, 28), enemy=(112, 120))
        on_axis_pos = Point2((80, 74))  # 主轴中点附近
        edge_pos = Point2((20, 130))  # 地图角落
        assert inst._off_attack_axis(edge_pos) > inst._off_attack_axis(on_axis_pos)
