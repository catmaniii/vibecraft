"""Directive 类型枚举。对应设计文档 §5.2。"""

from __future__ import annotations

from enum import Enum


class DirectiveType(str, Enum):
    """所有 directive type 的统一枚举。

    粒度分四档（设计文档 §8.1）：
    - 大略 (剧本): STRATEGY_SET
    - 中略 (全局调参): PRODUCTION_* / TECH_* / EXPANSION_* / ENGAGEMENT_*
    - 微粒度 (单位): UNIT_CLAIM / SCOUT / MOVE / UNIT_RELEASE
    - 微粒度 (建筑): BUILD_AT / PRODUCTION_OVERRIDE (带 building_tag)
    """

    # 剧本切换
    STRATEGY_SET = "strategy_set"

    # 中粒度 override
    PRODUCTION_OVERRIDE = "production_override"
    TECH_OVERRIDE = "tech_override"
    EXPANSION_OVERRIDE = "expansion_override"
    ENGAGEMENT_CONSTRAINT = "engagement_constraint"

    # 微粒度单位
    UNIT_CLAIM = "unit_claim"
    SCOUT = "scout"
    MOVE = "move"
    BUILD_AT = "build_at"

    # 释放
    UNIT_RELEASE = "unit_release"

    # 视野（不限频）
    VIEW_MOVE = "view_move"
    VIEW_FOLLOW = "view_follow"
    VIEW_ZOOM = "view_zoom"


class IssuedBy(str, Enum):
    """directive 来源，用于仲裁冲突时定优先级。

    voice > auto_transition > abort（设计文档 §5.5）。
    """

    VOICE = "voice"
    AUTO_TRANSITION = "auto_transition"
    ABORT = "abort"
    BOT_INTERNAL = "bot_internal"  # 例：bot 自己生成的 standing order release


class StageKind(str, Enum):
    """三阶段剧本 kind（同 strategy.kind）。"""

    OPENING = "opening"
    MIDGAME = "midgame"
    LATEGAME = "lategame"


# ---------------------------------------------------------------------------
# 来源优先级（数字越大越高）
# ---------------------------------------------------------------------------

ISSUED_BY_PRIORITY: dict[IssuedBy, int] = {
    IssuedBy.VOICE: 100,
    IssuedBy.AUTO_TRANSITION: 50,
    IssuedBy.ABORT: 80,
    IssuedBy.BOT_INTERNAL: 10,
}


def issued_by_priority(src: IssuedBy) -> int:
    """返回 IssuedBy 数字优先级（仅用于冲突仲裁，不参与 directive.priority 字段）。"""
    return ISSUED_BY_PRIORITY[src]


# ---------------------------------------------------------------------------
# 视野类 directive 不限频，identify 一下
# ---------------------------------------------------------------------------

VIEW_DIRECTIVE_TYPES: frozenset[DirectiveType] = frozenset(
    {
        DirectiveType.VIEW_MOVE,
        DirectiveType.VIEW_FOLLOW,
        DirectiveType.VIEW_ZOOM,
    }
)


def is_view_directive(t: DirectiveType) -> bool:
    """视野类 directive 不进 Board（独立流），见设计文档 §2.1。"""
    return t in VIEW_DIRECTIVE_TYPES
