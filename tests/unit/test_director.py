"""Director 单测：用 FakeFacade + MockLLMProvider 完整 mock。

覆盖：
- on_player_command + tick 推进 → facade 收到正确调用
- strategy_set → facade.set_build()
- production_override → facade.set_production_override()
- engagement_constraint → facade.set_engagement_stance()
- unit_claim → facade.set_unit_role(LLM_CONTROLLED) + execute_unit_action
- unit_release → facade.set_unit_role(IDLE/ARMY)
- view_move → facade.move_camera 立即（不走 Board）
- ParseError → facade 不变 (设计文档 §7.6)
- ParseContext 从 facade.get_state() + board.overlays 正确构造
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.bot import BotState, Director, FakeFacade, UnitRole
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
        aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
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
# strategy_set 全链路
# =========================================================================


class TestStrategySetDispatch:
    @pytest.mark.asyncio
    async def test_strategy_set_calls_facade_set_build(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade(state=BotState(game_time=100.0))
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "切到 IAC",
                "confidence": 0.95,
                "directives": [
                    {
                        "type": "strategy_set",
                        "payload": {"stage": "midgame", "strategy_id": "iac_2base"},
                    }
                ],
            },
        )

        await director.on_player_command("切 IAC", now=100.0)
        # 还没到 effective_at（100 + 1.5 = 101.5），tick 没事
        director.on_tick(now=100.5)
        assert facade.builds == []

        # 推过 effective_at
        director.on_tick(now=101.5)
        assert facade.builds == ["iac_2base"]


class TestUnitClaimDispatch:
    @pytest.mark.asyncio
    async def test_unit_claim_sets_role_and_executes_primary(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade(state=BotState(game_time=200.0))
        facade.selector_stub["Phoenix"] = [12345, 12346]
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "凤凰举不朽",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "unit_claim",
                        "payload": {
                            "selector": {"unit_type": "Phoenix"},
                            "task": {
                                "primary_action": {
                                    "verb": "lift_target",
                                    "target": {
                                        "kind": "unit_type",
                                        "unit_type": "Immortal",
                                    },
                                }
                            },
                        },
                    }
                ],
            },
        )
        await director.on_player_command("凤凰举不朽", now=200.0)
        director.on_tick(now=202.0)

        assert facade.unit_roles == {
            12345: UnitRole.LLM_CONTROLLED,
            12346: UnitRole.LLM_CONTROLLED,
        }
        assert len(facade.unit_actions) == 2
        assert all(a["verb"] == "lift_target" for a in facade.unit_actions)


class TestEngagementDispatch:
    @pytest.mark.asyncio
    async def test_engagement_constraint_dispatches(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "守家",
                "confidence": 0.95,
                "directives": [{"type": "engagement_constraint", "payload": {"stance": "defend"}}],
            },
        )
        await director.on_player_command("守家", now=50.0)
        director.on_tick(now=52.0)
        assert facade.engagement_stances == ["defend"]


class TestProductionDispatch:
    @pytest.mark.asyncio
    async def test_production_override(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "下个 BG 出俩哨兵",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "production_override",
                        "payload": {"unit_type": "Sentry", "count": 2},
                        "priority": 70,
                    }
                ],
            },
        )
        await director.on_player_command("出俩哨兵", now=10.0)
        director.on_tick(now=12.0)
        assert facade.production_overrides == [("Sentry", 2, None)]


# =========================================================================
# View directive 立即生效（不进 Board）
# =========================================================================


class TestViewDirectiveImmediate:
    @pytest.mark.asyncio
    async def test_view_move_skips_board_delay(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "看 11 点",
                "confidence": 0.95,
                "directives": [
                    {
                        "type": "view_move",
                        "payload": {"target_point": [55.0, 120.0]},
                    }
                ],
            },
        )
        await director.on_player_command("看 11 点", now=10.0)
        # 没 tick，相机已动
        assert facade.camera_moves == [(55.0, 120.0)]


# =========================================================================
# ParseError 不动 bot 状态 (§7.6)
# =========================================================================


class TestParseErrorIsNoop:
    @pytest.mark.asyncio
    async def test_unknown_strategy_does_not_change_facade(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "...",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "strategy_set",
                        "payload": {"stage": "midgame", "strategy_id": "nope_typo"},
                    }
                ],
            },
        )
        from vibecraft.llm import ParseError

        outcome = await director.on_player_command("...", now=10.0)
        assert isinstance(outcome, ParseError)
        director.on_tick(now=12.0)
        assert facade.builds == []
        assert facade.calls == []


# =========================================================================
# ParseContext 构造
# =========================================================================


class TestParseContextBuilding:
    @pytest.mark.asyncio
    async def test_context_pulls_from_facade_state(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade(
            state=BotState(
                game_time=300.0,
                minerals=800,
                gas=250,
                supply_used=42,
                supply_cap=50,
                expansion_count=3,
                army_summary={"Stalker": 12, "Sentry": 4},
                enemy_summary={"Marine": 8},
            )
        )
        director = _make_director(
            library,
            session,
            facade,
            {"interpretation_zh": "ok", "confidence": 0.9, "directives": []},
        )

        ctx = director.build_parse_context(now=300.0)
        assert ctx.minerals == 800
        assert ctx.gas == 250
        assert ctx.expansion_count == 3
        assert ctx.army_summary == {"Stalker": 12, "Sentry": 4}
        assert ctx.enemy_summary == {"Marine": 8}

    @pytest.mark.asyncio
    async def test_context_includes_recent_commands(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {"interpretation_zh": "ok", "confidence": 0.9, "directives": []},
        )
        await director.on_player_command("第一句", now=10.0)
        ctx = director.build_parse_context(now=11.0)
        assert "第一句" in ctx.recent_commands


# =========================================================================
# 多 directive 复合句
# =========================================================================


class TestCompoundCommands:
    @pytest.mark.asyncio
    async def test_compound_strategy_plus_engagement(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "切剧本然后守家",
                "confidence": 0.92,
                "directives": [
                    {
                        "type": "strategy_set",
                        "payload": {"stage": "midgame", "strategy_id": "iac_2base"},
                    },
                    {
                        "type": "engagement_constraint",
                        "payload": {"stance": "defend"},
                    },
                ],
            },
        )
        await director.on_player_command("切 IAC，守家", now=10.0)
        director.on_tick(now=12.0)
        assert facade.builds == ["iac_2base"]
        assert facade.engagement_stances == ["defend"]


# =========================================================================
# Logging 副作用
# =========================================================================


class TestLoggingIntegration:
    @pytest.mark.asyncio
    async def test_committed_event_logged(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        from vibecraft.logging_ import LogStream

        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "...",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "strategy_set",
                        "payload": {"stage": "midgame", "strategy_id": "iac_2base"},
                    }
                ],
            },
        )
        await director.on_player_command("...", now=10.0)
        director.on_tick(now=12.0)
        events = session.get_null_records(LogStream.EVENTS)
        kinds = [e["kind"] for e in events]
        assert "directive.committed" in kinds
        assert "strategy.set" in kinds
