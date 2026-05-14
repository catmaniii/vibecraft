"""Directive envelope 模型 + 每个 type 的 payload schema。

设计取舍：payload 用 discriminated union（discriminator='type'），让
单个 `Directive` 类型既能 ser/de，也能在 Python 内类型收窄。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Discriminator, Field

from voicecraft.directives.scope import ScopeSpec, Selector, TargetSpec
from voicecraft.directives.task import Task
from voicecraft.directives.types import DirectiveType, IssuedBy

# =========================================================================
# Payload models（每个 directive type 一个）
# =========================================================================


class _PayloadBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrategySetPayload(_PayloadBase):
    """切换某个阶段的剧本（设计文档 §8.1 A）。"""

    type: Literal[DirectiveType.STRATEGY_SET] = DirectiveType.STRATEGY_SET
    stage: Literal["opening", "midgame", "lategame"]
    strategy_id: str


class ProductionOverridePayload(_PayloadBase):
    """中粒度：override 当前剧本的生产计划。

    `unit_type` 指出生哪个兵种，`count` 数量，可选 `building_tag` 指定建筑。
    若设 `building_tag`，相当于 §8.1 D 的"这 Robo 改造 X"。
    """

    type: Literal[DirectiveType.PRODUCTION_OVERRIDE] = DirectiveType.PRODUCTION_OVERRIDE
    unit_type: str
    count: int = 1
    building_tag: int | None = None
    building_selector: Selector | None = None
    priority: int = 50


class TechOverridePayload(_PayloadBase):
    """优先研究某科技。"""

    type: Literal[DirectiveType.TECH_OVERRIDE] = DirectiveType.TECH_OVERRIDE
    upgrade_id: str
    building_tag: int | None = None
    priority: int = 50


class ExpansionOverridePayload(_PayloadBase):
    type: Literal[DirectiveType.EXPANSION_OVERRIDE] = DirectiveType.EXPANSION_OVERRIDE
    target_count: int = Field(description="期望分基数")
    priority: int = 50


class EngagementConstraintPayload(_PayloadBase):
    """全局交战策略：`守家` / `不要出门` / `撤退到家`。"""

    type: Literal[DirectiveType.ENGAGEMENT_CONSTRAINT] = DirectiveType.ENGAGEMENT_CONSTRAINT
    stance: Literal["defend", "hold", "retreat", "free"]
    rally_point: TargetSpec | None = None


class UnitClaimPayload(_PayloadBase):
    """临时或持久占住一组单位，让它们按 Task 执行。"""

    type: Literal[DirectiveType.UNIT_CLAIM] = DirectiveType.UNIT_CLAIM
    selector: Selector
    task: Task


class ScoutPayload(_PayloadBase):
    type: Literal[DirectiveType.SCOUT] = DirectiveType.SCOUT
    target: TargetSpec
    selector: Selector | None = None  # 没给则 bot 自选 idle probe


class MovePayload(_PayloadBase):
    type: Literal[DirectiveType.MOVE] = DirectiveType.MOVE
    selector: Selector
    target: TargetSpec


class BuildAtPayload(_PayloadBase):
    """指定位置建造某建筑 / 单位。"""

    type: Literal[DirectiveType.BUILD_AT] = DirectiveType.BUILD_AT
    structure_type: str
    point: tuple[float, float]


class UnitReleasePayload(_PayloadBase):
    """归还 claim。"""

    type: Literal[DirectiveType.UNIT_RELEASE] = DirectiveType.UNIT_RELEASE
    selector: Selector
    return_to_role: Literal["IDLE", "ARMY"] = "IDLE"


class ViewMovePayload(_PayloadBase):
    type: Literal[DirectiveType.VIEW_MOVE] = DirectiveType.VIEW_MOVE
    target_point: tuple[float, float]


class ViewFollowPayload(_PayloadBase):
    type: Literal[DirectiveType.VIEW_FOLLOW] = DirectiveType.VIEW_FOLLOW
    unit_tag: int


class ViewZoomPayload(_PayloadBase):
    type: Literal[DirectiveType.VIEW_ZOOM] = DirectiveType.VIEW_ZOOM
    level: float = Field(ge=0.1, le=2.0)


Payload = Annotated[
    StrategySetPayload
    | ProductionOverridePayload
    | TechOverridePayload
    | ExpansionOverridePayload
    | EngagementConstraintPayload
    | UnitClaimPayload
    | ScoutPayload
    | MovePayload
    | BuildAtPayload
    | UnitReleasePayload
    | ViewMovePayload
    | ViewFollowPayload
    | ViewZoomPayload,
    Discriminator("type"),
]


# type 值 → payload 模型类。供 IntentParser 在系统边界过滤 LLM 输出：
# LLM 可能在 payload 里塞 schema 外字段，按 model_fields 白名单过滤，
# 避免 _PayloadBase 的 extra=forbid 把整条 directive 拒掉。
_PAYLOAD_UNION = get_args(Payload)[0]
PAYLOAD_MODELS: dict[str, type[_PayloadBase]] = {
    m.model_fields["type"].default.value: m for m in get_args(_PAYLOAD_UNION)
}


# =========================================================================
# Directive envelope
# =========================================================================


def _gen_id() -> str:
    return f"d_{uuid.uuid4().hex[:6]}"


class Directive(BaseModel):
    """通用 envelope（设计文档 §5.2 通用字段）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_gen_id)
    payload: Payload
    issued_at: float = Field(description="收到时的游戏内秒")
    effective_at: float | None = Field(
        default=None,
        description="commit 起效时刻；None 时 Board 入队即填充 = issued_at + 1.5",
    )
    scope: ScopeSpec = Field(default_factory=ScopeSpec)
    priority: int = Field(default=50, ge=0, le=100)
    issued_by: IssuedBy = IssuedBy.VOICE
    source_text: str | None = Field(
        default=None,
        description="玩家原话（仅记录，不参与执行）",
    )

    @property
    def type(self) -> DirectiveType:
        """便捷访问 payload.type。"""
        return self.payload.type

    def is_view(self) -> bool:
        from voicecraft.directives.types import is_view_directive

        return is_view_directive(self.type)
