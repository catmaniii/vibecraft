"""AnthropicProvider —— 通过 anthropic SDK 调用 Claude 或 Anthropic 兼容端点。

仅在 production 路径用，单测全部 mock（不调真实 API）。
本文件 import anthropic SDK，但 LLMProvider Protocol 已在 provider.py
独立定义，import 失败不影响其他模块。

模型默认 `claude-sonnet-4-6`（设计文档 §7.4），可通过 LLMConfig 切换。
`base_url` 指向 DeepSeek 的 Anthropic 兼容端点（`https://api.deepseek.com/anthropic`）
时，同一份代码即可调 DeepSeek V4（见 ADR 0005）。

错误处理策略（设计文档 §7.6）：
- `anthropic.APITimeoutError` / `asyncio.TimeoutError` → 由上层 `asyncio.wait_for` 或
  SDK 内部超时触发，统一被 `IntentParser` 包裹成 `ParseError(kind=TIMEOUT)`。
- `anthropic.RateLimitError` / 其他 `anthropic.APIError` → 抛出，
  由 `IntentParser` 的 `except Exception` 包裹成 `ParseError(kind=PROVIDER_ERROR)`。
- `LLMError`（tool_use 缺失）→ 同上，走 `PROVIDER_ERROR`。

**关键不变量**：AnthropicProvider 本身不吞掉任何异常。所有错误都向上抛，
由 IntentParser 统一转成 ParseError。
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
    """Anthropic Claude 异步 provider，实现 `LLMProvider` Protocol。

    构造方式：
    - 直接构造：``AnthropicProvider(api_key="sk-...")``
    - 从配置工厂：``LLMConfig.from_yaml(path).build_provider()``

    API key 优先级：
    1. 显式传入 ``api_key`` 参数
    2. SDK 自动读 ``ANTHROPIC_API_KEY`` 环境变量（不传 key 时）

    secret **不进 git** —— yaml / 代码里不要写 key 字面值。
    """

    name: str

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 1024,
        use_prompt_cache: bool = True,
        base_url: str | None = None,
        provider_name: str = "anthropic",
    ) -> None:
        """
        Args:
            api_key: API key；为 None 时 SDK 自己读环境变量。
            model: 模型 id。
            max_tokens: 单次调用最大输出 token 数。
            use_prompt_cache: 是否给静态段（system / few-shot）打 cache_control。
            base_url: API 端点；None 走 anthropic SDK 默认（官方 Anthropic）。
                指向 DeepSeek 的 Anthropic 兼容端点时传
                `https://api.deepseek.com/anthropic`。
            provider_name: provider 标识，写进 ProviderResponse.provider + 日志。
                走 DeepSeek 兼容端点时传 "deepseek"。
        """
        try:
            from anthropic import AsyncAnthropic
        except ImportError as e:
            raise LLMError("未安装 anthropic SDK：`uv add anthropic`") from e

        # base_url=None 时不传给 SDK，走官方默认端点；
        # api_key=None 时 AsyncAnthropic 自己读环境变量
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url is not None:
            client_kwargs["base_url"] = base_url
        self.client: AsyncAnthropic = AsyncAnthropic(**client_kwargs)
        self.model = model
        self.max_tokens = max_tokens
        self.use_prompt_cache = use_prompt_cache
        self.base_url = base_url
        self.name = provider_name

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
        """调用 Claude API，强制走 tool_use 输出（§7.2 / §7.3）。

        Prompt 缓存策略（§7.3）：
        - system 段（含 strategy catalog）：打 cache_control=ephemeral
        - few_shot 段：打 cache_control=ephemeral
        - dynamic_context + user_text：不缓存（每次新）

        三段静态内容总约 4-5K tokens；Anthropic 的 ephemeral 缓存有效期 5 分钟。
        实际游戏节奏下两次 parse 间隔 << 5 分钟，缓存命中率极高。

        Args:
            system: 第 1-2 段合并（system prompt + strategy catalog），静态缓存。
            few_shot: 第 3 段 few-shot 示例，静态缓存。
            dynamic_context: 第 4 段动态 context（游戏时间 / 资源 / 战况等）。
            user_text: 玩家本条话语。
            tool_schema: `emit_directives` tool 的 JSON Schema。
            timeout_s: 给 SDK 的 HTTP 超时（同时外层有 `asyncio.wait_for`）。

        Returns:
            `ProviderResponse`，`raw` 已经是 `dict`（tool_use.input）。

        Raises:
            LLMError: Claude 未返回 tool_use block（不应发生，保险起见）。
            anthropic.APITimeoutError: SDK 层超时（上层 `asyncio.wait_for` 也会触发 `asyncio.TimeoutError`）。
            anthropic.RateLimitError: 429 限频。
            anthropic.APIError: 其他 4xx/5xx。
        """
        # ---- 构造 system blocks（前 3 段拼成 system；few_shot 进 messages） ----
        system_block: dict[str, Any] = {"type": "text", "text": system}
        if self.use_prompt_cache:
            system_block["cache_control"] = {"type": "ephemeral"}

        few_shot_block: dict[str, Any] = {"type": "text", "text": few_shot}
        if self.use_prompt_cache:
            few_shot_block["cache_control"] = {"type": "ephemeral"}

        # messages：few_shot / dynamic_context / user_text 三块放 user turn
        messages: list[dict[str, Any]] = [
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

        # Anthropic SDK 用 TypedDict 约束 system/messages/tools，
        # 我们用 dict 字面值在结构上等价，但 mypy 重载匹配通不过。
        # 集中在这里标 ignore，避免 mypy 报错蔓延到其他地方。
        msg: Message = await self.client.messages.create(  # type: ignore[call-overload]
            model=self.model,
            max_tokens=self.max_tokens,
            system=[system_block],
            messages=messages,
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": tool_schema["name"]},
            timeout=timeout_s,
        )
        latency_ms = (time.monotonic() - t0) * 1000

        # ---- 解析 tool_use block（强制 tool_choice 所以正常只有这一种） ----
        raw_obj: dict[str, Any] | None = None
        raw_text = ""
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
                f"{[getattr(b, 'type', '?') for b in msg.content]}）"
            )

        # ---- 构造返回值，把 token / cache 信息填进 extra（供日志层使用） ----
        usage = msg.usage
        cache_read = getattr(usage, "cache_read_input_tokens", None) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", None) or 0

        return ProviderResponse(
            raw=raw_obj,
            raw_text=raw_text,
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cache_hit=cache_read > 0,
            latency_ms=latency_ms,
            model=self.model,
            provider=self.name,
            extra={
                "stop_reason": msg.stop_reason,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
            },
        )
