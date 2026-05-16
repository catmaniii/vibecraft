"""LLM Provider 抽象（Protocol）+ MockLLMProvider（单测用）+ AnthropicProvider 占位。

设计文档 §7.4。Provider 屏蔽 Anthropic / OpenAI / DeepSeek 差异。

实际 AnthropicProvider 实现放 `anthropic_provider.py`（独立文件，因为依赖
anthropic SDK，单测时不需要 import）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ProviderResponse:
    """LLM 调用一次的完整返回。

    `raw` 是 provider 返回的结构化输出（已 JSON parse），但**未**经 IntentParseResult schema 验证。
    """

    raw: dict[str, Any]
    raw_text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit: bool = False
    latency_ms: float = 0.0
    model: str = ""
    provider: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    """LLM client 抽象。

    实现需保证：
    - parse 抛 asyncio.TimeoutError / 自定义异常都由 IntentParser 接住
    - 不在 provider 层做 schema 验证（留给 IntentParser）
    """

    name: str
    model: str

    async def parse(
        self,
        *,
        system: str,
        few_shot: str,
        dynamic_context: str,
        user_text: str,
        tool_schema: dict[str, Any],
        timeout_s: float,
    ) -> ProviderResponse: ...


# =========================================================================
# Mock provider（单测专用）
# =========================================================================


class MockLLMProvider:
    """可编程的 mock：把脚本好的 (input → response) 返回。

    用法::

        provider = MockLLMProvider(
            handler=lambda **kwargs: ProviderResponse(raw={"directives": [...]}, ...)
        )

    或一次性给 list[ProviderResponse]，按调用顺序消费::

        provider = MockLLMProvider(scripted=[resp1, resp2, ...])
    """

    name = "mock"

    def __init__(
        self,
        model: str = "mock-model",
        scripted: list[ProviderResponse] | None = None,
        handler: Callable[..., Awaitable[ProviderResponse] | ProviderResponse] | None = None,
    ) -> None:
        self.model = model
        self._scripted = list(scripted or [])
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    async def parse(
        self,
        *,
        system: str,
        few_shot: str,
        dynamic_context: str,
        user_text: str,
        tool_schema: dict[str, Any],
        timeout_s: float,
    ) -> ProviderResponse:
        self.calls.append(
            {
                "system": system,
                "few_shot": few_shot,
                "dynamic_context": dynamic_context,
                "user_text": user_text,
                "tool_schema": tool_schema,
                "timeout_s": timeout_s,
            }
        )
        if self._handler is not None:
            out = self._handler(
                system=system,
                few_shot=few_shot,
                dynamic_context=dynamic_context,
                user_text=user_text,
                tool_schema=tool_schema,
                timeout_s=timeout_s,
            )
            if hasattr(out, "__await__"):
                return await out
            assert isinstance(out, ProviderResponse)
            return out
        if not self._scripted:
            raise RuntimeError("MockLLMProvider 用完所有 scripted 响应")
        return self._scripted.pop(0)
