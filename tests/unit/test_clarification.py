"""Disambiguation/Clarification tool 单测(2026-05-24 用户)。

覆盖:
- IntentParser._build_clarification: LLM raw clarification 字段 → ClarificationRequest
- Director._pending_clarification 路径: 收到 ClarificationRequest → 标 pending
- Director.submit_clarification_choice: 选 option → submit directives
- Director.cancel_clarification: 点 × → 清 pending
- snapshot["pending_clarification"]: 字段透传给 PWA
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.bot import Director, FakeFacade
from vibecraft.directives.models import (
    Directive,
    ProductionItem,
    ProductionOverridePayload,
)
from vibecraft.directives.types import StageKind
from vibecraft.llm import IntentParser, MockLLMProvider, ProviderResponse
from vibecraft.llm.prompt import ParseContext
from vibecraft.llm.schema import ClarificationOption, ClarificationRequest, IntentParseResult
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


@pytest.fixture
def default_ctx() -> ParseContext:
    return ParseContext(game_time=120.0, current_stage=StageKind.MIDGAME)


def _provider_clarification(question: str, options_data: list[dict]) -> ProviderResponse:
    """构造 LLM raw response 含 clarification 字段。"""
    return ProviderResponse(
        raw={
            "interpretation_zh": "请玩家选择",
            "confidence": 0.4,
            "directives": [],
            "clarification": {
                "question": question,
                "options": options_data,
            },
        },
        input_tokens=10,
        output_tokens=20,
        latency_ms=100.0,
    )


# ============================================================
# Parser: clarification 解析
# ============================================================
class TestParserClarification:
    @pytest.mark.asyncio
    async def test_clarification_parsed(self, library, default_ctx) -> None:
        """LLM 返回 clarification → parser 转 ClarificationRequest。"""
        provider = MockLLMProvider(
            scripted=[
                _provider_clarification(
                    question="你要让哪个农民去对方三矿造水晶塔？",
                    options_data=[
                        {
                            "label": "刚才占瞭望塔那个农民",
                            "interpretation_zh": "让占瞭望塔的农民去建造",
                            "directives": [
                                {
                                    "type": "production_override",
                                    "payload": {"items": [{"unit_type": "Probe", "count": 1}]},
                                }
                            ],
                        },
                        {
                            "label": "派一个新农民",
                            "interpretation_zh": "另派一个 Probe",
                            "directives": [
                                {
                                    "type": "production_override",
                                    "payload": {"items": [{"unit_type": "Probe", "count": 1}]},
                                }
                            ],
                        },
                    ],
                ),
            ]
        )
        parser = IntentParser(provider, library)
        outcome = await parser.parse("那个农民去对方三矿造水晶塔", default_ctx)

        assert isinstance(outcome, ClarificationRequest)
        assert outcome.question.startswith("你要让")
        assert len(outcome.options) == 2
        assert outcome.options[0].label == "刚才占瞭望塔那个农民"
        assert outcome.options[0].directives[0].type.value == "production_override"
        assert outcome.source_text == "那个农民去对方三矿造水晶塔"

    @pytest.mark.asyncio
    async def test_clarification_options_too_few_falls_back(self, library, default_ctx) -> None:
        """clarification.options 少于 2 → schema 校验失败 → fallback 走 IntentParseResult。"""
        provider = MockLLMProvider(
            scripted=[
                ProviderResponse(
                    raw={
                        "interpretation_zh": "造水晶",
                        "confidence": 0.85,
                        "directives": [],
                        "clarification": {
                            "question": "?",
                            "options": [
                                {"label": "only one", "interpretation_zh": "x", "directives": []},
                            ],
                        },
                    },
                )
            ]
        )
        parser = IntentParser(provider, library)
        outcome = await parser.parse("某话", default_ctx)
        # 应 fallback 到 IntentParseResult(clarification 校验失败,直接走标准路径)
        assert isinstance(outcome, IntentParseResult)


# ============================================================
# Director: clarification 处理
# ============================================================
class TestDirectorClarification:
    def _make_director(self, session, library):
        provider = MockLLMProvider(
            scripted=[ProviderResponse(raw={}, input_tokens=0, output_tokens=0, latency_ms=0.0)]
        )
        parser = IntentParser(provider, library, session=session)
        return Director(facade=FakeFacade(), parser=parser, session=session, library=library)

    def _make_clarification(self) -> ClarificationRequest:
        return ClarificationRequest(
            question="你要哪个?",
            source_text="原话",
            options=[
                ClarificationOption(
                    label="A 选项",
                    interpretation_zh="A 解释",
                    directives=[
                        Directive(
                            payload=ProductionOverridePayload(
                                items=[ProductionItem(unit_type="Stalker", count=2)],
                            ),
                            issued_at=10.0,
                        )
                    ],
                ),
                ClarificationOption(
                    label="B 选项",
                    interpretation_zh="B 解释",
                    directives=[
                        Directive(
                            payload=ProductionOverridePayload(
                                items=[ProductionItem(unit_type="Zealot", count=2)],
                            ),
                            issued_at=10.0,
                        )
                    ],
                ),
            ],
        )

    def test_pending_set_when_clarification_arrives(self, library, session) -> None:
        director = self._make_director(session, library)
        cr = self._make_clarification()

        director._pending_clarification = cr

        # snapshot 含 pending_clarification
        snap = director.build_snapshot(now=10.0)
        assert "pending_clarification" in snap
        assert snap["pending_clarification"]["question"] == "你要哪个?"
        assert len(snap["pending_clarification"]["options"]) == 2
        assert snap["pending_clarification"]["options"][0]["label"] == "A 选项"
        assert snap["pending_clarification"]["options"][1]["directive_count"] == 1

    def test_submit_choice_submits_directives(self, library, session) -> None:
        director = self._make_director(session, library)
        director._pending_clarification = self._make_clarification()

        ok = director.submit_clarification_choice(option_index=0, now=15.0)
        assert ok is True
        # pending cleared
        assert director._pending_clarification is None
        # directives in production_overrides
        assert len(director.production_overrides) == 1
        # 是 A 选项的 Stalker
        po = director.production_overrides[0].payload
        assert po.items[0].unit_type == "Stalker"

    def test_submit_choice_oob_returns_false(self, library, session) -> None:
        director = self._make_director(session, library)
        director._pending_clarification = self._make_clarification()

        ok = director.submit_clarification_choice(option_index=99, now=15.0)
        assert ok is False
        # pending 仍在(没消费)
        assert director._pending_clarification is not None

    def test_submit_choice_no_pending_returns_false(self, library, session) -> None:
        director = self._make_director(session, library)
        # 无 pending
        ok = director.submit_clarification_choice(option_index=0, now=15.0)
        assert ok is False

    def test_cancel_clears_pending(self, library, session) -> None:
        director = self._make_director(session, library)
        director._pending_clarification = self._make_clarification()

        ok = director.cancel_clarification(now=15.0)
        assert ok is True
        assert director._pending_clarification is None
        # 没 submit 任何 directive
        assert len(director.production_overrides) == 0

    def test_cancel_no_pending_returns_false(self, library, session) -> None:
        director = self._make_director(session, library)
        ok = director.cancel_clarification(now=15.0)
        assert ok is False
