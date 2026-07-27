"""DSL evaluator：walk AST + 查询 GameContext。

GameContext 是一个嵌套 dict 包装。FieldAccess 路径走它查值。
特殊：`game.time` 允许左 numeric / 右 "M:SS" 字符串比较，evaluator 帮把
字符串规整为秒。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from vibecraft.dsl.ast_nodes import BoolOp, Compare, Expr, FieldAccess, In, Literal, Not
from vibecraft.dsl.errors import DSLEvalError
from vibecraft.dsl.parser import parse

_TIME_RE = re.compile(r"^(\d+):([0-5]?\d)$")


@dataclass
class GameContext:
    """传给 evaluator 的运行时状态。

    state 是嵌套 dict，与设计文档 §4.3 左值表对齐。例如::

        {
          "self": {
            "tech": {"warpgate": {"done": True, "started": True}},
            "units": {"stalker": {"count": 12}},
            "minerals": 600,
            "expansion_count": 2,
          },
          "enemy": {
            "units": {"zergling": {"count": 6}},
            "has_mech_units": False,
          },
          "game": {"time": 245.0},                  # 浮点秒
          "from_opening": "1g_robo_immortal",
        }
    """

    state: Mapping[str, Any] = field(default_factory=dict)

    def lookup(self, path: tuple[str, ...]) -> Any:
        """走 path，缺字段时抛 DSLEvalError。"""
        node: Any = self.state
        for i, key in enumerate(path):
            if not isinstance(node, Mapping):
                walked = ".".join(path[: i + 1])
                raise DSLEvalError(f"路径 {walked!r} 期望 dict，实际是 {type(node).__name__}")
            if key not in node:
                walked = ".".join(path[: i + 1])
                raise DSLEvalError(f"GameContext 缺少字段: {walked}")
            node = node[key]
        return node


# =========================================================================
# Evaluator
# =========================================================================


def evaluate(node: Expr | str, ctx: GameContext) -> bool:
    """求值；接收 AST 或源字符串。

    顶层结果约定为 bool；若 AST 顶层是 FieldAccess（例如 `self.tech.warpgate.done`）
    返回的 truthy 值，强制布尔化。
    """
    if isinstance(node, str):
        node = parse(node)
    val = _eval(node, ctx)
    return bool(val)


def _eval(node: Expr, ctx: GameContext) -> Any:
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, FieldAccess):
        return ctx.lookup(node.parts)
    if isinstance(node, Not):
        return not bool(_eval(node.operand, ctx))
    if isinstance(node, BoolOp):
        if node.op == "AND":
            return all(bool(_eval(o, ctx)) for o in node.operands)
        if node.op == "OR":
            return any(bool(_eval(o, ctx)) for o in node.operands)
        raise DSLEvalError(f"未知 BoolOp: {node.op!r}")
    if isinstance(node, Compare):
        left = _eval(node.left, ctx)
        right = _eval(node.right, ctx)
        return _compare(node.op, left, right, node)
    if isinstance(node, In):
        elem = _eval(node.elem, ctx)
        items = [_eval(x, ctx) for x in node.items]
        return elem in items
    raise DSLEvalError(f"未知 AST 节点: {type(node).__name__}")


def _compare(op: str, left: Any, right: Any, node: Compare) -> bool:
    # game.time 特殊：左 numeric，右 "M:SS" 字符串
    left, right = _coerce_time_pair(left, right, node)

    try:
        if op == "==":
            return bool(left == right)
        if op == "!=":
            return bool(left != right)
        if op == ">":
            return bool(left > right)
        if op == ">=":
            return bool(left >= right)
        if op == "<":
            return bool(left < right)
        if op == "<=":
            return bool(left <= right)
    except TypeError as e:
        raise DSLEvalError(f"比较 {left!r} {op} {right!r} 类型不兼容: {e}") from e
    raise DSLEvalError(f"未知比较运算符: {op!r}")


def _coerce_time_pair(left: Any, right: Any, node: Compare) -> tuple[Any, Any]:
    """若一侧是 'M:SS' 字符串、另一侧是数字 → 字符串转秒。"""
    if isinstance(left, str) and isinstance(right, (int, float)):
        parsed = _try_parse_time(left)
        if parsed is not None:
            return parsed, right
    if isinstance(right, str) and isinstance(left, (int, float)):
        parsed = _try_parse_time(right)
        if parsed is not None:
            return left, parsed
    return left, right


def _try_parse_time(s: str) -> float | None:
    m = _TIME_RE.match(s)
    if not m:
        return None
    minutes = int(m.group(1))
    seconds = int(m.group(2))
    return minutes * 60 + seconds
