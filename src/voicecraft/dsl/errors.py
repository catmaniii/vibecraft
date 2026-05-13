"""DSL 错误类型。"""

from __future__ import annotations


class DSLError(Exception):
    """DSL 通用基类。"""


class DSLSyntaxError(DSLError):
    """Parser 阶段错误。"""

    def __init__(self, message: str, position: int | None = None) -> None:
        super().__init__(message)
        self.position = position


class DSLEvalError(DSLError):
    """Evaluator 阶段错误。常见：未知字段、类型不匹配。"""
