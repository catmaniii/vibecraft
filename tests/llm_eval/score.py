"""ExpectedSpec 数据类 + ParseOutcome ↔ ExpectedSpec 评分。

ExpectedSpec 描述某个 inject 期望解析出来的 directive 长什么样
（type + 必须的字段 + 不应出现的字段）。`score_outcome` 把 IntentParser
的 ParseOutcome 跟 spec 比对,返回 Score。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vibecraft.directives.models import Directive
from vibecraft.directives.types import DirectiveType
from vibecraft.llm.schema import (
    AmbiguousParse,
    IntentParseResult,
    ParseError,
    ParseOutcome,
)


@dataclass
class ExpectedSpec:
    """单个测试 case 的期望规格。

    匹配语义:
    - `expect_type`: directives 列表里必须至少有一条 type 匹配
      (单 DirectiveType 或 list[DirectiveType] —— list 表示"任一即可",
       用于业务等价的多 type 路由，如 scout 既可走顶层 scout 也可走
       tactical_objective(verb=scout))
    - `must_have_paths`: 匹配那条 directive 必须满足所有 path → value 条件
    - `forbidden_paths`: 匹配那条 directive 不允许任一 path → value 命中
      (value 用 list 表示"不允许的值集合")
    - `allow_extra_directives`: True = 容忍 LLM 多生成额外 directive
      (e.g. 复合句拆开);False = 要求 directives 列表只有 1 条匹配的
    """

    name: str
    inject: str
    expect_type: DirectiveType | list[DirectiveType]
    must_have_paths: dict[str, Any] = field(default_factory=dict)
    forbidden_paths: dict[str, list[Any]] = field(default_factory=dict)
    allow_extra_directives: bool = True


@dataclass
class Score:
    """单次评分结果。"""

    passed: bool
    reason: str
    matched_paths: int = 0
    total_paths: int = 0


def _get_path(obj: Any, path: str) -> Any:
    """按点号路径深入 nested dict/object/list。

    例:`_get_path(d, "payload.selector.unit_type")` 等价 d.payload.selector.unit_type。
    数字段当 list/tuple 索引：`payload.items.0.unit_type` → d.payload.items[0].unit_type
    属性 / dict key / pydantic field / sequence index 都支持。
    """
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, (list, tuple)) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            cur = getattr(cur, part, None)
    return cur


def _normalize(v: Any) -> Any:
    """归一化比较值 —— enum.value / str 都看字面值。"""
    if hasattr(v, "value"):
        return v.value
    return v


def _matches(actual: Any, expected: Any) -> bool:
    """单条 path 匹配判定:支持精确值 / list-of-allowed / callable predicate。"""
    actual = _normalize(actual)
    expected = _normalize(expected)
    if callable(expected):
        return bool(expected(actual))
    if isinstance(expected, list):
        return actual in [_normalize(x) for x in expected]
    return actual == expected


def _find_matching_directive(
    directives: list[Directive], spec: ExpectedSpec
) -> Directive | None:
    """从 directives 列表里找 type 匹配 + must_have_paths 都满足的那条。

    expect_type 支持单值或 list(任一即可)。
    """
    allowed_types = (
        spec.expect_type if isinstance(spec.expect_type, list) else [spec.expect_type]
    )
    for d in directives:
        if d.type not in allowed_types:
            continue
        if all(_matches(_get_path(d, p), v) for p, v in spec.must_have_paths.items()):
            return d
    return None


def score_outcome(outcome: ParseOutcome, spec: ExpectedSpec) -> Score:
    """把 ParseOutcome 跟 ExpectedSpec 比对,返回 Score。

    规则:
    1. ParseError → FAIL(reason 抄 error message)
    2. AmbiguousParse → FAIL(spec 期望 directive,LLM 退化模糊)
    3. IntentParseResult 但没匹配的 directive → FAIL
    4. 找到匹配 directive,但 forbidden_paths 命中 → FAIL
    5. 全过 → PASS
    """
    if isinstance(outcome, ParseError):
        return Score(passed=False, reason=f"ParseError: {outcome.message[:160]}")
    if isinstance(outcome, AmbiguousParse):
        interp = outcome.result.interpretation_zh
        return Score(passed=False, reason=f"AmbiguousParse: {interp[:160]}")
    if not isinstance(outcome, IntentParseResult):
        return Score(passed=False, reason=f"未知 outcome 类型: {type(outcome).__name__}")

    matched = _find_matching_directive(outcome.directives, spec)
    if matched is None:
        seen_types = [d.type.value for d in outcome.directives]
        expected_str = (
            "|".join(t.value for t in spec.expect_type)
            if isinstance(spec.expect_type, list)
            else spec.expect_type.value
        )
        return Score(
            passed=False,
            reason=f"没找到 type∈{{{expected_str}}} 且 must_have 都满足的 directive (seen: {seen_types})",
        )

    # check forbidden
    for path, bad_values in spec.forbidden_paths.items():
        actual = _normalize(_get_path(matched, path))
        if actual in [_normalize(v) for v in bad_values]:
            return Score(
                passed=False,
                reason=f"forbidden 命中: {path}={actual!r} ∈ {bad_values}",
            )

    if not spec.allow_extra_directives and len(outcome.directives) > 1:
        extra = [d.type.value for d in outcome.directives if d is not matched]
        return Score(
            passed=False,
            reason=f"不允许 extra directive (expected 1, got {len(outcome.directives)}; extra types: {extra})",
        )

    return Score(
        passed=True,
        reason=f"matched directive id={matched.id} type={matched.type.value}",
        matched_paths=len(spec.must_have_paths),
        total_paths=len(spec.must_have_paths),
    )
