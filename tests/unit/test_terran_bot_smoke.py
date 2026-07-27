"""人族 bot smoke 测试（Task 3b M6.3b）。

测试范围：
1. make_terran_bot_class(...) 返回类，instance 初始化不抛
2. 类型继承链：issubclass(cls, VibeCraftBotBase)
3. cls.EXCLUDE_FROM_ARMY == {SCV, MULE}
4. cls.DEFAULT_OPENING_ID == "reaper_expand"
5. 基础属性 event_bus / _llm_controlled_tags / named_spots / _voice_step_count
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture()
def _fake_env(fake_sharpy_bot_env: Any) -> Any:
    """复用 conftest 的重型 fake sharpy 注入（非 autouse）。"""
    return fake_sharpy_bot_env


def _make_terran_cls(fake_env: Any) -> type:
    """在 fake sharpy 环境中调用 make_terran_bot_class。"""
    from vibecraft.bot.auto_combat.terran.bot import make_terran_bot_class

    return make_terran_bot_class(
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


class TestMakeTerranBotClass:
    """make_terran_bot_class 工厂冒烟测试。"""

    def test_make_terran_bot_class_returns_type(self, _fake_env: Any) -> None:
        """make_terran_bot_class 返回 type（可实例化的类）。"""
        cls = _make_terran_cls(_fake_env)
        assert isinstance(cls, type)

    def test_terran_bot_instantiates_without_error(self, _fake_env: Any) -> None:
        """实例化不抛异常。"""
        cls = _make_terran_cls(_fake_env)
        inst = cls()
        assert inst is not None

    def test_terran_bot_inherits_vibecraft_bot_base(self, _fake_env: Any) -> None:
        """issubclass(cls, VibeCraftBotBase) == True。"""
        cls = _make_terran_cls(_fake_env)
        mro_names = [c.__name__ for c in cls.__mro__]
        assert "VibeCraftBotBase" in mro_names, f"MRO 中没有 VibeCraftBotBase: {mro_names}"

    def test_terran_bot_exclude_from_army(self, _fake_env: Any) -> None:
        """EXCLUDE_FROM_ARMY 包含 SCV / MULE 两个值。"""
        cls = _make_terran_cls(_fake_env)
        assert hasattr(cls, "EXCLUDE_FROM_ARMY")
        excluded_names = {getattr(u, "name", str(u)) for u in cls.EXCLUDE_FROM_ARMY}
        assert "SCV" in excluded_names, f"SCV 不在 EXCLUDE_FROM_ARMY: {excluded_names}"
        assert "MULE" in excluded_names, f"MULE 不在 EXCLUDE_FROM_ARMY: {excluded_names}"

    def test_terran_bot_default_opening_id(self, _fake_env: Any) -> None:
        """DEFAULT_OPENING_ID == 'reaper_expand'（最稳的标准 TvX 开局）。"""
        cls = _make_terran_cls(_fake_env)
        assert cls.DEFAULT_OPENING_ID == "reaper_expand"
        inst = cls()
        assert inst.active_recipe == "reaper_expand"

    def test_terran_bot_has_event_bus(self, _fake_env: Any) -> None:
        """实例有 event_bus 属性。"""
        from vibecraft.bot.event_bus import EventBus

        cls = _make_terran_cls(_fake_env)
        inst = cls()
        assert hasattr(inst, "event_bus")
        assert isinstance(inst.event_bus, EventBus)

    def test_terran_bot_has_llm_controlled_tags(self, _fake_env: Any) -> None:
        """实例有 _llm_controlled_tags（空 set）。"""
        cls = _make_terran_cls(_fake_env)
        inst = cls()
        assert hasattr(inst, "_llm_controlled_tags")
        assert isinstance(inst._llm_controlled_tags, set)
        assert len(inst._llm_controlled_tags) == 0

    def test_terran_bot_has_named_spots(self, _fake_env: Any) -> None:
        """实例有 named_spots（NamedSpotRegistry）。"""
        from vibecraft.bot.named_spot import NamedSpotRegistry

        cls = _make_terran_cls(_fake_env)
        inst = cls()
        assert hasattr(inst, "named_spots")
        assert isinstance(inst.named_spots, NamedSpotRegistry)

    def test_terran_bot_voice_step_count_zero(self, _fake_env: Any) -> None:
        """_voice_step_count / _sharpy_iteration 初始为 0。"""
        cls = _make_terran_cls(_fake_env)
        inst = cls()
        assert inst._voice_step_count == 0
        assert inst._sharpy_iteration == 0

    def test_terran_bot_exclude_from_army_size(self, _fake_env: Any) -> None:
        """EXCLUDE_FROM_ARMY 恰好 2 个元素（SCV / MULE）。"""
        cls = _make_terran_cls(_fake_env)
        assert len(cls.EXCLUDE_FROM_ARMY) == 2

    def test_on_step_swallows_exceptions(self, _fake_env: Any) -> None:
        """on_step 顶层兜底：_on_step_body 抛异常被吞，不冒泡杀整局（2026-06-19）。

        模拟单帧 plan/act 崩溃（如占位 enum 训练触发 AssertionError）——on_step 必须
        catch + 落 log，让游戏继续。这是"打到一半异常退出"的根治保险。
        """
        import asyncio

        cls = _make_terran_cls(_fake_env)
        inst = cls()

        async def _boom(_iteration: int) -> None:
            raise AssertionError("Ability is not of type 'AbilityData', but was NoneType")

        inst._on_step_body = _boom  # type: ignore[method-assign]
        # 不抛 = PASS；若 on_step 没兜底，这里会把 AssertionError 冒出来
        asyncio.run(inst.on_step(0))
