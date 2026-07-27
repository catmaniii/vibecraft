"""EmitPhaseEventAct: phase 事件 latch act 纯逻辑测试。

ActBase 子类绕过 __init__,只测核心:check_fn → notify → 一次性 latch。
"""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _fake_sharpy():
    """phase_events 顶层只 import sharpy.plans.acts.ActBase。强制 fresh fake +
    pop phase_events 模块,避免跨文件 module cache 冲突(其他 test 可能预先
    fake 过 sharpy.plans.acts.ActBase 为别的 class,phase_events 顶层 import 已
    bind 到旧 ActBase,直接用就会和我们的 mocked direcrtor 类型不匹配)。"""
    # 强制覆盖 — 不管之前有没有 fake
    for name in ("sharpy", "sharpy.plans", "sharpy.plans.acts"):
        sys.modules[name] = ModuleType(name)
    sys.modules["sharpy.plans.acts"].ActBase = type(  # type: ignore[attr-defined]
        "ActBase", (), {}
    )
    sys.modules.pop("vibecraft.bot.auto_combat.protoss.plans.phase_events", None)
    yield
    sys.modules.pop("vibecraft.bot.auto_combat.protoss.plans.phase_events", None)
    for name in ("sharpy.plans.acts", "sharpy.plans", "sharpy"):
        sys.modules.pop(name, None)


def _act(event_name: str, check_fn):
    from vibecraft.bot.auto_combat.protoss.plans.phase_events import EmitPhaseEventAct

    act = EmitPhaseEventAct.__new__(EmitPhaseEventAct)
    act._event_name = event_name
    act._check_fn = check_fn
    act._signaled = False
    return act


def _run(coro):
    # Python 3.11:get_event_loop() 在无 running loop 时报错 → 用 asyncio.run。
    return asyncio.run(coro)


def test_check_fn_false_returns_false_no_notify():
    director = MagicMock()
    act = _act("dt_rush_forward_pylon_ready", lambda _ai: False)
    act.ai = SimpleNamespace(director=director, time=10.0)
    result = _run(act.execute())
    assert result is False
    assert act._signaled is False
    director.notify_phase_event.assert_not_called()


def test_check_fn_true_triggers_notify_and_latches():
    director = MagicMock()
    act = _act("dt_rush_forward_pylon_ready", lambda _ai: True)
    act.ai = SimpleNamespace(director=director, time=180.0)
    result = _run(act.execute())
    assert result is True
    assert act._signaled is True
    director.notify_phase_event.assert_called_once_with("dt_rush_forward_pylon_ready")


def test_already_signaled_short_circuits():
    director = MagicMock()
    act = _act(
        "dt_rush_dt_killed_worker",
        lambda _ai: pytest.fail("check_fn should not be called when latched"),
    )
    act._signaled = True
    act.ai = SimpleNamespace(director=director)
    result = _run(act.execute())
    assert result is True
    director.notify_phase_event.assert_not_called()


def test_no_director_silent_skip():
    """ai.director 不存在(测试场景) → 不崩,仍 latch。"""
    act = _act("dt_rush_forward_pylon_ready", lambda _ai: True)
    act.ai = SimpleNamespace(time=10.0)
    result = _run(act.execute())
    assert result is True
    assert act._signaled is True


def test_notify_exception_still_latches():
    """notify_phase_event 抛异常 → 仍 latch(避免无限重试),log 警告。"""
    director = MagicMock()
    director.notify_phase_event.side_effect = RuntimeError("boom")
    act = _act("dt_rush_dt_killed_worker", lambda _ai: True)
    act.ai = SimpleNamespace(director=director, time=10.0)
    result = _run(act.execute())
    assert result is True
    assert act._signaled is True
