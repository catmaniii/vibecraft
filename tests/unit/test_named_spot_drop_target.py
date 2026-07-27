"""DropTarget 解析 + 钟点位置 + 矿区 drop_pos 圆周贴边优化。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vibecraft.bot.named_spot import NamedSpotRegistry


@pytest.fixture
def reg() -> NamedSpotRegistry:
    return NamedSpotRegistry()


def _mock_zone(center: tuple[float, float], behind_mineral: tuple[float, float]):
    """sharpy Zone 的极简 mock。"""
    from sc2.position import Point2

    z = MagicMock()
    z.center_location = Point2(center)
    z.behind_mineral_position_center = Point2(behind_mineral)
    return z


def _mock_bot_with_zones(zones: list, playable_size: tuple[float, float] = (160, 160)):
    """bot 含 knowledge.zone_manager.enemy_expansion_zones + expansion_locations_list。"""
    from sc2.position import Point2

    bot = MagicMock()
    bot.knowledge.zone_manager.enemy_expansion_zones = zones
    bot.game_info.map_center = Point2((playable_size[0] / 2, playable_size[1] / 2))
    # playable_area: x/y/width/height
    bot.game_info.playable_area = MagicMock(
        x=0, y=0, width=playable_size[0], height=playable_size[1]
    )
    # expansion_locations_list 给 clock_X 用
    bot.expansion_locations_list = [z.center_location for z in zones]
    return bot


class TestResolveDropTarget:
    def test_enemy_main_mineral(self, reg: NamedSpotRegistry) -> None:
        zones = [
            _mock_zone((48, 28), behind_mineral=(45, 31)),  # enemy_main
        ]
        bot = _mock_bot_with_zones(zones)
        result = reg.resolve_drop_target("enemy_main:mineral", bot)
        assert result is not None
        assert result.zone_kind == "mineral"
        assert result.base_index == 0
        assert result.source_spec == "enemy_main:mineral"
        # mineral drop_pos 经过 optimize_drop_pos_to_edge 处理
        # M=(45,31), playable 160x160, 最近边 = bottom (y=31 距 0 是 31, 最近)
        # 实际 dl=45, dr=115, dt=129, db=31 → bottom 最近
        # drop_pos = (45, 31-15) = (45, 16)
        assert result.position.x == pytest.approx(45)
        assert result.position.y == pytest.approx(16)

    def test_enemy_main_safe_edge(self, reg: NamedSpotRegistry) -> None:
        """safe_edge: zone.center_location 沿最近地图边推到边缘留 2 格 clearance。
        保证棱镜 warp 远离 nexus 视野(≥ 25 grid)。"""
        zones = [_mock_zone((48, 28), behind_mineral=(45, 31))]
        bot = _mock_bot_with_zones(zones)
        result = reg.resolve_drop_target("enemy_main:safe_edge", bot)
        assert result is not None
        assert result.zone_kind == "safe_edge"
        assert result.base_index == 0
        # nexus (48, 28), playable 0..160, 最近边 = bottom (db=28)
        # safe_edge = (48, 0 + 2) = (48, 2)
        assert result.position.x == pytest.approx(48)
        assert result.position.y == pytest.approx(2)
        # 距 nexus 至少 25 grid
        dist = ((result.position.x - 48) ** 2 + (result.position.y - 28) ** 2) ** 0.5
        assert dist >= 25

    def test_safe_edge_other_corner(self, reg: NamedSpotRegistry) -> None:
        """另一对角线 spawn:(127, 119) → 右边最近(dr=160-127=33 < dt=41)。"""
        zones = [_mock_zone((127, 119), behind_mineral=(130, 116))]
        bot = _mock_bot_with_zones(zones)
        result = reg.resolve_drop_target("enemy_main:safe_edge", bot)
        assert result is not None
        # safe_edge = (160 - 2, 119) = (158, 119)
        assert result.position.x == pytest.approx(158)
        assert result.position.y == pytest.approx(119)
        dist = ((result.position.x - 127) ** 2 + (result.position.y - 119) ** 2) ** 0.5
        assert dist >= 25

    def test_enemy_main_production(self, reg: NamedSpotRegistry) -> None:
        zones = [_mock_zone((48, 28), behind_mineral=(45, 31))]
        bot = _mock_bot_with_zones(zones)
        result = reg.resolve_drop_target("enemy_main:production", bot)
        assert result is not None
        assert result.zone_kind == "production"
        # production = center_location 直接(无 optimize)
        assert result.position.x == pytest.approx(48)
        assert result.position.y == pytest.approx(28)

    def test_enemy_natural_mineral_default(self, reg: NamedSpotRegistry) -> None:
        zones = [
            _mock_zone((48, 28), behind_mineral=(45, 31)),
            _mock_zone((30, 49), behind_mineral=(33, 52)),
        ]
        bot = _mock_bot_with_zones(zones)
        result = reg.resolve_drop_target("enemy_natural:mineral", bot)
        assert result is not None
        assert result.base_index == 1

    def test_clock_11_mineral(self, reg: NamedSpotRegistry) -> None:
        """clock_X 找钟点方向最近 expansion。"""
        from sc2.position import Point2

        from vibecraft.bot.named_spot import _clock_at_expansion

        # map_center=(80,80). 11 点钟方向(约 60°): 应取左上扩张点。
        # zones[0] enemy_main (48, 28) — 4-5 点方向(下偏左)
        # zones[1] (30, 49)        — 9 点方向(左)
        # zones[2] (50, 130)       — 11 点方向(左上),target_angle ≈ 60° (12点=90°)
        zones = [
            _mock_zone((48, 28), behind_mineral=(45, 31)),
            _mock_zone((30, 49), behind_mineral=(33, 52)),
            _mock_zone((50, 130), behind_mineral=(53, 127)),
        ]
        bot = _mock_bot_with_zones(zones)

        # 直接验证 _clock_at_expansion 选了 (50, 130)
        assert _clock_at_expansion(11, bot) == Point2((50, 130))

        # 再验证 resolve_drop_target 返回正确 spec
        result = reg.resolve_drop_target("clock_11:mineral", bot)
        assert result is not None
        assert result.source_spec == "clock_11:mineral"

    def test_clock_X_production_invalid(self, reg: NamedSpotRegistry) -> None:
        """clock_X 没有 production 概念。"""
        zones = [_mock_zone((48, 28), behind_mineral=(45, 31))]
        bot = _mock_bot_with_zones(zones)
        result = reg.resolve_drop_target("clock_11:production", bot)
        assert result is None  # 不允许

    def test_enemy_main_ramp_outside(self, reg: NamedSpotRegistry) -> None:
        """enemy_main:ramp_outside → zone.ramp.bottom_center 向外偏 5 格。"""
        from sc2.position import Point2

        # zone center=(48, 28), ramp.bottom_center=(48, 20)
        # towards(center=(48,28), distance=-5) → 从 bottom(48,20) 向远离 center 方向走 5
        # direction: center - bottom = (0, 8) → unit=(0,1) → towards(-5)=(48, 20-5)=(48, 15)
        zone = _mock_zone((48, 28), behind_mineral=(45, 31))
        zone.ramp = MagicMock()
        zone.ramp.bottom_center = Point2((48, 20))
        bot = _mock_bot_with_zones([zone])
        result = reg.resolve_drop_target("enemy_main:ramp_outside", bot)
        assert result is not None
        assert result.zone_kind == "ramp_outside"
        assert result.base_index == 0
        assert result.source_spec == "enemy_main:ramp_outside"
        # towards(target=(48,28), distance=-5) from (48,20):
        # vector=(0,8), length=8, unit=(0,1), result=(48, 20 + (-5)*1)=(48,15)
        assert result.position.x == pytest.approx(48)
        assert result.position.y == pytest.approx(15)

    def test_unknown_spec(self, reg: NamedSpotRegistry) -> None:
        zones = [_mock_zone((48, 28), behind_mineral=(45, 31))]
        bot = _mock_bot_with_zones(zones)
        assert reg.resolve_drop_target("garbage:mineral", bot) is None
        assert reg.resolve_drop_target("enemy_main:garbage", bot) is None


class TestOptimizeDropPosToEdge:
    """矿区 drop_pos 圆周贴最近地图边."""

    def test_close_to_left(self, reg: NamedSpotRegistry) -> None:
        from sc2.position import Point2

        from vibecraft.bot.named_spot import _optimize_drop_pos_to_edge

        playable = MagicMock(x=0, y=0, width=160, height=160)
        # M=(30, 80), 最近边 left(距 30)
        pos = _optimize_drop_pos_to_edge(Point2((30, 80)), R=15, playable=playable)
        assert pos.x == pytest.approx(15)  # 30-15
        assert pos.y == pytest.approx(80)

    def test_close_to_bottom(self, reg: NamedSpotRegistry) -> None:
        from sc2.position import Point2

        from vibecraft.bot.named_spot import _optimize_drop_pos_to_edge

        playable = MagicMock(x=0, y=0, width=160, height=160)
        # M=(80, 30), 最近边 bottom(距 30)
        pos = _optimize_drop_pos_to_edge(Point2((80, 30)), R=15, playable=playable)
        assert pos.x == pytest.approx(80)
        assert pos.y == pytest.approx(15)  # 30-15


class TestClockAtExpansion:
    def test_clock_0_is_right(self, reg: NamedSpotRegistry) -> None:
        """clock 3 = 正右方 (atan2 angle 0)."""
        from sc2.position import Point2

        from vibecraft.bot.named_spot import _clock_at_expansion

        bot = MagicMock()
        bot.game_info.map_center = Point2((80, 80))
        bot.expansion_locations_list = [
            Point2((150, 80)),  # 3 点钟方向(正右)
            Point2((80, 150)),  # 12 点钟方向(正上,SC2 +y 向上)
        ]
        assert _clock_at_expansion(3, bot) == Point2((150, 80))
        assert _clock_at_expansion(12, bot) == Point2((80, 150))
        # 12 和 0 应该等价
        assert _clock_at_expansion(0, bot) == _clock_at_expansion(12, bot)
