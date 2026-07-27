"""LLM 解析"出 4 叉子棱镜空投对面二矿"类话语。

用 MockLLMProvider(scripted)模拟 LLM 输出 JSON,验证 IntentParser 能拿到正确
DropActPayload。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibecraft.directives.models import DropActPayload
from vibecraft.directives.types import StageKind
from vibecraft.llm import (
    IntentParser,
    IntentParseResult,
    MockLLMProvider,
    ParseContext,
    ProviderResponse,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def library():
    from vibecraft.strategy import StrategyLibrary

    return StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )


@pytest.fixture
def session():
    from vibecraft.logging_ import GameSession, GameSessionConfig

    s = GameSession(GameSessionConfig(use_null_sinks=True))
    yield s
    s.close()


@pytest.fixture
def default_ctx() -> ParseContext:
    return ParseContext(
        game_time=245.0,
        current_stage=StageKind.OPENING,
        active_strategies={StageKind.OPENING: "1g_robo_immortal"},
        minerals=600,
        gas=200,
        supply_used=36,
        supply_cap=44,
        expansion_count=1,
        army_summary={"Stalker": 8},
    )


def _scripted_provider(raw_json: dict) -> MockLLMProvider:
    """让 mock LLM 第一个响应返回指定 JSON。"""
    return MockLLMProvider(
        scripted=[
            ProviderResponse(
                raw=raw_json,
                input_tokens=10,
                output_tokens=20,
                latency_ms=50.0,
            )
        ]
    )


@pytest.mark.asyncio
async def test_parse_zealot_drop_natural(library, session, default_ctx) -> None:
    """模拟 LLM 输出 → DropActPayload."""
    raw = {
        "interpretation_zh": "4 个叉子棱镜空投对面二矿(矿区)",
        "confidence": 0.95,
        "directives": [
            {
                "type": "drop_act",
                "payload": {
                    "style": "simple",
                    "cargo_unit": "Zealot",
                    "cargo_count": 4,
                    "transport": "WarpPrism",
                    "drop_target": "enemy_natural:mineral",
                    "after_unload": "attack_workers",
                },
            }
        ],
    }
    parser = IntentParser(_scripted_provider(raw), library, session=session)
    outcome = await parser.parse("4 叉子棱镜空投对面二矿", default_ctx)
    assert isinstance(outcome, IntentParseResult)
    assert len(outcome.directives) == 1
    payload = outcome.directives[0].payload
    assert isinstance(payload, DropActPayload)
    assert payload.cargo_unit == "Zealot"
    assert payload.cargo_count == 4
    assert payload.drop_target == "enemy_natural:mineral"


@pytest.mark.asyncio
async def test_parse_dt_warp_drop_production(library, session, default_ctx) -> None:
    raw = {
        "interpretation_zh": "棱镜带 4 DT 前线 warp + 二段空投主基地产能",
        "confidence": 0.92,
        "directives": [
            {
                "type": "drop_act",
                "payload": {
                    "style": "warp_then_drop",
                    "cargo_unit": "DarkTemplar",
                    "cargo_count": 4,
                    "transport": "WarpPrism",
                    "warp_at": "enemy_main:ramp_outside",
                    "drop_target": "enemy_main:production",
                },
            }
        ],
    }
    parser = IntentParser(_scripted_provider(raw), library, session=session)
    outcome = await parser.parse("棱镜前线 warp 4 DT 再空投主基地", default_ctx)
    assert isinstance(outcome, IntentParseResult)
    payload = outcome.directives[0].payload
    assert isinstance(payload, DropActPayload)
    assert payload.style == "warp_then_drop"
    assert payload.warp_at == "enemy_main:ramp_outside"


@pytest.mark.asyncio
async def test_strategy_set_still_works(library, session, default_ctx) -> None:
    """回归:strategy_set / production_override 不受新 drop_act 影响。"""
    raw = {
        "interpretation_zh": "切 4bg 开局",
        "confidence": 0.95,
        "directives": [
            {
                "type": "strategy_set",
                "payload": {"stage": "opening", "strategy_id": "4bg"},
            }
        ],
    }
    parser = IntentParser(_scripted_provider(raw), library, session=session)
    outcome = await parser.parse("切 4bg", default_ctx)
    assert isinstance(outcome, IntentParseResult)
    assert outcome.directives[0].type.value == "strategy_set"
