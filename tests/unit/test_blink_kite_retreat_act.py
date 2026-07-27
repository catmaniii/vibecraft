"""BlinkKiteRetreatAct 单测(2026-05-28 Step 7 闪追风筝)。

验:
- ENTER: ready_ratio < 0.3 且 avg_shield < 0.5 → kite_retreat=True
- EXIT: kite_retreat=True 时 ready_ratio > 0.6 → kite_retreat=False
- HYSTERESIS: 中间区域(0.3-0.6)不 flip 已有状态
- forward_stalker < MIN_STALKER_COUNT(5)→ 自动 clear kite_retreat
- 缺 cooldown_manager / vibecraft 命名空间 → 安全返 True(non-blocking)

不依赖 sharpy KnowledgeBot 实例化(避免 config.ini),直接 mock ai/knowledge。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VENDOR_SHARPY = _PROJECT_ROOT / "vendor" / "sharpy"

if str(_VENDOR_SHARPY) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SHARPY))


@pytest.fixture
def act_instance():
    """构造 BlinkKiteRetreatAct,绕过 ActBase __init__。"""
    try:
        from vibecraft.bot.auto_combat.protoss.blink_kite_retreat_act import (
            BlinkKiteRetreatAct,
        )
    except ImportError as e:
        pytest.skip(f"sharpy import 失败: {e}")
    return BlinkKiteRetreatAct.__new__(BlinkKiteRetreatAct)


def _make_stalker(distance_to_home: float, shield_pct: float, tag: int = 1):
    """构造一个 mock stalker unit。"""
    home = SimpleNamespace(x=50.0, y=50.0)
    # 把 stalker 放在 home + (distance_to_home, 0) — 一维偏移简化距离计算
    pos = SimpleNamespace(x=home.x + distance_to_home, y=home.y)
    pos.distance_to = lambda other, _d=distance_to_home: _d
    return SimpleNamespace(
        tag=tag,
        position=pos,
        shield_percentage=shield_pct,
    )


class _UnitGroup:
    """模拟 sharpy ai.units(UnitTypeId.STALKER) 返回的 units 对象。

    支持 .amount 属性 + .filter(lambda) 返回相同类型 + 可 iterate。
    """

    def __init__(self, units: list):
        self._units = list(units)

    @property
    def amount(self) -> int:
        return len(self._units)

    def filter(self, fn):
        return _UnitGroup([u for u in self._units if fn(u)])

    def __iter__(self):
        return iter(self._units)


def _make_ai(
    stalkers: list,
    blink_ready_tags: set[int] | None = None,
    has_cd_manager: bool = True,
    has_vibecraft: bool = True,
    initial_kite_retreat: bool = False,
):
    """构造 mock ai + knowledge with vibecraft.kite_retreat。

    blink_ready_tags: blink CD 已 ready 的 stalker tag 集合。
    """
    ai = MagicMock()
    ai.start_location = SimpleNamespace(x=50.0, y=50.0)

    # units(UnitTypeId.STALKER) → _UnitGroup
    group = _UnitGroup(stalkers)
    ai.units = MagicMock(return_value=group)

    # cooldown_manager.is_ready(tag, AbilityId) → blink_ready_tags 包含即 True
    ready = blink_ready_tags or set()

    class _CDManager:
        def is_ready(self, tag, ability):
            return tag in ready

    cd_mgr = _CDManager() if has_cd_manager else None

    if has_vibecraft:
        vbc = SimpleNamespace(kite_retreat=initial_kite_retreat)
    else:
        vbc = None

    ai.knowledge = SimpleNamespace(
        cooldown_manager=cd_mgr,
        vibecraft=vbc,
    )
    return ai


def _setup_act(act_instance, ai):
    """把 ai/knowledge 装到 act instance(对齐 sharpy ActBase.start 的副作用)。"""
    act_instance.ai = ai
    act_instance.knowledge = ai.knowledge
    return act_instance


class TestEnterCondition:
    """触发 kite_retreat=True 的 ENTER 条件。"""

    @pytest.mark.asyncio
    async def test_enter_when_ready_low_and_shield_low(self, act_instance) -> None:
        """5 个前线 stalker,全无 blink ready(0/5=0%<30%),
        avg_shield 30%(<50%)→ ENTER → kite_retreat=True。"""
        # 5 个前线 stalker(距 home > 25 算前线),shield 30%,全无 blink ready
        stalkers = [
            _make_stalker(distance_to_home=30.0, shield_pct=0.3, tag=i) for i in range(1, 6)
        ]
        ai = _make_ai(stalkers, blink_ready_tags=set())
        act = _setup_act(act_instance, ai)
        result = await act.execute()
        assert result is True  # non-blocking
        assert ai.knowledge.vibecraft.kite_retreat is True

    @pytest.mark.asyncio
    async def test_no_enter_when_ready_high(self, act_instance) -> None:
        """5 stalker 全 blink ready(5/5=100%>30%)→ 不 ENTER 即使 shield 低。"""
        stalkers = [
            _make_stalker(distance_to_home=30.0, shield_pct=0.2, tag=i) for i in range(1, 6)
        ]
        ai = _make_ai(stalkers, blink_ready_tags={1, 2, 3, 4, 5})
        act = _setup_act(act_instance, ai)
        await act.execute()
        # 默认 initial=False,不应升 True
        assert ai.knowledge.vibecraft.kite_retreat is False

    @pytest.mark.asyncio
    async def test_no_enter_when_shield_high(self, act_instance) -> None:
        """5 stalker 全无 ready 但 shield 满 → 不 ENTER。"""
        stalkers = [
            _make_stalker(distance_to_home=30.0, shield_pct=1.0, tag=i) for i in range(1, 6)
        ]
        ai = _make_ai(stalkers, blink_ready_tags=set())
        act = _setup_act(act_instance, ai)
        await act.execute()
        assert ai.knowledge.vibecraft.kite_retreat is False


class TestExitCondition:
    """已 kite_retreat=True 时退出条件。"""

    @pytest.mark.asyncio
    async def test_exit_when_ready_recovered(self, act_instance) -> None:
        """initial=True, 5/5 ready(100%>60%) → EXIT → kite_retreat=False。"""
        stalkers = [
            _make_stalker(distance_to_home=30.0, shield_pct=0.3, tag=i) for i in range(1, 6)
        ]
        ai = _make_ai(
            stalkers,
            blink_ready_tags={1, 2, 3, 4, 5},
            initial_kite_retreat=True,
        )
        act = _setup_act(act_instance, ai)
        await act.execute()
        assert ai.knowledge.vibecraft.kite_retreat is False

    @pytest.mark.asyncio
    async def test_no_exit_in_hysteresis_zone(self, act_instance) -> None:
        """ready_ratio 在 (0.3, 0.6] 区间(中间 hysteresis)→ kite_retreat 保持。

        2/5=40%(>30% 不 ENTER,<60% 不 EXIT)→ already True 仍 True。
        """
        stalkers = [
            _make_stalker(distance_to_home=30.0, shield_pct=0.3, tag=i) for i in range(1, 6)
        ]
        ai = _make_ai(
            stalkers,
            blink_ready_tags={1, 2},  # 2/5=40%
            initial_kite_retreat=True,
        )
        act = _setup_act(act_instance, ai)
        await act.execute()
        # already True,中间区域不该 flip
        assert ai.knowledge.vibecraft.kite_retreat is True


class TestSafetyClears:
    """安全条件:前线 stalker 不足 / 无 vibecraft 命名空间。"""

    @pytest.mark.asyncio
    async def test_clear_when_total_stalkers_below_min(self, act_instance) -> None:
        """总 stalker < 5(MIN_STALKER_COUNT)→ 自动 clear kite_retreat(防开局误触)。"""
        stalkers = [
            _make_stalker(distance_to_home=30.0, shield_pct=0.3, tag=i)
            for i in range(1, 4)  # 只 3 个
        ]
        ai = _make_ai(
            stalkers,
            blink_ready_tags=set(),
            initial_kite_retreat=True,
        )
        act = _setup_act(act_instance, ai)
        await act.execute()
        assert ai.knowledge.vibecraft.kite_retreat is False

    @pytest.mark.asyncio
    async def test_clear_when_forward_stalkers_below_min(self, act_instance) -> None:
        """总 stalker ≥ 5 但前线(距 home > 25)< 5 → 自动 clear。

        大部分 stalker 在家就不需要"撤退" — 直接清 flag,免持续 retreat 拖兵。
        """
        # 8 个 stalker:5 在家(距 10),3 在前线(距 30)
        home_stalkers = [
            _make_stalker(distance_to_home=10.0, shield_pct=0.3, tag=i) for i in range(1, 6)
        ]
        forward_stalkers = [
            _make_stalker(distance_to_home=30.0, shield_pct=0.3, tag=i) for i in range(6, 9)
        ]
        ai = _make_ai(
            home_stalkers + forward_stalkers,
            blink_ready_tags=set(),
            initial_kite_retreat=True,
        )
        act = _setup_act(act_instance, ai)
        await act.execute()
        # 前线 3 < 5 → 清
        assert ai.knowledge.vibecraft.kite_retreat is False

    @pytest.mark.asyncio
    async def test_returns_true_when_no_vibecraft_namespace(self, act_instance) -> None:
        """knowledge.vibecraft 缺失(mock 场景)→ return True non-blocking,不 crash。"""
        stalkers = [
            _make_stalker(distance_to_home=30.0, shield_pct=0.3, tag=i) for i in range(1, 6)
        ]
        ai = _make_ai(stalkers, has_vibecraft=False)
        act = _setup_act(act_instance, ai)
        result = await act.execute()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_no_cd_manager(self, act_instance) -> None:
        """cooldown_manager 缺失(mock 场景)→ return True,不 enter kite_retreat。"""
        stalkers = [
            _make_stalker(distance_to_home=30.0, shield_pct=0.3, tag=i) for i in range(1, 6)
        ]
        ai = _make_ai(stalkers, has_cd_manager=False, initial_kite_retreat=False)
        act = _setup_act(act_instance, ai)
        result = await act.execute()
        assert result is True
        # 没 cd manager 时不应主动设 kite_retreat
        assert ai.knowledge.vibecraft.kite_retreat is False
