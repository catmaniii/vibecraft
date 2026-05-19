"""三种剧本 kind 的 pydantic 模型 + Phase / BuildStep 子模型。

schema 对齐设计文档 §4.2。
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, model_validator


class StrategyKind(str, Enum):
    OPENING = "opening_build"
    MIDGAME = "midgame_stance"  # 过渡期保留，下一步迁移后删
    LATEGAME = "lategame_doctrine"  # 过渡期保留，下一步迁移后删
    PERSISTENT = "persistent_doctrine"  # 2026-05-19 两层架构新增


# =========================================================================
# 共用子模型
# =========================================================================


class Phase(BaseModel):
    """剧本的阶段标识，UI 用 phase stepper 渲染。

    `start_at_supply` / `start_at_time` 任一非 None 时,PhaseTracker 用阈值
    推断 current phase(已完成的 ✓ / 当前的 ▶ / 未来的 ○)。两者皆 None 时
    该 phase 无法被推断为"已开始",停在前一个 phase 上。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    display: str
    subtitle: str = ""
    start_at_supply: int | None = None
    start_at_time: float | None = None  # 游戏内秒


# Build step 紧凑三段式："<supply> <verb> <object> [@modifier]"
_BUILD_STEP_RE = re.compile(
    r"^(?P<supply>\d+)\s+(?P<verb>build|train|research|send_probe)\s+"
    r"(?P<obj>[A-Za-z0-9_一-鿿]+)(?:\s+@(?P<modifier>[A-Za-z0-9_]+))?\s*$"
)


class BuildStep(BaseModel):
    """opening_build.steps 的单步。"""

    model_config = ConfigDict(extra="forbid")

    supply: int = Field(ge=1)
    verb: Literal["build", "train", "research", "send_probe"]
    obj: str
    modifier: str | None = None

    @classmethod
    def parse(cls, raw: str) -> BuildStep:
        m = _BUILD_STEP_RE.match(raw)
        if not m:
            raise ValueError(f"build step 格式非法: {raw!r}")
        return cls(
            supply=int(m.group("supply")),
            verb=m.group("verb"),  # type: ignore[arg-type]
            obj=m.group("obj"),
            modifier=m.group("modifier"),
        )


class AbortSignal(BaseModel):
    """opening_build.abort_signals 的一条。"""

    model_config = ConfigDict(extra="forbid")

    sees: str  # condition DSL
    then: str  # transition:<strategy_id>


class DefaultTransition(BaseModel):
    """opening → midgame 的默认接续。"""

    model_config = ConfigDict(extra="forbid")

    midgame_id: str
    when: str  # condition DSL 或 "default"


class LategameTransition(BaseModel):
    """midgame → lategame 的默认接续。"""

    model_config = ConfigDict(extra="forbid")

    lategame_id: str
    when: str


class AttackWindow(BaseModel):
    """midgame_stance.attack_window —— bot 发起进攻的时间窗。"""

    model_config = ConfigDict(extra="forbid")

    open_at: str = Field(description="M:SS 字符串")
    close_at: str
    target_priority: list[str]


class EngagementDoctrineRule(BaseModel):
    """lategame_doctrine.engagement_doctrine 的一条。

    M0：仅保留原文，evaluator 暂不解析。后续单测会断言 list 完整 ser/de。
    """

    model_config = ConfigDict(extra="forbid")

    raw: str


# =========================================================================
# OpeningCompletion (2026-05-19 两层架构：opening 完成判定)
# =========================================================================


class OpeningCompletion(BaseModel):
    """开局完成条件 —— goal 或 timeout 任一触发即完成 (Q1 选 C)。

    完成后 Director 自动调用 pick_best_persistent() 切到持续运营 doctrine。
    """

    model_config = ConfigDict(extra="forbid")

    timeout_s: float = Field(
        gt=0,
        description="兜底时间（游戏内秒），超时强制完成无论 goal 是否满足",
    )
    goal_when: dict[str, Any] | None = Field(
        default=None,
        description=(
            "完成判定（done_when DSL dict）；None = 仅靠 timeout。"
            "schema 同 directives/done_when.py 的 evaluator，运行时复用 evaluator。"
        ),
    )


# =========================================================================
# OpeningBuild
# =========================================================================


class OpeningBuild(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[StrategyKind.OPENING] = StrategyKind.OPENING
    id: str
    display_name_zh: str
    summary_zh: str = ""
    aliases: list[str] = Field(default_factory=list)
    matchup: list[str] = Field(default_factory=list)
    phases: list[Phase]
    steps: list[str]  # 原文保留，需要时 BuildStep.parse() 解析
    scout_at: str | None = None
    abort_signals: list[AbortSignal] = Field(default_factory=list)
    default_transitions: list[DefaultTransition] = Field(default_factory=list)
    # M2+M3: sharpy dummy bot class（格式 "module:ClassName"，相对于 vendor/sharpy/dummies/）
    # 非 None 时 create_plan() 会从该 dummy 拉 BuildOrder，接入 IfElse 路由树。
    sharpy_dummy_class: str | None = None
    # 2026-05-19 两层架构：开局完成条件（None = 过渡期 yaml 暂未迁移，仍按旧行为）
    # 完成后 Director 触发 pick_best_persistent + phase 切换。
    completion: OpeningCompletion | None = None

    @model_validator(mode="after")
    def _validate_steps_parseable(self) -> OpeningBuild:
        for s in self.steps:
            BuildStep.parse(s)
        return self

    def parsed_steps(self) -> list[BuildStep]:
        return [BuildStep.parse(s) for s in self.steps]


# =========================================================================
# MidgameStance
# =========================================================================


class MidgameStance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[StrategyKind.MIDGAME] = StrategyKind.MIDGAME
    id: str
    display_name_zh: str
    summary_zh: str = ""
    aliases: list[str] = Field(default_factory=list)
    enter_when: list[str] = Field(default_factory=list)
    commitments: dict[str, dict[str, int] | int | list[str]] = Field(default_factory=dict)
    attack_window: AttackWindow | None = None
    micro_doctrine: list[str] = Field(default_factory=list)
    expire_action: list[str] = Field(default_factory=list)
    lategame_transitions: list[LategameTransition] = Field(default_factory=list)
    # M2+M3: sharpy dummy class（格式 "module:ClassName"）—— 留 M4 commitments 注入时用
    sharpy_dummy_class: str | None = None


# =========================================================================
# LategameDoctrine（过渡期保留；P1 Step 5 yaml 迁移后删）
# =========================================================================


class LategameDoctrine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[StrategyKind.LATEGAME] = StrategyKind.LATEGAME
    id: str
    display_name_zh: str
    summary_zh: str = ""
    aliases: list[str] = Field(default_factory=list)
    target_composition: dict[str, int]
    required_tech: list[str] = Field(default_factory=list)
    required_structures: dict[str, int] = Field(default_factory=dict)
    engagement_doctrine: list[str] = Field(default_factory=list)
    # win_condition value 可以是 str（type / description）或 list[str]（signals 列表）
    win_condition: dict[str, str | list[str]] = Field(default_factory=dict)
    counters_against: list[str] = Field(default_factory=list)
    weak_against: list[str] = Field(default_factory=list)
    # 后期阶段进度（可选，PWA 显示用；lategame 不强制 supply 触发条件）
    phases: list[Phase] = Field(default_factory=list)
    # lategame 也允许 abort_signals（"该投降的时候投降" / warn 玩家硬转）；
    # then 支持 "warn:<reason>" / "transition:<strategy_id>" 两种 action
    abort_signals: list[AbortSignal] = Field(default_factory=list)
    # M2+M3: sharpy dummy class（格式 "module:ClassName"）—— 留 M4 lategame 注入时用
    sharpy_dummy_class: str | None = None


# =========================================================================
# PersistentDoctrine (2026-05-19 两层架构：持续运营 doctrine)
#
# 取代 LategameDoctrine + MidgameStance 的"中后期持续策略"概念。开局完成后
# Director 自动选最低 transition_cost 的 doctrine。玩家可任意手动切换。
#
# vs LategameDoctrine：
#   - 加 gas_intensity（high/medium/low）—— transition_cost 算 gas bottleneck 用
#   - 加 ramp_up_time_s —— 从切换到第一波目标组合的预期时间
#   - 不变量：active_recipe 进入 persistent 阶段后只能切别的 persistent，不能回 opening
# =========================================================================


class PersistentDoctrine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[StrategyKind.PERSISTENT] = StrategyKind.PERSISTENT
    id: str
    display_name_zh: str
    summary_zh: str = ""
    aliases: list[str] = Field(default_factory=list)
    # 目标兵力组合（unit_type -> 目标数量）；transition_cost.W_UNIT 算缺口
    target_composition: dict[str, int]
    # 必备升级（如 ProtossGroundWeapons / Charge / Stim）
    required_tech: list[str] = Field(default_factory=list)
    # 必备建筑 + 数量（含 prereq；如 {RoboticsFacility: 2, TwilightCouncil: 1}）
    required_structures: dict[str, int] = Field(default_factory=dict)
    engagement_doctrine: list[str] = Field(default_factory=list)
    win_condition: dict[str, str | list[str]] = Field(default_factory=dict)
    # 敌方组合 tag（canonical 集见 enemy_tags.py）—— counter / weak 影响 transition_cost
    counters_against: list[str] = Field(default_factory=list)
    weak_against: list[str] = Field(default_factory=list)
    phases: list[Phase] = Field(default_factory=list)
    abort_signals: list[AbortSignal] = Field(default_factory=list)
    sharpy_dummy_class: str | None = None
    # ---- 两层架构新字段 ----
    gas_intensity: Literal["low", "medium", "high"] = Field(
        default="medium",
        description=(
            "doctrine 的气矿需求强度。high = 双 VS / VR + Charge + Storm 都吃气;"
            "low = 主要烧矿（如 ling_bane_muta / marine_widow）。"
            "transition_cost 用它算 gas bottleneck penalty。"
        ),
    )
    ramp_up_time_s: float = Field(
        default=90.0,
        gt=0,
        description="从 doctrine 启动到产出第一波目标组合的预期时间（秒）",
    )


# =========================================================================
# Discriminated union（过渡期含 4 个 kind；P1 Step 5 后会删 MIDGAME / LATEGAME）
# =========================================================================

Strategy = Annotated[
    OpeningBuild | MidgameStance | LategameDoctrine | PersistentDoctrine,
    Discriminator("kind"),
]
