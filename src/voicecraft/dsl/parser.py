"""DSL parser（recursive descent）。

优先级（从低到高）::

    expr        := or_expr
    or_expr     := and_expr ('OR' and_expr)*
    and_expr    := not_expr ('AND' not_expr)*
    not_expr    := 'NOT' not_expr | compare
    compare     := primary (op_or_in primary)?
    op_or_in    := '>' | '>=' | '<' | '<=' | '==' | '!=' | 'IN'
    primary     := literal | field | '(' expr ')'
    literal     := NUMBER | STRING | NUMERIC_IDENT (in list 上下文)
    field       := IDENT ('.' IDENT)*
    list        := '[' primary (',' primary)* ']'
"""

from __future__ import annotations

from voicecraft.dsl.ast_nodes import BoolOp, Compare, Expr, FieldAccess, In, Literal, Not
from voicecraft.dsl.errors import DSLSyntaxError
from voicecraft.dsl.lexer import Token, TokKind, tokenize

_COMPARE_OPS = {">", ">=", "<", "<=", "==", "!="}


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.i = 0

    def _peek(self, offset: int = 0) -> Token:
        return self.tokens[self.i + offset]

    def _eat(self, kind: TokKind, value: str | None = None) -> Token:
        tok = self._peek()
        if tok.kind != kind or (value is not None and tok.value != value):
            expected = value if value is not None else kind.name
            raise DSLSyntaxError(
                f"期望 {expected}，实际得到 {tok.value!r}",
                position=tok.pos,
            )
        self.i += 1
        return tok

    # ----- 优先级 -----------------------------------------------------

    def expr(self) -> Expr:
        return self.or_expr()

    def or_expr(self) -> Expr:
        first = self.and_expr()
        rest: list[Expr] = []
        while self._peek().kind == TokKind.OR:
            self.i += 1
            rest.append(self.and_expr())
        if not rest:
            return first
        return BoolOp(op="OR", operands=(first, *rest))

    def and_expr(self) -> Expr:
        first = self.not_expr()
        rest: list[Expr] = []
        while self._peek().kind == TokKind.AND:
            self.i += 1
            rest.append(self.not_expr())
        if not rest:
            return first
        return BoolOp(op="AND", operands=(first, *rest))

    def not_expr(self) -> Expr:
        if self._peek().kind == TokKind.NOT:
            self.i += 1
            return Not(operand=self.not_expr())
        return self.compare()

    def compare(self) -> Expr:
        left = self.primary()
        tok = self._peek()
        if tok.kind == TokKind.OP and tok.value in _COMPARE_OPS:
            self.i += 1
            right = self.primary()
            return Compare(op=tok.value, left=left, right=right)
        if tok.kind == TokKind.IN:
            self.i += 1
            self._eat(TokKind.LBRACKET)
            items: list[Expr] = [self._list_element()]
            while self._peek().kind == TokKind.COMMA:
                self.i += 1
                items.append(self._list_element())
            self._eat(TokKind.RBRACKET)
            return In(elem=left, items=tuple(items))
        return left

    def primary(self) -> Expr:
        tok = self._peek()
        if tok.kind == TokKind.LPAREN:
            self.i += 1
            inner = self.expr()
            self._eat(TokKind.RPAREN)
            return inner
        if tok.kind == TokKind.NUMBER:
            self.i += 1
            return Literal(value=_parse_number(tok.value))
        if tok.kind == TokKind.STRING:
            self.i += 1
            return Literal(value=tok.value)
        if tok.kind == TokKind.IDENT:
            return self._field()
        if tok.kind == TokKind.NUMERIC_IDENT:
            # 顶层位置上 NUMERIC_IDENT 不合法 —— 只能在 in-list 里出现
            raise DSLSyntaxError(
                f"标识符 {tok.value!r} 以数字开头，仅可作为 'in [...]' 列表元素",
                position=tok.pos,
            )
        raise DSLSyntaxError(f"非法 token: {tok.value!r}", position=tok.pos)

    def _field(self) -> FieldAccess:
        first = self._eat(TokKind.IDENT)
        parts = [first.value]
        while self._peek().kind == TokKind.DOT:
            self.i += 1
            nxt = self._peek()
            if nxt.kind != TokKind.IDENT:
                raise DSLSyntaxError(
                    f"点号后期望标识符，实际 {nxt.value!r}",
                    position=nxt.pos,
                )
            self.i += 1
            parts.append(nxt.value)
        return FieldAccess(parts=tuple(parts))

    def _list_element(self) -> Expr:
        """`in [...]` 内允许更宽松的字面值：裸 IDENT / NUMERIC_IDENT 当字符串。"""
        tok = self._peek()
        if tok.kind == TokKind.NUMBER:
            self.i += 1
            return Literal(value=_parse_number(tok.value))
        if tok.kind == TokKind.STRING:
            self.i += 1
            return Literal(value=tok.value)
        if tok.kind == TokKind.IDENT:
            # 在 list 上下文里，单个 IDENT 视为字面字符串（不展开成 FieldAccess）
            # 但有点不直观；保留 FieldAccess 也行，evaluator 会读 context
            # 为可读性 + 玩家心智，把 list 元素全部当字符串
            self.i += 1
            return Literal(value=tok.value)
        if tok.kind == TokKind.NUMERIC_IDENT:
            self.i += 1
            return Literal(value=tok.value)
        raise DSLSyntaxError(f"list 元素非法: {tok.value!r}", position=tok.pos)


def _parse_number(raw: str) -> int | float:
    if "." in raw:
        return float(raw)
    return int(raw)


def parse(source: str) -> Expr:
    """字符串 → AST。"""
    tokens = tokenize(source)
    parser = _Parser(tokens)
    ast = parser.expr()
    tail = parser._peek()
    if tail.kind != TokKind.EOF:
        raise DSLSyntaxError(
            f"表达式末尾有多余 token: {tail.value!r}",
            position=tail.pos,
        )
    return ast


def compile_expression(source: str) -> Expr:
    """parse 的同义词，语义更明确：把表达式编译进 AST 缓存起来。"""
    return parse(source)
