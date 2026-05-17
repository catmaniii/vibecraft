"""tests/llm_eval/score.py 的逻辑单测(不调 LLM,纯数据)。

放 tests/unit/ 是因为它纯函数测试,不属于 llm_eval marker 范畴。
"""

from __future__ import annotations

from vibecraft.directives.models import (
    Directive,
    ProductionOverridePayload,
    StrategySetPayload,
    UnitClaimPayload,
)
from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
from vibecraft.directives.task import Action, Task, Verb
from vibecraft.directives.types import DirectiveType
from vibecraft.llm.schema import (
    AmbiguousParse,
    IntentParseResult,
    ParseError,
    ParseErrorKind,
)

from tests.llm_eval.score import ExpectedSpec, _get_path, _matches, score_outcome


# =========================================================================
# _get_path / _matches 基础
# =========================================================================


def test_get_path_nested_attribute() -> None:
    d = Directive(
        payload=StrategySetPayload(stage="opening", strategy_id="1g_robo_immortal"),
        issued_at=10.0,
    )
    # stage 是 Literal str(不是 enum)
    assert _get_path(d, "payload.stage") == "opening"
    assert _get_path(d, "payload.strategy_id") == "1g_robo_immortal"
    assert _get_path(d, "payload.nonexistent") is None


def test_get_path_dict_fallback() -> None:
    obj = {"a": {"b": {"c": 42}}}
    assert _get_path(obj, "a.b.c") == 42
    assert _get_path(obj, "a.b.missing") is None


def test_matches_exact() -> None:
    assert _matches("Probe", "Probe")
    assert not _matches("Probe", "Zealot")


def test_matches_list_of_allowed() -> None:
    assert _matches("scout", ["scout", "vision"])
    assert not _matches("attack", ["scout", "vision"])


def test_matches_callable_predicate() -> None:
    assert _matches(5, lambda v: v > 3)
    assert not _matches(2, lambda v: v > 3)


def test_matches_enum_normalize() -> None:
    """enum.value 跟 str literal 比较。"""
    assert _matches(Verb.MOVE_TO, "move_to")
    assert _matches(Verb.MOVE_TO, ["move_to", "patrol"])


# =========================================================================
# score_outcome
# =========================================================================


def _strategy_directive() -> Directive:
    return Directive(
        payload=StrategySetPayload(stage="midgame", strategy_id="iac_2base"),
        issued_at=1.0,
    )


def _unit_claim_directive(verb: Verb = Verb.MOVE_TO) -> Directive:
    return Directive(
        payload=UnitClaimPayload(
            selector=Selector(unit_type="Probe"),
            task=Task(
                primary_action=Action(
                    verb=verb,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="natural"),
                )
            ),
            persistent=False,
        ),
        issued_at=1.0,
    )


def test_score_parse_error_fails() -> None:
    spec = ExpectedSpec(
        name="any", inject="x", expect_type=DirectiveType.STRATEGY_SET
    )
    outcome = ParseError(kind=ParseErrorKind.PROVIDER_ERROR, message="boom")
    score = score_outcome(outcome, spec)
    assert not score.passed
    assert "ParseError" in score.reason


def test_score_ambiguous_fails() -> None:
    spec = ExpectedSpec(
        name="any", inject="x", expect_type=DirectiveType.STRATEGY_SET
    )
    inner = IntentParseResult(interpretation_zh="哪个?", confidence=0.3, directives=[])
    outcome = AmbiguousParse(result=inner, interpretations=["哪个?", "另一个?"])
    score = score_outcome(outcome, spec)
    assert not score.passed
    assert "AmbiguousParse" in score.reason


def test_score_match_strategy_set() -> None:
    spec = ExpectedSpec(
        name="L1a",
        inject="切叉球一波",
        expect_type=DirectiveType.STRATEGY_SET,
        must_have_paths={
            "payload.stage": "midgame",
            "payload.strategy_id": "iac_2base",
        },
    )
    outcome = IntentParseResult(
        interpretation_zh="切到叉球一波",
        confidence=0.9,
        directives=[_strategy_directive()],
    )
    score = score_outcome(outcome, spec)
    assert score.passed
    assert score.matched_paths == 2


def test_score_wrong_type_fails() -> None:
    spec = ExpectedSpec(
        name="L1a",
        inject="切叉球一波",
        expect_type=DirectiveType.STRATEGY_SET,
    )
    outcome = IntentParseResult(
        interpretation_zh="x",
        confidence=0.9,
        directives=[
            Directive(
                payload=ProductionOverridePayload(unit_type="Sentry", count=2),
                issued_at=1.0,
            )
        ],
    )
    score = score_outcome(outcome, spec)
    assert not score.passed
    assert "production_override" in score.reason  # seen types 列出来了


def test_score_forbidden_path_fails() -> None:
    spec = ExpectedSpec(
        name="L3b",
        inject="让那个探机移动到气矿",
        expect_type=DirectiveType.UNIT_CLAIM,
        must_have_paths={
            "payload.selector.unit_type": "Probe",
        },
        forbidden_paths={
            "payload.task.primary_action.verb": ["scout", "move", "gather"],
        },
    )
    # 拼一个 verb=HARASS_WORKERS 的 directive(假装 LLM 出错给了 forbidden 之外的)
    outcome_ok = IntentParseResult(
        interpretation_zh="x", confidence=0.9,
        directives=[_unit_claim_directive(verb=Verb.MOVE_TO)],
    )
    assert score_outcome(outcome_ok, spec).passed

    # 故意构造一个 verb 不该出现的情况 → 但 forbidden 用 list,需要把 verb 设
    # forbidden 里的字面值才能命中,enum 里没有 "scout"/"move"/"gather"
    # 实际场景是 LLM 返回了非法字符串通不过 pydantic 校验。
    # 这里用 gather 验证(enum 里有 GATHER):
    outcome_bad = IntentParseResult(
        interpretation_zh="x", confidence=0.9,
        directives=[_unit_claim_directive(verb=Verb.GATHER)],
    )
    score = score_outcome(outcome_bad, spec)
    assert not score.passed
    assert "forbidden" in score.reason


def test_score_allow_extra_directives_default_true() -> None:
    """默认 allow_extra=True:LLM 多生成 directive 不算 FAIL。"""
    spec = ExpectedSpec(
        name="L1a",
        inject="切叉球一波,然后凤凰骚扰",
        expect_type=DirectiveType.STRATEGY_SET,
        must_have_paths={"payload.strategy_id": "iac_2base"},
    )
    outcome = IntentParseResult(
        interpretation_zh="x", confidence=0.9,
        directives=[
            _strategy_directive(),
            _unit_claim_directive(),  # 额外的 unit_claim
        ],
    )
    assert score_outcome(outcome, spec).passed


def test_score_disallow_extra_directives() -> None:
    spec = ExpectedSpec(
        name="L1a_strict",
        inject="切叉球一波",
        expect_type=DirectiveType.STRATEGY_SET,
        must_have_paths={"payload.strategy_id": "iac_2base"},
        allow_extra_directives=False,
    )
    outcome = IntentParseResult(
        interpretation_zh="x", confidence=0.9,
        directives=[_strategy_directive(), _unit_claim_directive()],
    )
    score = score_outcome(outcome, spec)
    assert not score.passed
    assert "extra directive" in score.reason
