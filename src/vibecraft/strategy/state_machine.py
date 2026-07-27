"""Two-tier strategy state machine + 不变量 enforcement (P0 Step 4)。

设计 doc §5。Director 用本模块的 StrategyState + validate_transition 集中
管理策略状态变更，确保 4 个核心不变量：

1. current_strategy_id 永不为 None / "" / "sustain"
2. phase ∈ {"opening", "persistent"}
3. phase=opening 时 strategy 必须是 OpeningBuild kind
   phase=persistent 时 strategy 必须是 PersistentDoctrine kind
4. strategy 的种族必须匹配 my_race

设计意图（不变量 5 / Q3 锁定）：
   一旦 phase 从 opening → persistent 切换后，不可回退到 opening。
   玩家说"切回 4bg"会被拦下，Director 推送"已锁定持续阶段"提示。

本模块是**纯逻辑**：不依赖 facade / sharpy / sc2，便于单元测试。
Director 调 validate_transition() 决定是否接受变更，调 apply_transition()
执行状态更新。失败时返回 TransitionRejected 让 Director 决定 PWA 提示什么。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from vibecraft.strategy.library import StrategyLibrary

# 两层架构 phase
Phase = Literal["opening", "persistent"]


# =========================================================================
# State + Event
# =========================================================================


@dataclass
class StrategyState:
    """当前宏观策略状态（Director 持有一个实例）。

    不变量（由 validate_transition + apply_transition 维护）：
      - current_strategy_id 非空 / 不为 "sustain"
      - phase ∈ {"opening", "persistent"}
      - kind_of(current_strategy_id) 匹配 phase
      - race_of(current_strategy_id) == my_race
    """

    phase: Phase = "opening"
    current_strategy_id: str = ""
    opening_completed_at: float | None = None  # game_time；None 表示开局未完成

    def __post_init__(self) -> None:
        # 构造时如果未指定 current_strategy_id（如 game 启动前），允许临时空
        # 但首次 apply_transition 之前禁止其它代码读 current_strategy_id 当作 active
        pass


# =========================================================================
# Transition validation
# =========================================================================


@dataclass(frozen=True)
class TransitionAccepted:
    """变更通过校验，可执行。"""

    new_phase: Phase
    new_strategy_id: str
    # 是否触发 phase 切换（用于 PWA 推送）
    phase_changed: bool = False


@dataclass(frozen=True)
class TransitionRejected:
    """变更被拒绝，附拒绝原因。"""

    reason_code: (
        str  # "empty_id" / "sustain_forbidden" / "kind_mismatch" / "race_mismatch" / "phase_locked"
    )
    reason_zh: str  # 中文给玩家
    suggested_action: str | None = None  # 建议的下一步（如"用 PICK_PERSISTENT 切持续策略"）


TransitionResult = TransitionAccepted | TransitionRejected


def validate_transition(
    state: StrategyState,
    new_strategy_id: str,
    library: StrategyLibrary,
    my_race: str,
) -> TransitionResult:
    """校验把当前 state 切到 new_strategy_id 是否合法。

    不实际改 state（pure function），调用方拿到 Accepted 后才 apply_transition。

    校验顺序：
      1. new_id 非空 / 非 sustain
      2. id 在 library 注册
      3. kind 跟 phase 匹配（含 phase 锁定语义：persistent 时 set opening 拒绝）
      4. race 匹配
    """
    from vibecraft.strategy.models import StrategyKind

    # ---- 1. 非空 / 非 sustain ----
    if not new_strategy_id:
        return TransitionRejected(
            reason_code="empty_id",
            reason_zh="策略 id 不能为空",
        )
    if new_strategy_id.lower() == "sustain":
        return TransitionRejected(
            reason_code="sustain_forbidden",
            reason_zh="sustain 已废弃（两层架构禁止滞空状态）。请选 persistent doctrine。",
            suggested_action="使用 pick_best_persistent 自动选最低成本的 persistent doctrine",
        )

    # ---- 2. 注册检查 + race 匹配 ----
    kind = library.kind_of(new_strategy_id)
    if kind is None:
        return TransitionRejected(
            reason_code="unknown_id",
            reason_zh=f"未注册的策略 id: {new_strategy_id}",
        )

    sid_race = library.race_of(new_strategy_id)
    my_race_l = my_race.lower()
    if sid_race is not None and sid_race != my_race_l:
        return TransitionRejected(
            reason_code="race_mismatch",
            reason_zh=f"策略 {new_strategy_id} 属于 {sid_race}，当前种族 {my_race_l}",
        )

    # ---- 3. kind ↔ phase 匹配（含 phase 锁定语义）----
    # 规则:
    #   phase=opening 时:
    #     - 可切 OPENING（在开局阶段内自由换 opening）
    #     - 可切 PERSISTENT（提前进入持续阶段，强制 opening_completed）
    #   phase=persistent 时:
    #     - 拒绝 OPENING（Q3 锁定，不可回退）
    #     - 可切 PERSISTENT
    if state.phase == "persistent" and kind == StrategyKind.OPENING:
        return TransitionRejected(
            reason_code="phase_locked",
            reason_zh="已进入持续运营阶段，不可切回开局策略",
            suggested_action="切换其它 persistent doctrine 或保留当前",
        )

    # 不接受老的 MIDGAME / LATEGAME kind（过渡期清理后会从 library 移除）
    if kind not in (StrategyKind.OPENING, StrategyKind.PERSISTENT):
        return TransitionRejected(
            reason_code="kind_deprecated",
            reason_zh=f"策略 {new_strategy_id} 的 kind={kind.value} 已废弃；请用 opening_build 或 persistent_doctrine",
        )

    # ---- 计算结果 phase ----
    if kind == StrategyKind.OPENING:
        new_phase: Phase = "opening"
    else:
        new_phase = "persistent"
    phase_changed = new_phase != state.phase

    return TransitionAccepted(
        new_phase=new_phase,
        new_strategy_id=new_strategy_id,
        phase_changed=phase_changed,
    )


def apply_transition(state: StrategyState, accepted: TransitionAccepted, now: float) -> None:
    """把 Accepted 变更应用到 state（in-place）。

    幂等：多次 apply 同一 transition 行为相同。
    """
    # 首次进入 persistent → 记录 opening 完成时间
    if (
        accepted.phase_changed
        and accepted.new_phase == "persistent"
        and state.opening_completed_at is None
    ):
        state.opening_completed_at = now
    state.phase = accepted.new_phase
    state.current_strategy_id = accepted.new_strategy_id


# =========================================================================
# 不变量 assertion（debug-only / test 用）
# =========================================================================


def assert_invariants(
    state: StrategyState,
    library: StrategyLibrary,
    my_race: str,
) -> None:
    """检查 state 当前满足所有不变量；不满足时 raise AssertionError。

    Director 在关键步骤（每帧 / 每次 set）末尾 assert 一次防回归。
    生产环境可关掉（PYTHONOPTIMIZE）。
    """
    from vibecraft.strategy.models import StrategyKind

    # 1. 非空
    assert state.current_strategy_id, "current_strategy_id is empty"
    assert state.current_strategy_id.lower() != "sustain", "sustain forbidden"

    # 2. phase 合法
    assert state.phase in ("opening", "persistent"), f"invalid phase: {state.phase}"

    # 3. kind 匹配 phase
    kind = library.kind_of(state.current_strategy_id)
    assert kind is not None, f"unknown strategy id: {state.current_strategy_id}"
    if state.phase == "opening":
        assert kind == StrategyKind.OPENING, (
            f"phase=opening but {state.current_strategy_id} kind={kind.value}"
        )
    else:  # persistent
        assert kind == StrategyKind.PERSISTENT, (
            f"phase=persistent but {state.current_strategy_id} kind={kind.value}"
        )

    # 4. race 匹配
    race = library.race_of(state.current_strategy_id)
    if race is not None:  # 旧测试构造 library 不传 races 时跳过
        assert race == my_race.lower(), f"strategy race {race} != my_race {my_race}"
