"""DeepSeek / LLM provider 真实 API 冒烟测试（不需要 SC2）。

只需要网络 + API key 环境变量（按 config/llm.yaml 的 provider 决定：
deepseek → DEEPSEEK_API_KEY）。发一句真实玩家话语，验证 provider 能正常返回
tool_use 结构化输出 —— 用来验 ADR 0005 里 DeepSeek 兼容端点的 tool_choice /
cache_control 兼容性。

用法（PowerShell）::

    $env:DEEPSEEK_API_KEY = "sk-..."
    uv run --no-sync python scripts/llm_smoke.py
    uv run --no-sync python scripts/llm_smoke.py "切IAC双矿"
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from vibecraft.directives.types import StageKind
from vibecraft.llm.config import LLMConfig
from vibecraft.llm.parser import IntentParser, ParserConfig
from vibecraft.llm.prompt import ParseContext
from vibecraft.llm.schema import AmbiguousParse, IntentParseResult, ParseError
from vibecraft.strategy.library import StrategyLibrary

_ROOT = Path(__file__).resolve().parents[1]


async def main() -> int:
    user_text = sys.argv[1] if len(sys.argv) > 1 else "切1门Robo"

    cfg = LLMConfig.from_yaml(_ROOT / "config" / "llm.yaml")
    print(f"provider={cfg.provider}  model={cfg.model}")
    print(f"base_url={cfg.base_url}")
    print(f"use_prompt_cache={cfg.use_prompt_cache}  timeout_s={cfg.timeout_s}")

    provider = cfg.build_provider()
    library = StrategyLibrary.from_directories(
        _ROOT / "strategies", _ROOT / "docs" / "aliases" / "protoss.yaml"
    )
    print(f"剧本库 id: {sorted(library.all_ids())}")

    parser = IntentParser(
        provider,
        library,
        config=ParserConfig(
            timeout_s=cfg.timeout_s,
            confidence_threshold=cfg.confidence_threshold,
            max_directives_per_call=cfg.max_directives_per_call,
        ),
    )
    ctx = ParseContext(game_time=120.0, current_stage=StageKind.OPENING)

    print(f"\n>>> 玩家话语: {user_text!r}")
    outcome = await parser.parse(user_text, ctx)
    print(f"<<< 结果类型: {type(outcome).__name__}\n")

    if isinstance(outcome, IntentParseResult):
        print(f"  解释: {outcome.interpretation_zh}")
        print(f"  置信度: {outcome.confidence}")
        for d in outcome.directives:
            print(f"  directive: {d.type.value}  payload={d.payload!r}")
        print("\n[PASS] DeepSeek 返回了合法结构化输出（tool_use 兼容）")
        return 0
    if isinstance(outcome, AmbiguousParse):
        print(f"  解释: {outcome.result.interpretation_zh}")
        print(f"  置信度: {outcome.result.confidence}（低于阈值，判为模糊）")
        print("\n[PASS] DeepSeek 返回了合法结构化输出（tool_use 兼容），置信度偏低")
        return 0
    if isinstance(outcome, ParseError):
        print(f"  kind: {outcome.kind.value}")
        print(f"  message: {outcome.message}")
        print("\n[FAIL] parse 失败 —— 看 message 判断是 tool_use 不兼容还是别的")
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
