"""P1b: ENGAGEMENT_CONSTRAINT → TacticalObjective(persistent=True) 自动转换单测。

覆盖：
- ENGAGEMENT_CONSTRAINT(stance=defend) 自动转 TacticalObjective(persistent=True,verb=defend)
  → facade.set_engagement_stance("defend") + facade.set_combat_intent_override("defend")
- ENGAGEMENT_CONSTRAINT(stance=retreat) 同上走 retreat 路径
- ENGAGEMENT_CONSTRAINT(stance=hold) fallback 直接 set_engagement_stance（不走 l2_global）
- ENGAGEMENT_CONSTRAINT(stance=free) fallback 同上
- TacticalObjectivePayload.persistent 字段默认 False
- TacticalObjectivePayload.persistent=True 直接走链路（不经 ENGAGEMENT_CONSTRAINT 转换）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.bot import BotState, Director, FakeFacade
from vibecraft.llm import (
    IntentParser,
    MockLLMProvider,
    ProviderResponse,
)
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


def _make_director(
    library: StrategyLibrary,
    session: GameSession,
    facade: FakeFacade,
    provider_response: dict,
) -> Director:
    provider = MockLLMProvider(
        scripted=[
            ProviderResponse(
                raw=provider_response,
                input_tokens=100,
                output_tokens=20,
                latency_ms=10.0,
            )
        ]
    )
    parser = IntentParser(provider, library, session=session)
    return Director(facade=facade, parser=parser, session=session)


# =========================================================================
# TacticalObjectivePayload 字段默认值
# =========================================================================


class TestTacticalObjectivePayloadPersistentField:
    def test_persistent_defaults_to_false(self) -> None:
        from vibecraft.directives.models import TacticalObjectivePayload

        p = TacticalObjectivePayload(verb="defend", target_area=None)
        assert p.persistent is False

    def test_persistent_can_be_set_true(self) -> None:
        from vibecraft.directives.models import TacticalObjectivePayload

        p = TacticalObjectivePayload(verb="defend", target_area=None, persistent=True)
        assert p.persistent is True

    def test_persistent_roundtrips_json(self) -> None:
        from vibecraft.directives.models import TacticalObjectivePayload

        p = TacticalObjectivePayload(verb="retreat", target_area=None, persistent=True)
        d = p.model_dump(mode="json")
        assert d["persistent"] is True
        p2 = TacticalObjectivePayload.model_validate(d)
        assert p2.persistent is True


# =========================================================================
# ENGAGEMENT_CONSTRAINT → TacticalObjective(persistent=True) 自动转换
# =========================================================================


class TestEngagementConstraintAutoConvert:
    """旧 engagement_constraint 链路向后兼容：自动转 TacticalObjective(persistent=True)。"""

    @pytest.mark.asyncio
    async def test_defend_converts_to_tactical_objective(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """stance=defend → facade.set_engagement_stance + set_combat_intent_override。"""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "持续守家",
                "confidence": 0.95,
                "directives": [{"type": "engagement_constraint", "payload": {"stance": "defend"}}],
            },
        )
        await director.on_player_command("接下来一直守家", now=10.0)
        director.on_tick(now=12.0)

        # 旧 engagement_constraint(defend) 应触发 set_engagement_stance("defend")
        assert "defend" in facade.engagement_stances

    @pytest.mark.asyncio
    async def test_retreat_converts_to_tactical_objective(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """stance=retreat → facade.set_engagement_stance("retreat")。"""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "持续撤退",
                "confidence": 0.9,
                "directives": [{"type": "engagement_constraint", "payload": {"stance": "retreat"}}],
            },
        )
        await director.on_player_command("一直撤", now=10.0)
        director.on_tick(now=12.0)

        assert "retreat" in facade.engagement_stances

    @pytest.mark.asyncio
    async def test_hold_fallback_direct_stance(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """stance=hold（不是 TacticalVerb）→ fallback 直接 set_engagement_stance("hold")。"""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "所有人别动",
                "confidence": 0.9,
                "directives": [{"type": "engagement_constraint", "payload": {"stance": "hold"}}],
            },
        )
        await director.on_player_command("所有人原地别动", now=10.0)
        director.on_tick(now=12.0)

        assert "hold" in facade.engagement_stances

    @pytest.mark.asyncio
    async def test_free_fallback_direct_stance(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """stance=free → fallback 直接 set_engagement_stance("free")。"""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "恢复自由攻击",
                "confidence": 0.9,
                "directives": [{"type": "engagement_constraint", "payload": {"stance": "free"}}],
            },
        )
        await director.on_player_command("随便打", now=10.0)
        director.on_tick(now=12.0)

        assert "free" in facade.engagement_stances


# =========================================================================
# tactical_objective(persistent=True) 直接走链路
# =========================================================================


class TestTacticalObjectivePersistentDirect:
    """新路径：LLM 直接输出 tactical_objective(persistent=True)。"""

    @pytest.mark.asyncio
    async def test_persistent_defend_calls_stance(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """tactical_objective(verb=defend, persistent=True) → set_engagement_stance("defend")。"""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "持续守家",
                "confidence": 0.95,
                "directives": [
                    {
                        "type": "tactical_objective",
                        "payload": {
                            "verb": "defend",
                            "target_area": None,
                            "persistent": True,
                        },
                    }
                ],
            },
        )
        await director.on_player_command("接下来一直守家", now=10.0)
        director.on_tick(now=12.0)

        assert "defend" in facade.engagement_stances

    @pytest.mark.asyncio
    async def test_non_persistent_defend_does_not_set_stance(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """tactical_objective(verb=defend, persistent=False) → 不写 stance_override。"""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "守一波",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "tactical_objective",
                        "payload": {
                            "verb": "defend",
                            "target_area": None,
                            "persistent": False,
                        },
                    }
                ],
            },
        )
        await director.on_player_command("守一波", now=10.0)
        director.on_tick(now=12.0)

        # 一次性命令不写 stance
        assert facade.engagement_stances == []

    @pytest.mark.asyncio
    async def test_persistent_retreat_calls_stance(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """tactical_objective(verb=retreat, persistent=True) → set_engagement_stance("retreat")。"""
        facade = FakeFacade(state=BotState(game_time=10.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "持续撤退",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "tactical_objective",
                        "payload": {
                            "verb": "retreat",
                            "target_area": None,
                            "persistent": True,
                        },
                    }
                ],
            },
        )
        await director.on_player_command("一直撤", now=10.0)
        director.on_tick(now=12.0)

        assert "retreat" in facade.engagement_stances
