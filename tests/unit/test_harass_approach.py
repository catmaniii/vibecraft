"""plan_avoid_path / plan_harass_approach 几何单测（#581 BC 骚扰接近路径）。

② 目标：直奔矿后点，只在直线会穿敌方主基地时贴其视野边缘绕行，从矿背后/外侧切入。
"""

from __future__ import annotations

from types import SimpleNamespace

from sc2.position import Point2

from vibecraft.bot.drop_path import (
    plan_avoid_path,
    plan_harass_approach,
    project_point_onto_segment,
)


def _pa(x: float = 0.0, y: float = 0.0, w: float = 200.0, h: float = 200.0) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, width=w, height=h)


def _dist_pt_seg(m: Point2, a: Point2, b: Point2) -> float:
    p = project_point_onto_segment(m, a, b)
    return ((p.x - m.x) ** 2 + (p.y - m.y) ** 2) ** 0.5


# ── plan_avoid_path ──────────────────────────────────────────────────────


def test_avoid_direct_when_no_blocker():
    """避障中心离直线很远 → 原样直线 [start, end]。"""
    start = Point2((0.0, 0.0))
    end = Point2((50.0, 0.0))
    far = Point2((25.0, 100.0))  # 距线 100 >> r_avoid
    assert plan_avoid_path(start, end, [far], _pa(), r_avoid=13.0) == [start, end]


def test_avoid_direct_when_no_centers():
    """无避障中心 → 直线。"""
    start = Point2((0.0, 0.0))
    end = Point2((50.0, 0.0))
    assert plan_avoid_path(start, end, [], _pa(), r_avoid=13.0) == [start, end]


def test_avoid_bends_around_blocker_on_line():
    """中心正在线段上 → 插拐点，拐点距中心 ≈ r_avoid+push，且整条不再直穿中心。"""
    start = Point2((0.0, 50.0))
    end = Point2((100.0, 50.0))
    m = Point2((50.0, 50.0))
    path = plan_avoid_path(start, end, [m], _pa(0, 0, 200, 200), r_avoid=13.0, push=5.0)
    assert len(path) >= 3  # 插了拐点
    # 存在一个拐点 ≈ reach(18) 远离中心
    assert any(abs(p.distance_to(m) - 18.0) < 1.5 for p in path[1:-1])


def test_avoid_degenerate_picks_boundary_ward_side():
    """退化（线穿中心）→ 拐点选离地图中心更远（更贴边）那侧（评审 #2）。

    map center=(100,100)，M=(50,50) 在线上，AB 沿 +x → 远离中心侧是 y<50。
    """
    start = Point2((0.0, 50.0))
    end = Point2((100.0, 50.0))
    m = Point2((50.0, 50.0))
    path = plan_avoid_path(start, end, [m], _pa(0, 0, 200, 200), r_avoid=13.0, push=5.0)
    assert any(p.y < 50.0 for p in path[1:-1]), f"未选贴边侧: {path}"


# ── plan_harass_approach ─────────────────────────────────────────────────


def test_harass_approach_ends_at_behind_and_stages_outside():
    """末点=矿后点；含场外集结点 stage（在矿线背基地一侧、主基地视野外）；直线穿主基地时绕开。"""
    start = Point2((0.0, 100.0))
    th = Point2((140.0, 100.0))  # 敌方主基地
    ml = Point2((150.0, 100.0))  # 矿线在基地 +x 侧
    behind = Point2((150.5, 100.0))  # 矿后（略往外 0.5）
    path = plan_harass_approach(start, ml, th, behind, th, _pa(0, 0, 200, 200))

    assert path[-1] == behind  # 硬目标：末点=矿后点
    assert path[-2].distance_to(th) > 13.0  # stage 在主基地视野半径外
    assert path[-2].x > ml.x  # stage 在矿线背 TH（+x）一侧 = 从矿背后切入
    # 中途绕开主基地所在水平线（贴边绕，不直穿）
    assert any(abs(p.y - 100.0) > 5.0 for p in path[1:-1]), f"未绕开主基地: {path}"


def test_harass_approach_no_enemy_main_direct_to_stage():
    """无 enemy_main（None）→ 不避障，直接 start→stage→behind。"""
    start = Point2((0.0, 100.0))
    th = Point2((140.0, 100.0))
    ml = Point2((150.0, 100.0))
    behind = Point2((150.5, 100.0))
    path = plan_harass_approach(start, ml, th, behind, None, _pa(0, 0, 200, 200))
    assert path[0] == start
    assert path[-1] == behind
    # 无避障 → 中间只有 stage（在矿外侧）
    assert path[-2].x > ml.x
