"""Director auto_switch_to persistent doctrine 单测 (Task #350)。

覆盖:
- opening_completed 后 delay 秒 → facade.set_build(target) 被调用
- auto_switch_to 空串时不 trigger（普通 opening 验收路径不受影响）
- latch: 已 trigger 不重复调
- delay 未到时不 trigger
- opening_completed_signaled=False 时不 trigger
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

_PERSISTENT_TARGET = "persistent_lurker_hydra"
_DELAY_S = 10.0


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def library() -> StrategyLibrary:
    return StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "zerg.yaml",
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
    my_race: str = "zerg",
) -> Director:
    _facade = facade or FakeFacade()
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    parser = IntentParser(provider, library, session=session, my_race=my_race)
    return Director(facade=_facade, parser=parser, session=session, library=library)


def _seed_opening_slot(director: Director, strategy_id: str = "macro_hatch") -> None:
    """board OPENING slot 초기화 — notify_opening_completed 전제조건."""
    director.board.set_initial_slot(StageKind.OPENING, strategy_id, now=0.0)


def _setup_auto_switch(
    director: Director,
    target: str = _PERSISTENT_TARGET,
    delay_s: float = _DELAY_S,
) -> None:
    """模拟 game_process.director_factory 写入 auto_switch 字段。"""
    director._auto_switch_to = target
    director._auto_switch_delay_s = delay_s


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


class TestAutoSwitchToPersistentDoctrine:
    def test_triggered_after_opening_completed_delay(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """auto_switch_to 非空: opening_completed at t=10, on_tick at t=21 (>10+10) → set_build called."""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(library, session, facade)
        _seed_opening_slot(director)
        _setup_auto_switch(director)

        ok = director.notify_opening_completed(now=10.0)
        assert ok, "notify_opening_completed 应成功（zerg library 有 persistent doctrine）"

        # 未到 delay 时不 trigger
        director.on_tick(now=19.0)  # 19-10=9 < 10
        set_build_calls = [c for c in facade.calls if c.method == "set_build"]
        assert len(set_build_calls) == 0, "delay 未到不应 trigger"

        # 超过 delay 后 trigger
        director.on_tick(now=21.0)  # 21-10=11 > 10
        set_build_calls = [c for c in facade.calls if c.method == "set_build"]
        assert len(set_build_calls) == 1
        assert set_build_calls[0].args == (_PERSISTENT_TARGET,)
        assert director._auto_switch_triggered is True

    def test_not_triggered_if_no_target(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """auto_switch_to 空串时 on_tick 不 trigger（普通 opening 验收路径不受影响）。"""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(library, session, facade)
        _seed_opening_slot(director)
        # 不调 _setup_auto_switch → _auto_switch_to 默认 ""

        director.notify_opening_completed(now=10.0)
        director.on_tick(now=300.0)  # 很长时间

        set_build_calls = [c for c in facade.calls if c.method == "set_build"]
        assert len(set_build_calls) == 0, "空 auto_switch_to 不应触发 set_build"

    def test_latch_no_repeat_trigger(self, library: StrategyLibrary, session: GameSession) -> None:
        """已 trigger 后多次 on_tick 不重复调 facade.set_build。"""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(library, session, facade)
        _seed_opening_slot(director)
        _setup_auto_switch(director)

        director.notify_opening_completed(now=10.0)
        director.on_tick(now=21.0)  # trigger
        director.on_tick(now=22.0)  # 再 tick
        director.on_tick(now=50.0)  # 再 tick

        set_build_calls = [c for c in facade.calls if c.method == "set_build"]
        assert len(set_build_calls) == 1, "latch: 只应 trigger 一次"
        assert director._auto_switch_triggered is True

    def test_not_triggered_before_delay(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """opening_completed at t=10, on_tick at t=15 (5s < 10s delay) → not triggered yet。"""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(library, session, facade)
        _seed_opening_slot(director)
        _setup_auto_switch(director, delay_s=10.0)

        director.notify_opening_completed(now=10.0)
        director.on_tick(now=15.0)  # 5s < 10s delay

        set_build_calls = [c for c in facade.calls if c.method == "set_build"]
        assert len(set_build_calls) == 0, "delay 未到不应 trigger"
        assert director._auto_switch_triggered is False

    def test_not_triggered_without_opening_completed(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """未调 notify_opening_completed → _opening_completed_signaled=False, 不 trigger。"""
        facade = FakeFacade(state=BotState(game_time=0.0))
        director = _make_director(library, session, facade)
        _setup_auto_switch(director)
        # 不调 notify_opening_completed

        director.on_tick(now=300.0)

        set_build_calls = [c for c in facade.calls if c.method == "set_build"]
        assert len(set_build_calls) == 0, "opening_completed 未 signal 时不应 trigger"

    def test_init_defaults(self, library: StrategyLibrary, session: GameSession) -> None:
        """Director 初始状态:_auto_switch_to="" / _auto_switch_delay_s=10.0 / _auto_switch_triggered=False。"""
        director = _make_director(library, session)
        assert director._auto_switch_to == ""
        assert director._auto_switch_delay_s == pytest.approx(10.0)
        assert director._auto_switch_triggered is False

    def test_custom_delay(self, library: StrategyLibrary, session: GameSession) -> None:
        """自定义 delay_s=5.0: 5s 到了就 trigger。"""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(library, session, facade)
        _seed_opening_slot(director)
        _setup_auto_switch(director, delay_s=5.0)

        director.notify_opening_completed(now=10.0)

        # 3s 后还没到
        director.on_tick(now=13.0)  # 3 < 5
        set_build_calls = [c for c in facade.calls if c.method == "set_build"]
        assert len(set_build_calls) == 0

        # 6s 后 trigger
        director.on_tick(now=16.0)  # 6 > 5
        set_build_calls = [c for c in facade.calls if c.method == "set_build"]
        assert len(set_build_calls) == 1
        assert set_build_calls[0].args == (_PERSISTENT_TARGET,)
