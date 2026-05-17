"""Directive envelope 模型 + 每个 type 的 payload schema。

设计取舍：payload 用 discriminated union（discriminator='type'），让
单个 `Directive` 类型既能 ser/de，也能在 Python 内类型收窄。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Discriminator, Field

from vibecraft.directives.scope import ScopeSpec, Selector, TargetSpec
from vibecraft.directives.task import Task
from vibecraft.directives.types import DirectiveType, IssuedBy

# =========================================================================
# DoneWhen discriminated union（8 kind + 2 复合）
# =========================================================================


class UnitCountBuiltSince(BaseModel):
    """某兵种产量达到阈值（自 directive 下达以来）。"""

    kind: Literal["unit_count_built_since"]
    unit_type: str
    op: Literal[">=", "<=", "==", ">", "<"]
    value: int


class TechDone(BaseModel):
    """升级/科技研究完成。"""

    kind: Literal["tech_done"]
    upgrade_id: str


class ExpansionCount(BaseModel):
    """己方分基数量满足条件。"""

    kind: Literal["expansion_count"]
    op: Literal[">=", "<=", "==", ">", "<"]
    value: int


class TargetDestroyed(BaseModel):
    """目标建筑/单位被摧毁。"""

    kind: Literal["target_destroyed"]
    target_kind: Literal["natural", "third", "main", "building_at", "unit_type"]
    target_param: str | None = None
    area: str | None = None  # 可选，e.g. "enemy_natural"


class OwnArmySizeRatio(BaseModel):
    """己方军队规模比例满足条件（相对于满编）。"""

    kind: Literal["own_army_size_ratio"]
    op: Literal[">=", "<=", "==", ">", "<"]
    value: float


class VisionAcquired(BaseModel):
    """在指定区域保持视野 N 秒。"""

    kind: Literal["vision_acquired"]
    area: str  # named_spot
    hold_seconds: float


class EnemyKilledInArea(BaseModel):
    """在指定区域击杀敌方单位数量满足条件。"""

    kind: Literal["enemy_killed_in_area"]
    area: str
    unit_type: str
    op: Literal[">=", "<=", "==", ">", "<"]
    value: int


class TimeElapsedSince(BaseModel):
    """自某时间点起经过 N 秒。"""

    kind: Literal["time_elapsed_since"]
    seconds: float
    ref: Literal["directive_issued", "game_start"] = "directive_issued"


# ---------------------------------------------------------------------------
# P0d L4 done_when 扩词表（运营类指令）
# ---------------------------------------------------------------------------

_OP = Literal[">=", "<=", "==", ">", "<"]


class StructureCount(BaseModel):
    """当前建筑存量（含 pending）。区别于 unit_count_built_since（增量）。"""

    kind: Literal["structure_count"]
    structure_type: str
    op: _OP
    value: int


class OwnUnitCount(BaseModel):
    """己方某兵种当前存量（含 pending）。"""

    kind: Literal["own_unit_count"]
    unit_type: str
    op: _OP
    value: int


class SupplyUsed(BaseModel):
    """当前人口已用。"""

    kind: Literal["supply_used"]
    op: _OP
    value: int


class SupplyCap(BaseModel):
    """当前人口上限。"""

    kind: Literal["supply_cap"]
    op: _OP
    value: int


class Minerals(BaseModel):
    """当前晶矿。"""

    kind: Literal["minerals"]
    op: _OP
    value: int


class Gas(BaseModel):
    """当前瓦斯。"""

    kind: Literal["gas"]
    op: _OP
    value: int


class WorkerCount(BaseModel):
    """当前工人数。"""

    kind: Literal["worker_count"]
    op: _OP
    value: int


class AnyOf(BaseModel):
    """复合：任意一个子条件满足即完成。"""

    kind: Literal["any_of"]
    conditions: list[DoneWhen]  # forward ref


class AllOf(BaseModel):
    """复合：所有子条件都满足才完成。"""

    kind: Literal["all_of"]
    conditions: list[DoneWhen]


DoneWhen = Annotated[
    UnitCountBuiltSince | TechDone | ExpansionCount | TargetDestroyed
    | OwnArmySizeRatio | VisionAcquired | EnemyKilledInArea | TimeElapsedSince
    | StructureCount | OwnUnitCount | SupplyUsed | SupplyCap
    | Minerals | Gas | WorkerCount
    | AnyOf | AllOf,
    Field(discriminator="kind"),
]

# 解决 forward ref（AnyOf/AllOf 嵌套 DoneWhen）
AnyOf.model_rebuild()
AllOf.model_rebuild()


# =========================================================================
# TacticalVerb
# =========================================================================

TacticalVerb = Literal[
    "attack",
    "defend",
    "scout",
    "expand",
    "harass",
    "drop",
    "vision",
    "raze",
    "retreat",
    "regroup",
    "split",
]


# =========================================================================
# Payload models（每个 directive type 一个）
# =========================================================================


class _PayloadBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # P3 新增（可选；L1 STRATEGY_SET 通常保持 None）：
    done_when: DoneWhen | None = None
    timeout_s: int | None = None


class StrategySetPayload(_PayloadBase):
    """切换某个阶段的剧本（设计文档 §8.1 A）。"""

    type: Literal[DirectiveType.STRATEGY_SET] = DirectiveType.STRATEGY_SET
    stage: Literal["opening", "midgame", "lategame"]
    strategy_id: str


class StrategyCancelPayload(_PayloadBase):
    """取消(单/全)阶段剧本:清掉 board slot,bot 降级 sustain plan(不主动出门)。

    stage="all" 取消所有阶段,bot 完全停在 sustain 模式等下个指令。
    """

    type: Literal[DirectiveType.STRATEGY_CANCEL] = DirectiveType.STRATEGY_CANCEL
    stage: Literal["opening", "midgame", "lategame", "all"] = "all"


class ProductionItem(BaseModel):
    """单个出兵需求：unit_type + count。production_override.items 元素。"""

    model_config = ConfigDict(extra="forbid")

    unit_type: str
    count: int = 1


class ProductionOverridePayload(_PayloadBase):
    """中粒度：override 当前剧本的生产计划。

    `items` 是出兵需求列表，**一条 directive 可含多兵种**（一句话「出 2 个叉子加
    3 个追猎」=>一条 directive 两个 item）。可选 `building_tag` 指定建筑，
    相当于 §8.1 D 的"这 Robo 改造 X"。
    """

    type: Literal[DirectiveType.PRODUCTION_OVERRIDE] = DirectiveType.PRODUCTION_OVERRIDE
    items: list[ProductionItem] = Field(min_length=1)
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


class StructureItem(BaseModel):
    """单个建筑需求：structure_type + target_count + 可选 location_hint。

    structure_override.items 元素。location_hint 在 item 级（不同建筑可以放
    不同位置 —— "二矿放 2 PY 1 BF" 中 PY 和 BF 可以同 hint）。
    """

    model_config = ConfigDict(extra="forbid")

    structure_type: str
    target_count: int = Field(ge=1)
    location_hint: str | None = None


class StructureOverridePayload(_PayloadBase):
    """L4 建筑数量目标。**一条 directive 可含多建筑**（一句话"二矿放 2 PY 1 BF"
    = 一条 directive 两个 item，作为单卡跟踪、全部完成才消失）。

    一次性：达成 target_count 就 done，被打掉不自动补
    （MVP 决策，参见 design doc §2 边界 case）。
    location_hint 在 item 级：main / natural / ramp / front / None（None = bot 自选）。
    """

    type: Literal[DirectiveType.STRUCTURE_OVERRIDE] = DirectiveType.STRUCTURE_OVERRIDE
    items: list[StructureItem] = Field(min_length=1)
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
    # P1.1 新增：true 进 Director.standing_orders（L3 standing order），false 一次性
    persistent: bool = False


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


class TacticalObjectivePayload(_PayloadBase):
    """L2 战术指令：跨单位的中粒度战术目标（设计文档 §8.1 L2）。"""

    type: Literal[DirectiveType.TACTICAL_OBJECTIVE] = DirectiveType.TACTICAL_OBJECTIVE
    verb: TacticalVerb
    target_area: str | tuple[float, float] | None = None  # named_spot 或坐标
    unit_count_hint: int | None = None  # None = bot 自决
    unit_type_hint: list[str] | None = None  # None = bot 自决
    priority: int = 50


Payload = Annotated[
    StrategySetPayload
    | StrategyCancelPayload
    | ProductionOverridePayload
    | TechOverridePayload
    | ExpansionOverridePayload
    | StructureOverridePayload  # NEW: P0e Task 8
    | EngagementConstraintPayload
    | TacticalObjectivePayload
    | UnitClaimPayload
    | ScoutPayload
    | MovePayload
    | BuildAtPayload
    | UnitReleasePayload,
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
