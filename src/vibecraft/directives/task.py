"""Task / Action / Reaction / Verb。

对应设计文档 §5.3。Task 是 unit_claim payload 的核心——
描述"占住一个单位让它执行什么动作"。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from vibecraft.directives.scope import TargetSpec


class Verb(str, Enum):
    """LLMControlBehavior 的动作动词。

    所有 verb 在设计文档 §5.3 中定义。归纳为四组：静止/移动、战斗、技能、工人/建筑。
    """

    # 静止 / 移动
    HOLD_POSITION = "hold_position"
    GUARD_POSITION = "guard_position"
    MOVE_TO = "move_to"
    PATROL = "patrol"
    FOLLOW = "follow"
    RETREAT = "retreat"

    # 战斗
    ATTACK_MOVE = "attack_move"
    FOCUS_FIRE = "focus_fire"
    KITE = "kite"
    HARASS_WORKERS = "harass_workers"
    LIFT_TARGET = "lift_target"

    # 技能
    CAST_ABILITY = "cast_ability"

    # 工人 / 建筑
    GATHER = "gather"
    BUILD = "build"
    CANCEL = "cancel"


RoleHint = Literal["defender", "attacker", "harasser", "scout", "none"]


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verb: Verb
    target: TargetSpec
    ability_id: str | None = Field(
        default=None,
        description="仅 verb=CAST_ABILITY 用；如 'PsiStorm'、'ForceField'",
    )


class Reaction(BaseModel):
    """触发式子动作。`when` 表达式成立时插队执行 `do`，cooldown 期内不重复。"""

    model_config = ConfigDict(extra="forbid")

    when: str  # condition DSL
    do: Action
    cooldown_s: float = 0.0
    priority_within_task: int = 50


class Task(BaseModel):
    """unit_claim 的 payload 核心。"""

    model_config = ConfigDict(extra="forbid")

    primary_action: Action
    reactions: list[Reaction] = Field(default_factory=list)
    role_hint: RoleHint = "none"
