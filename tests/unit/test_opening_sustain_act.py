"""OpeningSustainAct 单测。

覆盖:
- flag=False: execute() return True, 不 kick off sub_act
- flag=True, PROTOSS: kick off, _sub_act 创建出来
- 重复 execute: _sub_act 不重新实例化
- race dispatch: PROTOSS/ZERG/TERRAN 各调对应 _build_xxx
- invalid race: ValueError
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibecraft.bot.auto_combat.opening_sustain_act import OpeningSustainAct

# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _make_knowledge(sustain_uncap_active: bool = False) -> Any:
    """构造一个最小 knowledge mock。"""
    k = MagicMock()
    k.vibecraft = SimpleNamespace(sustain_uncap_active=sustain_uncap_active)
    return k


# ---------------------------------------------------------------------------
# test cases
# ---------------------------------------------------------------------------


class TestOpeningSustainActNoOp:
    """flag=False 时: no-op, 不 kick off sub_act."""

    @pytest.mark.asyncio
    async def test_act_no_op_when_flag_inactive(self) -> None:
        act = OpeningSustainAct(race="PROTOSS")
        knowledge = _make_knowledge(sustain_uncap_active=False)
        await act.start(knowledge)

        result = await act.execute()

        assert result is True
        assert act._sub_act is None
        assert act._kicked_off is False

    @pytest.mark.asyncio
    async def test_act_returns_true_multiple_ticks_while_inactive(self) -> None:
        act = OpeningSustainAct(race="ZERG")
        knowledge = _make_knowledge(sustain_uncap_active=False)
        await act.start(knowledge)

        for _ in range(3):
            result = await act.execute()
            assert result is True
        assert act._sub_act is None


class TestOpeningSustainActKickOff:
    """flag=True 时: kick off, 不重复初始化."""

    @pytest.mark.asyncio
    async def test_act_kicks_off_when_flag_active_protoss(self) -> None:
        act = OpeningSustainAct(race="PROTOSS")
        knowledge = _make_knowledge(sustain_uncap_active=True)
        await act.start(knowledge)

        # 用真实 _build_sub_act 但 mock sharpy imports
        fake_sub_act = AsyncMock()
        fake_sub_act.execute = AsyncMock(return_value=True)
        fake_sub_act.start = AsyncMock()

        with patch.object(act, "_build_sub_act", return_value=fake_sub_act):
            result = await act.execute()

        assert result is True
        assert act._kicked_off is True
        assert act._sub_act is fake_sub_act
        fake_sub_act.start.assert_awaited_once_with(knowledge)
        fake_sub_act.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_act_kicks_off_only_once(self) -> None:
        """重复 execute: _sub_act 不重建, _build_sub_act 只调一次."""
        act = OpeningSustainAct(race="PROTOSS")
        knowledge = _make_knowledge(sustain_uncap_active=True)
        await act.start(knowledge)

        fake_sub_act = AsyncMock()
        fake_sub_act.execute = AsyncMock(return_value=True)
        fake_sub_act.start = AsyncMock()

        build_call_count = 0

        def _build_side_effect() -> Any:
            nonlocal build_call_count
            build_call_count += 1
            return fake_sub_act

        with patch.object(act, "_build_sub_act", side_effect=_build_side_effect):
            await act.execute()
            first_sub_act = act._sub_act
            await act.execute()
            second_sub_act = act._sub_act

        assert build_call_count == 1  # only built once
        assert first_sub_act is second_sub_act
        assert fake_sub_act.execute.await_count == 2  # called both ticks


class TestOpeningSustainActRaceDispatch:
    """_build_sub_act: 各 race 调对应 _build_xxx."""

    def test_act_race_dispatch_protoss(self) -> None:
        act = OpeningSustainAct(race="PROTOSS")
        with patch.object(act, "_build_protoss", return_value=MagicMock()) as mock_p:
            act._build_sub_act()
        mock_p.assert_called_once()

    def test_act_race_dispatch_zerg(self) -> None:
        act = OpeningSustainAct(race="ZERG")
        with patch.object(act, "_build_zerg", return_value=MagicMock()) as mock_z:
            act._build_sub_act()
        mock_z.assert_called_once()

    def test_act_race_dispatch_terran(self) -> None:
        act = OpeningSustainAct(race="TERRAN")
        with patch.object(act, "_build_terran", return_value=MagicMock()) as mock_t:
            act._build_sub_act()
        mock_t.assert_called_once()

    def test_invalid_race_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid race"):
            OpeningSustainAct(race="RANDOM")

    def test_invalid_race_raises_empty(self) -> None:
        with pytest.raises(ValueError, match="invalid race"):
            OpeningSustainAct(race="")


class TestOpeningSustainActMissingVibecraft:
    """knowledge.vibecraft 不存在时: 不崩，return True."""

    @pytest.mark.asyncio
    async def test_act_no_vibecraft_attr(self) -> None:
        act = OpeningSustainAct(race="PROTOSS")
        knowledge = MagicMock(spec=[])  # 无任何属性
        await act.start(knowledge)

        result = await act.execute()
        assert result is True
        assert act._sub_act is None
