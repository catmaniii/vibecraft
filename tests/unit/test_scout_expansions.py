"""野矿侦查快捷指令单测 (2026-06-29).

覆盖:
1. 轻侦查野矿 → 2 条 scout directive，分别 target enemy_natural + enemy_third
2. 火力侦查野矿 → 1 条 tactical_objective(verb=recon, target_area=enemy_natural)
3. ScoutPayload 能接受 enemy_natural / enemy_third named_spot
4. few_shot.md 含新例子关键词
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.directives.models import ScoutPayload, TacticalObjectivePayload
from vibecraft.directives.scope import TargetKind, TargetSpec
from vibecraft.directives.types import DirectiveType, StageKind
from vibecraft.llm import (
    IntentParser,
    IntentParseResult,
    MockLLMProvider,
    ParseContext,
    ProviderResponse,
    build_few_shot,
)
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def library() -> StrategyLibrary:
    return StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )


@pytest.fixture
def default_ctx() -> ParseContext:
    return ParseContext(
        game_time=180.0,
        current_stage=StageKind.OPENING,
        active_strategies={
            StageKind.OPENING: "1g_robo_immortal",
            StageKind.MIDGAME: None,
            StageKind.LATEGAME: None,
        },
        minerals=400,
        gas=100,
        supply_used=28,
        supply_cap=36,
        expansion_count=1,
        army_summary={"Stalker": 4},
        recent_commands=[],
    )


def _mock_response(raw: dict) -> ProviderResponse:
    return ProviderResponse(
        raw=raw,
        input_tokens=2000,
        output_tokens=60,
        cache_hit=True,
        latency_ms=10.0,
        model="mock",
        provider="mock",
    )


# ---------------------------------------------------------------------------
# ScoutPayload model tests (no LLM, just model validation)
# ---------------------------------------------------------------------------


class TestScoutPayloadModel:
    def test_scout_enemy_natural(self) -> None:
        """ScoutPayload 能接受 enemy_natural named_spot."""
        payload = ScoutPayload(
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_natural"),
            selector=None,
        )
        assert payload.target.named_spot == "enemy_natural"
        assert payload.type == DirectiveType.SCOUT

    def test_scout_enemy_third(self) -> None:
        """ScoutPayload 能接受 enemy_third named_spot."""
        payload = ScoutPayload(
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_third"),
            selector=None,
        )
        assert payload.target.named_spot == "enemy_third"

    def test_two_scouts_different_targets(self) -> None:
        """两条 scout payload 可以各指不同目标，模型层无冲突."""
        s1 = ScoutPayload(
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_natural"),
        )
        s2 = ScoutPayload(
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_third"),
        )
        assert s1.target.named_spot != s2.target.named_spot


# ---------------------------------------------------------------------------
# IntentParser + MockLLMProvider: 轻侦查野矿
# ---------------------------------------------------------------------------


class TestLightScoutExpansions:
    @pytest.mark.asyncio
    async def test_light_scout_emits_two_scout_directives(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        """轻侦查野矿 → 2 条 scout，分别 enemy_natural + enemy_third."""
        provider = MockLLMProvider(
            scripted=[
                _mock_response(
                    {
                        "interpretation_zh": "派 2 个工人分头侦查对方二矿和三矿",
                        "confidence": 0.92,
                        "directives": [
                            {
                                "type": "scout",
                                "payload": {
                                    "target": {
                                        "kind": "named_spot",
                                        "named_spot": "enemy_natural",
                                    },
                                    "selector": None,
                                    "done_when": {
                                        "kind": "vision_acquired",
                                        "area": "enemy_natural",
                                        "hold_seconds": 1,
                                    },
                                    "timeout_s": 30,
                                },
                            },
                            {
                                "type": "scout",
                                "payload": {
                                    "target": {
                                        "kind": "named_spot",
                                        "named_spot": "enemy_third",
                                    },
                                    "selector": None,
                                    "done_when": {
                                        "kind": "vision_acquired",
                                        "area": "enemy_third",
                                        "hold_seconds": 1,
                                    },
                                    "timeout_s": 30,
                                },
                            },
                        ],
                    }
                )
            ]
        )
        parser = IntentParser(provider, library)
        outcome = await parser.parse("侦查野矿", default_ctx)

        assert isinstance(outcome, IntentParseResult)
        assert outcome.confidence == pytest.approx(0.92)
        assert len(outcome.directives) == 2, "轻侦查野矿必须发 2 条 scout directive"

        scout_directives = [d for d in outcome.directives if d.type == DirectiveType.SCOUT]
        assert len(scout_directives) == 2, "两条都应是 SCOUT 类型"

        targets = {d.payload.target.named_spot for d in scout_directives}
        assert "enemy_natural" in targets, "必须包含 enemy_natural（对方二矿）"
        assert "enemy_third" in targets, "必须包含 enemy_third（对方三矿）"

    @pytest.mark.asyncio
    async def test_light_scout_no_selector(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        """轻侦查 selector=None → bot 自选最便宜单位."""
        provider = MockLLMProvider(
            scripted=[
                _mock_response(
                    {
                        "interpretation_zh": "派工人侦查野矿",
                        "confidence": 0.90,
                        "directives": [
                            {
                                "type": "scout",
                                "payload": {
                                    "target": {
                                        "kind": "named_spot",
                                        "named_spot": "enemy_natural",
                                    },
                                    "selector": None,
                                },
                            },
                            {
                                "type": "scout",
                                "payload": {
                                    "target": {
                                        "kind": "named_spot",
                                        "named_spot": "enemy_third",
                                    },
                                    "selector": None,
                                },
                            },
                        ],
                    }
                )
            ]
        )
        parser = IntentParser(provider, library)
        outcome = await parser.parse("看对方开矿没", default_ctx)

        assert isinstance(outcome, IntentParseResult)
        for d in outcome.directives:
            assert d.type == DirectiveType.SCOUT
            assert isinstance(d.payload, ScoutPayload)
            assert d.payload.selector is None, "轻侦查不指定单位，bot 自选"


# ---------------------------------------------------------------------------
# IntentParser + MockLLMProvider: 火力侦查野矿
# ---------------------------------------------------------------------------


class TestFireReconExpansions:
    @pytest.mark.asyncio
    async def test_fire_recon_emits_single_recon_to_enemy_natural(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        """火力侦查野矿 → 1 条 tactical_objective(verb=recon, target_area=enemy_natural)."""
        provider = MockLLMProvider(
            scripted=[
                _mock_response(
                    {
                        "interpretation_zh": "火力侦查小队去对方二矿",
                        "confidence": 0.93,
                        "directives": [
                            {
                                "type": "tactical_objective",
                                "payload": {
                                    "verb": "recon",
                                    "target_area": "enemy_natural",
                                    "unit_count_hint": 4,
                                    "unit_type_hint": ["Stalker"],
                                    "done_when": {
                                        "kind": "any_of",
                                        "conditions": [
                                            {
                                                "kind": "vision_acquired",
                                                "area": "enemy_natural",
                                                "hold_seconds": 2,
                                            },
                                            {
                                                "kind": "own_army_size_ratio",
                                                "op": "<=",
                                                "value": 0.6,
                                            },
                                            {
                                                "kind": "time_elapsed_since",
                                                "seconds": 30,
                                                "ref": "directive_issued",
                                            },
                                        ],
                                    },
                                    "timeout_s": 90,
                                },
                            }
                        ],
                    }
                )
            ]
        )
        parser = IntentParser(provider, library)
        outcome = await parser.parse("火力侦查野矿", default_ctx)

        assert isinstance(outcome, IntentParseResult)
        assert len(outcome.directives) == 1, "火力侦查野矿只发 1 条 directive（不拆成两个 recon）"

        d = outcome.directives[0]
        assert d.type == DirectiveType.TACTICAL_OBJECTIVE
        assert isinstance(d.payload, TacticalObjectivePayload)
        assert d.payload.verb == "recon"
        assert d.payload.target_area == "enemy_natural"
        assert d.payload.unit_count_hint == 4

    @pytest.mark.asyncio
    async def test_fire_recon_has_done_when(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        """火力侦查必须带 done_when（B 类 verb 规则）."""
        provider = MockLLMProvider(
            scripted=[
                _mock_response(
                    {
                        "interpretation_zh": "带兵查野矿",
                        "confidence": 0.91,
                        "directives": [
                            {
                                "type": "tactical_objective",
                                "payload": {
                                    "verb": "recon",
                                    "target_area": "enemy_natural",
                                    "unit_count_hint": 4,
                                    "unit_type_hint": ["Stalker"],
                                    "done_when": {
                                        "kind": "time_elapsed_since",
                                        "seconds": 30,
                                        "ref": "directive_issued",
                                    },
                                    "timeout_s": 90,
                                },
                            }
                        ],
                    }
                )
            ]
        )
        parser = IntentParser(provider, library)
        outcome = await parser.parse("带兵查野矿", default_ctx)

        assert isinstance(outcome, IntentParseResult)
        d = outcome.directives[0]
        assert isinstance(d.payload, TacticalObjectivePayload)
        assert d.payload.done_when is not None, "recon 必须带 done_when"


# ---------------------------------------------------------------------------
# few_shot.md 内容回归
# ---------------------------------------------------------------------------


class TestFewShotContent:
    def test_few_shot_has_expansion_scout_examples(self) -> None:
        """few_shot.md 含野矿侦查新例子关键词."""
        fs = build_few_shot()
        assert "野矿轻侦查" in fs, "应含例 66 标题"
        assert "enemy_natural" in fs
        assert "enemy_third" in fs

    def test_few_shot_has_fire_recon_expansion_example(self) -> None:
        """few_shot.md 含火力侦查野矿例子."""
        fs = build_few_shot()
        assert "火力侦查野矿" in fs, "应含例 67 标题"
        assert "带兵查野矿" in fs

    def test_few_shot_two_scouts_note(self) -> None:
        """few_shot.md 明确说两条 scout 分头扫（不能只发一条）."""
        fs = build_few_shot()
        assert "两条 scout" in fs or "2 条 scout" in fs
