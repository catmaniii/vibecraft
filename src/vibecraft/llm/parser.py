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
from vibecraft.directives.scope import (
    DEFAULT_MAX_VOICE_GROUPS,
    MAX_VOICE_GROUPS_LIMIT,
    set_max_voice_groups,
)
from vibecraft.directives.types import DirectiveType
from vibecraft.i18n import t
from vibecraft.llm.prompt import (
    ParseContext,
    build_dynamic_context,
    build_few_shot,
    build_few_shot_en_supplement,
    build_race_block,
    build_system_prompt,
    build_tool_schema,
)
from vibecraft.llm.provider import LLMProvider, ProviderResponse
from vibecraft.llm.schema import (
    AmbiguousParse,
    ClarificationRequest,
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
    """内部异常：directive pydantic 校验失败，需要 retry feedback。"""

    def __init__(self, index: int, raw: dict[str, Any], exc: ValidationError | ValueError) -> None:
        self.index = index  # 第几条（0-based）
        self.raw_response = raw  # provider 的完整 raw 响应
        self.validation_exc = exc
        super().__init__(str(exc))


class ParserConfig(BaseModel):
    """运行时配置。"""

    model_config = ConfigDict(extra="forbid")

    timeout_s: float = 15.0
    max_retries: int = 0
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    max_directives_per_call: int = 10
    # 任意 schema validation 失败时,把 error 回灌 LLM 重 call 几次。
    # 默认 0 = 不 retry(向后兼容旧测试期望);eval 实测 retry=3 把 Flash
    # 从 90.5% → 95%+(L3c/L4a 等 LLM 偶发结构错本来一次过不了,二次大概率修正)。
    max_validation_retries: int = 0
    # 语音编队上限。默认取 scope 的唯一常量 DEFAULT_MAX_VOICE_GROUPS（要改总数改那一行）。
    # 应用后会同步到 schema 校验(group_id 范围)、LLM 提示词范围说明、Director 快照
    # (web 编队条槽位数)。硬上限 MAX_VOICE_GROUPS_LIMIT。
    max_voice_groups: int = Field(default=DEFAULT_MAX_VOICE_GROUPS, ge=1, le=MAX_VOICE_GROUPS_LIMIT)


class IntentParser:
    """玩家话语 → ParseOutcome 的编排器。"""

    def __init__(
        self,
        provider: LLMProvider,
        library: StrategyLibrary,
        aliases: AliasTable | None = None,
        config: ParserConfig | None = None,
        session: GameSession | None = None,
        my_race: str | None = None,
        locale: str = "zh",
    ) -> None:
        self.provider = provider
        self.library = library
        self.aliases = aliases or library.aliases
        self.config = config or ParserConfig()
        self.session = session
        # 玩家语言（zh/en）：决定 LLM interpretation 复述语言 + 将来按语言选 few-shot/prompt（Layer C）。
        self.locale: str = locale or "zh"
        # 编队上限：把配置应用到 schema 校验的运行时全局（Selector/GroupAssign/
        # GroupClear 的 group_id 范围都读 scope.MAX_VOICE_GROUPS）。
        set_max_voice_groups(self.config.max_voice_groups)
        # 我方种族（"protoss" / "zerg" / "terran"）。给定时：
        # 1) Strategy Catalog 只列当前种族剧本，LLM 看不到跨种族选项
        # 2) strategy_set 校验拒绝其它种族 id（防 LLM 仍 hallucinate）
        self.my_race: str | None = my_race.lower() if my_race else None

        # 静态 prompt 段(cache 友好,2026-05-25 用户重排):
        # - system 块 = rules.md(永久 cache,跨族跨局都命中)
        # - few_shot 块 = race_block(alias + catalog + race 声明) + few_shot.md
        #   (同族 cache 命中,切族 invalid 但 system 块仍命中)
        self._system_prompt = build_system_prompt(
            self.aliases, max_voice_groups=self.config.max_voice_groups
        )
        self._race_block = build_race_block(self.aliases, self.library, self.my_race)
        self._few_shot_text = build_few_shot()
        # 玩家 UI 语言=英文时,在 few_shot 尾追加英文输入补充示例(提升英文话语解析一致性)。
        # 拼进静态 _few_shot(按 session locale 恒定)→ cache 友好,不污染每次 dynamic。
        if self.locale == "en":
            _en_supp = build_few_shot_en_supplement()
            if _en_supp:
                self._few_shot_text = self._few_shot_text + "\n\n" + _en_supp
        # 提前拼接(race_block + few_shot.md)给 anthropic_provider 当 few_shot block
        self._few_shot = self._race_block + "\n\n" + self._few_shot_text
        self._tool_schema = build_tool_schema()

    # ------------------------------------------------------------------

    async def parse(self, user_text: str, context: ParseContext) -> ParseOutcome:
        dynamic = build_dynamic_context(context)
        # 玩家界面语言 = 英文时：让 LLM 用英文写 interpretation_zh（玩家看英文）。
        # 指令解析本身不受影响（rules 本就支持中/英混合输入）；directives 仍是结构化 enum。
        if self.locale == "en":
            dynamic += (
                "\n\n[Player UI language: English] "
                "Write the `interpretation_zh` field IN ENGLISH (the player reads English). "
                "The player may speak English; interpret commands normally. "
                "SC2 unit/building names: use official English names (Stalker, Immortal, Gateway, ...)."
            )
        # 2026-05-25: system 块只放 rules(永久 cache);race_block + few_shot 合并
        # 进 few_shot 块(同族 cache,切族 invalid)
        system_full = self._system_prompt
        # 保存本次 prompt 快照（供 _log_call 写入 llm_calls/call_NNN.json）
        self._last_prompts: dict[str, Any] = {
            "system": system_full,
            "few_shot": self._few_shot,
            "dynamic_context": dynamic,
        }

        t0 = time.monotonic()
        # 初次 LLM call + empty response retry(DeepSeek v4-flash 偶发 stop_reason=
        # tool_use 但 content={} 空响应,实测 2/4 失败率。修复:空响应自动 retry 2 次)
        response = None
        last_exc: Exception | None = None
        for empty_attempt in range(3):  # 共最多 3 次(1 原 + 2 retry)
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
                if empty_attempt < 2:
                    last_exc = TimeoutError("LLM 响应超时")
                    continue
                err = ParseError(
                    kind=ParseErrorKind.TIMEOUT, message=t("parse.timeout", self.locale)
                )
                self._log_call(
                    user_text, context, None, err, latency_ms=(time.monotonic() - t0) * 1000
                )
                return err
            except Exception as e:
                if empty_attempt < 2:
                    last_exc = e
                    continue
                err = ParseError(
                    kind=ParseErrorKind.PROVIDER_ERROR,
                    message=t("parse.providerError", self.locale, err=f"{type(e).__name__}: {e}"),
                )
                self._log_call(
                    user_text, context, None, err, latency_ms=(time.monotonic() - t0) * 1000
                )
                return err

            # 检查 response.raw 是否空 / 缺关键字段 → retry
            raw = response.raw if response else {}
            is_empty = not isinstance(raw, dict) or not raw or "directives" not in raw
            if not is_empty:
                break  # 拿到合法响应,跳出 retry loop
            if empty_attempt < 2:
                logger.warning(
                    "empty LLM response, retrying (%d/2): raw=%s",
                    empty_attempt + 1,
                    str(raw)[:100],
                )
                continue
            # 3 次都空 → fall through 走原有 _build_outcome 报 schema_mismatch

        # 兜底:理论不可达(except 已 return),为 mypy
        if response is None:
            err = ParseError(
                kind=ParseErrorKind.PROVIDER_ERROR,
                message=t("parse.noResponse", self.locale, exc=last_exc),
            )
            self._log_call(user_text, context, None, err, latency_ms=(time.monotonic() - t0) * 1000)
            return err

        # validate + retry loop:max_validation_retries 个额外回合(每回合把
        # 上次 LLM 错误信息回灌让它纠正)。retry 全失败后降级 strip done_when。
        max_iter = 1 + max(0, self.config.max_validation_retries)
        outcome: ParseOutcome | None = None
        last_fail: _DirectiveValidationFailed | None = None
        for attempt in range(max_iter):
            try:
                # 始终 raise(让最后一次失败也走 fallback strip,而不是 ParseError)
                outcome = self._build_outcome(
                    response, user_text, context, raise_on_validation=True
                )
                break  # validate 成功
            except _DirectiveValidationFailed as fail:
                last_fail = fail
                is_last = attempt == max_iter - 1
                if is_last:
                    break  # 不再 retry,出循环走 fallback
                self._log_validate_fail(user_text, fail.raw_response, fail.validation_exc)
                try:
                    response = await asyncio.wait_for(
                        self._call_llm_with_validation_error(
                            user_text,
                            context,
                            dynamic,
                            system_full,
                            fail.raw_response,
                            fail.validation_exc,
                        ),
                        timeout=self.config.timeout_s,
                    )
                except Exception as e:
                    err = ParseError(
                        kind=ParseErrorKind.PROVIDER_ERROR,
                        message=t(
                            "parse.validateRetryError", self.locale, err=f"{type(e).__name__}: {e}"
                        ),
                    )
                    self._log_call(
                        user_text, context, response, err, latency_ms=response.latency_ms
                    )
                    return err

        # 所有 attempt 都没 break 成功(outcome is None) → fallback strip done_when 兜底
        if outcome is None and last_fail is not None:
            outcome = self._fallback_strip_done_when(
                response, user_text, context, last_fail.validation_exc
            )
        assert outcome is not None  # 上面两条路径之一总会赋值

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
        """把 LLM 原响应 + validation error 组合成 retry prompt，再调一次 LLM。

        通用 retry feedback:把上次完整 raw 输出 + pydantic error 详情灌回去。
        LLM 看 error 里写明哪个 path 错、需要哪些字面值,大概率能修正。
        """
        error_text = str(exc)
        retry_few_shot = (
            self._few_shot + f"\n\n[Retry] 你上次的输出（schema 校验失败,需要修正）：\n"
            f"{json.dumps(raw_response, ensure_ascii=False)}\n\n"
            f"pydantic 校验错误详情（注意 path 和 enum 字面值约束）：\n{error_text}\n\n"
            "请按 system prompt 里的 enum 白名单严格修正,"
            "**重新输出完整的 directive 数组**（不要只输出 diff;不要解释,只走 emit_directives tool）。"
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
        """记录 validate 失败（Python logger + llm_call JSONL）。"""
        logger.warning(
            "validate failed, retrying with feedback: user_text=%r error=%s",
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
        logger.warning("done_when validate retry failed twice, stripping done_when: %s", exc)
        if self.session is not None:
            self.session.log_llm_call(
                {
                    "event": "llm_validate_fallback_strip_done_when",
                    "user_text": user_text,
                    "error": str(exc),
                }
            )
        outcome = self._build_outcome(
            response_stripped, user_text, context, raise_on_validation=False
        )
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
                message=t("parse.missingFields", self.locale, fields=list(raw.keys())),
            )

        directives_raw = raw.get("directives", [])
        if not isinstance(directives_raw, list):
            return ParseError(
                kind=ParseErrorKind.SCHEMA_MISMATCH,
                message=t("parse.directivesNotArray", self.locale),
            )
        if len(directives_raw) > self.config.max_directives_per_call:
            return ParseError(
                kind=ParseErrorKind.SCHEMA_MISMATCH,
                message=t(
                    "parse.tooManyDirectives",
                    self.locale,
                    n=len(directives_raw),
                    max=self.config.max_directives_per_call,
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
                # 任意 ValidationError 都触发 retry(eval 实测:不只 done_when,
                # verb/stance/missing required 字段等 retry 后大概率被 LLM 修正)。
                # raise_on_validation=False 时(已 retry 完 或 fallback 路径)
                # 直接返回 ParseError 不抛。
                if raise_on_validation:
                    raise _DirectiveValidationFailed(i, raw, e) from e
                return ParseError(
                    kind=ParseErrorKind.DIRECTIVE_INVALID,
                    message=t("parse.directiveInvalid", self.locale, n=i + 1, e=e),
                )

        # 检查 strategy_set 引用的 id 在 library 里存在 + 属于当前种族。
        # my_race 给定时：跨种族 id（如神族玩家说"切 12pool"，12pool 是 zerg）
        # 也算 UNKNOWN_STRATEGY，directive 整批拒绝 → bot 不切策略，PWA 显示失败。
        allowed_ids = (
            self.library.all_ids_for_race(self.my_race) if self.my_race else self.library.all_ids()
        )
        allowed_set = set(allowed_ids)
        for d in directives:
            if d.type == DirectiveType.STRATEGY_SET:
                sid = d.payload.strategy_id  # type: ignore[union-attr]
                if sid not in allowed_set:
                    candidates = self._fuzzy_match_strategy(sid, user_text)
                    other_race = self.library.race_of(sid)
                    if self.my_race and other_race and other_race != self.my_race:
                        msg = t(
                            "parse.wrongRace",
                            self.locale,
                            sid=repr(sid),
                            other_race=other_race,
                            my_race=self.my_race,
                        )
                    else:
                        msg = t("parse.unknownStrategy", self.locale, sid=sid)
                    return ParseError(
                        kind=ParseErrorKind.UNKNOWN_STRATEGY,
                        message=msg,
                        candidates=candidates,
                    )

        try:
            confidence = float(raw["confidence"])
        except (TypeError, ValueError):
            return ParseError(
                kind=ParseErrorKind.SCHEMA_MISMATCH,
                message=t("parse.confidenceNotNumber", self.locale, val=repr(raw["confidence"])),
            )

        # 2026-05-24: clarification 字段(LLM 给玩家选项)优先于 ambiguous。
        # LLM 不确定时输出 directives=[] + clarification={question, options}。
        if raw.get("clarification"):
            try:
                return self._build_clarification(raw["clarification"], user_text, context)
            except (ValidationError, ValueError) as exc:
                # clarification schema 校验失败 → fallback 走 ambiguous
                if raise_on_validation:
                    raise _DirectiveValidationFailed(0, raw, exc) from exc
                # 不抛,降级走下面 IntentParseResult / ambiguous

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

    def _build_clarification(
        self,
        raw_clarification: dict[str, Any],
        user_text: str,
        context: ParseContext | None,
    ) -> ClarificationRequest:
        """把 LLM 返回的 clarification 字段转成 ClarificationRequest。

        每个 option 的 directives 通过 _normalize_directive_raw 转,跟主 directives
        路径一样走 auto-fill done_when / field 兜底。
        """
        from vibecraft.llm.schema import ClarificationOption, ClarificationRequest

        opts_raw = raw_clarification.get("options", [])
        if not isinstance(opts_raw, list) or len(opts_raw) < 2:
            raise ValueError(f"clarification.options 须 ≥ 2 项: {opts_raw}")

        options: list[ClarificationOption] = []
        for opt in opts_raw:
            if not isinstance(opt, dict):
                raise ValueError(f"option 不是 dict: {opt}")
            dirs_raw = opt.get("directives", [])
            dirs: list[Directive] = []
            for d_raw in dirs_raw:
                envelope = self._normalize_directive_raw(d_raw, user_text, context)
                dirs.append(Directive.model_validate(envelope))
            options.append(
                ClarificationOption(
                    label=str(opt.get("label", ""))[:30],
                    interpretation_zh=str(opt.get("interpretation_zh", "")),
                    directives=dirs,
                )
            )

        return ClarificationRequest(
            question=str(raw_clarification.get("question", "请选择一项")),
            options=options,
            source_text=user_text,
        )

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

        # done_when 兜底(2026-05-24 用户):L4 必带 done_when(LLM 偶发漏给,
        # 实测 deepseek-v4-flash 给 vision_acquired 但不给 production_override
        # 的 unit_count_built_since)。按 payload 字段自动推导 default,保证
        # 端到端"自动关单"能跑通。LLM 真给的优先,不覆盖。
        if not payload.get("done_when"):
            if d_type == "production_override":
                items = payload.get("items", [])
                if items:
                    conds = [
                        {
                            "kind": "unit_count_built_since",
                            "unit_type": it["unit_type"],
                            "op": ">=",
                            "value": int(it["count"]),
                        }
                        for it in items
                    ]
                    payload["done_when"] = (
                        conds[0] if len(conds) == 1 else {"kind": "all_of", "conditions": conds}
                    )
            elif d_type == "structure_override":
                items = payload.get("items", [])
                if items:
                    # 2026-05-28 delta vs target_count 二选一:
                    # - target_count → done_when=structure_count(绝对值)
                    # - delta → done_when=structure_count_built_since(增量)
                    conds = []
                    for it in items:
                        if it.get("target_count") is not None:
                            conds.append(
                                {
                                    "kind": "structure_count",
                                    "structure_type": it["structure_type"],
                                    "op": ">=",
                                    "value": int(it["target_count"]),
                                }
                            )
                        elif it.get("delta") is not None:
                            conds.append(
                                {
                                    "kind": "structure_count_built_since",
                                    "structure_type": it["structure_type"],
                                    "op": ">=",
                                    "value": int(it["delta"]),
                                }
                            )
                    if conds:
                        payload["done_when"] = (
                            conds[0] if len(conds) == 1 else {"kind": "all_of", "conditions": conds}
                        )
            elif d_type == "tech_override":
                if upgrade_id := payload.get("upgrade_id"):
                    payload["done_when"] = {
                        "kind": "tech_done",
                        "upgrade_id": upgrade_id,
                    }
            elif d_type == "expansion_override":
                payload["done_when"] = {
                    "kind": "expansion_count",
                    "op": ">=",
                    "value": int(payload.get("target_count", 2)),
                }
            elif d_type == "build_at":
                # 建筑落地 1 个即关单(用户决策 #1:建筑建造完毕)
                if structure_type := payload.get("structure_type"):
                    payload["done_when"] = {
                        "kind": "structure_count_built_since",
                        "structure_type": structure_type,
                        "op": ">=",
                        "value": 1,
                    }
            elif d_type == "move":
                # safe=true 走 _tick_safe_move_orders 自己关单,不需要 done_when
                if not payload.get("safe"):
                    target = payload.get("target")
                    if isinstance(target, dict):
                        area = target.get("named_spot")
                        if area:
                            payload["done_when"] = {
                                "kind": "unit_arrived",
                                "area": area,
                                "within_grid": 5.0,
                            }
            elif d_type == "scout":
                # 顶层 SCOUT directive(非 tactical_objective verb=scout):
                # 用 vision_acquired 跟 tactical_objective(verb=scout) 对齐 — "侦察"
                # 玩家诉求是"拿到信息",区域可见即可,不必单位 hold 到位
                # (unit_held_position 对农民走远路太严格,game 短易 NOT_REACHED)
                target = payload.get("target")
                if isinstance(target, dict):
                    area = target.get("named_spot")
                    if area:
                        payload["done_when"] = {
                            "kind": "vision_acquired",
                            "area": area,
                            "hold_seconds": 1.0,
                        }

        env: dict[str, Any] = {
            "payload": payload,
            "issued_at": 0.0,  # IntentParser 不知道游戏时间;由 Board.submit() 时按 now 校正
            "source_text": user_text,
        }
        if "priority" in d_raw:
            env["priority"] = d_raw["priority"]
        return env

    def _fuzzy_match_strategy(self, sid: str, _user_text: str) -> list[str]:
        # 跨种族 fuzzy 没有意义（神族玩家看到"建议你切 12pool"会更困惑）
        pool = (
            self.library.all_ids_for_race(self.my_race) if self.my_race else self.library.all_ids()
        )
        return difflib.get_close_matches(sid, pool, n=3, cutoff=0.5)

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
