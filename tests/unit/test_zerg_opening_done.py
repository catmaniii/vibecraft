"""虫族 plan _opening_done 单测 (2026-05-28 fix「Lair morph 后永不触发」bug)。

Bug 场景:
- macro_hatch plan 设 MorphLair() → 主基地 HATCHERY → LAIR
- 原 _opening_done 用 ai.structures(HATCHERY).amount,morph 后这个数 -1
- 比如 3 个 town hall + 1 个升 Lair → HATCHERY=2 + LAIR=1,但
  `ai.structures(HATCHERY)` 只返 2 → bool(2 >= 3) = False
- opening_completed_signaled 永不触发 → OpeningSustainAct 120s 超时永不启动
- → 蟑螂卡 28 上限,supply 卡 127/200,钱多不出兵摆烂

修复:用 ai.townhalls.amount(自动合并 HATCH+LAIR+HIVE)。

验:
- 全 HATCHERY: 3 town hall + 10 蟑螂 → True
- 部分 Lair: 2 HATCHERY + 1 LAIR + 10 蟑螂 → True(townhalls=3)
- 全 Lair/Hive: 1 HATCHERY + 1 LAIR + 1 HIVE + 10 蟑螂 → True
- 不足: 2 town hall + 10 蟑螂 → False
- 蟑螂不足: 3 town hall + 5 蟑螂 → False
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VENDOR_SHARPY = _PROJECT_ROOT / "vendor" / "sharpy"

if str(_VENDOR_SHARPY) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SHARPY))


def _make_ai_for_macro_hatch(
    townhall_count: int,
    roach_count: int,
) -> SimpleNamespace:
    """构造 mock ai 支持 ai.townhalls.amount + ai.units(ROACH).amount。

    townhalls 是 sharpy ai 的合并属性(HATCH+LAIR+HIVE)。
    """
    townhalls = SimpleNamespace(amount=townhall_count)

    # ai.units(UnitTypeId.ROACH).amount
    def _units(unit_id):
        return SimpleNamespace(amount=roach_count)

    return SimpleNamespace(
        townhalls=townhalls,
        units=_units,
    )


def _make_ai_for_twelve_pool(
    townhall_count: int,
    time_s: float,
) -> SimpleNamespace:
    townhalls = SimpleNamespace(amount=townhall_count)
    return SimpleNamespace(
        townhalls=townhalls,
        time=time_s,
    )


class TestMacroHatchOpeningDone:
    """macro_hatch._opening_done:townhalls ≥ 3 且 ROACH ≥ 10。"""

    @pytest.fixture
    def opening_done(self):
        try:
            from vibecraft.bot.auto_combat.zerg.plans.macro_hatch import MacroHatch
        except ImportError as e:
            pytest.skip(f"sharpy import 失败: {e}")
        return MacroHatch._opening_done

    def test_full_hatcheries_satisfies(self, opening_done) -> None:
        """3 个未升级 HATCHERY + 10 蟑螂 → True。"""
        ai = _make_ai_for_macro_hatch(townhall_count=3, roach_count=10)
        assert opening_done(ai) is True

    def test_lair_morphed_still_satisfies(self, opening_done) -> None:
        """主基地升 Lair 后,townhalls 合并仍 3 → True(修复点)。

        修前:用 ai.structures(HATCHERY).amount 只数 2 个未升级的 → False
        修后:用 ai.townhalls.amount 自动合并 → 3 → True
        """
        ai = _make_ai_for_macro_hatch(townhall_count=3, roach_count=10)  # 2 hatch + 1 lair
        assert opening_done(ai) is True, (
            "升 Lair 后 ai.structures(HATCHERY) 数减少,但 ai.townhalls 合并"
            "(HATCH+LAIR+HIVE)应仍 3 → opening_done True"
        )

    def test_all_morphed_still_satisfies(self, opening_done) -> None:
        """全升级到 Lair/Hive 也 OK(townhalls 合并)。"""
        ai = _make_ai_for_macro_hatch(townhall_count=3, roach_count=10)  # 1 hatch + 1 lair + 1 hive
        assert opening_done(ai) is True

    def test_insufficient_townhalls(self, opening_done) -> None:
        """townhalls < 3 → False(蟑螂够也不行)。"""
        ai = _make_ai_for_macro_hatch(townhall_count=2, roach_count=20)
        assert opening_done(ai) is False

    def test_insufficient_roaches(self, opening_done) -> None:
        """ROACH < 10 → False(townhalls 够也不行)。"""
        ai = _make_ai_for_macro_hatch(townhall_count=4, roach_count=5)
        assert opening_done(ai) is False

    def test_exception_returns_false(self, opening_done) -> None:
        """ai 访问异常(early game ai 不可用)→ False(安全 fallback)。"""
        # 没 townhalls 属性
        ai = SimpleNamespace()
        assert opening_done(ai) is False


class TestTwelvePoolOpeningDone:
    """twelve_pool._opening_done:t ≥ 180s 且 townhalls ≥ 2。"""

    @pytest.fixture
    def opening_done(self):
        try:
            from vibecraft.bot.auto_combat.zerg.plans.twelve_pool import TwelvePool
        except ImportError as e:
            pytest.skip(f"sharpy import 失败: {e}")
        return TwelvePool._opening_done

    def test_both_satisfied(self, opening_done) -> None:
        """t=200s + 2 town hall → True。"""
        ai = _make_ai_for_twelve_pool(townhall_count=2, time_s=200.0)
        assert opening_done(ai) is True

    def test_lair_morphed_still_satisfies(self, opening_done) -> None:
        """有 Lair 升级后 townhalls 仍 ≥ 2 → True(防御性修复)。"""
        ai = _make_ai_for_twelve_pool(townhall_count=2, time_s=300.0)  # 1 hatch + 1 lair
        assert opening_done(ai) is True

    def test_time_too_early(self, opening_done) -> None:
        """t<180s → False(早期不算 opening_done)。"""
        ai = _make_ai_for_twelve_pool(townhall_count=3, time_s=100.0)
        assert opening_done(ai) is False

    def test_only_one_townhall(self, opening_done) -> None:
        """townhalls=1 → False(没扩二矿)。"""
        ai = _make_ai_for_twelve_pool(townhall_count=1, time_s=200.0)
        assert opening_done(ai) is False
