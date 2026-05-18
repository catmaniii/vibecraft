"""P0b Task 13: protoss bot _tick_bot_channel 接 director.execute_tactics_step。

两种测试策略:
1. source 层面: inspect.getsource 验证方法体里有 execute_tactics_step 调用 (稳)
2. async wire: mock director, 真实跑 _tick_bot_channel, 验证 awaited (强)

Task 13 目标: overrides_step(L4) → tactics_step(L2) → super().on_step(sharpy).
"""

from __future__ import annotations

import importlib
import inspect
import queue
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_PROTOSS_BOT_MOD = "vibecraft.bot.auto_combat.protoss.bot"


# ---------------------------------------------------------------------------
# autouse: 每 test 独立的 fake sharpy 环境
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fake_sharpy_env(fake_sharpy_bot_env: Any) -> Any:
    """复用 conftest 的重型 fake sharpy 注入。"""
    return fake_sharpy_bot_env


# ---------------------------------------------------------------------------
# 辅助: 构造最小化 bot class (无需 director_factory 产物)
# ---------------------------------------------------------------------------


def _make_bot_class() -> type:
    mod = importlib.import_module(_PROTOSS_BOT_MOD)

    def _noop_director_factory(facade: Any) -> None:
        return None

    return mod.make_protoss_bot_class(
        director_factory=_noop_director_factory,
        strategy_library=None,
        status_callback=None,
        down_q=queue.Queue(),
        echo_callback=None,
        snapshot_callback=None,
        event_callback=None,
        minimap_callback=None,
        run_command_with_echo_fn=lambda cmd, echo: None,
    )


# ---------------------------------------------------------------------------
# Test 1: source 层面检查 (稳，不依赖 event loop)
# ---------------------------------------------------------------------------


def test_tick_bot_channel_source_calls_execute_tactics_step() -> None:
    """_tick_bot_channel 方法体应包含 execute_tactics_step 调用。"""
    BotClass = _make_bot_class()
    src = inspect.getsource(BotClass._tick_bot_channel)
    assert "execute_tactics_step" in src, (
        "_tick_bot_channel 源码未找到 execute_tactics_step；Task 13 实现未完成。"
    )


def test_tactics_step_called_after_overrides_step_in_source() -> None:
    """execute_tactics_step 应出现在 execute_overrides_step 之后（顺序约束）。"""
    BotClass = _make_bot_class()
    src = inspect.getsource(BotClass._tick_bot_channel)
    idx_overrides = src.find("execute_overrides_step")
    idx_tactics = src.find("execute_tactics_step")
    assert idx_overrides != -1, "源码缺少 execute_overrides_step"
    assert idx_tactics != -1, "源码缺少 execute_tactics_step"
    assert idx_tactics > idx_overrides, (
        "execute_tactics_step 应在 execute_overrides_step 之后调用；"
        "L4 overrides 先占 action slot, L2 tactics 后跟。"
    )


# ---------------------------------------------------------------------------
# Test 2: async wire (强, 验证真实 await 调用)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_bot_channel_awaits_execute_tactics_step() -> None:
    """_tick_bot_channel 调用时, director.execute_tactics_step 应被 await。"""
    BotClass = _make_bot_class()
    bot = BotClass()

    # 注入 mock director
    mock_director = MagicMock()
    mock_director.execute_overrides_step = AsyncMock()
    mock_director.execute_tactics_step = AsyncMock()
    bot.director = mock_director

    # mock super().on_step 避免触发真实 sharpy 逻辑
    with patch.object(BotClass.__bases__[0], "on_step", new=AsyncMock()):
        await bot._tick_bot_channel(py_sc2_iteration=0, now_s=42.0)

    mock_director.execute_tactics_step.assert_awaited_once_with(42.0)


@pytest.mark.asyncio
async def test_tick_bot_channel_awaits_tactics_after_overrides() -> None:
    """execute_tactics_step 在 execute_overrides_step 之后被调用（调用顺序）。"""
    BotClass = _make_bot_class()
    bot = BotClass()

    call_order: list[str] = []

    async def _fake_overrides(now: float) -> None:
        call_order.append("overrides")

    async def _fake_tactics(now: float) -> None:
        call_order.append("tactics")

    mock_director = MagicMock()
    mock_director.execute_overrides_step = _fake_overrides
    mock_director.execute_tactics_step = _fake_tactics
    bot.director = mock_director

    with patch.object(BotClass.__bases__[0], "on_step", new=AsyncMock()):
        await bot._tick_bot_channel(py_sc2_iteration=0, now_s=10.0)

    assert call_order == ["overrides", "tactics"], (
        f"调用顺序应为 overrides → tactics, 实际: {call_order}"
    )


@pytest.mark.asyncio
async def test_tick_bot_channel_no_director_no_crash() -> None:
    """director 为 None 时 _tick_bot_channel 不崩溃（防守性）。"""
    BotClass = _make_bot_class()
    bot = BotClass()
    bot.director = None  # type: ignore[assignment]

    with patch.object(BotClass.__bases__[0], "on_step", new=AsyncMock()):
        # 不应抛出 AttributeError / NoneType error
        await bot._tick_bot_channel(py_sc2_iteration=0, now_s=5.0)
