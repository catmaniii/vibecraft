"""Strategy 加载 / 查询错误。"""

from __future__ import annotations


class StrategyError(Exception):
    """Strategy 通用基类。"""


class StrategyNotFoundError(StrategyError):
    """`get(id)` 找不到。"""


class StrategyValidationError(StrategyError):
    """YAML schema 校验失败 / 引用 id 未注册。"""
