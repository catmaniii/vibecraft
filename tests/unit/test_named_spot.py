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
10. KNOWN_SPOTS 包含 70 个 spot
11. enemy_third 用 enemy_expansion_zones[2]
12. natural_ramp 用 zone_manager.expansion_zones[1].ramp.top_center
13. townhalls 空时 own_main 返回 None
14. enemy_start_locations 空时 enemy_main 返回 None
15. enemy_clock_11 → expansion NW of enemy_main
16. own_clock_5 → expansion SE of own_main
17. clock_3 无前缀 → map_center 锚点
18. enemy_top alias → enemy_clock_12
19. own_top_left alias → own_clock_11
20. clock_0 / clock_13 → out of range → None
21. watchtower_left/right regression
22. resolve_drop_target clock_X regression
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

    def test_own_main_picks_townhall_closest_to_start_deterministic(self) -> None:
        """2026-06-17 真因回归:多基地时 "main" 必须取**距 start_location 最近**的 townhall,
        **不能用 townhalls.first**(帧间顺序不稳 → 解析跳变 → 下游目标点抖、航母回家抽搐)。
        """
        reg = NamedSpotRegistry()
        start = object()
        main_th = MagicMock()
        main_th.position = object()  # 期望返回这个(最近的)
        expansion_th = MagicMock()
        expansion_th.position = object()  # first 可能是它(顺序不稳),但不该被选
        bot = MagicMock(spec=[])
        townhalls = MagicMock()
        townhalls.__bool__ = lambda self: True
        townhalls.first.position = expansion_th.position  # 模拟 first 返回了分基地
        townhalls.closest_to = lambda s: main_th if s is start else expansion_th
        bot.townhalls = townhalls
        bot.start_location = start
        # 必须返回 closest_to(start) 的位置,而不是 first 的位置
        assert reg.resolve("main", bot) is main_th.position
        assert reg.resolve("main", bot) is not expansion_th.position

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
        # 18 original + 1 forward + 12 clock_base + 12 own_clock + 12 enemy_clock
        # + 8 own_direction + 8 enemy_direction = 71
        assert len(NamedSpotRegistry.KNOWN_SPOTS) == 71

    def test_known_spots_contains_original_spots(self) -> None:
        expected_original = {
            "natural",
            "third",
            "main",
            "enemy_main",
            "enemy_natural",
            "enemy_third",
            "main_ramp",
            "natural_ramp",
            "enemy_main_ramp",
            "watchtower",
            "watchtower_left",
            "watchtower_right",
            "natural_gas",
            "third_gas",
            "main_gas",
            "enemy_main_gas",
            "enemy_natural_gas",
            "enemy_third_gas",
        }
        assert expected_original.issubset(NamedSpotRegistry.KNOWN_SPOTS)

    def test_known_spots_contains_clock_spots(self) -> None:
        for i in range(1, 13):
            assert f"clock_{i}" in NamedSpotRegistry.KNOWN_SPOTS
            assert f"own_clock_{i}" in NamedSpotRegistry.KNOWN_SPOTS
            assert f"enemy_clock_{i}" in NamedSpotRegistry.KNOWN_SPOTS

    def test_known_spots_contains_direction_spots(self) -> None:
        directions = [
            "top",
            "bottom",
            "left",
            "right",
            "top_left",
            "top_right",
            "bottom_left",
            "bottom_right",
        ]
        for d in directions:
            assert f"own_{d}" in NamedSpotRegistry.KNOWN_SPOTS
            assert f"enemy_{d}" in NamedSpotRegistry.KNOWN_SPOTS


class TestWatchtower:
    """2026-05-25 bug 6+7:Xel'Naga 瞭望塔解析。"""

    def _make_bot_with_towers(self, positions: list[tuple[float, float]]) -> MagicMock:
        """构造带 bot.all_units(XELNAGATOWER) 返回指定坐标 towers 的 bot mock。"""
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.position import Point2

        bot = MagicMock(spec=[])

        def all_units_fn(type_id: object) -> list[MagicMock]:
            if type_id == UnitTypeId.XELNAGATOWER:
                towers = []
                for px, py in positions:
                    t = MagicMock()
                    t.position = Point2((px, py))
                    towers.append(t)
                return towers
            return []

        bot.all_units = all_units_fn
        return bot

    def test_watchtower_left_picks_min_x(self) -> None:
        reg = NamedSpotRegistry()
        bot = self._make_bot_with_towers([(50, 30), (10, 30)])
        pos = reg.resolve("watchtower_left", bot)
        assert pos is not None and pos.x == 10

    def test_watchtower_right_picks_max_x(self) -> None:
        reg = NamedSpotRegistry()
        bot = self._make_bot_with_towers([(50, 30), (10, 30)])
        pos = reg.resolve("watchtower_right", bot)
        assert pos is not None and pos.x == 50

    def test_watchtower_no_towers_returns_none(self) -> None:
        reg = NamedSpotRegistry()
        bot = self._make_bot_with_towers([])
        assert reg.resolve("watchtower_left", bot) is None
        assert reg.resolve("watchtower_right", bot) is None
        assert reg.resolve("watchtower", bot) is None

    def test_watchtower_single_tower_returns_same_for_left_right(self) -> None:
        reg = NamedSpotRegistry()
        bot = self._make_bot_with_towers([(30, 20)])
        left = reg.resolve("watchtower_left", bot)
        right = reg.resolve("watchtower_right", bot)
        assert left is not None and right is not None
        assert (left.x, left.y) == (right.x, right.y) == (30, 20)


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


class TestForward:
    """2026-05-27 用户:"前线" named_spot(real crash trigger 修正)。

    crash 触发链:玩家"前线去个农民刷个水晶方便折跃追猎"
    → LLM 误判为 named_spot="enemy_main"(送农民去敌方主基地造水晶)
    → directive 渲染时 build_at.point=None 触发 unpack crash。

    fix:加 named_spot="forward",LLM few_shot 显式指定"前线"映射 forward。
    """

    def _make_bot(
        self,
        enemy_main: object,
        townhall_positions: list[object],
        ramp_bottom: object | None = None,
        ramp_top: object | None = None,
    ) -> MagicMock:
        bot = MagicMock(spec=[])
        bot.enemy_start_locations = [enemy_main] if enemy_main is not None else []
        if townhall_positions:
            townhalls = MagicMock()
            townhalls.__bool__ = lambda self: True
            townhalls.__len__ = lambda self: len(townhall_positions)
            mocks = [MagicMock(position=p) for p in townhall_positions]
            townhalls.first = mocks[0]

            # closest_to(target) 返回距 target 最近的 townhall(欧氏)
            def closest_to(target: object) -> MagicMock:
                tx = getattr(target, "x", 0)
                ty = getattr(target, "y", 0)
                return min(
                    mocks,
                    key=lambda m: (m.position.x - tx) ** 2 + (m.position.y - ty) ** 2,
                )

            townhalls.closest_to = closest_to
            bot.townhalls = townhalls
        if ramp_bottom is not None or ramp_top is not None:
            ramp = MagicMock()
            ramp.bottom_center = ramp_bottom
            ramp.top_center = ramp_top
            bot.main_base_ramp = ramp
        return bot

    def test_forward_multi_base_picks_townhall_closest_to_enemy(self) -> None:
        """多矿:取距 enemy_main 最近的自方 nexus。"""
        from sc2.position import Point2

        enemy = Point2((100.0, 100.0))
        # main 远(0,0), natural 中(40,40), third 近敌方(80,80)
        main_pos = Point2((0.0, 0.0))
        nat_pos = Point2((40.0, 40.0))
        third_pos = Point2((80.0, 80.0))
        bot = self._make_bot(enemy, [main_pos, nat_pos, third_pos])
        reg = NamedSpotRegistry()
        result = reg.resolve("forward", bot)
        assert result is third_pos, f"应选最靠近敌方的 third,实际 {result}"

    def test_forward_single_base_falls_back_to_ramp_bottom(self) -> None:
        """单矿:fallback main_ramp.bottom_center。"""
        enemy = object()
        main_pos = object()
        ramp_bottom = object()
        bot = self._make_bot(
            enemy_main=enemy,
            townhall_positions=[main_pos],
            ramp_bottom=ramp_bottom,
        )
        reg = NamedSpotRegistry()
        assert reg.resolve("forward", bot) is ramp_bottom

    def test_forward_single_base_no_ramp_bottom_uses_top(self) -> None:
        """单矿无 ramp_bottom:fallback main_ramp.top_center。"""
        enemy = object()
        main_pos = object()
        ramp_top = object()
        bot = self._make_bot(
            enemy_main=enemy,
            townhall_positions=[main_pos],
            ramp_bottom=None,
            ramp_top=ramp_top,
        )
        reg = NamedSpotRegistry()
        assert reg.resolve("forward", bot) is ramp_top

    def test_forward_no_enemy_returns_none(self) -> None:
        """enemy_start_locations 空 → None(无 reference 点定义"前线")。"""
        bot = self._make_bot(enemy_main=None, townhall_positions=["any"])
        reg = NamedSpotRegistry()
        assert reg.resolve("forward", bot) is None

    def test_forward_in_known_spots(self) -> None:
        assert "forward" in NamedSpotRegistry.KNOWN_SPOTS


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


# ---------------------------------------------------------------------------
# Clock / direction spot 新单测（T18）
# ---------------------------------------------------------------------------


def _make_point2(x: float, y: float) -> MagicMock:
    """构造有 .x/.y 的 Point2-like mock。"""
    p = MagicMock()
    p.x = x
    p.y = y
    return p


def _make_clock_bot(
    expansions: list[tuple[float, float]],
    map_center: tuple[float, float] = (64.0, 64.0),
    enemy_main: tuple[float, float] | None = None,
    own_main: tuple[float, float] | None = None,
) -> MagicMock:
    """构造带 expansion_locations_list / game_info.map_center 的 bot mock。"""
    bot = MagicMock(spec=[])
    bot.expansion_locations_list = [_make_point2(x, y) for x, y in expansions]

    game_info = MagicMock()
    game_info.map_center = _make_point2(*map_center)
    bot.game_info = game_info

    if enemy_main is not None:
        bot.enemy_start_locations = [_make_point2(*enemy_main)]
    else:
        bot.enemy_start_locations = []

    if own_main is not None:
        townhalls = MagicMock()
        townhalls.__bool__ = lambda self: True
        townhalls.first.position = _make_point2(*own_main)
        bot.townhalls = townhalls
    else:
        townhalls = MagicMock()
        townhalls.__bool__ = lambda self: False
        bot.townhalls = townhalls

    return bot


class TestClockSpots:
    """enemy_clock_X / own_clock_X / clock_X 解析。"""

    def test_enemy_clock_11_returns_expansion_northwest_of_enemy_main(self) -> None:
        """enemy_clock_11 → 敌方主基地 NW 方向最近 expansion。
        11 点 = 北偏西 30°。target_angle = π/2 - 11*π/6 = π/2 - 11π/6 = -4π/3 (mod 2π)
        实际用 atan2 angle diff 找最近角 —— 放一个正好在 NW 的点。
        """
        # enemy_main at (100, 100); NW expansion at (85, 115) — angle ≈ 135° from enemy_main
        # 11 点 angle = π/2 - 11*(π/6) = π/2 - 11π/6 ≈ -4π/3 → normalise: 2π/3 ≈ 120°
        # (85-100, 115-100) = (-15, 15) → atan2(15, -15) = 135° close enough
        bot = _make_clock_bot(
            expansions=[(85.0, 115.0), (115.0, 85.0), (100.0, 80.0)],
            map_center=(64.0, 64.0),
            enemy_main=(100.0, 100.0),
        )
        reg = NamedSpotRegistry()
        result = reg.resolve("enemy_clock_11", bot)
        assert result is not None
        assert (result.x, result.y) == (85.0, 115.0)

    def test_own_clock_5_returns_expansion_southeast_of_own_main(self) -> None:
        """own_clock_5 → 自方主基地 SE 方向最近 expansion。
        5 点 angle = π/2 - 5*(π/6) = π/2 - 5π/6 = -π/3 ≈ -60° → SE 方向。
        (own_main + (15, -8)) 大约在 -60° 方向上。
        """
        # own_main at (50, 50); SE expansion at (65, 37) ≈ angle -40° from own_main
        # closest to 5 点 (-60°) among candidates
        bot = _make_clock_bot(
            expansions=[(65.0, 37.0), (35.0, 63.0), (50.0, 35.0)],
            map_center=(64.0, 64.0),
            own_main=(50.0, 50.0),
        )
        reg = NamedSpotRegistry()
        result = reg.resolve("own_clock_5", bot)
        assert result is not None
        # (65, 37) is SE (angle ≈ -40°) vs (50, 35) which is due south (angle=-90°)
        # 5 点 = -60°, diff to (65,37): ≈20°, diff to (50,35): ≈30° → (65,37) wins
        assert (result.x, result.y) == (65.0, 37.0)

    def test_clock_3_no_prefix_uses_map_center_anchor(self) -> None:
        """clock_3 无前缀 → 锚点 = map_center。
        3 点 = 正东(0°)。放一个在 map_center 正东的 expansion。
        """
        # map_center (64, 64), expansion due east at (80, 64)
        bot = _make_clock_bot(
            expansions=[(80.0, 64.0), (64.0, 80.0), (48.0, 64.0)],
            map_center=(64.0, 64.0),
        )
        reg = NamedSpotRegistry()
        result = reg.resolve("clock_3", bot)
        assert result is not None
        # (80, 64): angle from (64,64) = atan2(0, 16) = 0°  — exact 3 点
        assert (result.x, result.y) == (80.0, 64.0)

    def test_enemy_clock_no_enemy_main_returns_none(self) -> None:
        """enemy_clock_X 当 enemy_main 不可用时返回 None。"""
        bot = _make_clock_bot(
            expansions=[(80.0, 64.0)],
            map_center=(64.0, 64.0),
            enemy_main=None,
        )
        reg = NamedSpotRegistry()
        assert reg.resolve("enemy_clock_6", bot) is None

    def test_own_clock_no_own_main_returns_none(self) -> None:
        """own_clock_X 当 own_main 不可用时返回 None。"""
        bot = _make_clock_bot(
            expansions=[(80.0, 64.0)],
            map_center=(64.0, 64.0),
            own_main=None,
        )
        reg = NamedSpotRegistry()
        assert reg.resolve("own_clock_6", bot) is None

    def test_clock_out_of_range_unknown_spot_returns_none(self) -> None:
        """clock_0 / clock_13 不在 KNOWN_SPOTS → 返回 None 且记 warning。"""
        bot = _make_clock_bot(expansions=[(80.0, 64.0)])
        reg = NamedSpotRegistry()
        assert reg.resolve("clock_0", bot) is None
        assert reg.resolve("clock_13", bot) is None


class TestDirectionAliasSpots:
    """方位 alias: own_top / enemy_bottom_right 等。"""

    def test_enemy_top_alias_equals_enemy_clock_12(self) -> None:
        """enemy_top → enemy_clock_12 (12 点 = 正北)。
        在 enemy_main 正北放一个 expansion — 应被两者都选中。
        """
        # enemy_main (100, 100); due north expansion (100, 115)
        bot = _make_clock_bot(
            expansions=[(100.0, 115.0), (85.0, 100.0), (115.0, 100.0)],
            map_center=(64.0, 64.0),
            enemy_main=(100.0, 100.0),
        )
        reg = NamedSpotRegistry()
        result_alias = reg.resolve("enemy_top", bot)
        result_clock = reg.resolve("enemy_clock_12", bot)
        assert result_alias is not None
        assert result_clock is not None
        assert (result_alias.x, result_alias.y) == (result_clock.x, result_clock.y)

    def test_own_top_left_alias_equals_own_clock_11(self) -> None:
        """own_top_left → own_clock_11 (11 点 = NW)。"""
        # own_main (50, 50); NW expansion (35, 65) ≈ 135°
        # 11 点 = π/2 - 11π/6 → normalised to 120° = NNW; NW point closest
        bot = _make_clock_bot(
            expansions=[(35.0, 65.0), (65.0, 35.0), (50.0, 65.0)],
            map_center=(64.0, 64.0),
            own_main=(50.0, 50.0),
        )
        reg = NamedSpotRegistry()
        result_alias = reg.resolve("own_top_left", bot)
        result_clock = reg.resolve("own_clock_11", bot)
        assert result_alias is not None
        assert result_clock is not None
        assert (result_alias.x, result_alias.y) == (result_clock.x, result_clock.y)

    def test_own_bottom_right_maps_to_clock_5(self) -> None:
        """own_bottom_right → clock 5。"""
        bot = _make_clock_bot(
            expansions=[(65.0, 37.0), (35.0, 63.0)],
            map_center=(64.0, 64.0),
            own_main=(50.0, 50.0),
        )
        reg = NamedSpotRegistry()
        result_alias = reg.resolve("own_bottom_right", bot)
        result_clock = reg.resolve("own_clock_5", bot)
        assert result_alias is not None
        assert result_clock is not None
        assert (result_alias.x, result_alias.y) == (result_clock.x, result_clock.y)

    def test_direction_alias_no_anchor_returns_none(self) -> None:
        """enemy_bottom 当 enemy_main 无法解析时返回 None。"""
        bot = _make_clock_bot(
            expansions=[(80.0, 64.0)],
            map_center=(64.0, 64.0),
            enemy_main=None,
        )
        reg = NamedSpotRegistry()
        assert reg.resolve("enemy_bottom", bot) is None


class TestClockAtExpansionRegression:
    """resolve_drop_target 内 _clock_at_expansion(clock, bot) 仍 work（锚点默认 map_center）。"""

    def test_existing_resolve_drop_target_clock_X_still_works(self) -> None:
        """_clock_at_expansion 重构后，resolve_drop_target clock_X 路径仍正常。"""
        from vibecraft.bot.named_spot import _clock_at_expansion

        bot = _make_clock_bot(
            expansions=[(80.0, 64.0), (64.0, 80.0)],
            map_center=(64.0, 64.0),
        )
        # 3 点 = 正东 (0°): expansion at (80, 64) is due east → should win
        result = _clock_at_expansion(3, bot)  # no anchor arg = backward compat
        assert result is not None
        assert (result.x, result.y) == (80.0, 64.0)

    def test_existing_watchtower_left_still_works(self) -> None:
        """regression: watchtower_left 不被新代码影响。"""
        from sc2.ids.unit_typeid import UnitTypeId
        from sc2.position import Point2

        bot = MagicMock(spec=[])

        def all_units_fn(type_id: object) -> list[MagicMock]:
            if type_id == UnitTypeId.XELNAGATOWER:
                towers = []
                for px, py in [(50, 30), (10, 30)]:
                    t = MagicMock()
                    t.position = Point2((px, py))
                    towers.append(t)
                return towers
            return []

        bot.all_units = all_units_fn
        reg = NamedSpotRegistry()
        pos = reg.resolve("watchtower_left", bot)
        assert pos is not None and pos.x == 10


# ---------------------------------------------------------------------------
# 23. closest_expansion_location —— 建 townhall snap 到贴矿最优位（2026-06-09）
# ---------------------------------------------------------------------------


class TestClosestExpansionLocation:
    """closest_expansion_location：把任意点 snap 到最近的 expansion 落点。

    修"在这里造基地造歪了"：build townhall 前用它把镜头点 snap 到贴矿最优位。
    """

    def test_snaps_to_nearest_zone_center(self) -> None:
        """优先用 sharpy zone_manager.expansion_zones[i].center_location，返最近者。"""
        from sc2.position import Point2

        from vibecraft.bot.named_spot import closest_expansion_location

        zones = [
            _make_zone(Point2((10.0, 10.0))),  # main
            _make_zone(Point2((50.0, 50.0))),  # natural
            _make_zone(Point2((90.0, 20.0))),  # third
        ]
        bot = _make_bot_with_zone_manager(zones, [])
        # 镜头点 (48,53) 最靠近 natural (50,50)
        result = closest_expansion_location((48.0, 53.0), bot)
        assert result is not None
        assert (result.x, result.y) == (50.0, 50.0)

    def test_fallback_expansion_locations_list(self) -> None:
        """zone_manager 不可用 → fallback python-sc2 expansion_locations_list，返最近者。"""
        from sc2.position import Point2

        from vibecraft.bot.named_spot import closest_expansion_location

        bot = MagicMock(spec=[])  # 无 knowledge
        bot.expansion_locations_list = [
            Point2((10.0, 10.0)),
            Point2((50.0, 50.0)),
            Point2((90.0, 20.0)),
        ]
        result = closest_expansion_location((88.0, 22.0), bot)  # 最靠近 (90,20)
        assert result is not None
        assert (result.x, result.y) == (90.0, 20.0)

    def test_no_expansion_data_returns_none(self) -> None:
        """既无 zone_manager 又无 expansion_locations_list → None（调用方退回原点）。"""
        from vibecraft.bot.named_spot import closest_expansion_location

        bot = MagicMock(spec=[])
        assert closest_expansion_location((5.0, 5.0), bot) is None


class TestSnapTownhallPoint:
    """snap_townhall_point：近矿 snap、偏太多尊重玩家指定位（挡路基地）。"""

    def _bot_with_natural(self, nat: object) -> MagicMock:
        from sc2.position import Point2

        return _make_bot_with_zone_manager([_make_zone(Point2((48.5, 28.5))), _make_zone(nat)], [])

    def test_near_point_snaps_to_expansion(self) -> None:
        """指定点离 natural 仅几格(≤15) → snap 到贴矿最优位，did_snap=True。"""
        from sc2.position import Point2

        from vibecraft.bot.named_spot import snap_townhall_point

        bot = self._bot_with_natural(Point2((50.0, 50.0)))
        pt, did = snap_townhall_point((52.0, 53.0), bot)  # 距 (50,50) ~3.6 格
        assert did is True
        assert (pt.x, pt.y) == (50.0, 50.0)

    def test_far_point_keeps_player_position(self) -> None:
        """指定点离任何 expansion 都 > 15 格（故意造偏的挡路基地）→ 原样返回，did_snap=False。"""
        from sc2.position import Point2

        from vibecraft.bot.named_spot import snap_townhall_point

        bot = self._bot_with_natural(Point2((50.0, 50.0)))
        # (80,80) 距 natural(50,50)=~42、距 main(48.5,28.5)=~60，都 > 15
        pt, did = snap_townhall_point((80.0, 80.0), bot)
        assert did is False
        assert (pt.x, pt.y) == (80.0, 80.0)

    def test_boundary_distance_snaps(self) -> None:
        """恰好在阈值内（距离 = TOWNHALL_SNAP_MAX_DIST=8）→ snap。"""
        from sc2.position import Point2

        from vibecraft.bot.named_spot import snap_townhall_point

        bot = self._bot_with_natural(Point2((50.0, 50.0)))
        pt, did = snap_townhall_point((58.0, 50.0), bot)  # 距 (50,50) 正好 8
        assert did is True
        assert (pt.x, pt.y) == (50.0, 50.0)

    def test_custom_max_distance(self) -> None:
        """max_distance 可调：传 2 格 → 距 3.6 格的点不再 snap。"""
        from sc2.position import Point2

        from vibecraft.bot.named_spot import snap_townhall_point

        bot = self._bot_with_natural(Point2((50.0, 50.0)))
        pt, did = snap_townhall_point((52.0, 53.0), bot, max_distance=2.0)
        assert did is False
        assert (pt.x, pt.y) == (52.0, 53.0)

    def test_no_expansion_data_keeps_point(self) -> None:
        """无 expansion 数据 → 原样返回，did_snap=False。"""
        from vibecraft.bot.named_spot import snap_townhall_point

        bot = MagicMock(spec=[])
        pt, did = snap_townhall_point((9.0, 9.0), bot)
        assert did is False
        assert (pt.x, pt.y) == (9.0, 9.0)
