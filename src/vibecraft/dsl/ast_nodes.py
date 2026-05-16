"""DSL AST 节点。

为什么不复用 Python 自带 ast：
- 我们的语法是 Python 子集 + 几个非 Python token (AND/OR/NOT 大小写不敏感)
- 沙箱要求不允许任意函数 / attribute；自定义 AST 可以彻底白名单
- evaluator 直接 walk 这些节点比 walk Python ast 简洁
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Literal:
    """字面值：int / float / str / bool。"""

    value: int | float | str | bool


@dataclass(frozen=True)
class FieldAccess:
    """点路径访问：`self.tech.warpgate.done` → parts=['self','tech','warpgate','done']。"""

    parts: tuple[str, ...]


@dataclass(frozen=True)
class Compare:
    """二元比较：`a >= b`。op 为 '>', '>=', '<', '<=', '==', '!='。"""

    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class BoolOp:
    """逻辑组合：`AND` / `OR`，n-ary 扁平存储。"""

    op: str  # "AND" / "OR"
    operands: tuple[Expr, ...]


@dataclass(frozen=True)
class Not:
    """`NOT expr`。"""

    operand: Expr


@dataclass(frozen=True)
class In:
    """`elem in [a, b, c]`。"""

    elem: Expr
    items: tuple[Expr, ...]


Expr = Literal | FieldAccess | Compare | BoolOp | Not | In
