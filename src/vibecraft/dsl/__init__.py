"""条件 DSL：剧本 YAML 里用的 entry_when / abort_signals / reactions 表达式。

通用形式：`<entity>.<attr> <op> <value>`，**禁止任意函数**（沙箱安全）。

例子（来自设计文档 §4.3 / §4.2）::

    self.tech.warpgate.done
    self.units.stalker.count >= 8
    enemy.units.zergling.count >= 8 AND game.time < '3:00'
    from_opening in [1g_robo_immortal, 4_gateway_pressure]
    NOT enemy.has_mech_units

不支持：任意函数调用、属性赋值、变量声明、循环、lambda。
"""

from __future__ import annotations

from vibecraft.dsl.errors import DSLError, DSLEvalError, DSLSyntaxError
from vibecraft.dsl.evaluator import GameContext, evaluate
from vibecraft.dsl.parser import compile_expression, parse

__all__ = [
    "DSLError",
    "DSLEvalError",
    "DSLSyntaxError",
    "GameContext",
    "compile_expression",
    "evaluate",
    "parse",
]
