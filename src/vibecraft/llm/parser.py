"""IntentParser：编排 prompt 拼装、调 provider、schema 校验、错误处理。

设计文档 §7.6 关键：**任何异常 → bot 状态完全不变**。
所有失败都返回 `ParseError`，**不抛**。

P3.4 新增：done_when validate retry 逻辑：
  第 1 次 LLM call → directive validate 失败 → 把 error 回灌 LLM retry 1 次。
  第 2 次仍失败 → 降级：strip done_when 后再 validate + echo 告知玩家。
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
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

logger = logging.getLogger(__name__)


class _DirectiveValidationFailed(Exception):
    """内部异常：directive pydantic 校验失败且涉及 done_when，需要 retry。"""

    def __init__(self, index: int, raw: dict[str, Any], exc: ValidationError | ValueError) -> None:
        self.index = index  # 第几条（0-based）
        self.raw_response = raw  # provider 的完整 raw 响应
        self.validation_exc = exc
        super().__init__(str(exc))


def _is_done_when_error(exc: ValidationError | ValueError) -> bool:
    """判断 ValidationError 是否跟 done_when 字段相关（才值得 retry）。"""
    if not isinstance(exc, ValidationError):
        return False
    for err in exc.errors():
        loc = err.get("loc", ())
        if any(str(part) == "done_when" for part in loc):
            return True
    return False


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

        # 第 1 次尝试 validate
        try:
            outcome = self._build_outcome(response, user_text, context, raise_on_validation=True)
        except _DirectiveValidationFailed as first_fail:
            # 第 1 次 directive validate 失败 → 把 error 回灌 LLM retry
            self._log_validate_fail(user_text, first_fail.raw_response, first_fail.validation_exc)
            try:
                retry_response = await asyncio.wait_for(
                    self._call_llm_with_validation_error(
                        user_text, context, dynamic, system_full,
                        first_fail.raw_response, first_fail.validation_exc,
                    ),
                    timeout=self.config.timeout_s,
                )
            except Exception as e:
                err = ParseError(
                    kind=ParseErrorKind.PROVIDER_ERROR,
                    message=f"validate retry provider 异常：{type(e).__name__}: {e}",
                )
                self._log_call(user_text, context, response, err, latency_ms=response.latency_ms)
                return err

            # 第 2 次尝试 validate
            try:
                outcome = self._build_outcome(
                    retry_response, user_text, context, raise_on_validation=True
                )
            except _DirectiveValidationFailed as second_fail:
                # 第 2 次仍失败 → 降级：strip done_when + echo
                outcome = self._fallback_strip_done_when(
                    retry_response, user_text, context, second_fail.validation_exc
                )
            response = retry_response

        self._log_call(user_text, context, response, outcome, latency_ms=response.latency_ms)
        return outcome

    # ------------------------------------------------------------------
    # validate retry 辅助方法
    # ------------------------------------------------------------------

    async def _call_llm_with_validation_error(
        self,
        user_text: str,
        context: ParseContext,
        dynamic: str,
        system_full: str,
        raw_response: dict[str, Any],
        exc: ValidationError | ValueError,
    ) -> ProviderResponse:
        """把 LLM 原响应 + validation error 组合成 retry prompt，再调一次 LLM。"""
        error_text = str(exc)
        # 把原 LLM 输出 + error 消息注入 few_shot retry 段
        retry_few_shot = (
            self._few_shot
            + f"\n\n[Retry] 你上次的输出：\n{json.dumps(raw_response, ensure_ascii=False)}\n"
            f"校验失败原因：{error_text}\n"
            "请修正 done_when / payload 字段后重新输出（只输出修正后的完整 directive 数组）。"
        )
        return await self.provider.parse(
            system=system_full,
            few_shot=retry_few_shot,
            dynamic_context=dynamic,
            user_text=user_text,
            tool_schema=self._tool_schema,
            timeout_s=self.config.timeout_s,
        )

    def _log_validate_fail(
        self,
        user_text: str,
        raw_response: dict[str, Any],
        exc: ValidationError | ValueError,
    ) -> None:
        """记录首次 validate 失败（Python logger + llm_call JSONL）。"""
        logger.warning(
            "done_when validate failed, retrying: user_text=%r error=%s",
            user_text,
            exc,
        )
        if self.session is not None:
            self.session.log_llm_call(
                {
                    "event": "llm_validate_retry",
                    "user_text": user_text,
                    "raw_response": raw_response,
                    "error": str(exc),
                }
            )

    def _fallback_strip_done_when(
        self,
        response: ProviderResponse,
        user_text: str,
        context: ParseContext,
        exc: ValidationError | ValueError,
    ) -> ParseOutcome:
        """第 2 次仍失败 → strip done_when 字段后降级 validate。"""
        raw = response.raw
        directives_raw = raw.get("directives", [])
        # 把每条 directive 的 payload.done_when 置 None
        stripped: list[dict[str, Any]] = []
        for d in directives_raw:
            if isinstance(d, dict) and isinstance(d.get("payload"), dict):
                d = {**d, "payload": {**d["payload"], "done_when": None}}
            stripped.append(d)
        raw_stripped = {**raw, "directives": stripped}
        response_stripped = ProviderResponse(
            raw=raw_stripped,
            raw_text=response.raw_text,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_hit=response.cache_hit,
            latency_ms=response.latency_ms,
            model=response.model,
            provider=response.provider,
            extra=response.extra,
        )
        logger.warning(
            "done_when validate retry failed twice, stripping done_when: %s", exc
        )
        if self.session is not None:
            self.session.log_llm_call(
                {
                    "event": "llm_validate_fallback_strip_done_when",
                    "user_text": user_text,
                    "error": str(exc),
                }
            )
        outcome = self._build_outcome(response_stripped, user_text, context, raise_on_validation=False)
        # 给 IntentParseResult 追加降级提示
        if isinstance(outcome, IntentParseResult):
            degraded_note = "[完成条件无效已降级为 EPHEMERAL]"
            outcome = IntentParseResult(
                interpretation_zh=outcome.interpretation_zh,
                confidence=outcome.confidence,
                directives=outcome.directives,
                notes=(outcome.notes + " " + degraded_note) if outcome.notes else degraded_note,
            )
        return outcome

    # ------------------------------------------------------------------

    def _build_outcome(
        self,
        response: ProviderResponse,
        user_text: str,
        context: ParseContext,
        raise_on_validation: bool = False,
    ) -> ParseOutcome:
        """把 ProviderResponse 转成 ParseOutcome。

        raise_on_validation=True 时，directive validate 失败会抛
        _DirectiveValidationFailed（而非返回 ParseError），供 parse() 的 retry 逻辑捕获。
        """
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
                # 只有 done_when 相关的 ValidationError 才触发 retry；
                # 其他字段缺失（如 unit_type 缺失）直接返回错误（retry 无意义）。
                if raise_on_validation and _is_done_when_error(e):
                    raise _DirectiveValidationFailed(i, raw, e) from e
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
