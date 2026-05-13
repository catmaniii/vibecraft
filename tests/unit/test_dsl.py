"""DSL parser + evaluator 单元测试。

测试目标 (§4.3 条件 DSL)：
- 词法 / 语法基础
- 比较 / 布尔组合 / NOT / 括号 / IN
- game.time 字符串与数字混比
- 错误场景：未知字段、语法非法、类型不匹配
"""

from __future__ import annotations

import pytest

from voicecraft.dsl import (
    DSLEvalError,
    DSLSyntaxError,
    GameContext,
    evaluate,
    parse,
)
from voicecraft.dsl.ast_nodes import BoolOp, Compare, FieldAccess, In, Literal, Not
from voicecraft.dsl.lexer import TokKind, tokenize

# =========================================================================
# Lexer
# =========================================================================


class TestLexer:
    def test_basic_tokens(self) -> None:
        toks = tokenize("self.tech.warpgate.done")
        kinds = [t.kind for t in toks]
        assert kinds == [
            TokKind.IDENT,
            TokKind.DOT,
            TokKind.IDENT,
            TokKind.DOT,
            TokKind.IDENT,
            TokKind.DOT,
            TokKind.IDENT,
            TokKind.EOF,
        ]

    def test_numbers(self) -> None:
        toks = tokenize("3 3.5 100")
        assert [t.value for t in toks[:-1]] == ["3", "3.5", "100"]

    def test_strings_both_quotes(self) -> None:
        toks = tokenize("'hello' \"world\"")
        assert toks[0].kind == TokKind.STRING and toks[0].value == "hello"
        assert toks[1].kind == TokKind.STRING and toks[1].value == "world"

    def test_keywords_case_insensitive(self) -> None:
        toks = tokenize("a AND b or c not d In [x]")
        assert toks[1].kind == TokKind.AND
        assert toks[3].kind == TokKind.OR
        assert toks[5].kind == TokKind.NOT
        assert toks[7].kind == TokKind.IN

    def test_operators(self) -> None:
        toks = tokenize(">= <= == != > <")
        ops = [t.value for t in toks if t.kind == TokKind.OP]
        assert ops == [">=", "<=", "==", "!=", ">", "<"]

    def test_numeric_ident(self) -> None:
        """1g_robo_immortal 是合法的剧本 id 字面值。"""
        toks = tokenize("1g_robo_immortal")
        assert toks[0].kind == TokKind.NUMERIC_IDENT
        assert toks[0].value == "1g_robo_immortal"

    def test_unterminated_string(self) -> None:
        with pytest.raises(DSLSyntaxError, match="未闭合"):
            tokenize("'hello")

    def test_unknown_char(self) -> None:
        with pytest.raises(DSLSyntaxError, match="未知字符"):
            tokenize("a + b")


# =========================================================================
# Parser
# =========================================================================


class TestParser:
    def test_simple_field(self) -> None:
        node = parse("self.tech.warpgate.done")
        assert isinstance(node, FieldAccess)
        assert node.parts == ("self", "tech", "warpgate", "done")

    def test_compare(self) -> None:
        node = parse("self.units.stalker.count >= 8")
        assert isinstance(node, Compare)
        assert node.op == ">="
        assert isinstance(node.left, FieldAccess)
        assert node.left.parts == ("self", "units", "stalker", "count")
        assert isinstance(node.right, Literal)
        assert node.right.value == 8

    def test_and_or(self) -> None:
        node = parse("a > 1 AND b < 2 OR c == 3")
        assert isinstance(node, BoolOp)
        assert node.op == "OR"
        assert len(node.operands) == 2

    def test_not_precedence(self) -> None:
        node = parse("NOT a > 1 AND b == 2")
        # NOT 绑得比 AND 紧：NOT(a>1) AND (b==2)
        assert isinstance(node, BoolOp)
        assert node.op == "AND"
        assert isinstance(node.operands[0], Not)

    def test_parens(self) -> None:
        node = parse("(a OR b) AND c")
        assert isinstance(node, BoolOp)
        assert node.op == "AND"
        # 第一个 operand 是 BoolOp(OR)
        assert isinstance(node.operands[0], BoolOp)
        assert node.operands[0].op == "OR"

    def test_in_list_with_numeric_idents(self) -> None:
        node = parse("from_opening in [1g_robo_immortal, 4_gateway_pressure]")
        assert isinstance(node, In)
        assert isinstance(node.elem, FieldAccess)
        assert node.elem.parts == ("from_opening",)
        assert len(node.items) == 2
        assert isinstance(node.items[0], Literal)
        assert node.items[0].value == "1g_robo_immortal"
        assert node.items[1].value == "4_gateway_pressure"

    def test_string_literal(self) -> None:
        node = parse("game.time < '3:00'")
        assert isinstance(node, Compare)
        assert node.op == "<"
        assert isinstance(node.right, Literal)
        assert node.right.value == "3:00"

    def test_trailing_garbage_rejected(self) -> None:
        with pytest.raises(DSLSyntaxError, match="多余 token"):
            parse("a > 1 b")

    def test_numeric_ident_outside_list_rejected(self) -> None:
        with pytest.raises(DSLSyntaxError, match="数字开头"):
            parse("1g_robo_immortal == 1")

    def test_dot_then_non_ident_rejected(self) -> None:
        with pytest.raises(DSLSyntaxError, match="点号后"):
            parse("self.tech.")


# =========================================================================
# Evaluator
# =========================================================================


@pytest.fixture
def ctx() -> GameContext:
    return GameContext(
        state={
            "self": {
                "tech": {
                    "warpgate": {"done": True, "started": True},
                    "blink": {"done": False, "started": True},
                },
                "units": {
                    "stalker": {"count": 12},
                    "sentry": {"count": 3},
                },
                "minerals": 600,
                "gas": 200,
                "supply_used": 50,
                "supply_cap": 60,
                "expansion_count": 2,
            },
            "enemy": {
                "units": {
                    "zergling": {"count": 4},
                    "phoenix": {"count": 0},
                },
                "has_mech_units": False,
                "race": "zerg",
            },
            "game": {"time": 245.0},
            "from_opening": "1g_robo_immortal",
        }
    )


class TestEvaluator:
    def test_field_truthy(self, ctx: GameContext) -> None:
        assert evaluate("self.tech.warpgate.done", ctx) is True
        assert evaluate("self.tech.blink.done", ctx) is False

    def test_compare_int(self, ctx: GameContext) -> None:
        assert evaluate("self.units.stalker.count >= 8", ctx) is True
        assert evaluate("self.units.sentry.count >= 8", ctx) is False
        assert evaluate("self.expansion_count == 2", ctx) is True

    def test_compare_time_string_vs_seconds(self, ctx: GameContext) -> None:
        # game.time = 245.0 = 4:05
        assert evaluate("game.time < '3:00'", ctx) is False
        assert evaluate("game.time < '5:00'", ctx) is True
        assert evaluate("game.time >= '4:00'", ctx) is True

    def test_and_or_not(self, ctx: GameContext) -> None:
        assert evaluate("self.tech.warpgate.done AND self.expansion_count >= 2", ctx) is True
        assert evaluate("self.tech.blink.done OR self.expansion_count >= 2", ctx) is True
        assert evaluate("NOT enemy.has_mech_units", ctx) is True

    def test_in_list(self, ctx: GameContext) -> None:
        assert evaluate("from_opening in [1g_robo_immortal, 4_gateway_pressure]", ctx) is True
        assert evaluate("from_opening in [skytoss, iac_2base]", ctx) is False

    def test_in_list_with_strings(self, ctx: GameContext) -> None:
        assert evaluate("enemy.race in ['zerg', 'terran']", ctx) is True

    def test_complex_combined(self, ctx: GameContext) -> None:
        expr = (
            "self.tech.warpgate.done AND "
            "self.expansion_count >= 2 AND "
            "from_opening in [1g_robo_immortal, 4_gateway_pressure]"
        )
        assert evaluate(expr, ctx) is True

    def test_abort_signal_example(self, ctx: GameContext) -> None:
        """对应设计文档剧本 abort_signals 例子。"""
        # enemy.units.zergling.count >= 8 AND game.time < '3:00'
        assert evaluate("enemy.units.zergling.count >= 8 AND game.time < '3:00'", ctx) is False

    def test_unknown_field_raises(self, ctx: GameContext) -> None:
        with pytest.raises(DSLEvalError, match="缺少字段"):
            evaluate("self.tech.unknown_research.done", ctx)

    def test_type_mismatch_raises(self, ctx: GameContext) -> None:
        # minerals (int) vs string 'hello' 比较 —— Python 3 不允许
        with pytest.raises(DSLEvalError, match="类型不兼容"):
            evaluate("self.minerals > 'hello'", ctx)
