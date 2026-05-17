"""Directive 类型枚举。对应设计文档 §5.2。"""

from __future__ import annotations

from enum import Enum


class DirectiveType(str, Enum):
    """所有 directive type 的统一枚举。

    粒度分四档（设计文档 §8.1）：
    - 大略 (剧本): STRATEGY_SET
    - 中略 (全局调参): PRODUCTION_* / TECH_* / EXPANSION_* / ENGAGEMENT_*
    - 微粒度 (单位): UNIT_CLAIM / SCOUT / MOVE / UNIT_RELEASE
    - 微粒度 (建筑): BUILD_AT / PRODUCTION_OVERRIDE (带 building_tag) / STRUCTURE_OVERRIDE
    """

    # 剧本切换
    STRATEGY_SET = "strategy_set"
    # 剧本取消(玩家 voice "取消当前剧本"/"停下"):清掉 board slot,bot 降级 sustain
    STRATEGY_CANCEL = "strategy_cancel"

    # 中粒度 override
    PRODUCTION_OVERRIDE = "production_override"
    TECH_OVERRIDE = "tech_override"
    EXPANSION_OVERRIDE = "expansion_override"
    STRUCTURE_OVERRIDE = "structure_override"
    ENGAGEMENT_CONSTRAINT = "engagement_constraint"

    # 战术目标（L2 中粒度，跨单位的战术指令）
    TACTICAL_OBJECTIVE = "tactical_objective"

    # 微粒度单位
    UNIT_CLAIM = "unit_claim"
    SCOUT = "scout"
    MOVE = "move"
    BUILD_AT = "build_at"

    # 释放
    UNIT_RELEASE = "unit_release"

    # 注：视野控制 directive 已删除（2026-05-17）。视角切换由 PWA 小地图拖拽产生的
    # WS frame `view_move` 直送 bot.facade.move_camera，不走 directive 系统。


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
