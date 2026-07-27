"""单元测试：转型推荐改成本驱动（2026-05-30）。

验证 _update_recommendation 不再写死 yaml default_transitions[0]，而是调
pick_best_persistent 算迁移成本选最低成本 persistent doctrine。

覆盖：
  - opening 完成 → _cost_based_recommendation 返回成本最低的 persistent doctrine
    （source="cost"，不是写死的 iac_2base）
  - 推荐目标确实是 library 里某个 persistent doctrine（不是 midgame build）
  - 被玩家忽略过 → 不再推该 doctrine
"""

from __future__ import annotations

from pathlib import Path

from vibecraft.bot.director import Director, DirectorConfig, StageKind
from vibecraft.bot.facade import BotState, FakeFacade
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _session() -> GameSession:
    return GameSession(GameSessionConfig(use_null_sinks=True))


def _make_library() -> StrategyLibrary:
    return StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )


def _director(facade: FakeFacade, session: GameSession) -> Director:
    from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    library = _make_library()
    parser = IntentParser(provider, library, session=session)
    parser.my_race = "protoss"
    return Director(
        facade=facade,
        parser=parser,
        session=session,
        library=library,
        config=DirectorConfig(commit_delay_s=0.0),
    )


def _persistent_ids(library: StrategyLibrary) -> set[str]:
    return {d.id for d in library.persistent_doctrines(race="protoss")}


class TestCostBasedRecommendation:
    def test_returns_persistent_doctrine_not_hardcoded(self) -> None:
        """phoenix_2base opening → 推荐成本最低的 persistent doctrine（不是写死 iac_2base）。"""
        facade = FakeFacade(state=BotState())
        session = _session()
        director = _director(facade, session)

        opening_strat = director.library.get("phoenix_2base")
        reco = director._cost_based_recommendation(opening_strat)

        assert reco is not None
        assert reco.source == "cost"
        assert reco.stage == StageKind.MIDGAME
        # 推荐目标必须是某个 persistent doctrine（cost 路径只在 persistent 里选）
        assert reco.strategy_id in _persistent_ids(director.library)
        # 不是写死的 iac_2base（它不是 persistent doctrine）
        assert reco.strategy_id != "iac_2base"

    def test_reason_mentions_migration_cost(self) -> None:
        """推荐理由里说明是迁移成本最低（给玩家看为什么选）。"""
        facade = FakeFacade(state=BotState())
        director = _director(facade, _session())
        reco = director._cost_based_recommendation(director.library.get("phoenix_2base"))
        assert reco is not None
        assert "迁移成本最低" in reco.reason

    def test_dismissed_recommendation_not_repeated(self) -> None:
        """玩家忽略过该 doctrine → 成本路径返回 None（调用方 fallback）。"""
        facade = FakeFacade(state=BotState())
        director = _director(facade, _session())

        opening_strat = director.library.get("phoenix_2base")
        reco1 = director._cost_based_recommendation(opening_strat)
        assert reco1 is not None
        # 标记为已忽略
        director._dismissed_recommendations.add((StageKind.MIDGAME, reco1.strategy_id))
        reco2 = director._cost_based_recommendation(opening_strat)
        assert reco2 is None

    def test_no_race_returns_none(self) -> None:
        """parser.my_race 空 → cost 路径不可用，返回 None（fallback yaml）。"""
        facade = FakeFacade(state=BotState())
        director = _director(facade, _session())
        director.parser.my_race = ""
        reco = director._cost_based_recommendation(director.library.get("phoenix_2base"))
        assert reco is None
