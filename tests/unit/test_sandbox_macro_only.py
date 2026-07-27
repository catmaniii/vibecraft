"""build 效率沙盒：Director._sandbox_macro_only=True 时每 tick 强制 defend
（combat_intent_override + engagement_stance = defend），bot 只 macro 不主动 moveout。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.bot import Director, FakeFacade
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def _make_director(session, library, facade) -> Director:
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    parser = IntentParser(provider, library, session=session)
    return Director(facade=facade, parser=parser, session=session, library=library)


def test_sandbox_forces_defend_each_tick(library, session):
    facade = FakeFacade()
    director = _make_director(session, library, facade)
    director._sandbox_macro_only = True

    director.on_tick(now=1.0)
    director.on_tick(now=2.0)

    # 每 tick 都重设（幂等防被清）→ 两次 defend
    assert facade.engagement_stances.count("defend") == 2
    assert facade.combat_intent_overrides.count("defend") == 2


def test_no_sandbox_does_not_force_defend(library, session):
    facade = FakeFacade()
    director = _make_director(session, library, facade)
    # 默认 False → 不强制
    assert director._sandbox_macro_only is False

    director.on_tick(now=1.0)

    assert "defend" not in facade.engagement_stances
    assert "defend" not in facade.combat_intent_overrides
