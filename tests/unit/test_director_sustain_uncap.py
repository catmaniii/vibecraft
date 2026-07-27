"""Director opening sustain uncap 超时触发单测 (Task #341)。

覆盖:
- opening_completed + 超时 > 120s → facade.set_sustain_uncap_active(True) 调用
- 已切 persistent (board.current_stage != OPENING) → 不 trigger
- latch: 已 trigger 不重复调
- 未到超时 (50s) → 不 trigger
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.bot import BotState, Director, FakeFacade
from vibecraft.directives.types import StageKind
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def library() -> StrategyLibrary:
    return StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )


@pytest.fixture
def session() -> GameSession:
    s = GameSession(GameSessionConfig(use_null_sinks=True))
    yield s
    s.close()


def _make_director(
    library: StrategyLibrary,
    session: GameSession,
    facade: FakeFacade | None = None,
    my_race: str = "protoss",
) -> Director:
    _facade = facade or FakeFacade()
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    parser = IntentParser(provider, library, session=session, my_race=my_race)
    return Director(facade=_facade, parser=parser, session=session, library=library)


def _seed_opening_slot(director: Director, strategy_id: str = "4bg") -> None:
    """board OPENING slot 초기화 — notify_opening_completed 전제조건."""
    director.board.set_initial_slot(StageKind.OPENING, strategy_id, now=0.0)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


class TestSustainUncapTrigger:
    def test_triggered_after_timeout(self, library: StrategyLibrary, session: GameSession) -> None:
        """opening_completed at t=10, t=130 → trigger."""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(library, session, facade)
        _seed_opening_slot(director)

        ok = director.notify_opening_completed(now=10.0)
        assert ok, "notify_opening_completed should succeed with protoss library"

        # 未到超时时,不触发
        director.on_tick(now=50.0)
        assert not any(c.method == "set_sustain_uncap_active" for c in facade.calls), (
            "should not trigger at 50s"
        )

        # 超时后触发
        director.on_tick(now=131.0)  # 131 - 10 = 121 > 120
        sustain_calls = [c for c in facade.calls if c.method == "set_sustain_uncap_active"]
        assert len(sustain_calls) == 1
        assert sustain_calls[0].args == (True,)
        assert director._sustain_uncap_triggered is True

    def test_not_triggered_before_timeout(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """t=10 opening_completed, t=120 (exactly 110s gap) → not triggered yet."""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(library, session, facade)
        _seed_opening_slot(director)

        director.notify_opening_completed(now=10.0)
        director.on_tick(now=120.0)  # 110s < 120s threshold

        sustain_calls = [c for c in facade.calls if c.method == "set_sustain_uncap_active"]
        assert len(sustain_calls) == 0

    def test_latch_no_repeat_trigger(self, library: StrategyLibrary, session: GameSession) -> None:
        """已触发后多次 on_tick 不重复调 facade.set_sustain_uncap_active。"""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(library, session, facade)
        _seed_opening_slot(director)

        director.notify_opening_completed(now=10.0)
        # 触发
        director.on_tick(now=131.0)
        # 再跑多几 tick
        director.on_tick(now=132.0)
        director.on_tick(now=150.0)

        sustain_calls = [c for c in facade.calls if c.method == "set_sustain_uncap_active"]
        assert len(sustain_calls) == 1  # latch: only called once

    def test_not_triggered_if_midgame_slot_filled(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """board.slots[MIDGAME] 非空 → 玩家已切 persistent doctrine, 不 trigger。"""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(library, session, facade)
        _seed_opening_slot(director)

        director.notify_opening_completed(now=10.0)

        # 模拟玩家点 toast confirm 切 midgame doctrine → fill MIDGAME slot
        director.board.set_initial_slot(StageKind.MIDGAME, "persistent_blink_harass", now=20.0)

        director.on_tick(now=200.0)  # 超时很久

        sustain_calls = [c for c in facade.calls if c.method == "set_sustain_uncap_active"]
        assert len(sustain_calls) == 0, "should not trigger when MIDGAME slot already set"

    def test_not_triggered_if_lategame_slot_filled(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """board.slots[LATEGAME] 非空 → 玩家已切 lategame doctrine, 不 trigger。"""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(library, session, facade)
        _seed_opening_slot(director)

        director.notify_opening_completed(now=10.0)
        director.board.set_initial_slot(StageKind.LATEGAME, "persistent_skytoss", now=20.0)

        director.on_tick(now=200.0)

        sustain_calls = [c for c in facade.calls if c.method == "set_sustain_uncap_active"]
        assert len(sustain_calls) == 0

    def test_triggered_even_without_opening_slot_set(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """default opening (4bg/12pool) 不走 strategy_set → OPENING slot None。
        sustain check 不依赖 opening_slot,仍应 trigger。"""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(library, session, facade)
        # 不 _seed_opening_slot — 模拟 default opening 没 fill OPENING slot

        director.notify_opening_completed(now=10.0)

        director.on_tick(now=131.0)  # > 120s timeout

        sustain_calls = [c for c in facade.calls if c.method == "set_sustain_uncap_active"]
        assert len(sustain_calls) == 1, "sustain should trigger even without OPENING slot"

    def test_not_triggered_without_opening_completed(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """未调 notify_opening_completed → _opening_completed_signaled=False, 不 trigger。"""
        facade = FakeFacade(state=BotState(game_time=0.0))
        director = _make_director(library, session, facade)

        director.on_tick(now=300.0)  # 很长时间

        sustain_calls = [c for c in facade.calls if c.method == "set_sustain_uncap_active"]
        assert len(sustain_calls) == 0

    def test_opening_completed_at_recorded(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """notify_opening_completed(now=42.0) → _opening_completed_at == 42.0."""
        facade = FakeFacade(state=BotState(game_time=42.0))
        director = _make_director(library, session, facade)
        _seed_opening_slot(director)

        director.notify_opening_completed(now=42.0)

        assert director._opening_completed_at == pytest.approx(42.0)
        assert director._opening_completed_signaled is True
