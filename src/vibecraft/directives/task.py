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
    # 2026-05-24 用户:待命 = 到达 target → 留守 + 受敌自动战斗 + 战斗后
    # 超出半径自动返回。比 hold_position 更智能(后者站着不动)。
    STANDBY = "standby"

    # 战斗
    ATTACK_MOVE = "attack_move"
    FOCUS_FIRE = "focus_fire"
    KITE = "kite"
    HARASS_WORKERS = "harass_workers"
    # 2026-06-29 #580 群体协同骚扰：整组大件由 GroupHarassAct 统一调度（健康分状态机 +
    # 抱团一个矿 + 边路转移）。target=NAMED_SPOT(enemy_main/natural/third) 锁定矿 / None=auto
    # picker 选最优矿。**必须进 skip_action**（director 只维护 tag 集，act 唯一控制者）。
    GROUP_HARASS = "group_harass"
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
    # target 可为 None：verb=HARASS_WORKERS 动态轮换目标时工厂指定 None，per-BC 卡沿用。
    # 其他 verb 通常非 None，但执行层已有 `if action.target` 防御性判断。
    target: TargetSpec | None = None
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
