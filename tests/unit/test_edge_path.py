"""plan_edge_path 独立单测（#580 BC 骚扰贴边接近）。

验收要点：
1. 所有 waypoint 在地图矩形边界附近（非穿中央）
2. 选了远离 enemy_start 的那条弧（最隐蔽）
3. 首尾包含 start 和 target
4. 弧对应的 waypoint 是矩形角点
"""

from __future__ import annotations

from types import SimpleNamespace

from sc2.position import Point2


def _pa(x=0.0, y=0.0, width=200.0, height=200.0):
    """Mock playable_area。"""
    return SimpleNamespace(x=x, y=y, width=width, height=height)


def _edge_path(start, target, pa, enemy_start):
    from vibecraft.bot.drop_path import plan_edge_path

    return plan_edge_path(
        Point2(start),
        Point2(target),
        pa,
        Point2(enemy_start),
    )


def _boundary_dist(p, pa):
    """最近边界距离。"""
    x0, y0 = pa.x, pa.y
    x1, y1 = x0 + pa.width, y0 + pa.height
    return min(p.x - x0, x1 - p.x, p.y - y0, y1 - p.y)


# ── 基本性质 ──────────────────────────────────────────────────────────────


def test_edge_path_starts_with_start_ends_with_target():
    """首 waypoint = start，末 waypoint = target。"""
    wps = _edge_path((0.0, 0.0), (180.0, 150.0), _pa(), (170.0, 160.0))
    assert wps[0] == Point2((0.0, 0.0))
    assert wps[-1] == Point2((180.0, 150.0))


def test_edge_path_at_least_two_points():
    """路径至少包含 start 和 target。"""
    wps = _edge_path((10.0, 10.0), (190.0, 190.0), _pa(), (180.0, 180.0))
    assert len(wps) >= 2


def test_edge_path_all_interior_waypoints_near_boundary():
    """中间 waypoint（非 start/end）都在矩形边界附近（≤ 3 格）。"""
    pa = _pa()
    wps = _edge_path((0.0, 0.0), (180.0, 150.0), pa, (170.0, 160.0))
    for wp in wps[1:-1]:
        d = _boundary_dist(wp, pa)
        assert d <= 3.0, f"intermediate waypoint {wp} not near boundary (dist={d:.1f})"


def test_edge_path_intermediate_include_corner_waypoints():
    """路径中间段包含角点（弧经过的矩形角）—— 用 HAIKU 小矩形确保必经角。

    start=(0,0)→target=(200,100)，enemy 在底部 → 选 top 弧 → 经 TL(0,200)、TR(200,200)。
    这两个角应出现在中间 waypoint 列表里。
    """
    pa = _pa(width=200.0, height=200.0)
    # enemy 在 bottom → 选 top 弧 (经 TL、TR)
    wps = _edge_path((0.0, 0.0), (200.0, 100.0), pa, (100.0, 1.0))
    interior = wps[1:-1]
    # 至少一个 waypoint 是角点（不是随机边界点）
    x0, y0, x1, y1 = 0.0, 0.0, 200.0, 200.0
    corners = {
        Point2((x0, y0)),
        Point2((x1, y0)),
        Point2((x1, y1)),
        Point2((x0, y1)),
    }
    has_corner = any(wp in corners for wp in interior)
    assert has_corner, f"no corner found in interior waypoints: {interior}"


# ── 弧选取（远离 enemy_start）────────────────────────────────────────────


def test_edge_path_selects_arc_far_from_enemy_bottom_right_not_top():
    """enemy 在右上角 → 选离右上更远的弧（底部/左侧）。

    Start(0,0)→Target(100,200): 两条弧:
      CW(底→右): 经 BR(200,0)、到达右边
      CCW(左→顶): 经 TL(0,200)，贴左边
    enemy 在 (190,190) → CCW(左侧)弧质心离 enemy 更远 → 选 CCW。
    """
    pa = _pa(width=200.0, height=200.0)
    wps = _edge_path((0.0, 0.0), (100.0, 200.0), pa, (190.0, 190.0))
    # CCW 经 TL(0,200)；CW 经 BR(200,0) + TR(200,200)
    # 若选 CCW: 中间 waypoint 应在 x=0 附近（TL）
    interior = wps[1:-1]
    if interior:
        # 质心的 x 坐标：CCW 弧在左侧（x≈0），CW 弧在右侧（x≈200）
        centroid_x = sum(p.x for p in wps) / len(wps)
        enemy_dist_left = ((0 - 190) ** 2 + (100 - 190) ** 2) ** 0.5
        enemy_dist_right = ((200 - 190) ** 2 + (100 - 190) ** 2) ** 0.5
        # 路径质心 x 应偏向离 enemy 更远的侧
        if enemy_dist_left > enemy_dist_right:
            assert centroid_x < 100.0, f"should use left arc (centroid_x={centroid_x:.1f})"
        else:
            assert centroid_x > 100.0, f"should use right arc (centroid_x={centroid_x:.1f})"


def test_edge_path_avoids_path_through_center():
    """路径不穿中央（中间点不在矩形中心附近）。"""
    pa = _pa()
    center = Point2((pa.x + pa.width / 2, pa.y + pa.height / 2))
    wps = _edge_path((5.0, 5.0), (190.0, 190.0), pa, (185.0, 185.0))
    for wp in wps[1:-1]:
        dist_to_center = wp.distance_to(center)
        assert dist_to_center > 20.0, (
            f"waypoint {wp} is near map center (dist={dist_to_center:.1f})"
        )


def test_edge_path_symmetric_enemy_picks_consistently():
    """同一 start/target，两种 enemy 位置→各自选对应弧，互补。"""
    pa = _pa()
    start = (0.0, 100.0)
    target = (200.0, 100.0)

    # enemy 在 top → 选 bottom 弧（经 BL/BR）
    wps_top = _edge_path(start, target, pa, (100.0, 199.0))
    # enemy 在 bottom → 选 top 弧（经 TL/TR）
    wps_bot = _edge_path(start, target, pa, (100.0, 1.0))

    centroid_y_top = sum(p.y for p in wps_top) / len(wps_top)
    centroid_y_bot = sum(p.y for p in wps_bot) / len(wps_bot)
    # enemy-top 时选 bottom 弧 → 质心 y 低
    # enemy-bot 时选 top 弧 → 质心 y 高
    assert centroid_y_top < centroid_y_bot, (
        f"enemy_top centroid_y={centroid_y_top:.1f} should be < enemy_bot centroid_y={centroid_y_bot:.1f}"
    )


# ── 边角情况 ─────────────────────────────────────────────────────────────


def test_edge_path_start_on_boundary_no_extra_proj():
    """start 在边界上（距 proj < 2）→ 不插入多余的 proj_start 点。"""
    pa = _pa()
    # start 在底边 BL 角
    wps = _edge_path((0.0, 0.0), (100.0, 190.0), pa, (150.0, 150.0))
    # 不应有两个完全相同的 (0,0)
    assert wps.count(Point2((0.0, 0.0))) == 1


def test_edge_path_bad_playable_area_falls_back():
    """playable_area 格式错误 → 降级为 [start, target]，不抛异常。"""
    from vibecraft.bot.drop_path import plan_edge_path

    bad_pa = "not_a_rect"
    wps = plan_edge_path(Point2((0.0, 0.0)), Point2((100.0, 100.0)), bad_pa, Point2((150.0, 150.0)))
    assert wps == [Point2((0.0, 0.0)), Point2((100.0, 100.0))]
