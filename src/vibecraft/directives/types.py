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

    # L4 复合空投（2026-05-23）
    DROP_ACT = "drop_act"

    # 视野跟随（2026-05-30）：镜头持续跟随某个单位；persistent，玩家 × 解除
    VIEW_FOLLOW = "view_follow"

    # 产能封锁（2026-05-30）：暂停造某种兵；persistent，玩家 × 解除
    PRODUCTION_BLOCK = "production_block"

    # 语音编队（2026-06-01）：把单位编入/清除 1-5 编队
    GROUP_ASSIGN = "group_assign"
    GROUP_CLEAR = "group_clear"

    # 出兵集结点（2026-06-07）：玩家设全局集结点，之后新出的兵自动 rally 到该点。
    # persistent 全局状态(覆盖 sharpy 默认 gather_point)，玩家 × 或重设才变。
    # 不占单位控制权(只管"未来新兵去哪"，不 claim 现有兵)。
    RALLY_POINT = "rally_point"

    # 偷矿（2026-06-10）：玩家指定地图一片区域，bot 在那偷偷开隐蔽基地自给自足采矿。
    # StealthCellManager 负责状态机驱动（PENDING→BUILDING→MINING→RELEASED/DESTROYED）。
    # 与代理建造的区别：持续运营的隔离经济单元，农民就地自产、受击交还 bot。
    STEALTH_MINE = "stealth_mine"

    # 通用建筑回收（2026-06-19）：对选中建筑下 salvage ability（地堡/感应塔等）。
    # 一次性动作，不占 Reserved；按建筑 type_id 自动映射对应 ability，不可回收→友好拒绝。
    SALVAGE = "salvage"

    # 地堡货舱控制（2026-06-19）：装兵进地堡（load）/ 卸出所有兵（unload）。
    # 一次性动作，不占 Reserved。load 时找最近的 Marine 进入；unload 走 UNLOADALL_BUNKER。
    BUNKER_CARGO = "bunker_cargo"

    # 通用维修指令（2026-06-19）：派 N 个农民持续维修目标单位/建筑。
    # 持续型：每 tick 派 SCV 修目标，所有目标满血/消失后自动完成。
    # 仅人族有效（SCV 才能 repair）；虫族/神族单位不能被 SCV 修理。
    REPAIR = "repair"

    # 人族建筑起飞/飞行/降落（2026-07-08）：主基地(CommandCenter/OrbitalCommand)
    # 起飞悬停，或飞到另一个 named_spot 降落。PlanetaryFortress 不能起飞（真机核对）。
    # 持续型：director 状态机每 tick 推进 LIFT→FLY→LAND，落地/悬停后自动完成。
    STRUCTURE_MOVE = "structure_move"

    # 农民基地调度（2026-07-08）：某基地的农民持续优先采水晶/气（复用全局
    # set_mining_priority），或把某基地全部采矿农民一次性转移去另一个基地采矿。
    WORKER_TASK = "worker_task"

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
