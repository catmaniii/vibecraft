"""IntentParser：编排 prompt 拼装、调 provider、schema 校验、错误处理。

设计文档 §7.6 关键：**任何异常 → bot 状态完全不变**。
所有失败都返回 `ParseError`，**不抛**。
"""

from __future__ import annotations

import asyncio
import difflib
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from voicecraft.directives.models import Directive
from voicecraft.directives.types import DirectiveType
from voicecraft.llm.prompt import (
    ParseContext,
    build_dynamic_context,
    build_few_shot,
    build_strategy_catalog,
    build_system_prompt,
    build_tool_schema,
)
from voicecraft.llm.provider import LLMProvider, ProviderResponse
from voicecraft.llm.schema import (
    AmbiguousParse,
    IntentParseResult,
    ParseError,
    ParseErrorKind,
    ParseOutcome,
)
from voicecraft.logging_.session import GameSession
from voicecraft.strategy.aliases import AliasTable
from voicecraft.strategy.library import StrategyLibrary


class ParserConfig(BaseModel):
    """运行时配置。"""

    model_config = ConfigDict(extra="forbid")

    timeout_s: float = 3.0
    max_retries: int = 0
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    max_directives_per_call: int = 10


class IntentParser:
    """玩家话语 → ParseOutcome 的编排器。"""

    def __init__(
        self,
        provider: LLMProvider,
        library: StrategyLibrary,
        aliases: AliasTable | None = None,
        config: ParserConfig | None = None,
        session: GameSession | None = None,
    ) -> None:
        self.provider = provider
        self.library = library
        self.aliases = aliases or library.aliases
        self.config = config or ParserConfig()
        self.session = session

        # 静态 prompt 段（cache 友好）
        self._system_prompt = build_system_prompt(self.aliases)
        self._strategy_catalog = build_strategy_catalog(self.library)
        self._few_shot = build_few_shot()
        self._tool_schema = build_tool_schema()

    # ------------------------------------------------------------------

    async def parse(self, user_text: str, context: ParseContext) -> ParseOutcome:
        dynamic = build_dynamic_context(context)
        system_full = self._system_prompt + "\n\n" + self._strategy_catalog

        t0 = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self.provider.parse(
                    system=system_full,
                    few_shot=self._few_shot,
                    dynamic_context=dynamic,
                    user_text=user_text,
                    tool_schema=self._tool_schema,
                    timeout_s=self.config.timeout_s,
                ),
                timeout=self.config.timeout_s,
            )
        except TimeoutError:
            err = ParseError(kind=ParseErrorKind.TIMEOUT, message="LLM 响应超时")
            self._log_call(user_text, context, None, err, latency_ms=(time.monotonic() - t0) * 1000)
            return err
        except Exception as e:
            err = ParseError(
                kind=ParseErrorKind.PROVIDER_ERROR,
                message=f"provider 异常：{type(e).__name__}: {e}",
            )
            self._log_call(user_text, context, None, err, latency_ms=(time.monotonic() - t0) * 1000)
            return err

        outcome = self._build_outcome(response, user_text)
        self._log_call(user_text, context, response, outcome, latency_ms=response.latency_ms)
        return outcome

    # ------------------------------------------------------------------

    def _build_outcome(self, response: ProviderResponse, user_text: str) -> ParseOutcome:
        raw = response.raw
        # 必须含 directives / interpretation_zh / confidence
        if "directives" not in raw or "interpretation_zh" not in raw or "confidence" not in raw:
            return ParseError(
                kind=ParseErrorKind.SCHEMA_MISMATCH,
                message=f"provider 响应缺少必需字段：{list(raw.keys())}",
            )

        directives_raw = raw.get("directives", [])
        if not isinstance(directives_raw, list):
            return ParseError(
                kind=ParseErrorKind.SCHEMA_MISMATCH,
                message="directives 字段不是数组",
            )
        if len(directives_raw) > self.config.max_directives_per_call:
            return ParseError(
                kind=ParseErrorKind.SCHEMA_MISMATCH,
                message=(
                    f"单条话语解析出 {len(directives_raw)} 条 directive，"
                    f"超出上限 {self.config.max_directives_per_call}"
                ),
            )

        # 将每条 raw directive 转成 Directive envelope。
        # LLM 输出的格式是 {"type":..., "payload":{...}, ...}，
        # 但 Directive 模型要求 payload 自带 type 字段（discriminated union），
        # 我们这里把 type copy 进 payload。
        directives: list[Directive] = []
        for i, d_raw in enumerate(directives_raw):
            try:
                envelope = self._normalize_directive_raw(d_raw, user_text)
                directives.append(Directive.model_validate(envelope))
            except (ValidationError, ValueError) as e:
                return ParseError(
                    kind=ParseErrorKind.DIRECTIVE_INVALID,
                    message=f"第 {i + 1} 条 directive 非法：{e}",
                )

        # 检查 strategy_set 引用的 id 在 library 里存在
        for d in directives:
            if d.type == DirectiveType.STRATEGY_SET:
                sid = d.payload.strategy_id  # type: ignore[union-attr]
                if sid not in self.library.all_ids():
                    candidates = self._fuzzy_match_strategy(sid, user_text)
                    return ParseError(
                        kind=ParseErrorKind.UNKNOWN_STRATEGY,
                        message=f"未注册的剧本 id: {sid}",
                        candidates=candidates,
                    )

        try:
            confidence = float(raw["confidence"])
        except (TypeError, ValueError):
            return ParseError(
                kind=ParseErrorKind.SCHEMA_MISMATCH,
                message=f"confidence 不是数字: {raw['confidence']!r}",
            )

        result = IntentParseResult(
            interpretation_zh=str(raw["interpretation_zh"]),
            confidence=max(0.0, min(1.0, confidence)),
            directives=directives,
            notes=raw.get("notes"),
        )

        if result.confidence < self.config.confidence_threshold:
            return AmbiguousParse(
                result=result,
                interpretations=[result.interpretation_zh],
            )
        return result

    @staticmethod
    def _normalize_directive_raw(d_raw: Any, user_text: str) -> dict[str, Any]:
        """把 LLM 返回的 {type, payload, priority, ...} 转成 Directive envelope dict。

        Directive 的 payload 字段本身是 discriminated union（含 type），
        因此把外层 type copy 进 payload 内层。
        """
        if not isinstance(d_raw, dict):
            raise ValueError(f"directive 不是 dict: {d_raw!r}")
        if "type" not in d_raw or "payload" not in d_raw:
            raise ValueError("directive 缺少 type 或 payload")
        payload = dict(d_raw["payload"])
        payload["type"] = d_raw["type"]
        env: dict[str, Any] = {
            "payload": payload,
            "issued_at": 0.0,  # IntentParser 不知道游戏时间；由 Board.submit() 时按 now 校正
            "source_text": user_text,
        }
        if "priority" in d_raw:
            env["priority"] = d_raw["priority"]
        return env

    def _fuzzy_match_strategy(self, sid: str, _user_text: str) -> list[str]:
        all_ids = self.library.all_ids()
        return difflib.get_close_matches(sid, all_ids, n=3, cutoff=0.5)

    # ------------------------------------------------------------------

    def _log_call(
        self,
        user_text: str,
        context: ParseContext,
        response: ProviderResponse | None,
        outcome: ParseOutcome,
        latency_ms: float,
    ) -> None:
        if self.session is None:
            return
        payload: dict[str, Any] = {
            "ts": context.game_time,
            "user_text": user_text,
            "provider": self.provider.name,
            "model": getattr(self.provider, "model", "?"),
            "latency_ms": latency_ms,
            "outcome_kind": type(outcome).__name__,
        }
        if response is not None:
            payload["input_tokens"] = response.input_tokens
            payload["output_tokens"] = response.output_tokens
            payload["cache_hit"] = response.cache_hit
        if isinstance(outcome, ParseError):
            payload["error_kind"] = outcome.kind.value
            payload["error_message"] = outcome.message
        elif isinstance(outcome, IntentParseResult):
            payload["confidence"] = outcome.confidence
            payload["directive_count"] = len(outcome.directives)
        elif isinstance(outcome, AmbiguousParse):
            payload["confidence"] = outcome.result.confidence
            payload["directive_count"] = len(outcome.result.directives)

        seq = self.session.log_llm_call(
            {
                "request": {
                    "user_text": user_text,
                    "context": context.model_dump(mode="json"),
                },
                "response_raw": response.raw if response is not None else None,
                "outcome": outcome.model_dump(mode="json"),
                "latency_ms": latency_ms,
            }
        )
        payload["call_seq"] = seq
