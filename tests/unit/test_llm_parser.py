"""IntentParser + prompt 拼装 + provider 抽象的单测。

策略：用 MockLLMProvider 注入响应，所有路径 mock —— 不调真实 API。

覆盖：
- 正常路径：strategy_set / production_override / 复合句多 directive
- 错误路径：timeout / provider exception / 无效 JSON shape / unknown_strategy / directive invalid
- AmbiguousParse：confidence < 阈值
- prompt 拼装：system / catalog / few_shot / dynamic_context 包含关键内容
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vibecraft.directives.models import (
    EngagementConstraintPayload,
    ProductionOverridePayload,
    StrategySetPayload,
    TacticalObjectivePayload,
)
from vibecraft.directives.types import DirectiveType, StageKind
from vibecraft.llm import (
    AmbiguousParse,
    IntentParser,
    IntentParseResult,
    MockLLMProvider,
    ParseContext,
    ParseError,
    ParseErrorKind,
    ParserConfig,
    ProviderResponse,
    build_dynamic_context,
    build_few_shot,
    build_strategy_catalog,
    build_system_prompt,
)
from vibecraft.strategy import StrategyLibrary

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def library() -> StrategyLibrary:
    return StrategyLibrary.from_directories(
        strategies_dir=PROJECT_ROOT / "strategies",
        aliases_path=PROJECT_ROOT / "docs" / "aliases" / "protoss.yaml",
    )


@pytest.fixture
def default_ctx() -> ParseContext:
    return ParseContext(
        game_time=245.0,
        current_stage=StageKind.OPENING,
        active_strategies={
            StageKind.OPENING: "1g_robo_immortal",
            StageKind.MIDGAME: None,
            StageKind.LATEGAME: None,
        },
        minerals=600,
        gas=200,
        supply_used=28,
        supply_cap=30,
        expansion_count=2,
        army_summary={"Stalker": 8, "Sentry": 3},
        recent_commands=["13 探机出门侦察"],
    )


# =========================================================================
# Prompt 拼装
# =========================================================================


class TestPromptBuilders:
    def test_system_prompt_includes_aliases(self, library: StrategyLibrary) -> None:
        sp = build_system_prompt(library.aliases)
        assert "BG" in sp  # 至少一个 building hotkey
        assert "VR" in sp  # 建筑 hotkey 别名
        assert "verb 消歧" in sp

    def test_strategy_catalog_lists_all_ids(self, library: StrategyLibrary) -> None:
        cat = build_strategy_catalog(library)
        assert "1g_robo_immortal" in cat
        assert "iac_2base" in cat
        assert "persistent_skytoss" in cat  # 两层架构改名
        assert "opening_build" in cat
        assert "midgame_stance" in cat
        assert "lategame_doctrine" in cat

    def test_few_shot_has_examples(self) -> None:
        fs = build_few_shot()
        assert "strategy_set" in fs
        assert "production_override" in fs
        assert "unit_claim" in fs

    def test_system_prompt_loaded_from_file_and_placeholders_replaced(
        self, library: StrategyLibrary
    ) -> None:
        """2026-05-25 用户:rules 不再含 aliases 段(race-specific 挪到 race_block)。

        防回归:rules.md 永久 cache 段不含种族数据,aliases 走 build_race_block。
        """
        from vibecraft.llm.prompt import build_race_block

        sp = build_system_prompt(library.aliases)
        # 占位符不应残留(rules.md 已删占位符,replace noop)
        assert "{building_aliases}" not in sp
        assert "{unit_aliases}" not in sp
        assert "{upgrade_aliases}" not in sp
        # rules.md 不含 alias 数据段(2026-05-25:已挪到 race_block,system prompt
        # 永久 cache;rules 仍可能在 verb 消歧/example 中提 BG/Forge 等字面)
        assert "建筑别名" not in sp
        assert "单位别名" not in sp
        assert "升级别名" not in sp
        # race_block 含 alias（2026-06-22 改分组格式：别名…→规范名）
        rb = build_race_block(library.aliases, library, my_race="Protoss")
        assert "别名表" in rb
        assert "→" in rb  # 分组映射箭头
        assert "BG" in rb
        assert "凤凰" in rb
        assert "冲锋" in rb

    def test_few_shot_loaded_from_file_and_has_compound(self) -> None:
        """few_shot 从 docs/llm_prompt/few_shot.md 读取,含复合 example(rule 5)。"""
        fs = build_few_shot()
        # 复合多 directive example
        assert "复合 L1+L3" in fs or "compound" in fs.lower() or "切凤凰运营" in fs

    def test_dynamic_context_includes_game_time(self, default_ctx: ParseContext) -> None:
        d = build_dynamic_context(default_ctx)
        assert "4:05" in d  # 245.0 秒 = 4:05
        assert "晶矿 600" in d
        assert "1g_robo_immortal" in d
        assert "Stalker:8" in d


# =========================================================================
# IntentParser 正常路径
# =========================================================================


def _provider_response_for(raw: dict, latency_ms: float = 50.0) -> ProviderResponse:
    return ProviderResponse(
        raw=raw,
        input_tokens=4000,
        output_tokens=80,
        cache_hit=True,
        latency_ms=latency_ms,
        model="mock-model",
        provider="mock",
    )


class TestIntentParserHappyPath:
    @pytest.mark.asyncio
    async def test_strategy_set(self, library: StrategyLibrary, default_ctx: ParseContext) -> None:
        provider = MockLLMProvider(
            scripted=[
                _provider_response_for(
                    {
                        "interpretation_zh": "切换到双矿 IAC 重装地面",
                        "confidence": 0.95,
                        "directives": [
                            {
                                "type": "strategy_set",
                                "payload": {
                                    "stage": "midgame",
                                    "strategy_id": "iac_2base",
                                },
                            }
                        ],
                    }
                )
            ]
        )
        parser = IntentParser(provider, library)
        outcome = await parser.parse("切到 IAC", default_ctx)
        assert isinstance(outcome, IntentParseResult)
        assert outcome.confidence == 0.95
        assert len(outcome.directives) == 1
        assert outcome.directives[0].type == DirectiveType.STRATEGY_SET
        payload = outcome.directives[0].payload
        assert isinstance(payload, StrategySetPayload)
        assert payload.strategy_id == "iac_2base"
        # source_text 应被填回原话
        assert outcome.directives[0].source_text == "切到 IAC"

    @pytest.mark.asyncio
    async def test_compound_command_multiple_directives(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        provider = MockLLMProvider(
            scripted=[
                _provider_response_for(
                    {
                        "interpretation_zh": "切剧本 + 守家",
                        "confidence": 0.9,
                        "directives": [
                            {
                                "type": "strategy_set",
                                "payload": {
                                    "stage": "midgame",
                                    "strategy_id": "iac_2base",
                                },
                            },
                            {
                                "type": "engagement_constraint",
                                "payload": {"stance": "defend"},
                            },
                        ],
                    }
                )
            ]
        )
        parser = IntentParser(provider, library)
        outcome = await parser.parse("切到 IAC，然后守家", default_ctx)
        assert isinstance(outcome, IntentParseResult)
        assert len(outcome.directives) == 2
        assert isinstance(outcome.directives[0].payload, StrategySetPayload)
        assert isinstance(outcome.directives[1].payload, EngagementConstraintPayload)
        assert outcome.directives[1].payload.stance == "defend"

    @pytest.mark.asyncio
    async def test_production_override_with_priority(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        provider = MockLLMProvider(
            scripted=[
                _provider_response_for(
                    {
                        "interpretation_zh": "下个 BG 出俩哨兵",
                        "confidence": 0.85,
                        "directives": [
                            {
                                "type": "production_override",
                                "payload": {"items": [{"unit_type": "Sentry", "count": 2}]},
                                "priority": 70,
                            }
                        ],
                    }
                )
            ]
        )
        parser = IntentParser(provider, library)
        outcome = await parser.parse("下个 BG 出俩哨兵", default_ctx)
        assert isinstance(outcome, IntentParseResult)
        d = outcome.directives[0]
        assert d.priority == 70
        assert isinstance(d.payload, ProductionOverridePayload)
        assert d.payload.items[0].unit_type == "Sentry"
        assert d.payload.items[0].count == 2

    @pytest.mark.asyncio
    async def test_payload_extra_fields_filtered(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        """LLM 在 payload 里塞 schema 外字段（如 options），应被边界过滤掉而非整条拒绝。"""
        provider = MockLLMProvider(
            scripted=[
                _provider_response_for(
                    {
                        "interpretation_zh": "切到 1门Robo",
                        "confidence": 0.95,
                        "directives": [
                            {
                                "type": "strategy_set",
                                "payload": {
                                    "stage": "opening",
                                    "strategy_id": "1g_robo_immortal",
                                    "options": {},  # LLM 幻觉的 schema 外字段
                                    "extra_note": "xxx",
                                },
                            }
                        ],
                    }
                )
            ]
        )
        parser = IntentParser(provider, library)
        outcome = await parser.parse("单BG VR出不朽", default_ctx)
        assert isinstance(outcome, IntentParseResult)
        assert len(outcome.directives) == 1
        payload = outcome.directives[0].payload
        assert isinstance(payload, StrategySetPayload)
        assert payload.strategy_id == "1g_robo_immortal"


# =========================================================================
# IntentParser 错误路径
# =========================================================================


class TestIntentParserErrors:
    @pytest.mark.asyncio
    async def test_timeout(self, library: StrategyLibrary, default_ctx: ParseContext) -> None:
        async def slow_handler(**_kwargs: object) -> ProviderResponse:
            await asyncio.sleep(5.0)
            return _provider_response_for({})

        provider = MockLLMProvider(handler=slow_handler)
        parser = IntentParser(provider, library, config=ParserConfig(timeout_s=0.05))
        outcome = await parser.parse("...", default_ctx)
        assert isinstance(outcome, ParseError)
        assert outcome.kind == ParseErrorKind.TIMEOUT

    @pytest.mark.asyncio
    async def test_provider_exception_wrapped(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        def fail_handler(**_kwargs: object) -> ProviderResponse:
            raise RuntimeError("API down")

        provider = MockLLMProvider(handler=fail_handler)
        parser = IntentParser(provider, library)
        outcome = await parser.parse("...", default_ctx)
        assert isinstance(outcome, ParseError)
        assert outcome.kind == ParseErrorKind.PROVIDER_ERROR
        assert "API down" in outcome.message

    @pytest.mark.asyncio
    async def test_missing_required_fields(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        provider = MockLLMProvider(scripted=[_provider_response_for({"directives": []})])
        parser = IntentParser(provider, library)
        outcome = await parser.parse("...", default_ctx)
        assert isinstance(outcome, ParseError)
        assert outcome.kind == ParseErrorKind.SCHEMA_MISMATCH

    @pytest.mark.asyncio
    async def test_unknown_strategy_id(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        provider = MockLLMProvider(
            scripted=[
                _provider_response_for(
                    {
                        "interpretation_zh": "切到不存在的剧本",
                        "confidence": 0.9,
                        "directives": [
                            {
                                "type": "strategy_set",
                                "payload": {
                                    "stage": "midgame",
                                    "strategy_id": "iac_2bass",  # typo
                                },
                            }
                        ],
                    }
                )
            ]
        )
        parser = IntentParser(provider, library)
        outcome = await parser.parse("切到 IAC", default_ctx)
        assert isinstance(outcome, ParseError)
        assert outcome.kind == ParseErrorKind.UNKNOWN_STRATEGY
        # 应有 fuzzy 候选
        assert "iac_2base" in outcome.candidates

    @pytest.mark.asyncio
    async def test_cross_race_strategy_rejected(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        """神族 parser 拒绝虫族 strategy_id（LLM hallucinate / 误选）→ 不切策略。"""
        provider = MockLLMProvider(
            scripted=[
                _provider_response_for(
                    {
                        "interpretation_zh": "切到 12pool",
                        "confidence": 0.9,
                        "directives": [
                            {
                                "type": "strategy_set",
                                "payload": {
                                    "stage": "opening",
                                    "strategy_id": "12pool",  # zerg 剧本
                                },
                            }
                        ],
                    }
                )
            ]
        )
        parser = IntentParser(provider, library, my_race="protoss")
        outcome = await parser.parse("切 12pool", default_ctx)
        assert isinstance(outcome, ParseError)
        assert outcome.kind == ParseErrorKind.UNKNOWN_STRATEGY
        # 错误消息明确指出种族不匹配
        assert "zerg" in outcome.message
        assert "protoss" in outcome.message
        assert "12pool" in outcome.message

    @pytest.mark.asyncio
    async def test_same_race_strategy_accepted(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        """my_race=protoss 时神族剧本 id 正常通过。"""
        provider = MockLLMProvider(
            scripted=[
                _provider_response_for(
                    {
                        "interpretation_zh": "切到 IAC",
                        "confidence": 0.95,
                        "directives": [
                            {
                                "type": "strategy_set",
                                "payload": {
                                    "stage": "midgame",
                                    "strategy_id": "iac_2base",
                                },
                            }
                        ],
                    }
                )
            ]
        )
        parser = IntentParser(provider, library, my_race="protoss")
        outcome = await parser.parse("切到 IAC", default_ctx)
        assert isinstance(outcome, IntentParseResult)
        assert outcome.directives[0].payload.strategy_id == "iac_2base"  # type: ignore[union-attr]

    def test_catalog_filtered_by_race(self, library: StrategyLibrary) -> None:
        """my_race=protoss 时 race_block(含 catalog) 不出现其它种族的剧本 id。

        2026-05-25: catalog 已挪到 race_block(原 _strategy_catalog 字段已删)。
        """
        from vibecraft.llm import MockLLMProvider as _MP

        protoss_parser = IntentParser(_MP(), library, my_race="protoss")
        assert "4bg" in protoss_parser._race_block
        assert "iac_2base" in protoss_parser._race_block
        assert "12pool" not in protoss_parser._race_block
        assert "macro_hatch" not in protoss_parser._race_block
        assert "marine_rush" not in protoss_parser._race_block

        zerg_parser = IntentParser(_MP(), library, my_race="zerg")
        assert "12pool" in zerg_parser._race_block
        assert "4bg" not in zerg_parser._race_block

    @pytest.mark.asyncio
    async def test_invalid_directive_payload(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        provider = MockLLMProvider(
            scripted=[
                _provider_response_for(
                    {
                        "interpretation_zh": "...",
                        "confidence": 0.9,
                        "directives": [
                            {
                                "type": "production_override",
                                "payload": {"foo": "bar"},  # 缺 unit_type
                            }
                        ],
                    }
                )
            ]
        )
        parser = IntentParser(provider, library)
        outcome = await parser.parse("...", default_ctx)
        assert isinstance(outcome, ParseError)
        assert outcome.kind == ParseErrorKind.DIRECTIVE_INVALID

    @pytest.mark.asyncio
    async def test_too_many_directives(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        provider = MockLLMProvider(
            scripted=[
                _provider_response_for(
                    {
                        "interpretation_zh": "...",
                        "confidence": 0.9,
                        "directives": [
                            {
                                "type": "production_override",
                                "payload": {"unit_type": "Stalker"},
                            }
                        ]
                        * 20,
                    }
                )
            ]
        )
        parser = IntentParser(provider, library, config=ParserConfig(max_directives_per_call=10))
        outcome = await parser.parse("...", default_ctx)
        assert isinstance(outcome, ParseError)
        assert outcome.kind == ParseErrorKind.SCHEMA_MISMATCH


# =========================================================================
# Ambiguous
# =========================================================================


class TestAmbiguous:
    @pytest.mark.asyncio
    async def test_low_confidence_becomes_ambiguous(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        provider = MockLLMProvider(
            scripted=[
                _provider_response_for(
                    {
                        "interpretation_zh": "不确定",
                        "confidence": 0.4,
                        "directives": [
                            {
                                "type": "production_override",
                                "payload": {"items": [{"unit_type": "Stalker", "count": 1}]},
                            }
                        ],
                    }
                )
            ]
        )
        parser = IntentParser(provider, library, config=ParserConfig(confidence_threshold=0.6))
        outcome = await parser.parse("...", default_ctx)
        assert isinstance(outcome, AmbiguousParse)
        assert outcome.result.confidence == 0.4


# =========================================================================
# Logging integration
# =========================================================================


class TestParserLogging:
    @pytest.mark.asyncio
    async def test_logs_llm_call_to_session(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        from vibecraft.logging_ import GameSession, GameSessionConfig

        session = GameSession(GameSessionConfig(use_null_sinks=True))
        provider = MockLLMProvider(
            scripted=[
                _provider_response_for(
                    {
                        "interpretation_zh": "ok",
                        "confidence": 0.9,
                        "directives": [],
                    }
                )
            ]
        )
        parser = IntentParser(provider, library, session=session)
        await parser.parse("test", default_ctx)
        # 即使 null sink，counter 也应该 +1
        assert parser.session is session
        # 重新跑应 +2：直接通过 session 计数
        seq = session.log_llm_call({})  # 加一条
        assert seq >= 2
        session.close()


# =========================================================================
# P3.4: done_when validate retry
# =========================================================================

# 合法的 tactical_objective（带合法 done_when）
_VALID_TACTICAL_OBJECTIVE_RAW = {
    "interpretation_zh": "进攻对方自然",
    "confidence": 0.9,
    "directives": [
        {
            "type": "tactical_objective",
            "payload": {
                "verb": "attack",
                "target_area": "enemy_natural",
                "done_when": {
                    "kind": "any_of",
                    "conditions": [
                        {"kind": "target_destroyed", "target_kind": "natural"},
                        {"kind": "own_army_size_ratio", "op": "<=", "value": 0.3},
                    ],
                },
                "timeout_s": 120,
            },
        }
    ],
}

# 第 1 次 LLM 返回 invalid done_when（kind 拼错）
_INVALID_DONE_WHEN_RAW = {
    "interpretation_zh": "进攻对方自然",
    "confidence": 0.9,
    "directives": [
        {
            "type": "tactical_objective",
            "payload": {
                "verb": "attack",
                "target_area": "enemy_natural",
                "done_when": {
                    "kind": "invalid_kind_xyz",  # 非法 kind → discriminator 失败
                    "some_field": 1,
                },
                "timeout_s": 120,
            },
        }
    ],
}


class TestDoneWhenValidate:
    """P3.4: done_when validate retry 路径测试。"""

    @pytest.mark.asyncio
    async def test_retry_success_on_first_invalid_done_when(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        """第 1 次 LLM 返回 invalid done_when → retry 第 2 次正确 → directive OK。"""
        provider = MockLLMProvider(
            scripted=[
                _provider_response_for(_INVALID_DONE_WHEN_RAW),  # 第 1 次：invalid
                _provider_response_for(_VALID_TACTICAL_OBJECTIVE_RAW),  # 第 2 次：合法
            ]
        )
        # max_validation_retries=1 启用 1 次 retry(默认 0 不 retry,向后兼容旧调用方)
        parser = IntentParser(
            provider,
            library,
            config=ParserConfig(max_validation_retries=1),
        )
        outcome = await parser.parse("进攻对方自然", default_ctx)

        assert isinstance(outcome, IntentParseResult), f"expected IntentParseResult, got {outcome}"
        assert len(outcome.directives) == 1
        d = outcome.directives[0]
        assert d.type.value == "tactical_objective"
        assert isinstance(d.payload, TacticalObjectivePayload)
        assert d.payload.verb == "attack"
        assert d.payload.done_when is not None
        # provider 应被调用了 2 次（1 次 + 1 次 retry）
        assert len(provider.calls) == 2
        # retry prompt 的 few_shot 应包含 "[Retry]"
        assert "[Retry]" in provider.calls[1]["few_shot"]

    @pytest.mark.asyncio
    async def test_fallback_strip_done_when_on_double_invalid(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        """第 1 次和第 2 次 LLM 均返回 invalid done_when → fallback strip + echo 含"降级"。"""
        provider = MockLLMProvider(
            scripted=[
                _provider_response_for(_INVALID_DONE_WHEN_RAW),  # 第 1 次：invalid
                _provider_response_for(_INVALID_DONE_WHEN_RAW),  # 第 2 次仍 invalid
            ]
        )
        parser = IntentParser(
            provider,
            library,
            config=ParserConfig(max_validation_retries=1),
        )
        outcome = await parser.parse("进攻对方自然", default_ctx)

        assert isinstance(outcome, IntentParseResult), f"expected IntentParseResult, got {outcome}"
        assert len(outcome.directives) == 1
        d = outcome.directives[0]
        assert isinstance(d.payload, TacticalObjectivePayload)
        # done_when 应被 strip 掉（降级后为 None）
        assert d.payload.done_when is None
        # notes 应含"降级"
        assert outcome.notes is not None
        assert "降级" in outcome.notes
        # provider 应被调用了 2 次
        assert len(provider.calls) == 2

    @pytest.mark.asyncio
    async def test_non_done_when_error_not_retried(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        """非 done_when 字段缺失（如 unit_type）→ 不 retry，直接返回 ParseError。"""
        provider = MockLLMProvider(
            scripted=[
                _provider_response_for(
                    {
                        "interpretation_zh": "...",
                        "confidence": 0.9,
                        "directives": [
                            {
                                "type": "production_override",
                                "payload": {"foo": "bar"},  # 缺 unit_type，非 done_when 问题
                            }
                        ],
                    }
                )
            ]
        )
        parser = IntentParser(provider, library)
        outcome = await parser.parse("...", default_ctx)
        # 不应该 retry，直接返回 DIRECTIVE_INVALID
        assert isinstance(outcome, ParseError)
        assert outcome.kind == ParseErrorKind.DIRECTIVE_INVALID
        # provider 只调用 1 次（不 retry）
        assert len(provider.calls) == 1

    @pytest.mark.asyncio
    async def test_valid_done_when_passes_through(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        """合法 done_when → 直接通过，不触发 retry。"""
        provider = MockLLMProvider(scripted=[_provider_response_for(_VALID_TACTICAL_OBJECTIVE_RAW)])
        parser = IntentParser(provider, library)
        outcome = await parser.parse("进攻对方自然", default_ctx)

        assert isinstance(outcome, IntentParseResult)
        d = outcome.directives[0]
        assert isinstance(d.payload, TacticalObjectivePayload)
        assert d.payload.done_when is not None
        # provider 只调用 1 次（无 retry）
        assert len(provider.calls) == 1


# =========================================================================
# P3.4: TacticalObjective prompt content
# =========================================================================


class TestTacticalObjectivePrompt:
    """验证 prompt 包含 verb 白名单 / done_when kind 白名单 / few-shot 例子。"""

    def test_system_prompt_contains_11_verbs(self, library: StrategyLibrary) -> None:
        sp = build_system_prompt(library.aliases)
        verbs = [
            "attack",
            "defend",
            "scout",
            "expand",
            "harass",
            "drop",
            "vision",
            "raze",
            "retreat",
            "regroup",
            "split",
        ]
        for verb in verbs:
            assert verb in sp, f"verb '{verb}' not found in system prompt"

    def test_system_prompt_contains_done_when_kinds(self, library: StrategyLibrary) -> None:
        sp = build_system_prompt(library.aliases)
        kinds = [
            "unit_count_built_since",
            "tech_done",
            "expansion_count",
            "target_destroyed",
            "own_army_size_ratio",
            "vision_acquired",
            "enemy_killed_in_area",
            "time_elapsed_since",
        ]
        for kind in kinds:
            assert kind in sp, f"done_when kind '{kind}' not found in system prompt"

    def test_system_prompt_explains_done_when_semantics(self, library: StrategyLibrary) -> None:
        sp = build_system_prompt(library.aliases)
        assert "done_when" in sp
        assert "timeout_s" in sp
        assert "L2" in sp or "tactical_objective" in sp  # 语义说明

    def test_few_shot_contains_done_when_examples(self) -> None:
        fs = build_few_shot()
        # 覆盖 done_when 典型 pattern（target_destroyed 在别处描述，例 10 改 recon 后移除）
        assert "done_when" in fs
        assert "vision_acquired" in fs
        assert "enemy_killed_in_area" in fs
        assert "time_elapsed_since" in fs
        assert "tech_done" in fs
        assert "unit_count_built_since" in fs

    def test_few_shot_done_when_examples_cover_all_6_patterns(self) -> None:
        fs = build_few_shot()
        # 典型例子（中文常说"打分矿/二矿"，不是"自然"借词）
        assert "打对方二矿" in fs or "打对方分矿" in fs
        assert "凤凰打死对方 5 个农民就回" in fs
        assert "30 秒后撤" in fs

    @pytest.mark.asyncio
    async def test_tactical_objective_directive_parsed_correctly(
        self, library: StrategyLibrary, default_ctx: ParseContext
    ) -> None:
        """mock LLM 返回合法 TacticalObjective → directive 进结果。"""
        provider = MockLLMProvider(scripted=[_provider_response_for(_VALID_TACTICAL_OBJECTIVE_RAW)])
        parser = IntentParser(provider, library)
        outcome = await parser.parse("打对方自然", default_ctx)
        assert isinstance(outcome, IntentParseResult)
        assert len(outcome.directives) == 1
        d = outcome.directives[0]
        assert d.type.value == "tactical_objective"
        assert isinstance(d.payload, TacticalObjectivePayload)
        assert d.payload.verb == "attack"
        assert d.payload.target_area == "enemy_natural"
