"""虫族 bot smoke 测试（Task 2b M6.2b）。

测试范围：
1. make_zerg_bot_class(...) 返回类，instance 初始化不抛
2. 类型继承链：issubclass(cls, VibeCraftBotBase)
3. cls.EXCLUDE_FROM_ARMY == {DRONE, OVERLORD, OVERSEER}
4. cls.DEFAULT_OPENING_ID == "12pool"
5. 基础属性 event_bus / _llm_controlled_tags / named_spots / _voice_step_count
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture()
def _fake_env(fake_sharpy_bot_env: Any) -> Any:
    """复用 conftest 的重型 fake sharpy 注入（非 autouse）。"""
    return fake_sharpy_bot_env


def _make_zerg_cls(fake_env: Any) -> type:
    """在 fake sharpy 环境中调用 make_zerg_bot_class。"""
    from vibecraft.bot.auto_combat.zerg.bot import make_zerg_bot_class

    return make_zerg_bot_class(
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


class TestMakeZergBotClass:
    """make_zerg_bot_class 工厂冒烟测试。"""

    def test_make_zerg_bot_class_returns_type(self, _fake_env: Any) -> None:
        """make_zerg_bot_class 返回 type（可实例化的类）。"""
        cls = _make_zerg_cls(_fake_env)
        assert isinstance(cls, type)

    def test_zerg_bot_instantiates_without_error(self, _fake_env: Any) -> None:
        """实例化不抛异常。"""
        cls = _make_zerg_cls(_fake_env)
        inst = cls()
        assert inst is not None

    def test_zerg_bot_inherits_vibecraft_bot_base(self, _fake_env: Any) -> None:
        """issubclass(cls, VibeCraftBotBase) == True。"""
        cls = _make_zerg_cls(_fake_env)
        # VibeCraftBotBase 是动态创建的类,通过 __name__ 检查继承链
        mro_names = [c.__name__ for c in cls.__mro__]
        assert "VibeCraftBotBase" in mro_names, f"MRO 中没有 VibeCraftBotBase: {mro_names}"

    def test_zerg_bot_exclude_from_army(self, _fake_env: Any) -> None:
        """EXCLUDE_FROM_ARMY 包含 DRONE / OVERLORD / OVERSEER 三个值。"""
        cls = _make_zerg_cls(_fake_env)
        assert hasattr(cls, "EXCLUDE_FROM_ARMY")
        # fake UnitTypeId 用字符串值,取 name 判断
        excluded_names = {getattr(u, "name", str(u)) for u in cls.EXCLUDE_FROM_ARMY}
        assert "DRONE" in excluded_names, f"DRONE 不在 EXCLUDE_FROM_ARMY: {excluded_names}"
        assert "OVERLORD" in excluded_names, f"OVERLORD 不在 EXCLUDE_FROM_ARMY: {excluded_names}"
        assert "OVERSEER" in excluded_names, f"OVERSEER 不在 EXCLUDE_FROM_ARMY: {excluded_names}"

    def test_zerg_bot_default_opening_id(self, _fake_env: Any) -> None:
        """DEFAULT_OPENING_ID == '12pool'。"""
        cls = _make_zerg_cls(_fake_env)
        assert cls.DEFAULT_OPENING_ID == "12pool"
        inst = cls()
        assert inst.active_recipe == "12pool"

    def test_zerg_bot_has_event_bus(self, _fake_env: Any) -> None:
        """实例有 event_bus 属性。"""
        from vibecraft.bot.event_bus import EventBus

        cls = _make_zerg_cls(_fake_env)
        inst = cls()
        assert hasattr(inst, "event_bus")
        assert isinstance(inst.event_bus, EventBus)

    def test_zerg_bot_has_llm_controlled_tags(self, _fake_env: Any) -> None:
        """实例有 _llm_controlled_tags（空 set）。"""
        cls = _make_zerg_cls(_fake_env)
        inst = cls()
        assert hasattr(inst, "_llm_controlled_tags")
        assert isinstance(inst._llm_controlled_tags, set)
        assert len(inst._llm_controlled_tags) == 0

    def test_zerg_bot_has_named_spots(self, _fake_env: Any) -> None:
        """实例有 named_spots（NamedSpotRegistry）。"""
        from vibecraft.bot.named_spot import NamedSpotRegistry

        cls = _make_zerg_cls(_fake_env)
        inst = cls()
        assert hasattr(inst, "named_spots")
        assert isinstance(inst.named_spots, NamedSpotRegistry)

    def test_zerg_bot_voice_step_count_zero(self, _fake_env: Any) -> None:
        """_voice_step_count / _sharpy_iteration 初始为 0。"""
        cls = _make_zerg_cls(_fake_env)
        inst = cls()
        assert inst._voice_step_count == 0
        assert inst._sharpy_iteration == 0

    def test_zerg_bot_exclude_from_army_size(self, _fake_env: Any) -> None:
        """EXCLUDE_FROM_ARMY 恰好 3 个元素（DRONE / OVERLORD / OVERSEER）。"""
        cls = _make_zerg_cls(_fake_env)
        assert len(cls.EXCLUDE_FROM_ARMY) == 3
