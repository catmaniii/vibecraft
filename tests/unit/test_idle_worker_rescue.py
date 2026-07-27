"""rescue_idle_workers 纯函数单测（通用闲置农民兜底）。

不依赖 sharpy / SC2：用 SimpleNamespace + 假 units 构造 mock。核心验证：
- 持续 ≥IDLE_PERSIST_S 秒「有效闲置」（不 gathering/returning/carrying）的农民 → w.gather 被调；
- 正在采矿（is_gathering）的农民 → 不动；
- 玩家 claim（_llm_controlled_tags）的农民 → 排除；
- 未够 IDLE_PERSIST_S → 先不动（躲过渡态/SpeedMining 微操）。
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

from vibecraft.bot.auto_combat.idle_worker_rescue import IDLE_PERSIST_S, rescue_idle_workers


class _FakeUnits(list):
    def filter(self, fn: Any) -> _FakeUnits:
        return _FakeUnits([u for u in self if fn(u)])

    @property
    def amount(self) -> int:
        return len(self)

    def closer_than(self, dist: float, ref: Any) -> _FakeUnits:
        rx, ry = ref.position
        return _FakeUnits(
            [u for u in self if math.hypot(u.position[0] - rx, u.position[1] - ry) < dist]
        )

    def closest_to(self, ref: Any) -> Any:
        rx, ry = ref.position if hasattr(ref, "position") else ref
        return min(self, key=lambda u: math.hypot(u.position[0] - rx, u.position[1] - ry))


def _worker(tag: int, pos=(50.0, 50.0), gathering=False, returning=False, carrying=False):
    gathered: list[Any] = []
    w = SimpleNamespace(
        tag=tag,
        position=pos,
        is_gathering=gathering,
        is_returning=returning,
        is_carrying_resource=carrying,
        _gathered=gathered,
    )
    w.distance_to = lambda o, _p=pos: math.hypot(
        _p[0] - (o.position[0] if hasattr(o, "position") else o[0]),
        _p[1] - (o.position[1] if hasattr(o, "position") else o[1]),
    )
    w.gather = lambda mf: gathered.append(mf)
    return w


def _mineral(tag: int, pos=(52.0, 50.0)):
    return SimpleNamespace(tag=tag, position=pos)


def _ai(workers, minerals, townhalls, time=100.0, claimed=None):
    return SimpleNamespace(
        time=time,
        _llm_controlled_tags=set(claimed or []),
        mineral_field=_FakeUnits(minerals),
        workers=_FakeUnits(workers),
        townhalls=SimpleNamespace(ready=_FakeUnits(townhalls)),
    )


def test_idle_worker_gathered_after_persist():
    """有效闲置农民持续 ≥IDLE_PERSIST_S → w.gather 被调（送去采矿）。"""
    w = _worker(1, gathering=False)
    th = SimpleNamespace(tag=100, position=(50.0, 50.0))
    mf = _mineral(200, pos=(51.0, 50.0))
    state: dict = {}
    # 第 1 帧 t=100:刚记录没干活,还没到门槛 → 不 gather
    rescue_idle_workers(_ai([w], [mf], [th], time=100.0), state)
    assert w._gathered == []
    # t=100+IDLE_PERSIST_S+0.5:够门槛 → gather
    rescue_idle_workers(_ai([w], [mf], [th], time=100.0 + IDLE_PERSIST_S + 0.5), state)
    assert w._gathered == [mf]


def test_gathering_worker_untouched():
    """正在采矿(is_gathering)的农民 → 不动。"""
    w = _worker(1, gathering=True)
    th = SimpleNamespace(tag=100, position=(50.0, 50.0))
    mf = _mineral(200)
    state: dict = {}
    rescue_idle_workers(_ai([w], [mf], [th], time=100.0), state)
    rescue_idle_workers(_ai([w], [mf], [th], time=200.0), state)
    assert w._gathered == []


def test_player_claimed_worker_excluded():
    """玩家 claim(_llm_controlled_tags)的闲置农民 → 排除,不派采矿。"""
    w = _worker(7, gathering=False)
    th = SimpleNamespace(tag=100, position=(50.0, 50.0))
    mf = _mineral(200)
    state: dict = {}
    rescue_idle_workers(_ai([w], [mf], [th], time=100.0, claimed={7}), state)
    rescue_idle_workers(_ai([w], [mf], [th], time=100.0 + IDLE_PERSIST_S + 1, claimed={7}), state)
    assert w._gathered == []
