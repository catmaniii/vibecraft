"""MarineStagingAct 单测：proxy_4rax 枪兵前向集结状态机（2026-07-09 新增）。

覆盖：①锚点 None 不动 ②未达 threshold 全部 Reserved+move ③达 threshold 全部
Idle+released ④玩家 claim 的枪兵不被 stage/release ⑤玩家全军 intent 立即释放
⑥fallback_time 到立即释放 ⑦hold_position 到位后幂等（不每帧重发）。

不拉起 SC2：sharpy 模块全 fake 注入（同 test_bc_raid_act.py 的 fixture 套路），
ai/knowledge/roles 全 SimpleNamespace/MagicMock mock。
"""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sc2.position import Point2

# ── fake-sharpy fixture（同 test_bc_raid_act.py 套路）─────────────────────────


@pytest.fixture(autouse=True)
def _fake_sharpy():
    """marine_staging_act 顶层 import sharpy.plans.acts.ActBase / UnitTask。注入 fake 让 import 过。"""
    created = []
    for name in (
        "sharpy",
        "sharpy.plans",
        "sharpy.plans.acts",
        "sharpy.managers",
        "sharpy.managers.core",
        "sharpy.managers.core.roles",
    ):
        if name not in sys.modules:
            sys.modules[name] = ModuleType(name)
            created.append(name)
    acts = sys.modules["sharpy.plans.acts"]
    if not hasattr(acts, "ActBase"):
        acts.ActBase = type("ActBase", (), {})  # type: ignore[attr-defined]
    roles = sys.modules["sharpy.managers.core.roles"]
    if not hasattr(roles, "UnitTask"):
        roles.UnitTask = SimpleNamespace(Reserved="Reserved", Idle="Idle")  # type: ignore[attr-defined]
    yield
    sys.modules.pop("vibecraft.bot.auto_combat.terran.plans.marine_staging_act", None)
    for name in created:
        sys.modules.pop(name, None)


# ── helpers ──────────────────────────────────────────────────────────────────


class _FakeUnits(list):
    """python-sc2 Units 的最小 stand-in：.ready 返回自身，.amount 返回长度。"""

    @property
    def ready(self):
        return self

    @property
    def amount(self):
        return len(self)


def _marine(tag: int, pos: tuple[float, float]) -> SimpleNamespace:
    p = Point2(pos)
    return SimpleNamespace(
        tag=tag,
        position=p,
        distance_to=lambda other, _p=p: _p.distance_to(
            other if isinstance(other, Point2) else other.position
        ),
        move=MagicMock(),
        hold_position=MagicMock(),
    )


def _wire(
    act,
    *,
    marines,
    anchor: Point2 | None = None,
    intent: str | None = None,
    player_tags: set[int] | None = None,
    now: float = 100.0,
):
    act.knowledge = SimpleNamespace(
        roles=MagicMock(),
        vibecraft=SimpleNamespace(proxy_anchor=anchor, combat_intent_override=intent),
    )
    act.cache = MagicMock()
    marine_units = _FakeUnits(marines)
    # 忽略传入的 unit_type：全套件里其它 test 可能 fake 掉 sc2 模块，导致 marine_staging_act
    # 被重导入后其 UnitTypeId.MARINE 与本文件的不是同一枚举对象（== 会 False）。act 只查
    # MARINE，直接返回 marine_units 即可，避免枚举身份污染。
    act.ai = SimpleNamespace(
        time=now,
        units=lambda ut: marine_units,
        _llm_controlled_tags=set(player_tags or set()),
    )


def _mk_act(threshold: int = 6, fallback_time: float = 170.0):
    from vibecraft.bot.auto_combat.terran.plans.marine_staging_act import MarineStagingAct

    return MarineStagingAct(threshold=threshold, fallback_time=fallback_time)


# ══════════════════════════════════════════════════════════════════════════════
# 1. 锚点 None → 不动
# ══════════════════════════════════════════════════════════════════════════════


def test_no_anchor_does_nothing():
    a = _mk_act()
    m = _marine(1, (10.0, 10.0))
    _wire(a, marines=[m], anchor=None)
    result = asyncio.run(a.execute())
    assert result is False, "proxy 还没选点，act 不应完成"
    m.move.assert_not_called()
    a.knowledge.roles.set_task.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 2. 未达 threshold → 全部 Reserved + move 到锚点
# ══════════════════════════════════════════════════════════════════════════════


def test_understaffed_stages_all_free_marines():
    a = _mk_act(threshold=6)
    anchor = Point2((50.0, 50.0))
    m1 = _marine(1, (10.0, 10.0))
    m2 = _marine(2, (12.0, 10.0))
    _wire(a, marines=[m1, m2], anchor=anchor)
    result = asyncio.run(a.execute())
    assert result is False
    m1.move.assert_called_once_with(anchor)
    m2.move.assert_called_once_with(anchor)

    from sharpy.managers.core.roles import UnitTask

    calls = a.knowledge.roles.set_task.call_args_list
    assert len(calls) == 2
    for call in calls:
        assert call.args[0] == UnitTask.Reserved


# ══════════════════════════════════════════════════════════════════════════════
# 3. 达 threshold → 全部 Idle + _released（之后再 execute 秒返回 True 不再碰）
# ══════════════════════════════════════════════════════════════════════════════


def test_threshold_reached_releases_all_and_latches():
    a = _mk_act(threshold=2)
    anchor = Point2((50.0, 50.0))
    m1 = _marine(1, (50.0, 50.0))
    m2 = _marine(2, (50.0, 50.0))
    _wire(a, marines=[m1, m2], anchor=anchor)
    result = asyncio.run(a.execute())
    assert result is True
    assert a._released is True

    from sharpy.managers.core.roles import UnitTask

    calls = a.knowledge.roles.set_task.call_args_list
    assert len(calls) == 2
    for call in calls:
        assert call.args[0] == UnitTask.Idle

    a.knowledge.roles.set_task.reset_mock()
    result2 = asyncio.run(a.execute())
    assert result2 is True
    a.knowledge.roles.set_task.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 4. 玩家 claim 的枪兵不被 stage / release
# ══════════════════════════════════════════════════════════════════════════════


def test_player_claimed_marine_not_staged():
    """低于 threshold 时：玩家 claim 的枪兵不被 Reserved / move。"""
    a = _mk_act(threshold=6)
    anchor = Point2((50.0, 50.0))
    m_free = _marine(1, (10.0, 10.0))
    m_claimed = _marine(2, (10.0, 10.0))
    _wire(a, marines=[m_free, m_claimed], anchor=anchor, player_tags={2})
    asyncio.run(a.execute())
    m_free.move.assert_called_once()
    m_claimed.move.assert_not_called()
    assert a.knowledge.roles.set_task.call_count == 1
    assert a.knowledge.roles.set_task.call_args.args[1] is m_free


def test_player_claimed_marine_not_released():
    """达到释放门时：玩家 claim 的枪兵不被 set Idle，只释放 free 的。"""
    a = _mk_act(threshold=1)
    anchor = Point2((50.0, 50.0))
    m_free = _marine(1, (10.0, 10.0))
    m_claimed = _marine(2, (10.0, 10.0))
    # marines.amount(总数)=2 >= threshold(1) → 触发释放门
    _wire(a, marines=[m_free, m_claimed], anchor=anchor, player_tags={2})
    result = asyncio.run(a.execute())
    assert result is True
    calls = a.knowledge.roles.set_task.call_args_list
    assert len(calls) == 1
    assert calls[0].args[1] is m_free


# ══════════════════════════════════════════════════════════════════════════════
# 5. 玩家全军 intent（attack/retreat/defend）→ 立即释放
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("intent", ["attack", "retreat", "defend"])
def test_player_intent_releases_immediately(intent):
    a = _mk_act(threshold=6)
    anchor = Point2((50.0, 50.0))
    m1 = _marine(1, (10.0, 10.0))
    _wire(a, marines=[m1], anchor=anchor, intent=intent)
    result = asyncio.run(a.execute())
    assert result is True
    assert a._released is True

    from sharpy.managers.core.roles import UnitTask

    a.knowledge.roles.set_task.assert_called_once()
    call = a.knowledge.roles.set_task.call_args
    assert call.args[0] == UnitTask.Idle


# ══════════════════════════════════════════════════════════════════════════════
# 6. fallback_time 到 → 释放
# ══════════════════════════════════════════════════════════════════════════════


def test_fallback_time_releases():
    a = _mk_act(threshold=6, fallback_time=170.0)
    anchor = Point2((50.0, 50.0))
    m1 = _marine(1, (10.0, 10.0))
    _wire(a, marines=[m1], anchor=anchor, now=175.0)
    result = asyncio.run(a.execute())
    assert result is True
    assert a._released is True


# ══════════════════════════════════════════════════════════════════════════════
# 7. hold_position 幂等：到位后不每帧重发 move / hold_position
# ══════════════════════════════════════════════════════════════════════════════


def test_hold_position_idempotent_once_arrived():
    a = _mk_act(threshold=6)
    anchor = Point2((50.0, 50.0))
    m1 = _marine(1, (50.0, 50.0))  # 已在锚点
    _wire(a, marines=[m1], anchor=anchor)
    asyncio.run(a.execute())
    assert m1.hold_position.call_count == 1
    m1.move.assert_not_called()
    asyncio.run(a.execute())
    assert m1.hold_position.call_count == 1, "到位后二次 execute 不应重发 hold_position"
