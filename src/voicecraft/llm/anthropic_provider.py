"""AnthropicProvider —— 真正的 Claude API 调用。

仅在 production 路径用，单测全部 mock。本文件 import anthropic SDK，
但 LLMProvider Protocol 已在 provider.py 里独立，所以 import 失败不影响
其他模块。

模型默认 `claude-sonnet-4-6`（设计文档 §7.4），可通过 ParserConfig 切换。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from voicecraft.llm.errors import LLMError
from voicecraft.llm.provider import ProviderResponse

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic
    from anthropic.types import Message


class AnthropicProvider:
    """同步接口：`async def parse(...) -> ProviderResponse`。"""

    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 1024,
        use_prompt_cache: bool = True,
    ) -> None:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as e:
            raise LLMError("未安装 anthropic SDK：`uv add anthropic`") from e

        self.client: AsyncAnthropic = AsyncAnthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.use_prompt_cache = use_prompt_cache

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
        # System prompt 走 cache_control（4 段拼前 3 段缓存，第 4 段每次新）
        system_blocks: list[dict[str, Any]] = [
            {"type": "text", "text": system},
        ]
        if self.use_prompt_cache:
            system_blocks[0]["cache_control"] = {"type": "ephemeral"}

        few_shot_block: dict[str, Any] = {"type": "text", "text": few_shot}
        if self.use_prompt_cache:
            few_shot_block["cache_control"] = {"type": "ephemeral"}

        messages = [
            {
                "role": "user",
                "content": [
                    few_shot_block,
                    {"type": "text", "text": dynamic_context},
                    {"type": "text", "text": f"\n玩家话语：{user_text}\n"},
                ],
            }
        ]

        t0 = time.monotonic()
        # Anthropic SDK 用 TypedDict 收 system/messages/tools，dict 字面值
        # 在结构上等价，但 mypy 重载匹配通不过。这里集中标 ignore。
        msg: Message = await self.client.messages.create(  # type: ignore[call-overload]
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_blocks,
            messages=messages,
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": tool_schema["name"]},
            timeout=timeout_s,
        )
        latency_ms = (time.monotonic() - t0) * 1000

        # 解析 tool_use block
        raw_obj: dict[str, Any] | None = None
        raw_text: str = ""
        for block in msg.content:
            btype = getattr(block, "type", None)
            if btype == "tool_use":
                tool_input = getattr(block, "input", None)
                if isinstance(tool_input, dict):
                    raw_obj = tool_input
                    raw_text = json.dumps(tool_input, ensure_ascii=False)
                break
            if btype == "text":
                raw_text += getattr(block, "text", "")

        if raw_obj is None:
            raise LLMError(
                f"Claude 未走 tool_use 输出（content blocks: "
                f"{[getattr(b, 'type', '?') for b in msg.content]})"
            )

        usage = msg.usage
        return ProviderResponse(
            raw=raw_obj,
            raw_text=raw_text,
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cache_hit=(getattr(usage, "cache_read_input_tokens", 0) or 0) > 0,
            latency_ms=latency_ms,
            model=self.model,
            provider="anthropic",
            extra={
                "stop_reason": msg.stop_reason,
            },
        )
