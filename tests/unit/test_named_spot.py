"""NamedSpotRegistry 单测。

覆盖:
1. unknown spot → None + warning log
2. own main 用 townhalls.first.position
3. own natural 用 zone_manager.expansion_zones[1]
4. own natural fallback expansion_locations_list (zone_manager 不可用)
5. enemy_main 用 enemy_start_locations[0]
6. enemy_natural 用 enemy_expansion_zones[1]
7. main_ramp 用 bot.main_base_ramp.top_center
8. *_gas 解析 (enemy_main_gas → closest vespene to enemy_main)
9. *_gas 当 base 无法解析时返回 None
10. KNOWN_SPOTS 包含 15 个 spot
11. enemy_third 用 enemy_expansion_zones[2]
12. natural_ramp 用 zone_manager.expansion_zones[1].ramp.top_center
13. townhalls 空时 own_main 返回 None
14. enemy_start_locations 空时 enemy_main 返回 None
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from vibecraft.bot.named_spot import NamedSpotRegistry

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_zone(center: object, ramp_top: object | None = None) -> MagicMock:
    """构造一个 sharpy zone mock。"""
    zone = MagicMock()
    zone.center_location = center
    if ramp_top is not None:
        zone.ramp = MagicMock()
        zone.ramp.top_center = ramp_top
    else:
        zone.ramp = None
    return zone


def _make_bot_with_zone_manager(
    own_zones: list[MagicMock],
    enemy_zones: list[MagicMock],
) -> MagicMock:
    """构造带 sharpy knowledge.zone_manager 的 bot mock。"""
    bot = MagicMock(spec=[])  # spec=[] 让额外属性不被 auto-mock，用 getattr 兜底
    knowledge = MagicMock(spec=[])
    zone_mgr = MagicMock(spec=[])
    zone_mgr.expansion_zones = own_zones
    zone_mgr.enemy_expansion_zones = enemy_zones
    knowledge.zone_manager = zone_mgr
    bot.knowledge = knowledge
    return bot


# ---------------------------------------------------------------------------
# 1. unknown spot → None + warning log
# ---------------------------------------------------------------------------


class TestUnknownSpot:
    def test_unknown_spot_returns_none(self, caplog: pytest.LogCaptureFixture) -> None:
        reg = NamedSpotRegistry()
        bot = MagicMock()
        with caplog.at_level(logging.WARNING, logger="vibecraft.bot.named_spot"):
            result = reg.resolve("totally_made_up", bot)
        assert result is None
        assert "named_spot_unknown" in caplog.text
        assert "totally_made_up" in caplog.text


# ---------------------------------------------------------------------------
# 2. own main 用 townhalls.first.position
# ---------------------------------------------------------------------------


class TestOwnMain:
    def test_own_main_returns_townhall_position(self) -> None:
        reg = NamedSpotRegistry()
        expected = object()
        bot = MagicMock(spec=[])
        townhalls = MagicMock()
        townhalls.__bool__ = lambda self: True  # truthy
        townhalls.first.position = expected
        bot.townhalls = townhalls
        assert reg.resolve("main", bot) is expected

    def test_own_main_townhalls_empty_returns_none(self) -> None:
        """13. townhalls 空时 own_main 返回 None。"""
        reg = NamedSpotRegistry()
        bot = MagicMock(spec=[])
        townhalls = MagicMock()
        townhalls.__bool__ = lambda self: False  # falsy
        bot.townhalls = townhalls
        assert reg.resolve("main", bot) is None

    def test_own_main_no_townhalls_attr_returns_none(self) -> None:
        """townhalls 属性不存在时返回 None。plain object 没有 townhalls attr。"""
        reg = NamedSpotRegistry()
        bot = object()  # plain object, 确实没有 townhalls
        assert reg.resolve("main", bot) is None


# ---------------------------------------------------------------------------
# 3. own natural 用 zone_manager
# ---------------------------------------------------------------------------


class TestOwnNatural:
    def test_natural_from_zone_manager(self) -> None:
        nat_pos = object()
        own_zones = [_make_zone("main_pos"), _make_zone(nat_pos)]
        bot = _make_bot_with_zone_manager(own_zones, enemy_zones=[])
        reg = NamedSpotRegistry()
        assert reg.resolve("natural", bot) is nat_pos

    def test_natural_fallback_expansion_locations_list(self) -> None:
        """4. zone_manager 不可用时，fallback expansion_locations_list[1]。"""
        nat_pos = object()
        bot = MagicMock(spec=[])
        # 没有 knowledge 属性 → getattr(bot, "knowledge", None) = None
        bot.expansion_locations_list = ["main_pos", nat_pos, "third_pos"]
        reg = NamedSpotRegistry()
        assert reg.resolve("natural", bot) is nat_pos

    def test_natural_returns_none_if_only_one_zone(self) -> None:
        own_zones = [_make_zone("main_pos")]
        bot = _make_bot_with_zone_manager(own_zones, enemy_zones=[])
        reg = NamedSpotRegistry()
        assert reg.resolve("natural", bot) is None


# ---------------------------------------------------------------------------
# 5. enemy_main 用 enemy_start_locations[0]
# ---------------------------------------------------------------------------


class TestEnemyMain:
    def test_enemy_main_from_start_locations(self) -> None:
        expected = object()
        bot = MagicMock(spec=[])
        bot.enemy_start_locations = [expected]
        reg = NamedSpotRegistry()
        assert reg.resolve("enemy_main", bot) is expected

    def test_enemy_main_empty_start_locations_returns_none(self) -> None:
        """14. enemy_start_locations 空时返回 None。"""
        bot = MagicMock(spec=[])
        bot.enemy_start_locations = []
        reg = NamedSpotRegistry()
        assert reg.resolve("enemy_main", bot) is None


# ---------------------------------------------------------------------------
# 6. enemy_natural 用 enemy_expansion_zones[1]
# ---------------------------------------------------------------------------


class TestEnemyNatural:
    def test_enemy_natural_from_zone_manager(self) -> None:
        en_nat_pos = object()
        enemy_zones = [_make_zone("enemy_main_pos"), _make_zone(en_nat_pos)]
        bot = _make_bot_with_zone_manager(own_zones=[], enemy_zones=enemy_zones)
        reg = NamedSpotRegistry()
        assert reg.resolve("enemy_natural", bot) is en_nat_pos

    def test_enemy_natural_returns_none_if_only_one_enemy_zone(self) -> None:
        enemy_zones = [_make_zone("enemy_main_pos")]
        bot = _make_bot_with_zone_manager(own_zones=[], enemy_zones=enemy_zones)
        reg = NamedSpotRegistry()
        assert reg.resolve("enemy_natural", bot) is None


# ---------------------------------------------------------------------------
# 7. main_ramp 用 bot.main_base_ramp.top_center
# ---------------------------------------------------------------------------


class TestMainRamp:
    def test_main_ramp_top_center(self) -> None:
        expected = object()
        bot = MagicMock(spec=[])
        ramp = MagicMock()
        ramp.top_center = expected
        bot.main_base_ramp = ramp
        reg = NamedSpotRegistry()
        assert reg.resolve("main_ramp", bot) is expected

    def test_main_ramp_no_ramp_returns_none(self) -> None:
        bot = object()  # no main_base_ramp
        reg = NamedSpotRegistry()
        assert reg.resolve("main_ramp", bot) is None


# ---------------------------------------------------------------------------
# 8. *_gas 解析 + 9. *_gas base 无法解析返回 None
# ---------------------------------------------------------------------------


class TestGasSpots:
    def test_enemy_main_gas_resolves_to_closest_geyser(self) -> None:
        """8. enemy_main_gas → enemy_start_locations[0] → closest vespene."""
        enemy_main_pos = object()
        gas_pos = object()

        bot = MagicMock(spec=[])
        bot.enemy_start_locations = [enemy_main_pos]

        geyser = MagicMock()
        geyser.position = gas_pos
        geysers = MagicMock()
        geysers.__bool__ = lambda self: True
        geysers.closest_to.return_value = geyser
        bot.vespene_geyser = geysers

        reg = NamedSpotRegistry()
        result = reg.resolve("enemy_main_gas", bot)
        assert result is gas_pos
        geysers.closest_to.assert_called_once_with(enemy_main_pos)

    def test_gas_returns_none_when_base_unresolvable(self) -> None:
        """9. base 无法解析时 *_gas 返回 None。"""
        bot = MagicMock(spec=[])
        bot.enemy_start_locations = []  # enemy_main → None
        reg = NamedSpotRegistry()
        assert reg.resolve("enemy_main_gas", bot) is None

    def test_natural_gas_uses_own_natural(self) -> None:
        nat_pos = object()
        gas_pos = object()
        own_zones = [_make_zone("main_pos"), _make_zone(nat_pos)]
        bot = _make_bot_with_zone_manager(own_zones, enemy_zones=[])

        geyser = MagicMock()
        geyser.position = gas_pos
        geysers = MagicMock()
        geysers.__bool__ = lambda self: True
        geysers.closest_to.return_value = geyser
        bot.vespene_geyser = geysers

        reg = NamedSpotRegistry()
        result = reg.resolve("natural_gas", bot)
        assert result is gas_pos
        geysers.closest_to.assert_called_once_with(nat_pos)


# ---------------------------------------------------------------------------
# 10. KNOWN_SPOTS 包含 15 个 spot
# ---------------------------------------------------------------------------


class TestKnownSpots:
    def test_known_spots_count(self) -> None:
        assert len(NamedSpotRegistry.KNOWN_SPOTS) == 15

    def test_known_spots_contains_expected(self) -> None:
        expected = {
            "natural",
            "third",
            "main",
            "enemy_main",
            "enemy_natural",
            "enemy_third",
            "main_ramp",
            "natural_ramp",
            "enemy_main_ramp",
            "natural_gas",
            "third_gas",
            "main_gas",
            "enemy_main_gas",
            "enemy_natural_gas",
            "enemy_third_gas",
        }
        assert expected == NamedSpotRegistry.KNOWN_SPOTS


# ---------------------------------------------------------------------------
# 11. enemy_third 用 enemy_expansion_zones[2]
# ---------------------------------------------------------------------------


class TestEnemyThird:
    def test_enemy_third_from_zone_manager(self) -> None:
        en_third_pos = object()
        enemy_zones = [
            _make_zone("enemy_main_pos"),
            _make_zone("enemy_nat_pos"),
            _make_zone(en_third_pos),
        ]
        bot = _make_bot_with_zone_manager(own_zones=[], enemy_zones=enemy_zones)
        reg = NamedSpotRegistry()
        assert reg.resolve("enemy_third", bot) is en_third_pos

    def test_enemy_third_returns_none_if_less_than_3_enemy_zones(self) -> None:
        enemy_zones = [_make_zone("em"), _make_zone("en")]
        bot = _make_bot_with_zone_manager(own_zones=[], enemy_zones=enemy_zones)
        reg = NamedSpotRegistry()
        assert reg.resolve("enemy_third", bot) is None


# ---------------------------------------------------------------------------
# 12. natural_ramp 用 zone_manager.expansion_zones[1].ramp.top_center
# ---------------------------------------------------------------------------


class TestNaturalRamp:
    def test_natural_ramp_from_zone_manager(self) -> None:
        ramp_top = object()
        own_zones = [_make_zone("main_pos"), _make_zone("nat_pos", ramp_top=ramp_top)]
        bot = _make_bot_with_zone_manager(own_zones, enemy_zones=[])
        reg = NamedSpotRegistry()
        assert reg.resolve("natural_ramp", bot) is ramp_top

    def test_natural_ramp_returns_none_if_no_ramp(self) -> None:
        own_zones = [_make_zone("main_pos"), _make_zone("nat_pos", ramp_top=None)]
        bot = _make_bot_with_zone_manager(own_zones, enemy_zones=[])
        reg = NamedSpotRegistry()
        assert reg.resolve("natural_ramp", bot) is None


# ---------------------------------------------------------------------------
# TestClosestNamedSpot (P5.D)
# ---------------------------------------------------------------------------


def _make_point(x: float, y: float) -> MagicMock:
    """构造有 .x/.y 属性的 Point2 mock。"""
    p = MagicMock()
    p.x = x
    p.y = y
    return p


class TestClosestNamedSpot:
    """closest_named_spot(point, bot, max_distance) 反向查找测试。"""

    def _make_bot_with_spots(
        self,
        own_main: tuple[float, float] | None = None,
        enemy_natural: tuple[float, float] | None = None,
        enemy_main: tuple[float, float] | None = None,
    ) -> MagicMock:
        """构造只有少数 spot 可解析的 bot mock（简化 resolve 结果）。"""
        bot = MagicMock(spec=[])
        # enemy_main 通过 enemy_start_locations
        if enemy_main is not None:
            ep = _make_point(*enemy_main)
            bot.enemy_start_locations = [ep]
        else:
            bot.enemy_start_locations = []

        # own_main 通过 townhalls.first.position
        if own_main is not None:
            mp = _make_point(*own_main)
            townhalls = MagicMock()
            townhalls.__bool__ = lambda self: True
            townhalls.first.position = mp
            bot.townhalls = townhalls
        else:
            townhalls = MagicMock()
            townhalls.__bool__ = lambda self: False
            bot.townhalls = townhalls

        # enemy_natural 通过 zone_manager.enemy_expansion_zones
        if enemy_natural is not None:
            enp = _make_point(*enemy_natural)
            en_zone1 = _make_zone("em")
            en_zone2 = _make_zone(enp)
            # 让 en_zone2.center_location.x/.y 可访问
            en_zone2.center_location = enp
            knowledge = MagicMock(spec=[])
            zone_mgr = MagicMock(spec=[])
            zone_mgr.expansion_zones = []
            zone_mgr.enemy_expansion_zones = [en_zone1, en_zone2]
            knowledge.zone_manager = zone_mgr
            bot.knowledge = knowledge
        return bot

    def test_spot_within_range_returns_name(self) -> None:
        """单位坐标在 enemy_main 15 格内 → 返回 'enemy_main'。"""
        bot = self._make_bot_with_spots(enemy_main=(100.0, 100.0))
        reg = NamedSpotRegistry()
        point = _make_point(105.0, 105.0)  # dist ≈ 7.07 < 15
        result = reg.closest_named_spot(point, bot)
        assert result == "enemy_main"

    def test_spot_outside_range_returns_none(self) -> None:
        """单位坐标离所有 spot 都 > max_distance → None。"""
        bot = self._make_bot_with_spots(enemy_main=(100.0, 100.0))
        reg = NamedSpotRegistry()
        point = _make_point(120.0, 120.0)  # dist ≈ 28.3 > 15
        result = reg.closest_named_spot(point, bot)
        assert result is None

    def test_returns_closest_of_multiple_spots(self) -> None:
        """两个 spot 都在范围内 → 返回距离更近的那个。"""
        # own_main=(0,0), enemy_main=(100,100)
        # point=(8,8): dist_to_main≈11.3, dist_to_enemy_main≈129 → 返回 "main"
        bot = self._make_bot_with_spots(
            own_main=(0.0, 0.0),
            enemy_main=(100.0, 100.0),
        )
        reg = NamedSpotRegistry()
        point = _make_point(8.0, 8.0)
        result = reg.closest_named_spot(point, bot)
        assert result == "main"

    def test_custom_max_distance(self) -> None:
        """max_distance=5 时，距离 7 的 spot 不匹配。"""
        bot = self._make_bot_with_spots(enemy_main=(100.0, 100.0))
        reg = NamedSpotRegistry()
        point = _make_point(105.0, 105.0)  # dist ≈ 7.07
        result = reg.closest_named_spot(point, bot, max_distance=5.0)
        assert result is None

    def test_no_resolvable_spots_returns_none(self) -> None:
        """所有 spot 都无法解析（bot 没有任何数据）→ None。"""
        bot = MagicMock(spec=[])
        # spec=[] 让 getattr 返回 AttributeError，NamedSpotRegistry 兜底 None
        reg = NamedSpotRegistry()
        point = _make_point(50.0, 50.0)
        result = reg.closest_named_spot(point, bot)
        assert result is None
