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

from vibecraft.directives.models import PAYLOAD_MODELS, Directive
from vibecraft.directives.types import DirectiveType
from vibecraft.llm.prompt import (
    ParseContext,
    build_dynamic_context,
    build_few_shot,
    build_strategy_catalog,
    build_system_prompt,
    build_tool_schema,
)
from vibecraft.llm.provider import LLMProvider, ProviderResponse
from vibecraft.llm.schema import (
    AmbiguousParse,
    IntentParseResult,
    ParseError,
    ParseErrorKind,
    ParseOutcome,
)
from vibecraft.logging_.session import GameSession
from vibecraft.strategy.aliases import AliasTable
from vibecraft.strategy.library import StrategyLibrary


class ParserConfig(BaseModel):
    """运行时配置。"""

    model_config = ConfigDict(extra="forbid")

    timeout_s: float = 15.0
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
        # 保存本次 prompt 快照（供 _log_call 写入 llm_calls/call_NNN.json）
        self._last_prompts: dict[str, Any] = {
            "system": system_full,
            "few_shot": self._few_shot,
            "dynamic_context": dynamic,
        }

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

        outcome = self._build_outcome(response, user_text, context)
        self._log_call(user_text, context, response, outcome, latency_ms=response.latency_ms)
        return outcome

    # ------------------------------------------------------------------

    def _build_outcome(
        self, response: ProviderResponse, user_text: str, context: ParseContext
    ) -> ParseOutcome:
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
                envelope = self._normalize_directive_raw(d_raw, user_text, context)
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
    def _normalize_directive_raw(
        d_raw: Any, user_text: str, context: ParseContext | None = None
    ) -> dict[str, Any]:
        """把 LLM 返回的 {type, payload, priority, ...} 转成 Directive envelope dict。

        Directive 的 payload 字段本身是 discriminated union(含 type),
        因此把外层 type copy 进 payload 内层。

        context: 可选,用于补 LLM 没给但 schema 必需的字段(如 expansion_override.target_count
        玩家说"开矿",LLM 给不出具体数字,这里 fallback 到 current_expansion + 1)。
        """
        if not isinstance(d_raw, dict):
            raise ValueError(f"directive 不是 dict: {d_raw!r}")
        if "type" not in d_raw or "payload" not in d_raw:
            raise ValueError("directive 缺少 type 或 payload")
        d_type = str(d_raw["type"])
        payload = dict(d_raw["payload"])
        payload["type"] = d_type
        # 系统边界过滤:LLM 可能在 payload 里塞 schema 外字段(如 options:{}),
        # 按目标 payload 模型的字段白名单过滤,避免 extra=forbid 整条拒绝。
        model = PAYLOAD_MODELS.get(d_type)
        if model is not None:
            payload = {k: v for k, v in payload.items() if k in model.model_fields}

        # 字段兜底:LLM 给不出具体数字时,从 game context 推断
        # expansion_override.target_count:玩家说"开矿"(没数字)→ current + 1
        if d_type == "expansion_override" and "target_count" not in payload:
            if context is not None:
                payload["target_count"] = int(context.expansion_count) + 1
            else:
                payload["target_count"] = 2  # 最保守:开第二矿

        env: dict[str, Any] = {
            "payload": payload,
            "issued_at": 0.0,  # IntentParser 不知道游戏时间;由 Board.submit() 时按 now 校正
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

        # llm_calls/call_NNN.json 全量保留（设计文档 §11.4）：
        # 包含 prompt 全文 / 响应 / 耗时 / token / 解析后 directives
        prompts = getattr(self, "_last_prompts", {})
        seq = self.session.log_llm_call(
            {
                "ts": context.game_time,
                "provider": self.provider.name,
                "model": getattr(self.provider, "model", "?"),
                # ---- prompt 全文 ----
                "system_prompt": prompts.get("system", ""),
                "few_shot": prompts.get("few_shot", ""),
                "dynamic_context": prompts.get("dynamic_context", ""),
                "user_text": user_text,
                # ---- 请求上下文摘要 ----
                "request": {
                    "context": context.model_dump(mode="json"),
                },
                # ---- 响应 ----
                "response_raw": response.raw if response is not None else None,
                "raw_text": response.raw_text if response is not None else None,
                # ---- token / cache / latency ----
                "input_tokens": response.input_tokens if response is not None else None,
                "output_tokens": response.output_tokens if response is not None else None,
                "cache_hit": response.cache_hit if response is not None else None,
                "latency_ms": latency_ms,
                "extra": response.extra if response is not None else {},
                # ---- 解析结果 ----
                "outcome": outcome.model_dump(mode="json"),
                "outcome_kind": type(outcome).__name__,
            }
        )
        payload["call_seq"] = seq
