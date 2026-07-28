"""Directives：内部 IR 与 Directive Board。

对应设计文档 §5。所有玩家输入（语音 / 文字 / 按钮）都最终归一到 directives 数组，
经 Board 仲裁后应用到 SC2。

本模块只负责数据模型与生命周期（commit / release / expire），不做仲裁。
仲裁需要游戏运行时状态 + 剧本对象，放在 bot/ 模块里组合。
"""

from __future__ import annotations

from vibecraft.directives.board import BoardEvent, BoardEventKind, DirectiveBoard
from vibecraft.directives.models import (
    AllOf,
    AnyOf,
    BuildAtPayload,
    Directive,
    DoneWhen,
    EnemyKilledInArea,
    EngagementConstraintPayload,
    ExpansionCount,
    ExpansionOverridePayload,
    MovePayload,
    OwnArmySizeRatio,
    ProductionOverridePayload,
    ScoutPayload,
    StrategySetPayload,
    TacticalObjectivePayload,
    TacticalVerb,
    TargetDestroyed,
    TechDone,
    TechOverridePayload,
    TimeElapsedSince,
    UnitClaimPayload,
    UnitCountBuiltSince,
    UnitReleasePayload,
    VisionAcquired,
)
from vibecraft.directives.scope import (
    ClaimRecord,
    ScopeKind,
    ScopeSpec,
    Selector,
    TargetSpec,
)
from vibecraft.directives.task import Action, Reaction, RoleHint, Task, Verb
from vibecraft.directives.types import DirectiveType, IssuedBy, StageKind

__all__ = [
    "Action",
    "AllOf",
    "AnyOf",
    "BoardEvent",
    "BoardEventKind",
    "BuildAtPayload",
    "ClaimRecord",
    "Directive",
    "DirectiveBoard",
    "DirectiveType",
    "DoneWhen",
    "EnemyKilledInArea",
    "EngagementConstraintPayload",
    "ExpansionCount",
    "ExpansionOverridePayload",
    "IssuedBy",
    "MovePayload",
    "OwnArmySizeRatio",
    "ProductionOverridePayload",
    "Reaction",
    "RoleHint",
    "ScopeKind",
    "ScopeSpec",
    "ScoutPayload",
    "Selector",
    "StageKind",
    "StrategySetPayload",
    "TacticalObjectivePayload",
    "TacticalVerb",
    "TargetDestroyed",
    "TargetSpec",
    "Task",
    "TechDone",
    "TechOverridePayload",
    "TimeElapsedSince",
    "UnitClaimPayload",
    "UnitCountBuiltSince",
    "UnitReleasePayload",
    "Verb",
    "VisionAcquired",
]
