"""terrain_harass 地形基础层单测（纯几何 + 合成地形,不起 SC2）。

覆盖 F114 探针结论落地:矿后悬崖(terrain_height 突降)判安全口袋 + 地面 BFS(从可走格起步)。
"""

from __future__ import annotations

from types import SimpleNamespace

from sc2.position import Point2

from vibecraft.bot.terrain_harass import (
    bfs_ground_reachable,
    build_enemy_highground_cells,
    find_mineback_pocket,
    path_highground_frac,
    plan_lowground_path,
    point_ground_reachable,
)


def _area(w=200, h=200):
    return SimpleNamespace(x=0, y=0, width=w, height=h)


def test_find_mineback_pocket_finds_cliff():
    """矿后方向上 terrain_height 突降处(悬崖)→ 返回越过悬崖的口袋点。"""
    townhall = Point2((0.0, 0.0))
    mineral_center = Point2((10.0, 0.0))  # 矿后方向 = +x

    # 高地 220,x >= 16 突降到 130(悬崖在 x=16,即矿线中心外 +6)
    def terrain_height(p):
        return 130.0 if float(p.x) >= 16.0 else 220.0

    pocket = find_mineback_pocket(townhall, mineral_center, terrain_height, _area())
    assert pocket is not None
    assert pocket.x > 16.0  # 越过悬崖边(在低地侧)
    assert abs(pocket.y) < 0.5  # 仍在矿后方向线上


def test_find_mineback_pocket_flat_returns_none():
    """平坦矿(无悬崖突降)→ None,调用方回退(不崩)。"""
    townhall = Point2((0.0, 0.0))
    mineral_center = Point2((10.0, 0.0))

    pocket = find_mineback_pocket(townhall, mineral_center, lambda p: 220.0, _area())
    assert pocket is None


def test_bfs_ground_reachable_blocked_by_wall():
    """地面 BFS 遇不可走墙(悬崖)止步:墙另一侧不可达。"""

    # x=5 一整列不可走(悬崖带),其余可走
    def is_pathable(c):
        return c[0] != 5

    reachable = bfs_ground_reachable(
        seeds=[Point2((0.0, 0.0))], is_pathable=is_pathable, bounds=(0, 0, 10, 10)
    )
    assert (2, 0) in reachable  # 墙这侧可达
    assert (4, 3) in reachable
    assert (6, 0) not in reachable  # 墙另一侧够不到
    assert (8, 5) not in reachable


def test_bfs_seed_on_unpathable_yields_nothing():
    """实现坑(F114):seed 落在不可走格(如基地建筑)→ BFS 卡死、集合空。"""
    reachable = bfs_ground_reachable(
        seeds=[Point2((3.0, 3.0))], is_pathable=lambda c: False, bounds=(0, 0, 10, 10)
    )
    assert reachable == set()


def test_point_ground_reachable_radius():
    """射程内有可达格才算敌地面够得到(F2)。"""
    reachable = {(5, 5), (6, 5)}
    assert point_ground_reachable(Point2((5.0, 5.0)), reachable, radius=0) is True
    assert point_ground_reachable(Point2((8.0, 5.0)), reachable, radius=0) is False
    # radius=3:(8,5) 距 (6,5)=2 <=3 → 够得到
    assert point_ground_reachable(Point2((8.0, 5.0)), reachable, radius=3) is True


# ── 低地路由（F122 真解）────────────────────────────────────────────────────────


def test_highground_cells_only_enemy_half():
    """评审必改①:两侧同高时,只有**敌方半场**的高地格进集合,自家高地不被惩罚。"""
    area = SimpleNamespace(x=0, y=0, width=40, height=40)
    enemy_start = Point2((35.0, 20.0))
    my_start = Point2((5.0, 20.0))
    # 全图同高 220(双方主基同台面);cliff_margin=10 → thresh=210,全格过高度门
    high = build_enemy_highground_cells(
        lambda p: 220.0, area, enemy_start, my_start, cliff_margin=10.0
    )
    assert (30, 20) in high  # 敌方半场高地 → 罚
    assert (10, 20) not in high  # 我方半场(同样高)→ 不罚(否则路径在自家台面乱绕)
    assert (5, 20) not in high


def test_highground_cells_low_ground_excluded():
    """低地格(terrain_height 低于阈值)即便在敌方半场也不进集合。"""
    area = SimpleNamespace(x=0, y=0, width=40, height=40)
    enemy_start = Point2((35.0, 20.0))
    my_start = Point2((5.0, 20.0))

    # 敌方半场高、我方半场也高,但 y<10 是一条低地带(悬崖下)
    def th(p):
        return 130.0 if float(p.y) < 10.0 else 220.0

    high = build_enemy_highground_cells(th, area, enemy_start, my_start, cliff_margin=10.0)
    assert (30, 20) in high  # 敌方高地
    assert (30, 5) not in high  # 敌方半场但低地带 → 不罚(凤凰可走的低地)


def test_lowground_path_routes_around_highblock():
    """A* 绕开中间高地块:路径落高地格比例明显低于直线。"""
    area = SimpleNamespace(x=0, y=0, width=20, height=20)
    start, end = Point2((0.0, 10.0)), Point2((20.0, 10.0))
    # 中间一块高地(x 8..12, y 8..12),直线正穿它
    high = frozenset((x, y) for x in range(8, 13) for y in range(8, 13))
    path = plan_lowground_path(start, end, high, area, high_penalty=8.0, max_detour=2.0)
    assert path is not None
    assert len(path) >= 3  # 有拐点(绕了)
    frac_routed = path_highground_frac(path, high)
    frac_straight = path_highground_frac([start, end], high)
    assert frac_straight > 0.2  # 直线确实穿高地
    assert frac_routed < frac_straight  # 绕后落高地格更少
    assert frac_routed < 0.1


def test_lowground_path_same_cell():
    """start==end 同格 → 直接 [start,end],不跑 A*。"""
    area = SimpleNamespace(x=0, y=0, width=20, height=20)
    p = Point2((5.0, 5.0))
    assert plan_lowground_path(p, p, frozenset(), area) == [p, p]


def test_lowground_path_detour_guard_returns_none():
    """绕路超 max_detour → None(调用方回退 snap),不硬绕大圈(F122 教训)。"""
    area = SimpleNamespace(x=0, y=0, width=20, height=20)
    start, end = Point2((0.0, 10.0)), Point2((20.0, 10.0))
    high = frozenset((x, y) for x in range(8, 13) for y in range(8, 13))
    # max_detour=1.0 卡死:任何绕行都超 → None
    path = plan_lowground_path(start, end, high, area, high_penalty=50.0, max_detour=1.0)
    assert path is None


def test_lowground_path_avoids_aa_point():
    """动态 AA/军队惩罚:avoid_pt 在直线上 → 路径绕开(评审必改②)。"""
    area = SimpleNamespace(x=0, y=0, width=20, height=20)
    start, end = Point2((0.0, 10.0)), Point2((20.0, 10.0))
    # 无高地,但 (10,10) 有 AA 威胁
    path = plan_lowground_path(
        start,
        end,
        frozenset(),
        area,
        avoid_pts=[Point2((10.0, 10.0))],
        aa_penalty=8.0,
        aa_r=4.0,
        max_detour=2.0,
    )
    assert path is not None
    # 路径任一顶点不应贴着 AA 点(绕开了)
    min_d = min(((float(p.x) - 10.0) ** 2 + (float(p.y) - 10.0) ** 2) ** 0.5 for p in path)
    assert min_d >= 2.0  # 绕出了 AA 核心圈
