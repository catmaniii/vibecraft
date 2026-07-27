"""Selector / TargetSpec / ScopeSpec / ClaimRecord。

ScopeSpec 决定一条 overlay 的生命周期：是临时的还是 standing order。
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ★ 编队上限的唯一旋钮：以后要改编队总数，只改这一行 ★
# 不做成界面控件（玩家不需要改），就是个代码常量，方便我们后续调整。
DEFAULT_MAX_VOICE_GROUPS = 5

# 编队数硬上限：再怎么调也不超过这个（防误配成天文数字撑爆 UI / 占位符）。
MAX_VOICE_GROUPS_LIMIT = 9

# 运行时单一真相源（初值 = 上面的默认）：
#   ParserConfig.max_voice_groups → set_max_voice_groups() → 此模块全局
#   → schema 校验(check_group_id_range) / Director 快照 / LLM 提示词 都读它。
# 玩家说的队号超出 1-N 必须报错，不静默 clamp。
MAX_VOICE_GROUPS = DEFAULT_MAX_VOICE_GROUPS


def set_max_voice_groups(n: int) -> None:
    """运行时设置编队上限（由 IntentParser 从 ParserConfig.max_voice_groups 应用）。

    1 ≤ n ≤ MAX_VOICE_GROUPS_LIMIT；越界抛 ValueError（配置错误应尽早暴露）。
    """
    global MAX_VOICE_GROUPS
    if not (1 <= n <= MAX_VOICE_GROUPS_LIMIT):
        raise ValueError(f"max_voice_groups 必须在 1-{MAX_VOICE_GROUPS_LIMIT} 之间，收到 {n}")
    MAX_VOICE_GROUPS = n


def check_group_id_range(v: int | None) -> int | None:
    """编队号范围校验：1-MAX_VOICE_GROUPS，越界给中文友好报错（None 透传，用于 optional 字段）。"""
    if v is not None and not (1 <= v <= MAX_VOICE_GROUPS):
        raise ValueError(
            f"编队号只能是 1-{MAX_VOICE_GROUPS}（最多 {MAX_VOICE_GROUPS} 个编队），收到 {v}"
        )
    return v


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
    # 2026-05-24 用户:"一个农民占瞭望塔" → count=1。selector 只有 unit_type 时
    # 不限 count 会把所有同类单位锁住(60 个农民全 Reserved → 不采气)。
    # LLM 应根据玩家"一个/N 个"填这个;None = 不限(适用于"所有凤凰持续巡逻")。
    count: int | None = Field(
        default=None,
        description="选 N 个匹配单位(None=全部)。玩家说'一个农民' → 1。",
        ge=1,
    )
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
    assigned_spot: str | None = Field(
        default=None,
        description=(
            "重选「正在守某地点的单位」：该单位被指派去守的 named_spot 标签"
            "（如 watchtower / watchtower_left）。Director 按指派时记下的语意匹配回那个"
            "单位的 tag。玩家说『守瞭望塔的追猎』『守 7 点那个叉子』时填这个 + unit_type。"
        ),
    )
    group_id: int | None = Field(
        default=None,
        description="语音编队 1-5；指挥某队时填，Director 解析为该队 tags。越界(0/6+)报错",
    )
    health_below_pct: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description=(
            "只选血量百分比低于此值的单位（残血/受伤）。"
            "玩家说'残血的/受伤的追猎'→填，如 50。与 unit_type/group_id 等 AND。"
        ),
    )
    shield_below_pct: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description=(
            "只选护盾百分比低于此值的单位（盾破/盾没了，神族）。玩家说'盾破的不朽'→填，如 20。"
        ),
    )
    position: Literal["forward", "back"] | None = Field(
        default=None,
        description=(
            "按**物理位置**选（不是按指派/任务）：forward=最靠前/离敌最近的，"
            "back=最靠后/离敌最远的。玩家说'前线那个追猎''最前面的叉子''后面那个' → "
            "填 position + unit_type + count。与 assigned_spot(按指派地点)不同：position 按"
            "单位**当前实际位置**离敌方主基地远近排序选。"
        ),
    )

    _check_group_id = field_validator("group_id")(check_group_id_range)
    chain_id: str | None = Field(
        default=None,
        description=(
            "连续指令任务链 id（一条链的 hash/短名）。同一链的多条 directive 用同一个 "
            "chain_id 绑定到同一个单位：第一步带具体 selector(unit_type/count)，Director "
            "解析后绑定 chain_id→tags；后续步骤只带 chain_id，解析回同一 tags（同一农民接力）。"
        ),
    )
    near_camera: bool = Field(
        default=False,
        description=(
            "True=只选下达那刻镜头视口矩形框(±12×±9格)内的匹配单位/建筑。"
            "Director submit 时一次性固化成具体 tags 写回 selector.tags、清 near_camera。"
            "必须与 unit_type 或 role 之一同时填写（否则守卫报错）。"
        ),
    )

    @model_validator(mode="after")
    def _check_near_camera(self) -> Selector:
        """near_camera=True 必须同时有 unit_type 或 role，防裸框选语义模糊。"""
        if self.near_camera and not (self.unit_type or self.role):
            raise ValueError(
                "near_camera=True 时必须同时指定 unit_type 或 role"
                "（防裸框选把镜头内所有己方单位框进来，语义模糊）"
            )
        return self


# ---------------------------------------------------------------------------
# TargetSpec：directive 的目标对象（位置 / 单位 / 建筑 / 抽象 spot）
# ---------------------------------------------------------------------------


class TargetKind(str, Enum):
    POINT = "point"
    UNIT_TAG = "unit_tag"
    BUILDING_TAG = "building_tag"
    NAMED_SPOT = "named_spot"  # 例如 "natural_choke" / "main_ramp" / "enemy_natural"
    UNIT_TYPE = "unit_type"  # 例如 "Probe"（agnostic 任意此类单位）
    CAMERA = "camera"  # 说话那刻镜头中心（"这里" / "这边"）
    SELF = "self"  # 自施法（就地）：寡妇雷埋地 / 单位自身潜伏 / cloak 等无外部目标的技能
    # 执行层 facade.cast_ability_on_units 对 self 直接 u(ability)（就地施法）。
    # 2026-06-17 修：LLM 给 burrow 类自施法本就吐 kind="self"，但枚举漏了 → schema
    # 校验失败 → 整条命令丢 = 玩家"地雷埋一下/埋到地上"看到"识别失败"。补上即修。


class TargetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TargetKind
    point: tuple[float, float] | None = None
    unit_tag: int | None = None
    building_tag: int | None = None
    named_spot: str | None = None
    unit_type: str | None = None
    waypoints: list[TargetSpec] | None = Field(
        default=None,
        description="巡逻两点 [A,B]；patrol verb 时填，每个仍是 TargetSpec",
    )


TargetSpec.model_rebuild()


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
