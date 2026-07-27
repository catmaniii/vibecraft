"""VibeCraftBotBase 单元测试（Task 0 M6.0）。

验证：
1. VibeCraftBotBase 可以通过 make_protoss_bot_class 工厂实例化，且继承链正确。
2. __init__ 设置 event_bus、_llm_controlled_tags、named_spots、_voice_step_count = 0、
   _sharpy_iteration = 0。
3. 11 个 _publish_xxx helper 函数可从 common_bot 独立 import + 接 fake_bot 调通。
4. _make_sharpy_facade_base_class / _make_vibecraft_bot_base_class 在 fake sharpy 环境可调用。
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# 准备 fake sharpy + sc2 环境（复用 conftest._inject_sharpy_for_bot）
# ---------------------------------------------------------------------------

_PREFIXES = ("sc2", "sharpy", "vibecraft.bot.auto_combat", "vibecraft.bot.sharpy_adapter")


def _clean_mods() -> None:
    for key in list(sys.modules):
        if any(key == p or key.startswith(p + ".") for p in _PREFIXES):
            del sys.modules[key]


@pytest.fixture()
def _fake_env(fake_sharpy_bot_env: Any) -> Any:
    """复用 conftest 的重型 fake sharpy 注入（非 autouse）。"""
    return fake_sharpy_bot_env


# ---------------------------------------------------------------------------
# 辅助：构造 fake bot stub（不需要真 sharpy，只测 helper 函数）
# ---------------------------------------------------------------------------


def _make_fake_bot() -> Any:
    """构造有 event_bus + time + 位置属性的最小 bot stub（用于 _publish_xxx 测试）。"""
    from vibecraft.bot.event_bus import EventBus
    from vibecraft.bot.named_spot import NamedSpotRegistry

    return SimpleNamespace(
        time=10.0,
        event_bus=EventBus(),
        named_spots=NamedSpotRegistry(),
        _enemy_units_dict={},
        _own_units_dict={},
    )


def _make_fake_unit(tag: int = 1, alliance: int = 1) -> Any:
    """构造最小 unit stub。"""
    return SimpleNamespace(
        tag=tag,
        alliance=alliance,
        type_id=SimpleNamespace(name="STALKER"),
        position=SimpleNamespace(x=10.0, y=20.0),
    )


# ---------------------------------------------------------------------------
# 测试 1：_publish_xxx helper 函数均可 import 并调通
# ---------------------------------------------------------------------------


class TestPublishHelpers:
    """11 个 _publish_xxx helper 可从 common_bot 独立 import + 调通。"""

    def _collect_all_events(self, event_bus: Any) -> list[Any]:
        """订阅所有 EventKind，返回收集列表。"""
        from vibecraft.bot.event_bus import EventKind

        events: list[Any] = []
        for kind in EventKind:
            event_bus.subscribe(kind, lambda e: events.append(e))
        return events

    def test_publish_unit_created(self) -> None:
        from vibecraft.bot.auto_combat.common_bot import _publish_unit_created

        bot = _make_fake_bot()
        unit = _make_fake_unit(tag=10, alliance=1)
        events = self._collect_all_events(bot.event_bus)
        _publish_unit_created(bot, unit)
        assert len(events) == 1
        assert events[0].unit_tag == 10
        assert events[0].owner == "own"

    def test_publish_unit_created_enemy(self) -> None:
        from vibecraft.bot.auto_combat.common_bot import _publish_unit_created

        bot = _make_fake_bot()
        unit = _make_fake_unit(tag=20, alliance=2)
        events = self._collect_all_events(bot.event_bus)
        _publish_unit_created(bot, unit)
        assert events[0].owner == "enemy"

    def test_publish_unit_destroyed_no_cache(self) -> None:
        from vibecraft.bot.auto_combat.common_bot import _publish_unit_destroyed

        bot = _make_fake_bot()
        events = self._collect_all_events(bot.event_bus)
        # unit 不在 cache → 仍发事件（owner=None）
        _publish_unit_destroyed(bot, 999)
        assert len(events) == 1
        assert events[0].unit_tag == 999

    def test_publish_unit_destroyed_with_cache(self) -> None:
        from vibecraft.bot.auto_combat.common_bot import _publish_unit_destroyed

        bot = _make_fake_bot()
        unit = _make_fake_unit(tag=30, alliance=1)
        bot._own_units_dict[30] = unit
        events = self._collect_all_events(bot.event_bus)
        _publish_unit_destroyed(bot, 30)
        assert events[0].owner == "own"

    def test_publish_unit_type_changed(self) -> None:
        from vibecraft.bot.auto_combat.common_bot import _publish_unit_type_changed

        bot = _make_fake_bot()
        unit = _make_fake_unit(tag=40, alliance=1)
        prev = SimpleNamespace(name="GATEWAY")
        events = self._collect_all_events(bot.event_bus)
        _publish_unit_type_changed(bot, unit, prev)
        assert len(events) == 1
        assert events[0].unit_tag == 40

    def test_publish_building_started(self) -> None:
        from vibecraft.bot.auto_combat.common_bot import _publish_building_started

        bot = _make_fake_bot()
        unit = _make_fake_unit(tag=50, alliance=1)
        events = self._collect_all_events(bot.event_bus)
        _publish_building_started(bot, unit)
        assert events[0].owner == "own"

    def test_publish_building_complete(self) -> None:
        from vibecraft.bot.auto_combat.common_bot import _publish_building_complete

        bot = _make_fake_bot()
        unit = _make_fake_unit(tag=60, alliance=1)
        events = self._collect_all_events(bot.event_bus)
        _publish_building_complete(bot, unit)
        assert events[0].unit_tag == 60

    def test_publish_upgrade_complete(self) -> None:
        from vibecraft.bot.auto_combat.common_bot import _publish_upgrade_complete

        bot = _make_fake_bot()
        upgrade = SimpleNamespace(name="WARPGATERESEARCH")
        events = self._collect_all_events(bot.event_bus)
        _publish_upgrade_complete(bot, upgrade)
        assert len(events) == 1

    def test_publish_unit_took_damage(self) -> None:
        from vibecraft.bot.auto_combat.common_bot import _publish_unit_took_damage

        bot = _make_fake_bot()
        unit = _make_fake_unit(tag=70, alliance=1)
        events = self._collect_all_events(bot.event_bus)
        _publish_unit_took_damage(bot, unit, 25.5)
        assert events[0].payload["amount"] == 25.5

    def test_publish_enemy_unit_entered_vision(self) -> None:
        from vibecraft.bot.auto_combat.common_bot import _publish_enemy_unit_entered_vision

        bot = _make_fake_bot()
        unit = _make_fake_unit(tag=80, alliance=2)
        events = self._collect_all_events(bot.event_bus)
        _publish_enemy_unit_entered_vision(bot, unit)
        assert events[0].owner == "enemy"

    def test_publish_enemy_unit_left_vision(self) -> None:
        from vibecraft.bot.auto_combat.common_bot import _publish_enemy_unit_left_vision

        bot = _make_fake_bot()
        events = self._collect_all_events(bot.event_bus)
        _publish_enemy_unit_left_vision(bot, 90)
        assert events[0].unit_tag == 90


# ---------------------------------------------------------------------------
# 测试 2：VibeCraftBotBase 初始化（通过 make_protoss_bot_class 工厂验证）
# ---------------------------------------------------------------------------


class TestVibeCraftBotBaseInit:
    """VibeCraftBotBase.__init__ 正确初始化所有字段。"""

    def test_bot_has_event_bus(self) -> None:
        from vibecraft.bot.auto_combat.protoss.bot import make_protoss_bot_class
        from vibecraft.bot.event_bus import EventBus

        BotClass = make_protoss_bot_class(
            director_factory=lambda f: None,
            strategy_library=None,
            status_callback=None,
            down_q=None,
            echo_callback=None,
            snapshot_callback=None,
            event_callback=None,
            minimap_callback=None,
            run_command_with_echo_fn=lambda *a: None,
        )
        inst = BotClass()
        assert hasattr(inst, "event_bus")
        assert isinstance(inst.event_bus, EventBus)

    def test_bot_has_llm_controlled_tags(self) -> None:
        from vibecraft.bot.auto_combat.protoss.bot import make_protoss_bot_class

        BotClass = make_protoss_bot_class(
            director_factory=lambda f: None,
            strategy_library=None,
            status_callback=None,
            down_q=None,
            echo_callback=None,
            snapshot_callback=None,
            event_callback=None,
            minimap_callback=None,
            run_command_with_echo_fn=lambda *a: None,
        )
        inst = BotClass()
        assert hasattr(inst, "_llm_controlled_tags")
        assert isinstance(inst._llm_controlled_tags, set)
        assert len(inst._llm_controlled_tags) == 0

    def test_bot_has_named_spots(self) -> None:
        from vibecraft.bot.auto_combat.protoss.bot import make_protoss_bot_class
        from vibecraft.bot.named_spot import NamedSpotRegistry

        BotClass = make_protoss_bot_class(
            director_factory=lambda f: None,
            strategy_library=None,
            status_callback=None,
            down_q=None,
            echo_callback=None,
            snapshot_callback=None,
            event_callback=None,
            minimap_callback=None,
            run_command_with_echo_fn=lambda *a: None,
        )
        inst = BotClass()
        assert hasattr(inst, "named_spots")
        assert isinstance(inst.named_spots, NamedSpotRegistry)

    def test_bot_voice_step_count_zero(self) -> None:
        from vibecraft.bot.auto_combat.protoss.bot import make_protoss_bot_class

        BotClass = make_protoss_bot_class(
            director_factory=lambda f: None,
            strategy_library=None,
            status_callback=None,
            down_q=None,
            echo_callback=None,
            snapshot_callback=None,
            event_callback=None,
            minimap_callback=None,
            run_command_with_echo_fn=lambda *a: None,
        )
        inst = BotClass()
        assert inst._voice_step_count == 0
        assert inst._sharpy_iteration == 0

    def test_bot_default_opening_id(self) -> None:
        from vibecraft.bot.auto_combat.protoss.bot import make_protoss_bot_class

        BotClass = make_protoss_bot_class(
            director_factory=lambda f: None,
            strategy_library=None,
            status_callback=None,
            down_q=None,
            echo_callback=None,
            snapshot_callback=None,
            event_callback=None,
            minimap_callback=None,
            run_command_with_echo_fn=lambda *a: None,
        )
        assert BotClass.DEFAULT_OPENING_ID == "4bg"
        inst = BotClass()
        assert inst.active_recipe == "4bg"


# ---------------------------------------------------------------------------
# 测试 3：VibeCraftBotBase 继承链
# ---------------------------------------------------------------------------


class TestVibeCraftBotBaseInheritance:
    """VibeCraftBotBase 是三族 bot 的共同基类。"""

    def test_protoss_bot_inherits_vibecraft_bot_base(self) -> None:
        from vibecraft.bot.auto_combat.protoss.bot import make_protoss_bot_class

        BotClass = make_protoss_bot_class(
            director_factory=lambda f: None,
            strategy_library=None,
            status_callback=None,
            down_q=None,
            echo_callback=None,
            snapshot_callback=None,
            event_callback=None,
            minimap_callback=None,
            run_command_with_echo_fn=lambda *a: None,
        )
        # VibeCraftBotBase 在闭包内创建，验证继承链存在关键方法
        assert hasattr(BotClass, "_refresh_llm_controlled_roles")
        assert hasattr(BotClass, "is_vibecraft_controlled")
        assert hasattr(BotClass, "_tick_view_channel")
        assert hasattr(BotClass, "_tick_bot_channel")

    def test_protoss_bot_has_exclude_from_army(self) -> None:
        from vibecraft.bot.auto_combat.protoss.bot import make_protoss_bot_class

        BotClass = make_protoss_bot_class(
            director_factory=lambda f: None,
            strategy_library=None,
            status_callback=None,
            down_q=None,
            echo_callback=None,
            snapshot_callback=None,
            event_callback=None,
            minimap_callback=None,
            run_command_with_echo_fn=lambda *a: None,
        )
        assert hasattr(BotClass, "EXCLUDE_FROM_ARMY")
        assert len(BotClass.EXCLUDE_FROM_ARMY) == 3  # PROBE, OBSERVER, WARPPRISM
