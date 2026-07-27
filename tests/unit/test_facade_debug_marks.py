"""WP-A: facade debug draw 接口单测（per-group 画框格式）。

mark = {shape("box"/"ring"), color(rgb), label, tags[], target[x,y]|None}
覆盖：
- FakeFacade.set_debug_marks 存进 .debug_marks，可读回（覆盖写）。
- 真实 _SharpyFacadeBase.draw_debug_marks：
    - shape=box → debug_box2_out；shape=ring → debug_sphere_out（每个存活单位）
    - label → debug_text_world 在质心飘一个（不是每个单位）
    - 死 tag 跳过；有 target → debug_line_out + 目标小球
    - 绝不调 client._send_debug
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


class TestFakeFacadeDebugMarks:
    def test_set_debug_marks_stores_and_readable(self) -> None:
        from vibecraft.bot.facade import FakeFacade

        f = FakeFacade()
        marks = [{"shape": "box", "color": (0, 220, 255), "label": "attack", "tags": [101]}]
        f.set_debug_marks(marks)
        assert f.debug_marks == marks

    def test_set_debug_marks_overwrites_previous(self) -> None:
        from vibecraft.bot.facade import FakeFacade

        f = FakeFacade()
        f.set_debug_marks([{"shape": "box", "color": (0, 0, 0), "label": "a", "tags": [1]}])
        f.set_debug_marks([{"shape": "ring", "color": (1, 1, 1), "label": "1", "tags": [2]}])
        assert len(f.debug_marks) == 1
        assert f.debug_marks[0]["shape"] == "ring"

    def test_set_debug_marks_initial_empty(self) -> None:
        from vibecraft.bot.facade import FakeFacade

        assert FakeFacade().debug_marks == []

    def test_draw_debug_marks_is_noop(self) -> None:
        from vibecraft.bot.facade import FakeFacade

        f = FakeFacade()
        f.set_debug_marks([{"shape": "box", "color": (1, 2, 3), "label": "x", "tags": [9]}])
        f.draw_debug_marks()  # 不应抛异常

    def test_set_debug_marks_recorded_in_calls(self) -> None:
        from vibecraft.bot.facade import FakeFacade

        f = FakeFacade()
        f.set_debug_marks([{"shape": "box", "color": (0, 0, 0), "label": "x", "tags": [55]}])
        assert "set_debug_marks" in [c.method for c in f.calls]


def _make_sharpy_facade(tags: list[int]):
    """用 fake bot 构造 _SharpyFacadeBase 实例。fake unit 带 position3d 供质心计算。"""
    from vibecraft.bot.auto_combat.common_bot import _make_sharpy_facade_base_class

    cls = _make_sharpy_facade_base_class()

    def mk_unit(tag: int):
        return SimpleNamespace(tag=tag, position3d=SimpleNamespace(x=10.0, y=20.0, z=12.0))

    units_map = {t: mk_unit(t) for t in tags}

    class _FakeUnits:
        def by_tag(self, tag: int):
            return units_map.get(tag)

    client = MagicMock()
    bot = SimpleNamespace(units=_FakeUnits(), client=client, get_terrain_z_height=lambda p: 12.0)
    return cls(bot), client


class TestSharpyFacadeDrawDebugMarks:
    def test_box_shape_calls_box2_out(self) -> None:
        facade, client = _make_sharpy_facade([101])
        facade.set_debug_marks(
            [{"shape": "box", "color": (235, 50, 50), "label": "attack", "tags": [101]}]
        )
        facade.draw_debug_marks()
        assert client.debug_box2_out.called
        _, kwargs = client.debug_box2_out.call_args
        assert kwargs.get("color") == (235, 50, 50)
        assert not client.debug_sphere_out.called  # box 不画球

    def test_ring_shape_calls_sphere_out(self) -> None:
        facade, client = _make_sharpy_facade([301])
        facade.set_debug_marks(
            [{"shape": "ring", "color": (255, 230, 0), "label": "1", "tags": [301]}]
        )
        facade.draw_debug_marks()
        assert client.debug_sphere_out.called
        assert not client.debug_box2_out.called  # ring 不画框

    def test_label_drawn_once_at_centroid(self) -> None:
        """一组多单位 → debug_text_world 只调一次（质心飘一个，不是每个单位）。"""
        facade, client = _make_sharpy_facade([101, 102, 103])
        facade.set_debug_marks(
            [{"shape": "box", "color": (0, 220, 255), "label": "attack", "tags": [101, 102, 103]}]
        )
        from vibecraft.bot.auto_combat.common_bot import _DEBUG_THICK_PASSES

        facade.draw_debug_marks()
        # 每个单位画 _DEBUG_THICK_PASSES 条同心线(线宽模拟) × 3 单位
        assert client.debug_box2_out.call_count == 3 * _DEBUG_THICK_PASSES
        assert client.debug_text_world.call_count == 1  # 文字只一个
        assert client.debug_text_world.call_args[0][0] == "attack"

    def test_no_label_skips_text(self) -> None:
        facade, client = _make_sharpy_facade([101])
        facade.set_debug_marks(
            [{"shape": "box", "color": (0, 220, 255), "label": "", "tags": [101]}]
        )
        facade.draw_debug_marks()
        assert client.debug_box2_out.called
        assert not client.debug_text_world.called

    def test_dead_tag_excluded_from_group(self) -> None:
        """组里 101 活 102 死 → 只画 101，文字仍一个。"""
        facade, client = _make_sharpy_facade([101])  # 102 不存在
        facade.set_debug_marks(
            [{"shape": "box", "color": (0, 220, 255), "label": "x", "tags": [101, 102]}]
        )
        from vibecraft.bot.auto_combat.common_bot import _DEBUG_THICK_PASSES

        facade.draw_debug_marks()
        assert client.debug_box2_out.call_count == _DEBUG_THICK_PASSES  # 只 101 活
        assert client.debug_text_world.call_count == 1

    def test_all_dead_group_draws_nothing(self) -> None:
        facade, client = _make_sharpy_facade([])  # 全死
        facade.set_debug_marks(
            [{"shape": "box", "color": (255, 0, 0), "label": "x", "tags": [999]}]
        )
        facade.draw_debug_marks()
        client.debug_box2_out.assert_not_called()
        client.debug_text_world.assert_not_called()

    def test_target_draws_line_and_marker(self) -> None:
        facade, client = _make_sharpy_facade([101])
        facade.set_debug_marks(
            [
                {
                    "shape": "ring",
                    "color": (255, 230, 0),
                    "label": "1",
                    "tags": [101],
                    "target": [50.0, 60.0],
                }
            ]
        )
        facade.draw_debug_marks()
        assert client.debug_line_out.called  # 质心→目标连线
        # 目标点小球：sphere 被调（单位环 1 次 + 目标球 1 次 = 2 次）
        assert client.debug_sphere_out.call_count >= 2

    def test_no_target_no_line(self) -> None:
        facade, client = _make_sharpy_facade([101])
        facade.set_debug_marks(
            [{"shape": "box", "color": (0, 220, 255), "label": "x", "tags": [101], "target": None}]
        )
        facade.draw_debug_marks()
        assert not client.debug_line_out.called

    def test_never_calls_send_debug(self) -> None:
        facade, client = _make_sharpy_facade([101, 202])
        facade.set_debug_marks(
            [
                {"shape": "box", "color": (0, 220, 255), "label": "a", "tags": [101]},
                {"shape": "ring", "color": (255, 230, 0), "label": "1", "tags": [202]},
            ]
        )
        facade.draw_debug_marks()
        client._send_debug.assert_not_called()

    def test_empty_marks_no_calls(self) -> None:
        facade, client = _make_sharpy_facade([101])
        facade.set_debug_marks([])
        facade.draw_debug_marks()
        client.debug_box2_out.assert_not_called()
        client._send_debug.assert_not_called()

    def test_draw_before_set_no_crash(self) -> None:
        facade, client = _make_sharpy_facade([101])
        facade.draw_debug_marks()  # 未 set，不崩
        client.debug_box2_out.assert_not_called()


def test_point_anchored_mark_draws_ring_and_vline() -> None:
    """出兵集结点标记:point 锚定 → 地面 6 层球环 + 接近无限高竖线(2026-06-10)。"""
    from vibecraft.bot.auto_combat.common_bot import (
        _RALLY_PILLAR_HEIGHT,
        _RALLY_RING_BASE,
        _RALLY_RING_PASSES,
        _RALLY_RING_STEP,
    )

    facade, client = _make_sharpy_facade([])
    facade.set_debug_marks([{"shape": "ring", "color": (80, 255, 120), "point": [50.0, 60.0]}])
    facade.draw_debug_marks()
    # 6 层同心环(层距=编队的 2 倍)
    assert client.debug_sphere_out.call_count == _RALLY_RING_PASSES
    radii = [c.args[1] for c in client.debug_sphere_out.call_args_list]
    assert radii == [_RALLY_RING_BASE + i * _RALLY_RING_STEP for i in range(_RALLY_RING_PASSES)]
    # 竖线两端 x/y 相同、接近无限高(指天)
    assert client.debug_line_out.called
    a, b = client.debug_line_out.call_args[0][0], client.debug_line_out.call_args[0][1]
    assert a.x == b.x == 50.0 and a.y == b.y == 60.0
    assert b.z - a.z == _RALLY_PILLAR_HEIGHT
