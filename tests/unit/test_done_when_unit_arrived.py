"""unit_arrived / unit_held_position done_when checker 单测(2026-05-24)。

用于 move/safe_move/scout 自动完成判定。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vibecraft.bot.event_bus import EventBus
from vibecraft.bot.task_monitor import TaskMonitor


@pytest.fixture
def monitor():
    return TaskMonitor(board=MagicMock(), event_bus=EventBus())


def _make_unit(tag: int, pos: tuple[float, float]):
    class Unit:
        pass

    u = Unit()
    u.tag = tag
    u.position = MagicMock(x=pos[0], y=pos[1])
    u.distance_to = lambda p: ((u.position.x - p.x) ** 2 + (u.position.y - p.y) ** 2) ** 0.5
    return u


class _UnitsList(list):
    """贴合真实 sc2 Units:除可迭代外还有 .center(重心)。

    unit_arrived checker 2026-06-06 改判队伍重心(units.center),普通 list 没有 .center
    → 真实游戏 tags_in 返回 sc2 Units(有 .center)能跑,而旧 mock 返回 list 会 AttributeError
    → 静默 False(测试假绿/假红)。这里补上 .center 让单测真正走重心逻辑。
    """

    @property
    def center(self):
        from sc2.position import Point2

        n = len(self)
        cx = sum(u.position.x for u in self) / n
        cy = sum(u.position.y for u in self) / n
        return Point2((cx, cy))


def _make_game_state(units):
    """mock bot,units.tags_in(tags) 返回过滤后单位(带 .center)+ named_spots 含 'natural'。"""
    from sc2.position import Point2

    from vibecraft.bot.named_spot import NamedSpotRegistry

    bot = MagicMock()
    bot.units.tags_in = lambda tags: _UnitsList(u for u in units if u.tag in tags)

    # 用真实 NamedSpotRegistry + 简单 own_natural stub
    bot.named_spots = NamedSpotRegistry()
    bot.townhalls = []
    bot.knowledge = MagicMock()
    # natural 对应位置 (100, 100)
    natural_zone = MagicMock()
    natural_zone.center_location = Point2((100.0, 100.0))
    bot.knowledge.zone_manager.expansion_zones = [
        MagicMock(center_location=Point2((50.0, 50.0))),  # main
        natural_zone,  # natural
    ]
    return bot


class TestUnitArrived:
    def test_all_units_in_range_returns_true(self, monitor):
        u1 = _make_unit(101, (102, 100))  # 距 (100,100) = 2
        u2 = _make_unit(102, (98, 100))  # 距 = 2
        bot = _make_game_state([u1, u2])
        monitor.attach_directive(
            "d_test",
            done_when={"kind": "unit_arrived", "area": "natural", "within_grid": 5.0},
            issued_at=10.0,
            timeout_s=None,
            unit_tags={101, 102},
        )
        # check
        from vibecraft.bot.task_monitor import DONE_CHECKERS

        check = DONE_CHECKERS["unit_arrived"]
        result = check(
            {"kind": "unit_arrived", "area": "natural", "within_grid": 5.0},
            "d_test",
            bot,
            monitor,
            15.0,
        )
        assert result is True

    def test_one_unit_far_returns_false(self, monitor):
        u1 = _make_unit(101, (102, 100))  # 近
        u2 = _make_unit(102, (200, 200))  # 远 > 5
        bot = _make_game_state([u1, u2])
        monitor.attach_directive(
            "d_test",
            done_when={"kind": "unit_arrived", "area": "natural", "within_grid": 5.0},
            issued_at=10.0,
            timeout_s=None,
            unit_tags={101, 102},
        )
        from vibecraft.bot.task_monitor import DONE_CHECKERS

        check = DONE_CHECKERS["unit_arrived"]
        result = check(
            {"kind": "unit_arrived", "area": "natural", "within_grid": 5.0},
            "d_test",
            bot,
            monitor,
            15.0,
        )
        assert result is False

    def test_no_units_returns_true(self, monitor):
        """selector 单位全死 → 视作 done(没必要继续)。"""
        bot = _make_game_state([])  # 空
        monitor.attach_directive(
            "d_test",
            done_when={"kind": "unit_arrived", "area": "natural", "within_grid": 5.0},
            issued_at=10.0,
            timeout_s=None,
            unit_tags={101, 102},
        )
        from vibecraft.bot.task_monitor import DONE_CHECKERS

        check = DONE_CHECKERS["unit_arrived"]
        result = check(
            {"kind": "unit_arrived", "area": "natural", "within_grid": 5.0},
            "d_test",
            bot,
            monitor,
            15.0,
        )
        assert result is True


class TestUnitHeldPosition:
    def test_held_accumulates_ts_when_in_range(self, monitor):
        u1 = _make_unit(101, (102, 100))
        bot = _make_game_state([u1])
        monitor.attach_directive(
            "d_test",
            done_when={
                "kind": "unit_held_position",
                "area": "natural",
                "within_grid": 5.0,
                "hold_seconds": 3.0,
            },
            issued_at=10.0,
            timeout_s=None,
            unit_tags={101},
        )
        from vibecraft.bot.task_monitor import DONE_CHECKERS

        check = DONE_CHECKERS["unit_held_position"]
        dw = {
            "kind": "unit_held_position",
            "area": "natural",
            "within_grid": 5.0,
            "hold_seconds": 3.0,
        }

        # t=15: 首次进入,标记 ts
        assert check(dw, "d_test", bot, monitor, 15.0) is False
        # t=17: 累计 2s < 3s
        assert check(dw, "d_test", bot, monitor, 17.0) is False
        # t=18.5: 累计 3.5s >= 3s → done
        assert check(dw, "d_test", bot, monitor, 18.5) is True

    def test_held_resets_when_unit_leaves(self, monitor):
        u1 = _make_unit(101, (102, 100))
        bot = _make_game_state([u1])
        monitor.attach_directive(
            "d_test",
            done_when={
                "kind": "unit_held_position",
                "area": "natural",
                "within_grid": 5.0,
                "hold_seconds": 3.0,
            },
            issued_at=10.0,
            timeout_s=None,
            unit_tags={101},
        )
        from vibecraft.bot.task_monitor import DONE_CHECKERS

        check = DONE_CHECKERS["unit_held_position"]
        dw = {
            "kind": "unit_held_position",
            "area": "natural",
            "within_grid": 5.0,
            "hold_seconds": 3.0,
        }

        # t=15: 在范围,首 ts 15
        assert check(dw, "d_test", bot, monitor, 15.0) is False
        # t=16: 单位移走
        u1.position = MagicMock(x=200, y=200)
        assert check(dw, "d_test", bot, monitor, 16.0) is False
        # ts 重置 → t=17 再到范围
        u1.position = MagicMock(x=102, y=100)
        assert check(dw, "d_test", bot, monitor, 17.0) is False
        # t=19 累计 2s < 3s
        assert check(dw, "d_test", bot, monitor, 19.0) is False
        # t=20.5 累计 3.5s >= 3s → done
        assert check(dw, "d_test", bot, monitor, 20.5) is True
