# tests/unit/test_drop_path.py
"""路径递归细分算法:A→B 穿过敌方基地时插入转折点 C。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vibecraft.bot.drop_path import (
    first_blocking_zone,
    plan_drop_path,
    project_point_onto_segment,
)


def _mock_zone_at(center: tuple[float, float], has_townhall: bool):
    """sharpy Zone mock,可选是否已确定有敌方基地。"""
    from sc2.position import Point2

    z = MagicMock()
    z.center_location = Point2(center)
    z.behind_mineral_position_center = Point2(center)  # 简化
    if has_townhall:
        # known_enemy_units.of_type(...).exists = True
        units = MagicMock()
        units.exists = True
        z.known_enemy_units.of_type = MagicMock(return_value=units)
    else:
        units = MagicMock()
        units.exists = False
        z.known_enemy_units.of_type = MagicMock(return_value=units)
    return z


def _mock_bot(
    zones: list,
    playable_x: float = 0,
    playable_y: float = 0,
    playable_w: float = 200,
    playable_h: float = 200,
):
    bot = MagicMock()
    bot.knowledge.zone_manager.enemy_expansion_zones = zones
    bot.game_info.playable_area = MagicMock(
        x=playable_x, y=playable_y, width=playable_w, height=playable_h
    )
    return bot


class TestProjectPointOntoSegment:
    def test_midpoint(self) -> None:
        from sc2.position import Point2

        A = Point2((0, 0))
        B = Point2((10, 0))
        M = Point2((5, 5))  # 在 AB 中点正上方
        P = project_point_onto_segment(M, A, B)
        assert P.x == pytest.approx(5)
        assert P.y == pytest.approx(0)

    def test_clamped_to_endpoint(self) -> None:
        """M 投影超出 AB 段 → clamp 到端点。"""
        from sc2.position import Point2

        A = Point2((0, 0))
        B = Point2((10, 0))
        M = Point2((20, 5))  # 在 B 右侧
        P = project_point_onto_segment(M, A, B)
        assert P.x == pytest.approx(10)  # = B.x
        assert P.y == pytest.approx(0)


class TestFirstBlockingZone:
    def test_unscouted_non_main_zone_not_blocker(self) -> None:
        """非主基地的 zone 没侦察到 townhall → 不算 blocker。
        2026-05-24:zones[0] = 敌方主基地无条件 block,加 dummy zones[0]
        在远处把待测 zone 放 zones[1]。"""
        from sc2.position import Point2

        zones = [
            _mock_zone_at((200, 200), has_townhall=False),  # dummy 主基地,远离 AB 段
            _mock_zone_at((50, 50), has_townhall=False),  # 待测扩张点
        ]
        bot = _mock_bot(zones)
        result = first_blocking_zone(Point2((0, 0)), Point2((100, 100)), bot, R=15)
        assert result is None

    def test_blocker_detected(self) -> None:
        """有 townhall 的 zone 在 AB 段距 < R → blocker。"""
        from sc2.position import Point2

        zones = [_mock_zone_at((50, 50), has_townhall=True)]
        bot = _mock_bot(zones)
        # AB 段 (0,0)→(100,100) 经过 (50,50),距离 0
        result = first_blocking_zone(Point2((0, 0)), Point2((100, 100)), bot, R=15)
        assert result is not None

    def test_far_zone_not_blocker(self) -> None:
        """zone 距 AB 段 > R → 不算 blocker(zones[0] 也得真在路径上才 block)。"""
        from sc2.position import Point2

        zones = [_mock_zone_at((90, 10), has_townhall=True)]
        bot = _mock_bot(zones)
        # AB 段 (0,0)→(0,100) 经过 x=0,zone x=90 距 90 > R=15
        result = first_blocking_zone(Point2((0, 0)), Point2((0, 100)), bot, R=15)
        assert result is None


class TestEnemyMainAlwaysBlocker:
    """2026-05-24 修:敌方主基地(enemy_expansion_zones[0])位置开局已知,
    无条件当 blocker;不要求侦察到 townhall。
    其他扩张点保留 _zone_has_known_townhall 过滤。"""

    def test_unscouted_enemy_main_still_blocks(self) -> None:
        """zones[0] 没侦察到 townhall(has_townhall=False),但仍当 blocker。"""
        from sc2.position import Point2

        # zones[0] = 敌方主基地,未侦察
        zones = [_mock_zone_at((50, 50), has_townhall=False)]
        bot = _mock_bot(zones)
        result = first_blocking_zone(Point2((0, 0)), Point2((100, 100)), bot, R=15)
        assert result is not None  # 主基地无条件 block

    def test_unscouted_non_main_expansion_not_blocker(self) -> None:
        """zones[1] 是扩张点,没侦察 → 不算 blocker(不知道对方扩没扩,绕行无意义)。"""
        from sc2.position import Point2

        # 用 AB=(0,0)→(0,100)(沿 y 轴),把 main 放 x=200 远离 AB(距 200>R)
        zones = [
            _mock_zone_at((200, 50), has_townhall=False),  # main 远离 AB,不 block
            _mock_zone_at((0, 50), has_townhall=False),  # 扩张点在 AB 上,但没侦察
        ]
        bot = _mock_bot(zones)
        result = first_blocking_zone(Point2((0, 0)), Point2((0, 100)), bot, R=15)
        assert result is None  # 扩张点未侦察,不 block

    def test_unscouted_enemy_main_path_inserts_detour(self) -> None:
        """端到端:home → warp_pos 直线穿过未侦察的敌方主基地 → 路径插 C 绕开。"""
        from sc2.position import Point2

        # home (48.5, 28.5) → warp_pos (126.7, 134.0),敌方主基地 (127.5, 119.5)
        # 直线距 enemy_main ~9.3 格 < R=15 → 应触发绕行
        zones = [_mock_zone_at((127.5, 119.5), has_townhall=False)]
        bot = _mock_bot(zones)
        path = plan_drop_path(Point2((48.5, 28.5)), Point2((126.7, 134.0)), bot)
        # 应该插入 C 点 → len >= 3
        assert len(path) >= 3
        # C 点远离敌方主基地
        for c in path[1:-1]:
            dist = ((c.x - 127.5) ** 2 + (c.y - 119.5) ** 2) ** 0.5
            assert dist >= 15


class TestCPointClampedToPlayable:
    """2026-05-24 修:plan_drop_path 算的 C 点必须 clamp 到 playable_area 内。
    否则棱镜飞到 playable 边缘后无法继续(SC2 不让出界),永远卡死。"""

    def test_c_point_clamped_when_outside_playable_top(self) -> None:
        """C 点在 playable.y+height 之上 → clamp 到 playable_area 内。

        场景:warp_pos 紧贴 playable top edge,enemy_main 也在 top 附近 →
        plan_drop_path 算 C = M + dir * 20 沿 top 方向超出 playable。
        """
        from sc2.position import Point2

        # playable: y=12 到 y=134 (height=122)
        zones = [_mock_zone_at((127, 119), has_townhall=False)]
        bot = _mock_bot(zones, playable_x=0, playable_y=12, playable_w=160, playable_h=122)

        path = plan_drop_path(Point2((48, 28)), Point2((127, 132)), bot)
        # 所有 waypoint 必须在 playable_area 内(留点 buffer)
        for wp in path:
            assert wp.x >= 0 and wp.x <= 160, f"wp x={wp.x} 出 playable"
            assert wp.y >= 12 and wp.y <= 134, f"wp y={wp.y} 出 playable [12, 134]"

    def test_c_point_unchanged_when_inside_playable(self) -> None:
        """C 点在 playable_area 内 → 不动。"""
        from sc2.position import Point2

        zones = [_mock_zone_at((100, 100), has_townhall=True)]
        bot = _mock_bot(zones, playable_x=0, playable_y=0, playable_w=200, playable_h=200)

        path = plan_drop_path(Point2((50, 50)), Point2((150, 150)), bot)
        # C 在 (100, 100) 附近 + push 20 grid,playable 内
        assert len(path) >= 3
        for wp in path:
            assert 0 <= wp.x <= 200 and 0 <= wp.y <= 200


class TestPlanDropPath:
    def test_no_blocker_returns_AB(self) -> None:
        """无 blocker → 返回 [A, B]。"""
        from sc2.position import Point2

        bot = _mock_bot([])
        path = plan_drop_path(Point2((0, 0)), Point2((100, 100)), bot)
        assert len(path) == 2
        assert path[0] == Point2((0, 0))
        assert path[1] == Point2((100, 100))

    def test_one_blocker_inserts_one_point(self) -> None:
        """1 个 blocker → 插入 1 个 C,返回 3 点。"""
        from sc2.position import Point2

        zones = [_mock_zone_at((50, 50), has_townhall=True)]
        bot = _mock_bot(zones)
        path = plan_drop_path(Point2((0, 0)), Point2((100, 100)), bot)
        assert len(path) == 3
        # C 应远离 (50, 50)
        C = path[1]
        dist_to_blocker = ((C.x - 50) ** 2 + (C.y - 50) ** 2) ** 0.5
        assert dist_to_blocker >= 15  # R_MINERAL_AVOID

    def test_max_depth_fallback(self) -> None:
        """超 max_depth(=3) fallback 原 AB 直线。"""
        from sc2.position import Point2

        # 故意 blocker 集中 → 递归很深 → max_depth tripped
        zones = [
            _mock_zone_at((25, 25), has_townhall=True),
            _mock_zone_at((50, 50), has_townhall=True),
            _mock_zone_at((75, 75), has_townhall=True),
            _mock_zone_at((40, 60), has_townhall=True),
        ]
        bot = _mock_bot(zones)
        path = plan_drop_path(Point2((0, 0)), Point2((100, 100)), bot)
        # 不爆栈;不超 2^max_depth + 1 = 9 个点(实际可能更少)
        assert 2 <= len(path) <= 9


# ── plan_air_path 地形感知选路（D60，2026-07-25 snap 版；评审后弃 A*）──────────────

from types import SimpleNamespace  # noqa: E402

from vibecraft.bot.drop_path import air_path_ground_frac, plan_air_path  # noqa: E402


def _pa(w=40, h=40):
    return SimpleNamespace(x=0, y=0, width=w, height=h)


def _pathable_from_cliff(cliff_pred):
    """造 is_pathable 回调(= ai.in_pathing_grid 语义):True=地面可走;cliff_pred 命中的格=悬崖不可走。"""

    def fn(p):
        return not cliff_pred(round(float(p.x)), round(float(p.y)))

    return fn


def test_air_path_snaps_onto_cliff():
    """贴近直线有悬崖带时，snap 把中间点贴到悬崖 → 路径少走地面(air_frac 更低)。"""
    from sc2.position import Point2

    # 竖向悬崖带 x∈[6,8]；start/end 都在 x=5 地面上，直线全走地面。
    is_pathable = _pathable_from_cliff(lambda x, y: 6 <= x <= 8)
    start, end = Point2((5.0, 5.0)), Point2((5.0, 35.0))

    path = plan_air_path(start, end, [], is_pathable, _pa())
    assert path[0] is start and path[-1] is end

    straight_frac = air_path_ground_frac([start, end], is_pathable)  # 直线：全地面 → ~1.0
    air_frac = air_path_ground_frac(path, is_pathable)  # snap 后：贴悬崖 → 更低
    assert straight_frac > 0.9
    assert air_frac < straight_frac  # 真的少走地面(贴悬崖)


def test_air_path_fallback_when_no_is_pathable():
    """is_pathable=None → 回退纯几何 plan_avoid_path(与直接调它同结果)。"""
    from sc2.position import Point2

    from vibecraft.bot.drop_path import plan_avoid_path

    start, end = Point2((0.0, 0.0)), Point2((50.0, 50.0))
    got = plan_air_path(start, end, [], None, _pa(60, 60))
    want = plan_avoid_path(start, end, [], _pa(60, 60))
    assert [(p.x, p.y) for p in got] == [(p.x, p.y) for p in want]


def test_air_path_all_ground_no_detour():
    """全是地面(无悬崖可 snap)→ 不绕路，路径≈直线、waypoint 少(评审 F2:半径受限不绕大圈)。"""
    import math
    from itertools import pairwise

    from sc2.position import Point2

    is_pathable = _pathable_from_cliff(lambda x, y: False)  # 全地面
    start, end = Point2((5.0, 5.0)), Point2((35.0, 35.0))
    path = plan_air_path(start, end, [], is_pathable, _pa())
    assert path[0] is start and path[-1] is end
    assert len(path) <= 4  # 无处可 snap → 近直线
    straight = math.dist((start.x, start.y), (end.x, end.y))
    plen = sum(math.dist((a.x, a.y), (b.x, b.y)) for a, b in pairwise(path))
    assert plen <= straight * 1.4  # 绕路比 ≤ budget(评审 F2 硬上限精神)


def test_air_path_preserves_enemy_avoidance():
    """snap 版复用 plan_avoid_path 作 base → 敌基绕行照旧保留(评审 F3:不破坏既有几何)。"""
    import math

    from sc2.position import Point2

    is_pathable = _pathable_from_cliff(lambda x, y: False)  # 全地面,隔离 snap,只看 base 绕敌基
    start, end = Point2((5.0, 30.0)), Point2((55.0, 30.0))
    enemy = Point2((30.0, 30.0))  # 正挡在直线中点
    path = plan_air_path(start, end, [enemy], is_pathable, _pa(60, 60), r_avoid=8.0)
    assert all(math.dist((p.x, p.y), (enemy.x, enemy.y)) > 6.0 for p in path[1:-1])
