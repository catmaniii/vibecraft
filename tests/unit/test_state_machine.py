"""state_machine.py 单元测试 —— 4 个不变量 + 状态转移合法性。"""

from __future__ import annotations

import pytest

from vibecraft.strategy import (
    OpeningBuild,
    PersistentDoctrine,
    StrategyLibrary,
)
from vibecraft.strategy.state_machine import (
    StrategyState,
    TransitionAccepted,
    TransitionRejected,
    apply_transition,
    assert_invariants,
    validate_transition,
)

# =========================================================================
# fixtures
# =========================================================================


def _make_opening(sid: str) -> OpeningBuild:
    return OpeningBuild.model_validate(
        {
            "kind": "opening_build",
            "id": sid,
            "display_name_zh": sid,
            "phases": [],
            "steps": ["13 build Pylon"],
        }
    )


def _make_persistent(sid: str) -> PersistentDoctrine:
    return PersistentDoctrine.model_validate(
        {
            "kind": "persistent_doctrine",
            "id": sid,
            "display_name_zh": sid,
            "target_composition": {"Stalker": 10},
        }
    )


@pytest.fixture
def library() -> StrategyLibrary:
    op_a = _make_opening("open_a")
    op_b = _make_opening("open_b")
    p_a = _make_persistent("persistent_a")
    p_b = _make_persistent("persistent_b")
    races = {
        "open_a": "protoss",
        "open_b": "protoss",
        "persistent_a": "protoss",
        "persistent_b": "protoss",
    }
    return StrategyLibrary(
        openings=[op_a, op_b], persistents=[p_a, p_b], races=races
    )


# =========================================================================
# StrategyState dataclass
# =========================================================================


class TestStrategyState:
    def test_default(self) -> None:
        s = StrategyState()
        assert s.phase == "opening"
        assert s.current_strategy_id == ""
        assert s.opening_completed_at is None

    def test_custom(self) -> None:
        s = StrategyState(
            phase="persistent",
            current_strategy_id="persistent_a",
            opening_completed_at=300.0,
        )
        assert s.phase == "persistent"
        assert s.opening_completed_at == 300.0


# =========================================================================
# validate_transition：不变量 1（非空 / 非 sustain）
# =========================================================================


class TestEmptyAndSustainRejected:
    def test_empty_id_rejected(self, library: StrategyLibrary) -> None:
        state = StrategyState(phase="opening", current_strategy_id="open_a")
        result = validate_transition(state, "", library, "protoss")
        assert isinstance(result, TransitionRejected)
        assert result.reason_code == "empty_id"

    def test_sustain_rejected(self, library: StrategyLibrary) -> None:
        state = StrategyState(phase="opening", current_strategy_id="open_a")
        result = validate_transition(state, "sustain", library, "protoss")
        assert isinstance(result, TransitionRejected)
        assert result.reason_code == "sustain_forbidden"
        # 包含建议
        assert result.suggested_action is not None
        assert "persistent" in result.suggested_action.lower()

    def test_sustain_case_insensitive(self, library: StrategyLibrary) -> None:
        state = StrategyState(phase="opening", current_strategy_id="open_a")
        for variant in ("Sustain", "SUSTAIN", "SuStAiN"):
            assert isinstance(
                validate_transition(state, variant, library, "protoss"),
                TransitionRejected,
            )


# =========================================================================
# validate_transition：不变量 2 / 3（phase ↔ kind 匹配）
# =========================================================================


class TestPhaseKindMatching:
    def test_opening_phase_accepts_opening(self, library: StrategyLibrary) -> None:
        state = StrategyState(phase="opening", current_strategy_id="open_a")
        result = validate_transition(state, "open_b", library, "protoss")
        assert isinstance(result, TransitionAccepted)
        assert result.new_phase == "opening"
        assert not result.phase_changed

    def test_opening_phase_accepts_persistent_with_phase_change(
        self, library: StrategyLibrary
    ) -> None:
        """opening 阶段切 persistent → 强制 phase 进入 persistent（提前完成）"""
        state = StrategyState(phase="opening", current_strategy_id="open_a")
        result = validate_transition(state, "persistent_a", library, "protoss")
        assert isinstance(result, TransitionAccepted)
        assert result.new_phase == "persistent"
        assert result.phase_changed  # phase 跳变

    def test_persistent_phase_accepts_persistent(self, library: StrategyLibrary) -> None:
        state = StrategyState(
            phase="persistent", current_strategy_id="persistent_a", opening_completed_at=300
        )
        result = validate_transition(state, "persistent_b", library, "protoss")
        assert isinstance(result, TransitionAccepted)
        assert result.new_phase == "persistent"
        assert not result.phase_changed

    def test_persistent_phase_rejects_opening_lock(
        self, library: StrategyLibrary
    ) -> None:
        """Q3 锁定：persistent 阶段不能回到 opening"""
        state = StrategyState(
            phase="persistent",
            current_strategy_id="persistent_a",
            opening_completed_at=300,
        )
        result = validate_transition(state, "open_a", library, "protoss")
        assert isinstance(result, TransitionRejected)
        assert result.reason_code == "phase_locked"
        assert "开局" in result.reason_zh


# =========================================================================
# validate_transition：不变量 4（race 匹配）
# =========================================================================


class TestRaceMismatch:
    def test_cross_race_rejected(self) -> None:
        proto = _make_persistent("p_a")
        zerg = _make_persistent("z_a")
        races = {"p_a": "protoss", "z_a": "zerg"}
        lib = StrategyLibrary(persistents=[proto, zerg], races=races)
        state = StrategyState(
            phase="persistent",
            current_strategy_id="p_a",
            opening_completed_at=300,
        )
        result = validate_transition(state, "z_a", lib, "protoss")
        assert isinstance(result, TransitionRejected)
        assert result.reason_code == "race_mismatch"

    def test_unregistered_race_allowed(self) -> None:
        """直接构造的 library 不传 races → race_of() 返回 None → 跳过 race 检查"""
        p = _make_persistent("p_a")
        lib = StrategyLibrary(persistents=[p])  # 不传 races
        state = StrategyState(phase="persistent", current_strategy_id="p_a", opening_completed_at=300)
        result = validate_transition(state, "p_a", lib, "protoss")
        assert isinstance(result, TransitionAccepted)


# =========================================================================
# validate_transition：未知 id
# =========================================================================


class TestUnknownId:
    def test_unknown_rejected(self, library: StrategyLibrary) -> None:
        state = StrategyState(phase="opening", current_strategy_id="open_a")
        result = validate_transition(state, "fake_id_not_registered", library, "protoss")
        assert isinstance(result, TransitionRejected)
        assert result.reason_code == "unknown_id"


# =========================================================================
# apply_transition：状态变更幂等性 + completion 时间
# =========================================================================


class TestApplyTransition:
    def test_apply_opening_to_opening(self, library: StrategyLibrary) -> None:
        state = StrategyState(phase="opening", current_strategy_id="open_a")
        accepted = TransitionAccepted(new_phase="opening", new_strategy_id="open_b", phase_changed=False)
        apply_transition(state, accepted, now=120.0)
        assert state.current_strategy_id == "open_b"
        assert state.phase == "opening"
        assert state.opening_completed_at is None  # 未进入 persistent

    def test_apply_first_persistent_records_completion(
        self, library: StrategyLibrary
    ) -> None:
        """第一次切到 persistent 时记 opening_completed_at"""
        state = StrategyState(phase="opening", current_strategy_id="open_a")
        accepted = TransitionAccepted(
            new_phase="persistent", new_strategy_id="persistent_a", phase_changed=True
        )
        apply_transition(state, accepted, now=420.0)
        assert state.phase == "persistent"
        assert state.opening_completed_at == 420.0

    def test_persistent_to_persistent_doesnt_reset_completion(
        self, library: StrategyLibrary
    ) -> None:
        """已经在 persistent 阶段切别的 persistent，completion 时间不变"""
        state = StrategyState(
            phase="persistent",
            current_strategy_id="persistent_a",
            opening_completed_at=420.0,
        )
        accepted = TransitionAccepted(
            new_phase="persistent", new_strategy_id="persistent_b", phase_changed=False
        )
        apply_transition(state, accepted, now=600.0)
        assert state.opening_completed_at == 420.0  # 不变
        assert state.current_strategy_id == "persistent_b"

    def test_apply_is_idempotent(self, library: StrategyLibrary) -> None:
        state = StrategyState(phase="opening", current_strategy_id="open_a")
        accepted = TransitionAccepted(
            new_phase="opening", new_strategy_id="open_b", phase_changed=False
        )
        apply_transition(state, accepted, now=120.0)
        apply_transition(state, accepted, now=130.0)
        assert state.current_strategy_id == "open_b"


# =========================================================================
# 端到端：validate + apply
# =========================================================================


class TestEndToEnd:
    def test_full_lifecycle(self, library: StrategyLibrary) -> None:
        state = StrategyState(phase="opening", current_strategy_id="open_a")

        # 1. 玩家在 opening 阶段切别的 opening
        r1 = validate_transition(state, "open_b", library, "protoss")
        assert isinstance(r1, TransitionAccepted)
        apply_transition(state, r1, now=60.0)
        assert state.current_strategy_id == "open_b"

        # 2. opening 完成自动切 persistent
        r2 = validate_transition(state, "persistent_a", library, "protoss")
        assert isinstance(r2, TransitionAccepted)
        assert r2.phase_changed
        apply_transition(state, r2, now=420.0)
        assert state.phase == "persistent"
        assert state.opening_completed_at == 420.0

        # 3. 玩家切别的 persistent
        r3 = validate_transition(state, "persistent_b", library, "protoss")
        assert isinstance(r3, TransitionAccepted)
        apply_transition(state, r3, now=500.0)
        assert state.current_strategy_id == "persistent_b"
        assert state.opening_completed_at == 420.0  # 不重置

        # 4. 玩家尝试切回 opening → 拒绝
        r4 = validate_transition(state, "open_a", library, "protoss")
        assert isinstance(r4, TransitionRejected)
        assert r4.reason_code == "phase_locked"
        # state 不变
        assert state.current_strategy_id == "persistent_b"
        assert state.phase == "persistent"


# =========================================================================
# assert_invariants
# =========================================================================


class TestAssertInvariants:
    def test_valid_state_no_raise(self, library: StrategyLibrary) -> None:
        state = StrategyState(phase="opening", current_strategy_id="open_a")
        assert_invariants(state, library, "protoss")  # no raise

        state2 = StrategyState(
            phase="persistent", current_strategy_id="persistent_a", opening_completed_at=420
        )
        assert_invariants(state2, library, "protoss")

    def test_empty_id_raises(self, library: StrategyLibrary) -> None:
        state = StrategyState(phase="opening", current_strategy_id="")
        with pytest.raises(AssertionError, match="empty"):
            assert_invariants(state, library, "protoss")

    def test_sustain_raises(self, library: StrategyLibrary) -> None:
        state = StrategyState(phase="opening", current_strategy_id="sustain")
        with pytest.raises(AssertionError, match="sustain"):
            assert_invariants(state, library, "protoss")

    def test_phase_kind_mismatch_raises(self, library: StrategyLibrary) -> None:
        """phase=opening 但 current_strategy_id 指 persistent → 不变量违反"""
        state = StrategyState(phase="opening", current_strategy_id="persistent_a")
        with pytest.raises(AssertionError, match="kind"):
            assert_invariants(state, library, "protoss")

    def test_race_mismatch_raises(self) -> None:
        z = _make_persistent("z_a")
        lib = StrategyLibrary(persistents=[z], races={"z_a": "zerg"})
        state = StrategyState(
            phase="persistent", current_strategy_id="z_a", opening_completed_at=300
        )
        with pytest.raises(AssertionError, match="race"):
            assert_invariants(state, lib, "protoss")
