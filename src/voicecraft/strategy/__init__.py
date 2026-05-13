"""剧本（strategy）库：opening / midgame / lategame 三种 kind 的 schema + 加载器。

设计文档 §4。剧本对象由 `StrategyLibrary` 统一加载，业务层通过
`library.get(id)` 取，不直接 import YAML 路径——这条 indirection 是
"recipe store 抽象"，未来好替换。
"""

from __future__ import annotations

from voicecraft.strategy.aliases import AliasTable, VerbHint
from voicecraft.strategy.errors import StrategyError, StrategyNotFoundError
from voicecraft.strategy.library import StrategyLibrary
from voicecraft.strategy.models import (
    AbortSignal,
    AttackWindow,
    BuildStep,
    DefaultTransition,
    EngagementDoctrineRule,
    LategameDoctrine,
    LategameTransition,
    MidgameStance,
    OpeningBuild,
    Phase,
    StrategyKind,
)

__all__ = [
    "AbortSignal",
    "AliasTable",
    "AttackWindow",
    "BuildStep",
    "DefaultTransition",
    "EngagementDoctrineRule",
    "LategameDoctrine",
    "LategameTransition",
    "MidgameStance",
    "OpeningBuild",
    "Phase",
    "StrategyError",
    "StrategyKind",
    "StrategyLibrary",
    "StrategyNotFoundError",
    "VerbHint",
]
