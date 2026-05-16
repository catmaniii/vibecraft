"""DSL lexer（tokenizer）。

支持 token::

    IDENT       /[A-Za-z_][A-Za-z0-9_]*/
    NUMERIC_ID  /[0-9][A-Za-z0-9_]*/        # 形如 '1g_robo_immortal', 但要排除纯数字
    NUMBER      整数或浮点
    STRING      'xxx' 或 "xxx"
    OP          > >= < <= == !=
    AND OR NOT IN                            # 大小写不敏感，但必须独立词
    .  ,  (  )  [  ]
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from vibecraft.dsl.errors import DSLSyntaxError


class TokKind(Enum):
    IDENT = auto()
    NUMERIC_IDENT = auto()  # 形如 1g_robo_immortal
    NUMBER = auto()
    STRING = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    IN = auto()
    OP = auto()
    DOT = auto()
    COMMA = auto()
    LPAREN = auto()
    RPAREN = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    EOF = auto()


@dataclass(frozen=True)
class Token:
    kind: TokKind
    value: str
    pos: int


_KEYWORDS = {
    "AND": TokKind.AND,
    "OR": TokKind.OR,
    "NOT": TokKind.NOT,
    "IN": TokKind.IN,
}


def tokenize(source: str) -> list[Token]:
    """字符串 → token 列表，包含末尾 EOF。"""
    tokens: list[Token] = []
    i = 0
    n = len(source)

    while i < n:
        c = source[i]

        # 空白
        if c.isspace():
            i += 1
            continue

        # 单字符 token
        single = {
            ".": TokKind.DOT,
            ",": TokKind.COMMA,
            "(": TokKind.LPAREN,
            ")": TokKind.RPAREN,
            "[": TokKind.LBRACKET,
            "]": TokKind.RBRACKET,
        }
        if c in single:
            tokens.append(Token(single[c], c, i))
            i += 1
            continue

        # 运算符
        if c in "><=!":
            two = source[i : i + 2]
            if two in {">=", "<=", "==", "!="}:
                tokens.append(Token(TokKind.OP, two, i))
                i += 2
                continue
            if c in {">", "<"}:
                tokens.append(Token(TokKind.OP, c, i))
                i += 1
                continue
            raise DSLSyntaxError(f"非法运算符 {c!r}", position=i)

        # 字符串字面值
        if c in {"'", '"'}:
            end = source.find(c, i + 1)
            if end < 0:
                raise DSLSyntaxError("未闭合的字符串字面值", position=i)
            tokens.append(Token(TokKind.STRING, source[i + 1 : end], i))
            i = end + 1
            continue

        # 数字或 numeric-ident
        if c.isdigit():
            j = i
            while j < n and (source[j].isalnum() or source[j] == "_" or source[j] == "."):
                j += 1
            raw = source[i:j]
            # 纯数字（int 或 float） vs numeric-ident（含字母 / 下划线）
            if all(ch.isdigit() or ch == "." for ch in raw) and raw.count(".") <= 1:
                tokens.append(Token(TokKind.NUMBER, raw, i))
            else:
                tokens.append(Token(TokKind.NUMERIC_IDENT, raw, i))
            i = j
            continue

        # 标识符 / 关键字
        if c.isalpha() or c == "_":
            j = i
            while j < n and (source[j].isalnum() or source[j] == "_"):
                j += 1
            raw = source[i:j]
            upper = raw.upper()
            if upper in _KEYWORDS:
                tokens.append(Token(_KEYWORDS[upper], upper, i))
            else:
                tokens.append(Token(TokKind.IDENT, raw, i))
            i = j
            continue

        raise DSLSyntaxError(f"未知字符 {c!r}", position=i)

    tokens.append(Token(TokKind.EOF, "", n))
    return tokens
