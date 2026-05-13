"""Directives：内部 IR 与 Directive Board。

对应设计文档 §5。所有玩家输入（语音 / 文字 / 按钮）都最终归一到 directives 数组，
经 Board 仲裁后由 ares hook 点应用到 SC2。

本模块只负责数据模型与生命周期（commit / release / expire），不做仲裁。
仲裁需要游戏运行时状态 + 剧本对象，放在 bot/ 模块里组合。
"""

from __future__ import annotations

from voicecraft.directives.board import BoardEvent, BoardEventKind, DirectiveBoard
from voicecraft.directives.models import (
    BuildAtPayload,
    Directive,
    EngagementConstraintPayload,
    ExpansionOverridePayload,
    MovePayload,
    ProductionOverridePayload,
    ScoutPayload,
    StrategySetPayload,
    TechOverridePayload,
    UnitClaimPayload,
    UnitReleasePayload,
    ViewFollowPayload,
    ViewMovePayload,
    ViewZoomPayload,
)
from voicecraft.directives.scope import (
    ClaimRecord,
    ScopeKind,
    ScopeSpec,
    Selector,
    TargetSpec,
)
from voicecraft.directives.task import Action, Reaction, RoleHint, Task, Verb
from voicecraft.directives.types import DirectiveType, IssuedBy, StageKind

__all__ = [
    "Action",
    "BoardEvent",
    "BoardEventKind",
    "BuildAtPayload",
    "ClaimRecord",
    "Directive",
    "DirectiveBoard",
    "DirectiveType",
    "EngagementConstraintPayload",
    "ExpansionOverridePayload",
    "IssuedBy",
    "MovePayload",
    "ProductionOverridePayload",
    "Reaction",
    "RoleHint",
    "ScopeKind",
    "ScopeSpec",
    "ScoutPayload",
    "Selector",
    "StageKind",
    "StrategySetPayload",
    "TargetSpec",
    "Task",
    "TechOverridePayload",
    "UnitClaimPayload",
    "UnitReleasePayload",
    "Verb",
    "ViewFollowPayload",
    "ViewMovePayload",
    "ViewZoomPayload",
]
