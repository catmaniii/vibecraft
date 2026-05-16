"""Selector / TargetSpec / ScopeSpec / ClaimRecord。

ScopeSpec 决定一条 overlay 的生命周期：是临时的还是 standing order。
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

# 给 ClaimRecord.is_expired 用：传入 DSL 字符串、返回 bool。
# 在 ClaimRecord 之前定义，避免 forward-ref。
ClaimUntilEvaluator: TypeAlias = Callable[[str], bool]


class ScopeKind(str, Enum):
    """overlay 的"作用域"——多久过期。"""

    EPHEMERAL = "ephemeral"  # 默认：一次执行完成即失效
    UNTIL = "until"  # 直到某条件成立（DSL 表达式）
    PERSISTENT = "persistent"  # 直到玩家显式 release（standing order）
    DURATION = "duration"  # 固定秒数


class ScopeSpec(BaseModel):
    """directive 的作用域 / 生命周期。"""

    model_config = ConfigDict(extra="forbid")

    kind: ScopeKind = ScopeKind.EPHEMERAL
    until_expr: str | None = Field(
        default=None,
        description="kind=UNTIL 时使用，DSL 表达式（条件成立即释放）",
    )
    duration_s: float | None = Field(
        default=None,
        description="kind=DURATION 时使用",
    )

    def validate_for_kind(self) -> None:
        """各 kind 必填字段校验。"""
        if self.kind == ScopeKind.UNTIL and self.until_expr is None:
            raise ValueError("ScopeKind.UNTIL 需要 until_expr")
        if self.kind == ScopeKind.DURATION and self.duration_s is None:
            raise ValueError("ScopeKind.DURATION 需要 duration_s")


# ---------------------------------------------------------------------------
# Selector：directive 作用对象选择器
# ---------------------------------------------------------------------------


class Selector(BaseModel):
    """选择若干单位 / 建筑作为 directive 的目标。

    各字段都是 optional；多字段同时填即 AND。
    全部为空 = "全部 own units"，调用方应在业务层禁止这种用法。
    """

    model_config = ConfigDict(extra="forbid")

    tag: int | None = Field(default=None, description="单个 unit tag（精确匹配）")
    tags: list[int] | None = Field(default=None, description="一组 tag")
    unit_type: str | None = Field(
        default=None,
        description="单位类型名（normalize 后的 protoss id），如 'Stalker'",
    )
    role: Literal["LLM_CONTROLLED", "IDLE", "ARMY", "ANY"] | None = None
    claimed: bool | None = Field(
        default=None,
        description="True 仅选已被 claim 的；False 仅选 free 的；None 不过滤",
    )
    near_point: tuple[float, float] | None = Field(
        default=None,
        description="坐标周围（与 near_radius 联合）",
    )
    near_radius: float | None = None
    primary_verb_prefix: str | None = Field(
        default=None,
        description="选择 primary_action.verb 前缀为某串的（如 'hold_'）",
    )


# ---------------------------------------------------------------------------
# TargetSpec：directive 的目标对象（位置 / 单位 / 建筑 / 抽象 spot）
# ---------------------------------------------------------------------------


class TargetKind(str, Enum):
    POINT = "point"
    UNIT_TAG = "unit_tag"
    BUILDING_TAG = "building_tag"
    NAMED_SPOT = "named_spot"  # 例如 "natural_choke" / "main_ramp" / "enemy_natural"
    UNIT_TYPE = "unit_type"  # 例如 "Probe"（agnostic 任意此类单位）


class TargetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TargetKind
    point: tuple[float, float] | None = None
    unit_tag: int | None = None
    building_tag: int | None = None
    named_spot: str | None = None
    unit_type: str | None = None


# ---------------------------------------------------------------------------
# ClaimRecord：单位互斥占用账本
# ---------------------------------------------------------------------------


class ClaimRecord(BaseModel):
    """一条 unit_claim 的运行时状态。"""

    model_config = ConfigDict(extra="forbid")

    unit_tag: int
    directive_id: str
    claimed_at: float
    scope: ScopeSpec
    expires_at: float | None = Field(
        default=None,
        description="按 scope.kind 计算的预期过期时刻（DURATION 必填）；UNTIL 由 evaluator 决定",
    )

    def is_expired(
        self,
        now: float,
        until_evaluator: ClaimUntilEvaluator | None = None,
    ) -> bool:
        """若 scope.kind==UNTIL 且需要 evaluator 求值时由调用方传入。"""
        if self.scope.kind == ScopeKind.PERSISTENT:
            return False
        if self.scope.kind == ScopeKind.DURATION:
            assert self.expires_at is not None
            return now >= self.expires_at
        if self.scope.kind == ScopeKind.EPHEMERAL:
            # ephemeral 由业务层在动作完成后显式 release；此处不主动过期
            return False
        if self.scope.kind == ScopeKind.UNTIL:
            if until_evaluator is None or self.scope.until_expr is None:
                return False
            return until_evaluator(self.scope.until_expr)
        # 未覆盖的 scope kind（不应发生）
        return False  # type: ignore[unreachable]
