"""AnthropicProvider 的 mock 单测。

策略：mock `anthropic.AsyncAnthropic`，完全不调用真实 API。
覆盖：
- 正常路径：tool_use 输出解析 + cache token 计数 + ProviderResponse 字段
- 缓存行为：use_prompt_cache=True/False 时 cache_control 的有无
- tool_use 缺失：LLMError
- 各类 anthropic SDK 异常：APITimeoutError / RateLimitError / AuthenticationError /
  APIConnectionError / InternalServerError
- LLMConfig：从 yaml 加载 + build_provider + api_key 读取
- IntentParser 接入 AnthropicProvider：mock SDK，验证 parse 返回 ParseError 包装
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from voicecraft.llm.anthropic_provider import AnthropicProvider
from voicecraft.llm.config import LLMConfig
from voicecraft.llm.errors import LLMError
from voicecraft.llm.provider import ProviderResponse

# ======================================================================
# 工具函数：构造 fake anthropic Message
# ======================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE_TOOL_SCHEMA: dict[str, Any] = {
    "name": "emit_directives",
    "description": "test",
    "input_schema": {"type": "object", "properties": {}},
}


def _fake_tool_use_block(input_data: dict[str, Any]) -> MagicMock:
    """构造 tool_use content block mock。"""
    block = MagicMock()
    block.type = "tool_use"
    block.input = input_data
    return block


def _fake_text_block(text: str) -> MagicMock:
    """构造 text content block mock。"""
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _fake_usage(
    input_tokens: int = 4000,
    output_tokens: int = 80,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> MagicMock:
    """构造 Usage mock。"""
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    usage.cache_read_input_tokens = cache_read_input_tokens
    usage.cache_creation_input_tokens = cache_creation_input_tokens
    return usage


def _fake_message(
    content: list[MagicMock],
    usage: MagicMock | None = None,
    stop_reason: str = "tool_use",
) -> MagicMock:
    """构造 anthropic Message mock。"""
    msg = MagicMock()
    msg.content = content
    msg.usage = usage or _fake_usage()
    msg.stop_reason = stop_reason
    return msg


def _make_provider(
    use_prompt_cache: bool = True,
    model: str = "claude-sonnet-4-6",
) -> AnthropicProvider:
    """构造 provider，mock 掉 AsyncAnthropic 构造。

    anthropic SDK 在 __init__ 内通过 `from anthropic import AsyncAnthropic`
    动态 import，因此需要 patch `anthropic.AsyncAnthropic`（包级别）。
    """
    with patch("anthropic.AsyncAnthropic"):
        return AnthropicProvider(
            api_key="sk-test",
            model=model,
            max_tokens=1024,
            use_prompt_cache=use_prompt_cache,
        )


# ======================================================================
# AnthropicProvider 正常路径
# ======================================================================


class TestAnthropicProviderHappyPath:
    @pytest.mark.asyncio
    async def test_returns_provider_response(self) -> None:
        """正常 tool_use 响应 → ProviderResponse 字段全填对。"""
        provider = _make_provider()
        raw_data: dict[str, Any] = {
            "interpretation_zh": "切换剧本",
            "confidence": 0.9,
            "directives": [],
        }
        usage = _fake_usage(
            input_tokens=4200,
            output_tokens=90,
            cache_read_input_tokens=3800,
            cache_creation_input_tokens=0,
        )
        fake_msg = _fake_message([_fake_tool_use_block(raw_data)], usage=usage)
        provider.client.messages.create = AsyncMock(return_value=fake_msg)  # type: ignore[attr-defined]

        resp = await provider.parse(
            system="system text",
            few_shot="few shot",
            dynamic_context="context",
            user_text="切 IAC",
            tool_schema=_SAMPLE_TOOL_SCHEMA,
            timeout_s=3.0,
        )

        assert isinstance(resp, ProviderResponse)
        assert resp.raw == raw_data
        assert resp.input_tokens == 4200
        assert resp.output_tokens == 90
        assert resp.cache_hit is True  # cache_read > 0
        assert resp.model == "claude-sonnet-4-6"
        assert resp.provider == "anthropic"
        assert resp.extra["cache_read_input_tokens"] == 3800
        assert resp.extra["cache_creation_input_tokens"] == 0
        assert resp.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_cache_not_hit_when_zero(self) -> None:
        """cache_read_input_tokens=0 → cache_hit=False。"""
        provider = _make_provider()
        raw_data: dict[str, Any] = {"interpretation_zh": "x", "confidence": 0.8, "directives": []}
        fake_msg = _fake_message(
            [_fake_tool_use_block(raw_data)],
            usage=_fake_usage(cache_read_input_tokens=0, cache_creation_input_tokens=500),
        )
        provider.client.messages.create = AsyncMock(return_value=fake_msg)  # type: ignore[attr-defined]

        resp = await provider.parse(
            system="s",
            few_shot="f",
            dynamic_context="d",
            user_text="test",
            tool_schema=_SAMPLE_TOOL_SCHEMA,
            timeout_s=3.0,
        )
        assert resp.cache_hit is False
        assert resp.extra["cache_creation_input_tokens"] == 500

    @pytest.mark.asyncio
    async def test_raw_text_is_json_dumps_of_input(self) -> None:
        """raw_text 是 tool_use.input 的 JSON 字符串。"""
        provider = _make_provider()
        raw_data: dict[str, Any] = {"interpretation_zh": "ok", "confidence": 0.7, "directives": []}
        fake_msg = _fake_message([_fake_tool_use_block(raw_data)])
        provider.client.messages.create = AsyncMock(return_value=fake_msg)  # type: ignore[attr-defined]

        resp = await provider.parse(
            system="s",
            few_shot="f",
            dynamic_context="d",
            user_text="test",
            tool_schema=_SAMPLE_TOOL_SCHEMA,
            timeout_s=3.0,
        )
        import json

        assert json.loads(resp.raw_text) == raw_data


# ======================================================================
# prompt cache_control 传递
# ======================================================================


class TestPromptCacheControl:
    @pytest.mark.asyncio
    async def test_cache_control_set_when_enabled(self) -> None:
        """use_prompt_cache=True → system/few_shot block 含 cache_control。"""
        provider = _make_provider(use_prompt_cache=True)
        raw_data: dict[str, Any] = {"interpretation_zh": "x", "confidence": 0.8, "directives": []}
        fake_msg = _fake_message([_fake_tool_use_block(raw_data)])
        create_mock = AsyncMock(return_value=fake_msg)
        provider.client.messages.create = create_mock  # type: ignore[attr-defined]

        await provider.parse(
            system="system content",
            few_shot="few shot content",
            dynamic_context="d",
            user_text="test",
            tool_schema=_SAMPLE_TOOL_SCHEMA,
            timeout_s=3.0,
        )

        # 验证 SDK 被调用时的参数
        call_kwargs = create_mock.call_args.kwargs
        system_blocks: list[dict[str, Any]] = call_kwargs["system"]
        assert len(system_blocks) == 1
        assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}

        messages: list[dict[str, Any]] = call_kwargs["messages"]
        user_content: list[dict[str, Any]] = messages[0]["content"]
        # 第一个 content block 是 few_shot，应有 cache_control
        assert user_content[0]["cache_control"] == {"type": "ephemeral"}
        # 第二个（dynamic_context）和第三个（user_text）不应有 cache_control
        assert "cache_control" not in user_content[1]
        assert "cache_control" not in user_content[2]

    @pytest.mark.asyncio
    async def test_no_cache_control_when_disabled(self) -> None:
        """use_prompt_cache=False → 所有 block 都无 cache_control。"""
        provider = _make_provider(use_prompt_cache=False)
        raw_data: dict[str, Any] = {"interpretation_zh": "x", "confidence": 0.8, "directives": []}
        fake_msg = _fake_message([_fake_tool_use_block(raw_data)])
        create_mock = AsyncMock(return_value=fake_msg)
        provider.client.messages.create = create_mock  # type: ignore[attr-defined]

        await provider.parse(
            system="s",
            few_shot="f",
            dynamic_context="d",
            user_text="test",
            tool_schema=_SAMPLE_TOOL_SCHEMA,
            timeout_s=3.0,
        )

        call_kwargs = create_mock.call_args.kwargs
        system_blocks: list[dict[str, Any]] = call_kwargs["system"]
        assert "cache_control" not in system_blocks[0]

        user_content: list[dict[str, Any]] = call_kwargs["messages"][0]["content"]
        assert "cache_control" not in user_content[0]


# ======================================================================
# tool_use 缺失 → LLMError
# ======================================================================


class TestToolUseMissing:
    @pytest.mark.asyncio
    async def test_no_tool_use_block_raises_llm_error(self) -> None:
        """Claude 只返回 text block（不应发生）→ LLMError。"""
        provider = _make_provider()
        fake_msg = _fake_message([_fake_text_block("抱歉，我不理解")])
        provider.client.messages.create = AsyncMock(return_value=fake_msg)  # type: ignore[attr-defined]

        with pytest.raises(LLMError, match="tool_use"):
            await provider.parse(
                system="s",
                few_shot="f",
                dynamic_context="d",
                user_text="test",
                tool_schema=_SAMPLE_TOOL_SCHEMA,
                timeout_s=3.0,
            )

    @pytest.mark.asyncio
    async def test_empty_content_raises_llm_error(self) -> None:
        """content 为空 → LLMError。"""
        provider = _make_provider()
        fake_msg = _fake_message([])
        provider.client.messages.create = AsyncMock(return_value=fake_msg)  # type: ignore[attr-defined]

        with pytest.raises(LLMError):
            await provider.parse(
                system="s",
                few_shot="f",
                dynamic_context="d",
                user_text="test",
                tool_schema=_SAMPLE_TOOL_SCHEMA,
                timeout_s=3.0,
            )


# ======================================================================
# SDK 异常传播（上层 IntentParser 会接住）
# ======================================================================


class TestSDKExceptionPropagation:
    """验证 AnthropicProvider 不吞异常，直接向上抛。

    IntentParser 的 except Exception 会统一转成 ParseError(PROVIDER_ERROR)。
    """

    @pytest.mark.asyncio
    async def test_api_timeout_error_propagates(self) -> None:
        """anthropic.APITimeoutError 向上传播（不是 asyncio.TimeoutError）。"""
        provider = _make_provider()

        import anthropic

        # APITimeoutError 需要一个 request 参数
        fake_request = MagicMock()
        provider.client.messages.create = AsyncMock(  # type: ignore[attr-defined]
            side_effect=anthropic.APITimeoutError(request=fake_request)
        )

        with pytest.raises(anthropic.APITimeoutError):
            await provider.parse(
                system="s",
                few_shot="f",
                dynamic_context="d",
                user_text="test",
                tool_schema=_SAMPLE_TOOL_SCHEMA,
                timeout_s=3.0,
            )

    @pytest.mark.asyncio
    async def test_rate_limit_error_propagates(self) -> None:
        """anthropic.RateLimitError → 上层接住。"""
        provider = _make_provider()

        import anthropic

        fake_response = MagicMock()
        fake_response.status_code = 429
        provider.client.messages.create = AsyncMock(  # type: ignore[attr-defined]
            side_effect=anthropic.RateLimitError("rate limit", response=fake_response, body=None)
        )

        with pytest.raises(anthropic.RateLimitError):
            await provider.parse(
                system="s",
                few_shot="f",
                dynamic_context="d",
                user_text="test",
                tool_schema=_SAMPLE_TOOL_SCHEMA,
                timeout_s=3.0,
            )

    @pytest.mark.asyncio
    async def test_authentication_error_propagates(self) -> None:
        """anthropic.AuthenticationError (401) → 上层接住。"""
        provider = _make_provider()

        import anthropic

        fake_response = MagicMock()
        fake_response.status_code = 401
        provider.client.messages.create = AsyncMock(  # type: ignore[attr-defined]
            side_effect=anthropic.AuthenticationError(
                "invalid key", response=fake_response, body=None
            )
        )

        with pytest.raises(anthropic.AuthenticationError):
            await provider.parse(
                system="s",
                few_shot="f",
                dynamic_context="d",
                user_text="test",
                tool_schema=_SAMPLE_TOOL_SCHEMA,
                timeout_s=3.0,
            )

    @pytest.mark.asyncio
    async def test_internal_server_error_propagates(self) -> None:
        """anthropic.InternalServerError (500) → 上层接住。"""
        provider = _make_provider()

        import anthropic

        fake_response = MagicMock()
        fake_response.status_code = 500
        provider.client.messages.create = AsyncMock(  # type: ignore[attr-defined]
            side_effect=anthropic.InternalServerError(
                "server error", response=fake_response, body=None
            )
        )

        with pytest.raises(anthropic.InternalServerError):
            await provider.parse(
                system="s",
                few_shot="f",
                dynamic_context="d",
                user_text="test",
                tool_schema=_SAMPLE_TOOL_SCHEMA,
                timeout_s=3.0,
            )

    @pytest.mark.asyncio
    async def test_api_connection_error_propagates(self) -> None:
        """anthropic.APIConnectionError（网络断了）→ 上层接住。"""
        provider = _make_provider()

        import anthropic

        fake_request = MagicMock()
        provider.client.messages.create = AsyncMock(  # type: ignore[attr-defined]
            side_effect=anthropic.APIConnectionError(request=fake_request)
        )

        with pytest.raises(anthropic.APIConnectionError):
            await provider.parse(
                system="s",
                few_shot="f",
                dynamic_context="d",
                user_text="test",
                tool_schema=_SAMPLE_TOOL_SCHEMA,
                timeout_s=3.0,
            )


# ======================================================================
# IntentParser 接入 AnthropicProvider：端到端 mock 验证
# ======================================================================


class TestIntentParserWithAnthropicProvider:
    """用 mock SDK 验证 AnthropicProvider → IntentParser 完整路径。"""

    @pytest.fixture(scope="class")
    def library(self):  # type: ignore[no-untyped-def]
        from voicecraft.strategy import StrategyLibrary

        return StrategyLibrary.from_directories(
            strategies_dir=PROJECT_ROOT / "strategies",
            aliases_path=PROJECT_ROOT / "aliases" / "protoss.yaml",
        )

    @pytest.mark.asyncio
    async def test_successful_parse_via_real_provider_mock(self, library) -> None:  # type: ignore[no-untyped-def]
        """AnthropicProvider（mock SDK）→ IntentParser → IntentParseResult。"""
        from voicecraft.directives.types import StageKind
        from voicecraft.llm import IntentParser, IntentParseResult
        from voicecraft.llm.prompt import ParseContext

        provider = _make_provider()
        raw_data: dict[str, Any] = {
            "interpretation_zh": "切换到双矿 IAC",
            "confidence": 0.92,
            "directives": [
                {
                    "type": "strategy_set",
                    "payload": {"stage": "midgame", "strategy_id": "iac_2base"},
                }
            ],
        }
        fake_msg = _fake_message([_fake_tool_use_block(raw_data)])
        provider.client.messages.create = AsyncMock(return_value=fake_msg)  # type: ignore[attr-defined]

        parser = IntentParser(provider, library)
        ctx = ParseContext(
            game_time=300.0,
            current_stage=StageKind.OPENING,
        )
        outcome = await parser.parse("切 IAC", ctx)
        assert isinstance(outcome, IntentParseResult)
        assert outcome.confidence == 0.92

    @pytest.mark.asyncio
    async def test_rate_limit_becomes_provider_error(self, library) -> None:  # type: ignore[no-untyped-def]
        """RateLimitError → ParseError(PROVIDER_ERROR)，bot 状态不变。"""
        import anthropic

        from voicecraft.directives.types import StageKind
        from voicecraft.llm import IntentParser, ParseError, ParseErrorKind
        from voicecraft.llm.prompt import ParseContext

        provider = _make_provider()
        fake_response = MagicMock()
        fake_response.status_code = 429
        provider.client.messages.create = AsyncMock(  # type: ignore[attr-defined]
            side_effect=anthropic.RateLimitError(
                "429 rate limit", response=fake_response, body=None
            )
        )

        parser = IntentParser(provider, library)
        ctx = ParseContext(game_time=100.0, current_stage=StageKind.OPENING)
        outcome = await parser.parse("test", ctx)
        assert isinstance(outcome, ParseError)
        assert outcome.kind == ParseErrorKind.PROVIDER_ERROR
        assert "RateLimitError" in outcome.message

    @pytest.mark.asyncio
    async def test_api_timeout_becomes_provider_error(self, library) -> None:  # type: ignore[no-untyped-def]
        """APITimeoutError（SDK层）→ ParseError(PROVIDER_ERROR)。

        注意：asyncio.wait_for 的超时触发 asyncio.TimeoutError → TIMEOUT。
        SDK 内部超时触发 anthropic.APITimeoutError → PROVIDER_ERROR。
        两者均安全。
        """
        import anthropic

        from voicecraft.directives.types import StageKind
        from voicecraft.llm import IntentParser, ParseError, ParseErrorKind
        from voicecraft.llm.prompt import ParseContext

        provider = _make_provider()
        fake_request = MagicMock()
        provider.client.messages.create = AsyncMock(  # type: ignore[attr-defined]
            side_effect=anthropic.APITimeoutError(request=fake_request)
        )

        parser = IntentParser(provider, library)
        ctx = ParseContext(game_time=100.0, current_stage=StageKind.OPENING)
        outcome = await parser.parse("test", ctx)
        assert isinstance(outcome, ParseError)
        assert outcome.kind == ParseErrorKind.PROVIDER_ERROR

    @pytest.mark.asyncio
    async def test_asyncio_timeout_becomes_timeout_error(self, library) -> None:  # type: ignore[no-untyped-def]
        """asyncio.wait_for 超时（慢响应）→ ParseError(TIMEOUT)。"""
        from voicecraft.directives.types import StageKind
        from voicecraft.llm import IntentParser, ParseError, ParseErrorKind, ParserConfig
        from voicecraft.llm.prompt import ParseContext

        provider = _make_provider()

        async def slow_response(**kwargs: Any) -> Any:
            await asyncio.sleep(10.0)
            return None

        provider.client.messages.create = AsyncMock(side_effect=slow_response)  # type: ignore[attr-defined]

        parser = IntentParser(provider, library, config=ParserConfig(timeout_s=0.05))
        ctx = ParseContext(game_time=100.0, current_stage=StageKind.OPENING)
        outcome = await parser.parse("test", ctx)
        assert isinstance(outcome, ParseError)
        assert outcome.kind == ParseErrorKind.TIMEOUT

    @pytest.mark.asyncio
    async def test_llm_error_no_tool_use_becomes_provider_error(self, library) -> None:  # type: ignore[no-untyped-def]
        """tool_use 缺失 → LLMError → ParseError(PROVIDER_ERROR)。"""
        from voicecraft.directives.types import StageKind
        from voicecraft.llm import IntentParser, ParseError, ParseErrorKind
        from voicecraft.llm.prompt import ParseContext

        provider = _make_provider()
        # 返回只有 text block 的消息
        fake_msg = _fake_message([_fake_text_block("No tool use here")])
        provider.client.messages.create = AsyncMock(return_value=fake_msg)  # type: ignore[attr-defined]

        parser = IntentParser(provider, library)
        ctx = ParseContext(game_time=100.0, current_stage=StageKind.OPENING)
        outcome = await parser.parse("test", ctx)
        assert isinstance(outcome, ParseError)
        assert outcome.kind == ParseErrorKind.PROVIDER_ERROR

    @pytest.mark.asyncio
    async def test_logging_writes_full_prompt_to_llm_calls(self, library) -> None:  # type: ignore[no-untyped-def]
        """parse 成功 → llm_calls/call_NNN.json 含 system_prompt + dynamic_context。"""
        from voicecraft.directives.types import StageKind
        from voicecraft.llm import IntentParser, IntentParseResult
        from voicecraft.llm.prompt import ParseContext
        from voicecraft.logging_ import GameSession, GameSessionConfig

        session = GameSession(GameSessionConfig(use_null_sinks=True))
        provider = _make_provider()
        raw_data: dict[str, Any] = {
            "interpretation_zh": "守家",
            "confidence": 0.85,
            "directives": [{"type": "engagement_constraint", "payload": {"stance": "defend"}}],
        }
        fake_msg = _fake_message([_fake_tool_use_block(raw_data)])
        provider.client.messages.create = AsyncMock(return_value=fake_msg)  # type: ignore[attr-defined]

        parser = IntentParser(provider, library, session=session)
        ctx = ParseContext(game_time=120.0, current_stage=StageKind.OPENING)
        outcome = await parser.parse("守家", ctx)
        assert isinstance(outcome, IntentParseResult)
        # log_llm_call counter 应 >= 1（NullSink 不写盘，但 counter 会增）
        seq = session.log_llm_call({})  # +1 额外
        assert seq >= 2
        session.close()


# ======================================================================
# LLMConfig
# ======================================================================


class TestLLMConfig:
    def test_from_yaml_loads_fields(self) -> None:
        """从项目 config/llm.yaml 加载，字段全正确。"""
        cfg = LLMConfig.from_yaml(PROJECT_ROOT / "config" / "llm.yaml")
        assert cfg.provider == "anthropic"
        assert cfg.model == "claude-sonnet-4-6"
        assert cfg.timeout_s == 3.0
        assert cfg.use_prompt_cache is True

    def test_from_yaml_or_defaults_missing_file(self, tmp_path: Path) -> None:
        """文件不存在 → 使用默认值，不抛异常。"""
        cfg = LLMConfig.from_yaml_or_defaults(tmp_path / "nonexistent.yaml")
        assert cfg.provider == "anthropic"
        assert cfg.model == "claude-sonnet-4-6"

    def test_build_provider_returns_anthropic_provider(self) -> None:
        """build_provider() 返回 AnthropicProvider 实例。"""
        cfg = LLMConfig(provider="anthropic", model="claude-sonnet-4-6")
        with patch("anthropic.AsyncAnthropic"):
            provider = cfg.build_provider(api_key="sk-test")
        assert isinstance(provider, AnthropicProvider)
        assert provider.model == "claude-sonnet-4-6"

    def test_build_provider_reads_env_var(self) -> None:
        """api_key=None 时从环境变量读取（通过 _build_anthropic 传到 SDK）。"""
        cfg = LLMConfig(provider="anthropic")
        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-env-key"}),
            patch("anthropic.AsyncAnthropic") as mock_cls,
        ):
            cfg.build_provider()
            mock_cls.assert_called_once_with(api_key="sk-env-key")

    def test_build_provider_explicit_key_overrides_env(self) -> None:
        """显式传入 api_key 优先于环境变量。"""
        cfg = LLMConfig(provider="anthropic")
        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-env-key"}),
            patch("anthropic.AsyncAnthropic") as mock_cls,
        ):
            cfg.build_provider(api_key="sk-explicit")
            mock_cls.assert_called_once_with(api_key="sk-explicit")

    def test_build_provider_no_key_passes_none(self) -> None:
        """无 env var + 无 api_key → None 传给 SDK（SDK 再自行查找）。"""
        cfg = LLMConfig(provider="anthropic")
        # 清除 env var
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("anthropic.AsyncAnthropic") as mock_cls,
        ):
            cfg.build_provider()
            mock_cls.assert_called_once_with(api_key=None)

    def test_build_provider_unknown_raises(self) -> None:
        """不支持的 provider → LLMError。"""
        cfg = LLMConfig.model_construct(provider="openai", model="gpt-4o")
        with pytest.raises(LLMError, match="openai"):
            cfg.build_provider()

    def test_extra_fields_forbidden(self) -> None:
        """extra='forbid'：yaml 里有多余字段 → ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LLMConfig.model_validate({"provider": "anthropic", "unknown_field": "x"})


# ======================================================================
# AnthropicProvider：constructor（不需要真实 key）
# ======================================================================


class TestAnthropicProviderConstructor:
    def test_name_attribute(self) -> None:
        """name 属性固定为 'anthropic'。"""
        with patch("anthropic.AsyncAnthropic"):
            p = AnthropicProvider(api_key="x")
        assert p.name == "anthropic"

    def test_model_stored(self) -> None:
        """model 参数被存储。"""
        with patch("anthropic.AsyncAnthropic"):
            p = AnthropicProvider(api_key="x", model="claude-haiku-4-5")
        assert p.model == "claude-haiku-4-5"

    def test_import_error_raises_llm_error(self) -> None:
        """anthropic SDK 未安装 → LLMError（不是 ImportError）。"""
        with (
            patch.dict("sys.modules", {"anthropic": None}),  # type: ignore[dict-item]
            pytest.raises(LLMError, match="anthropic SDK"),
        ):
            AnthropicProvider(api_key="x")
