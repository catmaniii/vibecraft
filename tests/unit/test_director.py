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
- standing_orders 路由 + revoke（P1.2）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.bot import BotState, Director, FakeFacade, UnitRole
from vibecraft.directives.models import Directive
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
        # P2: PRODUCTION_OVERRIDE 进 Director.production_overrides list,不再走
        # facade dispatch (P3 task_monitor 才接 sharpy 实际生产 wire)。
        assert len(director.production_overrides) == 1
        assert facade.production_overrides == []


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

    @pytest.mark.asyncio
    async def test_directives_stream_has_submitted(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """_submit_directives 应向 directives.jsonl 写 submitted 记录。"""
        from vibecraft.logging_ import LogStream

        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "切 IAC",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "strategy_set",
                        "payload": {"stage": "midgame", "strategy_id": "iac_2base"},
                    }
                ],
            },
        )
        await director.on_player_command("切 IAC", now=10.0)
        records = session.get_null_records(LogStream.DIRECTIVES)
        events = [r["event"] for r in records]
        assert "submitted" in events

    @pytest.mark.asyncio
    async def test_directives_stream_has_committed(
        self, library: StrategyLibrary, session: GameSession
    ) -> None:
        """tick 到 effective_at 后 directives.jsonl 应有 committed 记录。"""
        from vibecraft.logging_ import LogStream

        facade = FakeFacade()
        director = _make_director(
            library,
            session,
            facade,
            {
                "interpretation_zh": "切 IAC",
                "confidence": 0.9,
                "directives": [
                    {
                        "type": "strategy_set",
                        "payload": {"stage": "midgame", "strategy_id": "iac_2base"},
                    }
                ],
            },
        )
        await director.on_player_command("切 IAC", now=10.0)
        director.on_tick(now=12.0)
        records = session.get_null_records(LogStream.DIRECTIVES)
        events = [r["event"] for r in records]
        assert "submitted" in events
        assert "committed" in events

    def test_directives_stream_submitted_on_direct_submit(self, session: GameSession) -> None:
        """Director(session=mock_session) 构造 OK；_submit_directives 时 session.log 被 called。"""
        from unittest.mock import MagicMock

        from vibecraft.directives.models import ProductionOverridePayload
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        mock_session = MagicMock()
        director = Director(facade=facade, parser=parser, session=mock_session)

        # 直接 submit 一个 directive
        from vibecraft.directives.models import Directive

        payload = ProductionOverridePayload(unit_type="Stalker", count=3)
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)

        # session.log 应该被调用（写 directives.jsonl）
        mock_session.log.assert_called()
        # 验证第一个 call 是 DIRECTIVES stream，event=submitted
        from vibecraft.logging_ import LogStream

        call_args = mock_session.log.call_args_list[0]
        assert call_args[0][0] == LogStream.DIRECTIVES
        record = call_args[0][1]
        assert record["event"] == "submitted"


# =========================================================================
# P1.2 Standing Order 路由（persistent=True → standing_orders；False → _in_flight）
# =========================================================================


def _make_unit_claim_directive(persistent: bool) -> Directive:
    """构造一个 UNIT_CLAIM Directive，persistent 按参数。"""
    from vibecraft.directives.models import UnitClaimPayload
    from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
    from vibecraft.directives.task import Action, Task, Verb

    payload = UnitClaimPayload(
        selector=Selector(unit_type="Phoenix"),
        task=Task(
            primary_action=Action(
                verb=Verb.LIFT_TARGET,
                target=TargetSpec(kind=TargetKind.UNIT_TYPE, unit_type="Immortal"),
            )
        ),
        persistent=persistent,
    )
    return Directive(payload=payload, issued_at=10.0)


@pytest.fixture
def director(session: GameSession) -> Director:
    """最小 Director 实例，不需要 LLM provider（直接调 _submit_directives）。"""
    from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

    facade = FakeFacade()
    provider = MockLLMProvider(
        scripted=[
            ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)
        ]
    )
    library_inst = StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
    )
    parser = IntentParser(provider, library_inst, session=session)
    return Director(facade=facade, parser=parser, session=session)


class TestStandingOrderRouting:
    """P1.2 Director 按 persistent 路由 directive 到 standing_orders 或 _in_flight。"""

    def test_persistent_false_goes_to_in_flight(self, director: Director) -> None:
        d = _make_unit_claim_directive(persistent=False)
        director._submit_directives([d], now=10.0)
        # 进 pending → 还没 committed，但 _in_flight 里已有（board.submit 之后）
        assert d.id in director._in_flight
        assert not any(s.id == d.id for s in director.standing_orders)

    def test_persistent_true_goes_to_standing_orders(self, director: Director) -> None:
        d = _make_unit_claim_directive(persistent=True)
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.standing_orders)
        assert d.id not in director._in_flight

    def test_revoke_standing_order_removes(self, director: Director) -> None:
        d = _make_unit_claim_directive(persistent=True)
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.standing_orders)
        result = director.revoke_standing_order(d.id, now=15.0)
        assert result is True
        assert not any(s.id == d.id for s in director.standing_orders)


# =========================================================================
# P2: Production Override 路由（PRODUCTION_OVERRIDE/TECH_OVERRIDE/EXPANSION_OVERRIDE
#     → production_overrides 列表，不进 _in_flight）
# =========================================================================


def _make_production_override_directive() -> Directive:
    """构造一个 PRODUCTION_OVERRIDE Directive（出 2 哨兵）。"""
    from vibecraft.directives.models import ProductionOverridePayload

    payload = ProductionOverridePayload(unit_type="Sentry", count=2)
    return Directive(payload=payload, issued_at=10.0)


def _make_tech_override_directive() -> Directive:
    """构造一个 TECH_OVERRIDE Directive（研 Blink）。"""
    from vibecraft.directives.models import TechOverridePayload

    payload = TechOverridePayload(upgrade_id="Blink")
    return Directive(payload=payload, issued_at=10.0)


def _make_expansion_override_directive() -> Directive:
    """构造一个 EXPANSION_OVERRIDE Directive（开 3 矿）。"""
    from vibecraft.directives.models import ExpansionOverridePayload

    payload = ExpansionOverridePayload(target_count=3)
    return Directive(payload=payload, issued_at=10.0)


class TestProductionOverrideRouting:
    """P2 Director 把 PRODUCTION/TECH/EXPANSION override 路由到 production_overrides。"""

    def test_production_override_goes_to_production_overrides(self, director: Director) -> None:
        d = _make_production_override_directive()
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.production_overrides)
        assert d.id not in director._in_flight

    def test_tech_override_goes_to_production_overrides(self, director: Director) -> None:
        d = _make_tech_override_directive()
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.production_overrides)
        assert d.id not in director._in_flight

    def test_expansion_override_goes_to_production_overrides(self, director: Director) -> None:
        d = _make_expansion_override_directive()
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.production_overrides)
        assert d.id not in director._in_flight


# =========================================================================
# P2: revoke_directive unified（撤 standing + production override）
# =========================================================================


class TestRevokeDirectiveUnified:
    """P2 revoke_directive(id, now) 统一撤销 standing_orders 和 production_overrides。"""

    def test_revoke_directive_removes_standing_order(self, director: Director) -> None:
        d = _make_unit_claim_directive(persistent=True)
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.standing_orders)
        result = director.revoke_directive(d.id, now=15.0)
        assert result is True
        assert not any(s.id == d.id for s in director.standing_orders)

    def test_revoke_directive_removes_production_override(self, director: Director) -> None:
        d = _make_production_override_directive()
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.production_overrides)
        result = director.revoke_directive(d.id, now=15.0)
        assert result is True
        assert not any(s.id == d.id for s in director.production_overrides)


# =========================================================================
# P3.2: TaskMonitor wiring
# =========================================================================


def _make_director_with_task_monitor(session: GameSession) -> Director:
    """构造带 task_monitor 的 Director（传入 EventBus）。"""
    from vibecraft.bot.event_bus import EventBus
    from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

    facade = FakeFacade()
    provider = MockLLMProvider(
        scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
    )
    library_inst = StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
    )
    parser = IntentParser(provider, library_inst, session=session)
    event_bus = EventBus()
    return Director(facade=facade, parser=parser, session=session, event_bus=event_bus)


def _make_tactical_objective_directive(done_when_dict: dict | None = None) -> Directive:
    """构造一个 TACTICAL_OBJECTIVE Directive，可选带 done_when。"""
    from vibecraft.directives.models import TacticalObjectivePayload, TimeElapsedSince

    if done_when_dict is not None:
        dw = TimeElapsedSince(kind="time_elapsed_since", seconds=float(done_when_dict.get("seconds", 30)))
    else:
        dw = None

    payload = TacticalObjectivePayload(verb="attack", done_when=dw, timeout_s=None)
    return Directive(payload=payload, issued_at=10.0)


class TestTaskMonitorWire:
    """P3.2: task_monitor wiring 单测。"""

    def test_no_event_bus_task_monitor_is_none(self, session: GameSession) -> None:
        """不传 event_bus → task_monitor 为 None，Director 不崩。"""
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        d = Director(facade=facade, parser=parser, session=session)
        assert d.task_monitor is None
        # on_tick 不崩
        d.on_tick(now=10.0)

    def test_attach_called_when_done_when_set(self, session: GameSession) -> None:
        """_submit_directives 对有 done_when 的 directive 调 task_monitor.attach_directive。"""
        director = _make_director_with_task_monitor(session)
        assert director.task_monitor is not None

        # mock task_monitor.attach_directive
        original_attach = director.task_monitor.attach_directive
        attach_calls: list[dict] = []

        def _recording_attach(**kwargs: object) -> None:  # type: ignore[override]
            attach_calls.append(dict(kwargs))
            original_attach(**kwargs)  # type: ignore[arg-type]

        director.task_monitor.attach_directive = _recording_attach  # type: ignore[method-assign]

        d = _make_tactical_objective_directive(
            done_when_dict={"kind": "time_elapsed_since", "seconds": 90}
        )
        director._submit_directives([d], now=10.0)

        assert len(attach_calls) == 1
        assert attach_calls[0]["directive_id"] == d.id

    def test_no_attach_when_done_when_none(self, session: GameSession) -> None:
        """done_when=None 时不调 attach_directive。"""
        director = _make_director_with_task_monitor(session)
        assert director.task_monitor is not None

        attach_calls: list[object] = []
        original_attach = director.task_monitor.attach_directive

        def _spy(**kwargs: object) -> None:  # type: ignore[override]
            attach_calls.append(kwargs)
            original_attach(**kwargs)  # type: ignore[arg-type]

        director.task_monitor.attach_directive = _spy  # type: ignore[method-assign]

        d = _make_tactical_objective_directive(done_when_dict=None)
        director._submit_directives([d], now=10.0)

        assert len(attach_calls) == 0

    def test_tick_completed_id_triggers_complete_and_detach(self, session: GameSession) -> None:
        """task_monitor.tick 返回的 id 触发 board.complete + detach + 从 _in_flight 移除。"""
        from unittest.mock import patch

        director = _make_director_with_task_monitor(session)
        assert director.task_monitor is not None

        # 先 submit 一个有 done_when 的 directive
        d = _make_tactical_objective_directive(
            done_when_dict={"kind": "time_elapsed_since", "seconds": 30}
        )
        director._submit_directives([d], now=10.0)
        # 确认进了 _in_flight（还在 board.pending 里，key=d.id）
        assert d.id in director._in_flight

        # mock task_monitor.tick 返回这个 id（模拟 checker 判定已完成）
        completed_ids = [d.id]
        with patch.object(director.task_monitor, "tick", return_value=completed_ids) as mock_tick, \
             patch.object(director.task_monitor, "detach") as mock_detach:
            director.on_tick(now=40.0)
            mock_tick.assert_called_once()
            mock_detach.assert_called_once_with(d.id)

        # directive 应该从 _in_flight 移除
        assert d.id not in director._in_flight

    def test_tick_completed_production_override_removed(self, session: GameSession) -> None:
        """task_monitor 完成的 id 也从 production_overrides 移除。"""
        from unittest.mock import patch

        director = _make_director_with_task_monitor(session)
        assert director.task_monitor is not None

        # 构造带 done_when 的 production_override
        from vibecraft.directives.models import ProductionOverridePayload, TimeElapsedSince

        payload = ProductionOverridePayload(
            unit_type="Sentry",
            count=2,
            done_when=TimeElapsedSince(kind="time_elapsed_since", seconds=30),
        )
        d = Directive(payload=payload, issued_at=10.0)
        director._submit_directives([d], now=10.0)
        assert any(s.id == d.id for s in director.production_overrides)

        completed_ids = [d.id]
        with patch.object(director.task_monitor, "tick", return_value=completed_ids), \
             patch.object(director.task_monitor, "detach"):
            director.on_tick(now=40.0)

        assert not any(s.id == d.id for s in director.production_overrides)

    def test_setup_task_monitor_works(self, session: GameSession) -> None:
        """setup_task_monitor 事后注入 event_bus，task_monitor 从 None 变为有效实例。"""
        from vibecraft.bot.event_bus import EventBus
        from vibecraft.bot.task_monitor import TaskMonitor
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        d = Director(facade=facade, parser=parser, session=session)
        assert d.task_monitor is None

        bus = EventBus()
        d.setup_task_monitor(bus)
        assert isinstance(d.task_monitor, TaskMonitor)


# ---------------------------------------------------------------------------
# P5.C: Director bot backref
# ---------------------------------------------------------------------------


class TestDirectorBotBackref:
    def test_director_accepts_bot_kwarg(self, session: GameSession) -> None:
        """Director(bot=mock_bot) 构造 OK，_bot 保存 bot 引用。"""
        from unittest.mock import MagicMock

        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        mock_bot = MagicMock()
        d = Director(facade=facade, parser=parser, session=session, bot=mock_bot)
        assert d._bot is mock_bot

    def test_director_bot_none_by_default(self, session: GameSession) -> None:
        """不传 bot 时 _bot 为 None（向后兼容）。"""
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        d = Director(facade=facade, parser=parser, session=session)
        assert d._bot is None

    def test_on_tick_passes_bot_to_task_monitor(self, session: GameSession) -> None:
        """on_tick 时把 _bot 传给 task_monitor.tick 作为 game_state。"""
        from unittest.mock import MagicMock, patch

        from vibecraft.bot.event_bus import EventBus
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        mock_bot = MagicMock()
        d = Director(facade=facade, parser=parser, session=session, bot=mock_bot)

        # 注入 task_monitor
        bus = EventBus()
        d.setup_task_monitor(bus)

        with patch.object(d.task_monitor, "tick", return_value=[]) as mock_tick:
            d.on_tick(now=10.0)
            mock_tick.assert_called_once_with(10.0, game_state=mock_bot)


# =========================================================================
# P5.E: Standing order unit assign + sharpy 让位 + revoke release
# =========================================================================


def _make_persistent_unit_claim_directive(unit_type: str = "Phoenix") -> Directive:
    """构造 persistent=True 的 unit_claim Directive，用于 standing order 测试。"""
    from vibecraft.directives.models import UnitClaimPayload
    from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
    from vibecraft.directives.task import Action, Task, Verb

    payload = UnitClaimPayload(
        selector=Selector(unit_type=unit_type),
        task=Task(
            primary_action=Action(
                verb=Verb.PATROL,
                target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_natural"),
            )
        ),
        persistent=True,
    )
    return Directive(payload=payload, issued_at=10.0)


class TestStandingOrderUnitAssign:
    """P5.E: persistent unit_claim 进 standing_orders 时 resolve selector + 通知 sharpy 让位。"""

    def test_persistent_claim_calls_set_unit_role_on_submit(self, session: GameSession) -> None:
        """submit persistent unit_claim → facade.set_unit_role(LLM_CONTROLLED) 被调用。"""
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        facade.selector_stub["Phoenix"] = [1001, 1002]
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)

        d = _make_persistent_unit_claim_directive("Phoenix")
        director._submit_directives([d], now=10.0)

        # standing_orders に入っていること
        assert any(s.id == d.id for s in director.standing_orders)
        # set_unit_role(LLM_CONTROLLED) が両 tag に呼ばれること
        assert facade.unit_roles == {1001: UnitRole.LLM_CONTROLLED, 1002: UnitRole.LLM_CONTROLLED}
        set_role_calls = [c for c in facade.calls if c.method == "set_unit_role"]
        assert len(set_role_calls) == 2
        tags_called = {c.args[0] for c in set_role_calls}
        assert tags_called == {1001, 1002}

    def test_tags_tracked_in_standing_order_tags(self, session: GameSession) -> None:
        """_standing_order_tags directive_id → assigned tags 被正确记录。"""
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        facade.selector_stub["Phoenix"] = [2001, 2002]
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)

        d = _make_persistent_unit_claim_directive("Phoenix")
        director._submit_directives([d], now=10.0)

        assert d.id in director._standing_order_tags
        assert director._standing_order_tags[d.id] == {2001, 2002}

    def test_revoke_standing_order_calls_release_unit_role(self, session: GameSession) -> None:
        """revoke_standing_order → facade.release_unit_role 被每个 tag 调用。"""
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        facade.selector_stub["Phoenix"] = [3001, 3002]
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)

        d = _make_persistent_unit_claim_directive("Phoenix")
        director._submit_directives([d], now=10.0)

        # revoke 前 unit_roles 已记录
        assert 3001 in facade.unit_roles
        assert 3002 in facade.unit_roles

        result = director.revoke_standing_order(d.id, now=15.0)
        assert result is True

        # release_unit_role 被调用，unit_roles 从 FakeFacade 移除
        assert 3001 not in facade.unit_roles
        assert 3002 not in facade.unit_roles
        release_calls = [c for c in facade.calls if c.method == "release_unit_role"]
        assert len(release_calls) == 2
        released_tags = {c.args[0] for c in release_calls}
        assert released_tags == {3001, 3002}

    def test_revoke_clears_standing_order_tags(self, session: GameSession) -> None:
        """revoke 后 _standing_order_tags 中移除该 directive_id。"""
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        facade.selector_stub["Phoenix"] = [4001]
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)

        d = _make_persistent_unit_claim_directive("Phoenix")
        director._submit_directives([d], now=10.0)
        assert d.id in director._standing_order_tags

        director.revoke_standing_order(d.id, now=15.0)
        assert d.id not in director._standing_order_tags

    def test_non_persistent_claim_does_not_assign_units_early(
        self, session: GameSession
    ) -> None:
        """non-persistent unit_claim 不走 _assign_standing_order_units（不提前 set_unit_role）。

        set_unit_role 在 committed 时由 _apply_unit_claim 处理（现有逻辑）。
        """
        from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse

        facade = FakeFacade()
        facade.selector_stub["Phoenix"] = [5001]
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        library_inst = StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )
        parser = IntentParser(provider, library_inst, session=session)
        director = Director(facade=facade, parser=parser, session=session)

        d = _make_unit_claim_directive(persistent=False)
        director._submit_directives([d], now=10.0)

        # submit 时 _standing_order_tags 不应有记录
        assert d.id not in director._standing_order_tags
        # 且还未调 set_unit_role（committed 前不调）
        set_role_calls = [c for c in facade.calls if c.method == "set_unit_role"]
        assert len(set_role_calls) == 0
