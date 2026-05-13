"""LLM Intent Parser：玩家话语 → directives JSON 数组。

对应设计文档 §7。本模块只做"语言层"，不下任何 SC2 API 调用，也不评估
剧本能不能赢——失败就报错，**绝不"近似猜测"半懂半不懂的指令**。

公共入口：`IntentParser`。Provider 通过 `LLMProvider` Protocol 注入，
当前实现 `AnthropicProvider`，未来可加 OpenAI / DeepSeek。
"""

from __future__ import annotations

from voicecraft.llm.errors import LLMError
from voicecraft.llm.parser import IntentParser, ParserConfig
from voicecraft.llm.prompt import (
    ParseContext,
    build_dynamic_context,
    build_few_shot,
    build_strategy_catalog,
    build_system_prompt,
    build_tool_schema,
)
from voicecraft.llm.provider import (
    LLMProvider,
    MockLLMProvider,
    ProviderResponse,
)
from voicecraft.llm.schema import (
    AmbiguousParse,
    IntentParseResult,
    ParseError,
    ParseErrorKind,
    ParseOutcome,
)

__all__ = [
    "AmbiguousParse",
    "IntentParseResult",
    "IntentParser",
    "LLMError",
    "LLMProvider",
    "MockLLMProvider",
    "ParseContext",
    "ParseError",
    "ParseErrorKind",
    "ParseOutcome",
    "ParserConfig",
    "ProviderResponse",
    "build_dynamic_context",
    "build_few_shot",
    "build_strategy_catalog",
    "build_system_prompt",
    "build_tool_schema",
]
