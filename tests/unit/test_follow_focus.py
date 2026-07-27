"""compute_follow_focus 单测 —— 镜头跟随三规则（2026-06-03 用户）。

规则：移动看前方（质心+朝向×前瞻）/ 停止看本身（质心）/ 停止交战看双方团重心。
用 SimpleNamespace 假单位（is_moving 必须是真 bool，区别于 MagicMock 的 truthy）。
"""

from __future__ import annotations

from types import SimpleNamespace

from vibecraft.bot.telemetry import compute_follow_focus, strongest_cluster_units


def _pt(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=float(x), y=float(y))


def _unit(
    x: float,
    y: float,
    *,
    is_moving: bool = False,
    facing: float | None = None,
    order_target: object = None,
    tag: int = 0,
    type_name: str = "Stalker",
) -> SimpleNamespace:
    return SimpleNamespace(
        position=_pt(x, y),
        is_moving=is_moving,
        facing=facing,
        order_target=order_target,
        tag=tag,
        # _filter_enemy_army 按 type_id.name 排工人/支援；给个普通战斗兵种名
        type_id=SimpleNamespace(name=type_name),
        is_structure=False,
    )


def _bot(
    enemy: list | None = None, playable: tuple[float, float, float, float] = (0, 0, 200, 200)
) -> SimpleNamespace:
    px, py, pw, ph = playable
    return SimpleNamespace(
        enemy_units=enemy or [],
        all_units=SimpleNamespace(by_tag=lambda t: None),
        game_info=SimpleNamespace(playable_area=SimpleNamespace(x=px, y=py, width=pw, height=ph)),
    )


# --- 规则1：移动 → 前方 ------------------------------------------------------


def test_moving_focuses_ahead_via_order_target() -> None:
    """移动中（order_target 指向北）→ 镜头落在质心前方。"""
    bot = _bot()
    units = [
        _unit(50, 50, is_moving=True, order_target=_pt(50, 80)),
        _unit(49, 50, is_moving=True, order_target=_pt(50, 80)),
        _unit(51, 50, is_moving=True, order_target=_pt(50, 80)),
    ]
    focus = compute_follow_focus(bot, units, forward_offset=10.0)
    assert focus is not None
    assert abs(focus.x - 50.0) < 0.5  # 质心 x=50
    assert abs(focus.y - 60.0) < 0.5  # 50 + 10（向北前瞻）


def test_moving_via_centroid_delta() -> None:
    """无 order_target 时用质心位移（上一帧→当前）定朝向。"""
    bot = _bot()
    units = [_unit(60, 50, is_moving=True), _unit(60, 50, is_moving=True)]
    focus = compute_follow_focus(bot, units, prev_centroid=_pt(50, 50), forward_offset=10.0)
    assert focus is not None
    assert abs(focus.x - 70.0) < 0.5  # 向东 10
    assert abs(focus.y - 50.0) < 0.5


def test_moving_via_facing_fallback() -> None:
    """无 order_target 无位移 → 用 facing 均值（0 弧度=向东）。"""
    bot = _bot()
    units = [
        _unit(50, 50, is_moving=True, facing=0.0),
        _unit(50, 50, is_moving=True, facing=0.0),
    ]
    focus = compute_follow_focus(bot, units, forward_offset=10.0)
    assert focus is not None
    assert abs(focus.x - 60.0) < 0.5
    assert abs(focus.y - 50.0) < 0.5


def test_not_moving_when_minority_moves() -> None:
    """只有少数单位在动（< 40%）→ 不算移动 → 聚焦质心，不前瞻。"""
    bot = _bot()
    units = [
        _unit(50, 50, is_moving=True, order_target=_pt(50, 90)),
        _unit(50, 50),
        _unit(50, 50),
        _unit(50, 50),
    ]
    focus = compute_follow_focus(bot, units, forward_offset=10.0)
    assert focus is not None
    assert abs(focus.y - 50.0) < 0.5  # 没前瞻


# --- 规则2：停止 → 本身 ------------------------------------------------------


def test_stopped_no_enemy_focuses_centroid() -> None:
    bot = _bot()
    units = [_unit(50, 50), _unit(60, 50)]
    focus = compute_follow_focus(bot, units)
    assert focus is not None
    assert abs(focus.x - 55.0) < 0.5
    assert abs(focus.y - 50.0) < 0.5


def test_stopped_enemy_far_not_engaged() -> None:
    """敌军在 combat_radius 外 → 不算交战 → 聚焦己方质心。"""
    bot = _bot(enemy=[_unit(100, 100)])
    units = [_unit(50, 50), _unit(50, 50)]
    focus = compute_follow_focus(bot, units, combat_radius=16.0)
    assert focus is not None
    assert abs(focus.x - 50.0) < 0.5
    assert abs(focus.y - 50.0) < 0.5


# --- 规则3：停止 + 交战 → 双方团重心 -----------------------------------------


def test_stopped_combat_focuses_engagement_centroid() -> None:
    """停止 + 敌军近身 → 聚焦交战双方单位并集的重心（在双方之间）。"""
    enemy = [_unit(60, 50), _unit(62, 50)]  # 敌团质心 ~ (61,50)
    bot = _bot(enemy=enemy)
    units = [_unit(50, 50), _unit(50, 50)]  # 我方质心 (50,50)，距敌 ~10 < 16
    focus = compute_follow_focus(bot, units, combat_radius=16.0, engage_radius=14.0)
    assert focus is not None
    # 并集 (50,50)*2 + (60,50)+(62,50) → x=(50+50+60+62)/4=55.5
    assert abs(focus.x - 55.5) < 0.5
    assert abs(focus.y - 50.0) < 0.5
    # 落点应在我方与敌方之间
    assert 50.0 < focus.x < 61.0


def test_combat_picks_nearest_enemy_cluster() -> None:
    """有多个敌团时，取离我方最近的那个交战团（不是远处那团）。"""
    enemy = [
        _unit(60, 50),  # 近团
        _unit(61, 50),
        _unit(150, 150),  # 远团（在 combat_radius 外，不参与）
    ]
    bot = _bot(enemy=enemy)
    units = [_unit(50, 50), _unit(50, 50)]
    focus = compute_follow_focus(bot, units, combat_radius=16.0)
    assert focus is not None
    # 只跟近团 → 落点在 (50,50) 与 (60.5,50) 之间，不被远团拽走
    assert 50.0 < focus.x < 61.0


# --- 边界 --------------------------------------------------------------------


def test_empty_units_returns_none() -> None:
    assert compute_follow_focus(_bot(), []) is None


def test_clamp_to_map_keeps_focus_in_bounds() -> None:
    """前瞻把镜头推出地图 → 夹回可玩区域内。"""
    bot = _bot(playable=(0, 0, 100, 100))
    units = [_unit(95, 50, is_moving=True, order_target=_pt(200, 50))]  # 向东冲出边界
    focus = compute_follow_focus(bot, units, forward_offset=20.0)
    assert focus is not None
    assert focus.x <= 98.0 + 1e-6  # 夹到 width - margin(2)


# --- strongest_cluster_units -------------------------------------------------


def test_strongest_cluster_units_picks_bigger_group() -> None:
    """两团 → 返回造价/数量更强那团的单位列表。"""
    group_a = [_unit(10, 10), _unit(11, 10), _unit(10, 11)]  # 3 个
    group_b = [_unit(50, 50)]  # 1 个，远
    all_units = group_a + group_b
    bot = SimpleNamespace(
        units=SimpleNamespace(filter=lambda fn: all_units),
        calculate_cost=lambda t: SimpleNamespace(minerals=0, vespene=0),
        start_location=_pt(0, 0),
    )
    result = strongest_cluster_units(bot)
    assert len(result) == 3  # 大团（3 个）胜出
    xs = sorted(u.position.x for u in result)
    assert xs == [10.0, 10.0, 11.0]


def test_strongest_cluster_units_empty_when_no_army() -> None:
    bot = SimpleNamespace(units=SimpleNamespace(filter=lambda fn: []))
    assert strongest_cluster_units(bot) == []
