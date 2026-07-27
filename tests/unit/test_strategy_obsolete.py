"""_check_strategy_obsolete 单测(2026-05-24 用户:补齐成本算法)。

测:
- 当前 supply 远超 build → 缺很多建筑 → cost > 阈值 → 报"时机已过"
- 当前已造大部分需要建筑 → cost < 阈值 → 不报
- 互斥建筑(已造对方剧本不需要的)→ 报"科技路线冲突"
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.bot import Director, FakeFacade
from vibecraft.bot.facade import BotState
from vibecraft.directives.models import (
    Directive,
    StrategySetPayload,
)
from vibecraft.directives.types import IssuedBy
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def session() -> GameSession:
    s = GameSession(GameSessionConfig(use_null_sinks=True))
    yield s
    s.close()


@pytest.fixture
def library() -> StrategyLibrary:
    return StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )


def _make_director(session: GameSession, library: StrategyLibrary, facade: FakeFacade) -> Director:
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    parser = IntentParser(provider, library, session=session)
    return Director(facade=facade, parser=parser, session=session, library=library)


def _strategy_directive(strategy_id: str) -> Directive:
    payload = StrategySetPayload(stage="opening", strategy_id=strategy_id)
    return Directive(payload=payload, issued_at=10.0, issued_by=IssuedBy.VOICE)


class TestStrategyObsolete:
    def test_fresh_game_no_obsolete(self, library, session) -> None:
        """游戏刚开始(supply 13,无 tech 建筑)→ 切 dt_drop_iac → 不报。"""
        facade = FakeFacade()
        facade.state = BotState(
            game_time=10.0,
            supply_used=13,
            structures_built=frozenset({"NEXUS", "PYLON"}),
        )
        director = _make_director(session, library, facade)
        reasons = director._check_strategy_obsolete(_strategy_directive("dt_drop_iac"))
        assert reasons == []

    def test_late_no_tech_buildings_obsolete(self, library, session) -> None:
        """supply 30(过了 4bg 所有 build supply),只有 NEXUS+PYLON → 缺很多 → cost 大 → 报。

        4bg yaml steps: 9 BE, 13 BG, 15 BA, 16 BY, 17 BE。supply 30 应已建完所有。
        实际只有 NEXUS+PYLON → 缺 BG/BA/BY → cost > 800 阈值 → 报。
        """
        facade = FakeFacade()
        facade.state = BotState(
            game_time=180.0,
            supply_used=30,
            structures_built=frozenset({"NEXUS", "PYLON"}),
        )
        director = _make_director(session, library, facade)
        reasons = director._check_strategy_obsolete(_strategy_directive("4bg"))
        assert len(reasons) > 0
        # 应该有"补齐成本"原因
        assert any("成本" in r for r in reasons)

    def test_conflicting_tech_obsolete(self, library, session) -> None:
        """已造 STARGATE(4bg 不需要)→ 切 4bg → 报"科技路线冲突"。"""
        facade = FakeFacade()
        facade.state = BotState(
            game_time=180.0,
            supply_used=25,
            structures_built=frozenset(
                {"NEXUS", "PYLON", "GATEWAY", "CYBERNETICSCORE", "STARGATE"}
            ),
        )
        director = _make_director(session, library, facade)
        reasons = director._check_strategy_obsolete(_strategy_directive("4bg"))
        # 至少一个 reason 提"科技路线"
        assert any("科技路线" in r for r in reasons)

    def test_midgame_strategy_no_check(self, library, session) -> None:
        """midgame/lategame strategy 不检测 → 返回空。"""
        facade = FakeFacade()
        facade.state = BotState(
            game_time=300.0,
            supply_used=100,
            structures_built=frozenset({"NEXUS", "PYLON"}),
        )
        director = _make_director(session, library, facade)
        # midgame strategy 如 persistent_immortal_archon
        d = _strategy_directive("persistent_immortal_archon")
        reasons = director._check_strategy_obsolete(d)
        assert reasons == []  # midgame 不检测

    def test_cost_below_threshold_not_obsolete(self, library, session) -> None:
        """已造大部分 4bg 需要的建筑 → cost 低 → 不报。

        4bg steps: BE/BG/BA/BY/BE → 已造 NEXUS+PYLON+GATEWAY+ASSIMILATOR+CYBER → 全有 → cost=0。
        """
        facade = FakeFacade()
        facade.state = BotState(
            game_time=180.0,
            supply_used=30,
            structures_built=frozenset(
                {"NEXUS", "PYLON", "GATEWAY", "CYBERNETICSCORE", "ASSIMILATOR"}
            ),
        )
        director = _make_director(session, library, facade)
        reasons = director._check_strategy_obsolete(_strategy_directive("4bg"))
        # 所有需要的建筑都已有 → 不报"成本"
        assert not any("成本" in r for r in reasons)
