"""Directive schema + DirectiveBoard 单测。

覆盖 (§5.1-5.6)：
- 每个 payload type 的 ser/de
- Directive envelope 默认值 / 唯一 id
- Board submit → 1.5s 延迟 commit
- revoke (pending 期内)
- strategy_set：阶段单向、issued_by 仲裁、supersede 事件
- unit_claim：互斥、supersede、release
- overlay 过期：DURATION / PERSISTENT
- VIEW directive 拒绝进 Board
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import ValidationError

from vibecraft.directives import (
    Action,
    BoardEventKind,
    Directive,
    DirectiveBoard,
    DirectiveType,
    IssuedBy,
    ProductionOverridePayload,
    ScopeKind,
    ScopeSpec,
    Selector,
    StageKind,
    StrategySetPayload,
    TargetSpec,
    Task,
    UnitClaimPayload,
    UnitReleasePayload,
    Verb,
    ViewMovePayload,
)
from vibecraft.directives.board import DirectiveBoardError
from vibecraft.directives.scope import TargetKind

# =========================================================================
# Payload models
# =========================================================================


class TestPayloads:
    def test_strategy_set(self) -> None:
        p = StrategySetPayload(stage="opening", strategy_id="1g_robo_immortal")
        assert p.type == DirectiveType.STRATEGY_SET
        d = p.model_dump(mode="json")
        assert d["type"] == "strategy_set"

    def test_production_override(self) -> None:
        p = ProductionOverridePayload(unit_type="Phoenix", count=4, priority=70)
        assert p.unit_type == "Phoenix"
        assert p.priority == 70

    def test_unit_claim_with_task(self) -> None:
        task = Task(
            primary_action=Action(
                verb=Verb.HARASS_WORKERS,
                target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_main"),
            )
        )
        p = UnitClaimPayload(
            selector=Selector(tag=12345),
            task=task,
        )
        assert p.task.primary_action.verb == Verb.HARASS_WORKERS

    def test_directive_envelope_default_id(self) -> None:
        d = Directive(
            payload=StrategySetPayload(stage="opening", strategy_id="x"),
            issued_at=1.0,
        )
        assert d.id.startswith("d_")
        assert len(d.id) == 8  # d_ + 6 hex
        assert d.priority == 50
        assert d.issued_by == IssuedBy.VOICE

    def test_directive_round_trip(self) -> None:
        d = Directive(
            payload=ProductionOverridePayload(unit_type="Immortal", count=2),
            issued_at=100.0,
            source_text="下个 Robo 出俩不朽",
        )
        dumped = d.model_dump(mode="json")
        restored = Directive.model_validate(dumped)
        assert restored.id == d.id
        assert restored.type == DirectiveType.PRODUCTION_OVERRIDE
        assert isinstance(restored.payload, ProductionOverridePayload)
        assert restored.payload.unit_type == "Immortal"

    def test_view_directive_recognized(self) -> None:
        d = Directive(
            payload=ViewMovePayload(target_point=(50.0, 80.0)),
            issued_at=10.0,
        )
        assert d.is_view() is True


# =========================================================================
# DirectiveBoard
# =========================================================================


def _strategy_set(
    stage: str,
    strategy_id: str,
    issued_at: float,
    issued_by: IssuedBy = IssuedBy.VOICE,
    priority: int = 50,
) -> Directive:
    return Directive(
        payload=StrategySetPayload(stage=stage, strategy_id=strategy_id),  # type: ignore[arg-type]
        issued_at=issued_at,
        issued_by=issued_by,
        priority=priority,
    )


def _claim(
    tag: int,
    issued_at: float,
    priority: int = 50,
    scope: ScopeSpec | None = None,
) -> Directive:
    return Directive(
        payload=UnitClaimPayload(
            selector=Selector(tag=tag),
            task=Task(
                primary_action=Action(
                    verb=Verb.HOLD_POSITION,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="natural_choke"),
                )
            ),
        ),
        issued_at=issued_at,
        scope=scope or ScopeSpec(),
        priority=priority,
    )


class TestSubmitAndCommit:
    def test_submit_sets_effective_at_with_delay(self) -> None:
        board = DirectiveBoard(commit_delay_s=1.5)
        d = _strategy_set("opening", "1g_robo", issued_at=10.0)
        submitted = board.submit(d, now=10.0)
        assert submitted.effective_at == 11.5
        assert board.pending == [submitted]

    def test_submit_late_use_now(self) -> None:
        """若 now > issued_at + delay，effective_at = now（公平延迟下限）。"""
        board = DirectiveBoard(commit_delay_s=1.5)
        d = _strategy_set("opening", "1g_robo", issued_at=10.0)
        submitted = board.submit(d, now=20.0)
        assert submitted.effective_at == 20.0

    def test_tick_commits_pending(self) -> None:
        board = DirectiveBoard()
        d = _strategy_set("opening", "1g_robo", issued_at=10.0)
        board.submit(d, now=10.0)

        events = board.tick(now=11.0)
        assert events == []  # 还没到 effective_at
        assert len(board.pending) == 1

        events = board.tick(now=12.0)
        kinds = [e.kind for e in events]
        assert BoardEventKind.STRATEGY_CHANGED in kinds
        assert BoardEventKind.COMMITTED in kinds
        assert board.pending == []
        assert board.active_strategy(StageKind.OPENING) is not None

    def test_revoke_pending(self) -> None:
        board = DirectiveBoard()
        d = _strategy_set("opening", "1g_robo", issued_at=10.0)
        submitted = board.submit(d, now=10.0)
        assert board.revoke(submitted.id, now=10.5) is True
        assert board.pending == []

        events = board.tick(now=20.0)
        # 已撤销，不应再 commit
        committed_ids = [e.directive_id for e in events if e.kind == BoardEventKind.COMMITTED]
        assert submitted.id not in committed_ids

    def test_revoke_unknown_returns_false(self) -> None:
        board = DirectiveBoard()
        assert board.revoke("d_nonexistent", now=1.0) is False

    def test_view_directive_rejected(self) -> None:
        board = DirectiveBoard()
        d = Directive(payload=ViewMovePayload(target_point=(50, 80)), issued_at=1.0)
        with pytest.raises(DirectiveBoardError, match="VIEW"):
            board.submit(d, now=1.0)


class TestStageMonotonicity:
    def test_opening_then_midgame_advances_current_stage(self) -> None:
        board = DirectiveBoard()
        board.submit(_strategy_set("opening", "1g_robo", 0.0), now=0.0)
        board.tick(now=2.0)
        assert board.current_stage == StageKind.OPENING

        board.submit(_strategy_set("midgame", "iac_2base", 100.0), now=100.0)
        board.tick(now=102.0)
        assert board.current_stage == StageKind.MIDGAME

    def test_auto_transition_cannot_regress(self) -> None:
        board = DirectiveBoard()
        board.submit(_strategy_set("lategame", "skytoss", 0.0, issued_by=IssuedBy.VOICE), now=0.0)
        board.tick(now=2.0)
        # 此时 current_stage = lategame，且 midgame slot 是 None；
        # 但 auto_transition 想新设 midgame —— 由于 midgame slot 是 None，应允许
        # 我们测试的是"已经有 slot 时"的回退保护，所以先设 midgame
        board.submit(
            _strategy_set("midgame", "iac_2base", 10.0, issued_by=IssuedBy.VOICE), now=10.0
        )
        board.tick(now=12.0)
        # 现在 midgame slot 已存在；auto_transition 想再改 midgame slot —— 应拒绝
        board.submit(
            _strategy_set("midgame", "blink_timing", 20.0, issued_by=IssuedBy.AUTO_TRANSITION),
            now=20.0,
        )
        events = board.tick(now=22.0)
        rejects = [e for e in events if e.kind == BoardEventKind.REJECTED]
        assert any("auto_transition" in (e.reason or "") for e in rejects)

    def test_voice_can_override_auto_transition_slot(self) -> None:
        board = DirectiveBoard()
        board.submit(
            _strategy_set("midgame", "iac_2base", 10.0, issued_by=IssuedBy.AUTO_TRANSITION),
            now=10.0,
        )
        board.tick(now=12.0)
        # voice 后下达，优先级更高，应 supersede
        board.submit(
            _strategy_set("midgame", "blink_timing", 20.0, issued_by=IssuedBy.VOICE),
            now=20.0,
        )
        events = board.tick(now=22.0)
        assert any(e.kind == BoardEventKind.SUPERSEDED for e in events)
        slot = board.active_strategy(StageKind.MIDGAME)
        assert slot is not None
        assert slot.strategy_id == "blink_timing"


class TestUnitClaims:
    def test_claim_registers_in_account_book(self) -> None:
        board = DirectiveBoard()
        d = _claim(tag=12345, issued_at=10.0)
        board.submit(d, now=10.0)
        board.tick(now=12.0)
        assert board.is_claimed(12345)
        assert board.unit_claims[12345].directive_id == d.id

    def test_claim_conflict_higher_priority_wins(self) -> None:
        board = DirectiveBoard()
        d1 = _claim(tag=999, issued_at=10.0, priority=40)
        board.submit(d1, now=10.0)
        board.tick(now=12.0)

        d2 = _claim(tag=999, issued_at=15.0, priority=80)
        board.submit(d2, now=15.0)
        events = board.tick(now=17.0)
        assert any(e.kind == BoardEventKind.SUPERSEDED for e in events)
        assert board.unit_claims[999].directive_id == d2.id

    def test_claim_conflict_lower_priority_rejected(self) -> None:
        board = DirectiveBoard()
        d1 = _claim(tag=999, issued_at=10.0, priority=80)
        board.submit(d1, now=10.0)
        board.tick(now=12.0)

        d2 = _claim(tag=999, issued_at=15.0, priority=40)
        board.submit(d2, now=15.0)
        events = board.tick(now=17.0)
        rejects = [e for e in events if e.kind == BoardEventKind.REJECTED]
        assert len(rejects) >= 1
        assert board.unit_claims[999].directive_id == d1.id

    def test_unit_release_removes_claim(self) -> None:
        board = DirectiveBoard()
        d = _claim(tag=555, issued_at=10.0)
        board.submit(d, now=10.0)
        board.tick(now=12.0)
        assert board.is_claimed(555)

        rel = Directive(
            payload=UnitReleasePayload(selector=Selector(tag=555)),
            issued_at=20.0,
        )
        board.submit(rel, now=20.0)
        board.tick(now=22.0)
        assert not board.is_claimed(555)

    def test_release_claimed_true_releases_all(self) -> None:
        board = DirectiveBoard()
        for tag in [1, 2, 3]:
            board.submit(_claim(tag=tag, issued_at=10.0), now=10.0)
        board.tick(now=12.0)
        assert len(board.unit_claims) == 3

        rel = Directive(
            payload=UnitReleasePayload(selector=Selector(claimed=True)),
            issued_at=20.0,
        )
        board.submit(rel, now=20.0)
        board.tick(now=22.0)
        assert len(board.unit_claims) == 0


class TestScopeExpiration:
    def test_duration_scope_expires_after_seconds(self) -> None:
        board = DirectiveBoard()
        d = _claim(
            tag=42,
            issued_at=10.0,
            scope=ScopeSpec(kind=ScopeKind.DURATION, duration_s=30.0),
        )
        board.submit(d, now=10.0)
        board.tick(now=12.0)  # commit
        assert board.is_claimed(42)

        board.tick(now=20.0)  # 30s 没到（commit 后 30s = 41.5）
        assert board.is_claimed(42)

        board.tick(now=45.0)
        # overlay 过期 + 释放
        assert not board.is_claimed(42)

    def test_persistent_scope_never_expires(self) -> None:
        board = DirectiveBoard()
        d = _claim(tag=7, issued_at=0.0, scope=ScopeSpec(kind=ScopeKind.PERSISTENT))
        board.submit(d, now=0.0)
        board.tick(now=2.0)
        board.tick(now=1000.0)
        assert board.is_claimed(7)

    def test_ephemeral_scope_not_auto_expired(self) -> None:
        """ephemeral 由业务层主动 release，Board 不主动过期。"""
        board = DirectiveBoard()
        d = _claim(tag=7, issued_at=0.0, scope=ScopeSpec(kind=ScopeKind.EPHEMERAL))
        board.submit(d, now=0.0)
        board.tick(now=2.0)
        board.tick(now=1000.0)
        assert board.is_claimed(7)


class TestScopeSpecValidation:
    def test_until_requires_expr(self) -> None:
        s = ScopeSpec(kind=ScopeKind.UNTIL)
        with pytest.raises(ValueError, match="until_expr"):
            s.validate_for_kind()

    def test_duration_requires_seconds(self) -> None:
        s = ScopeSpec(kind=ScopeKind.DURATION)
        with pytest.raises(ValueError, match="duration_s"):
            s.validate_for_kind()


# =========================================================================
# TestStandingOrderSchema (P1.1)
# =========================================================================


class TestStandingOrderSchema:
    """L3 standing order schema (P1.1)。

    修 v0.1.0a3 M4 e2e 发现的 LLM prompt ↔ Pydantic schema 不匹配：
    - UnitClaimPayload 加 persistent: bool = False
    - TargetSpec 已支持 building_tag / named_spot（验证存在）
    - Selector 已拒绝 count（验证 extra=forbid 生效）
    """

    def test_unit_claim_payload_persistent_default_false(self) -> None:
        """persistent 默认 False。"""
        p = UnitClaimPayload(
            selector=Selector(unit_type="Phoenix"),
            task=Task(
                primary_action=Action(
                    verb=Verb.PATROL,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="natural"),
                )
            ),
        )
        assert p.persistent is False

    def test_unit_claim_payload_persistent_true(self) -> None:
        """persistent=True 可以传入并保存。"""
        p = UnitClaimPayload(
            selector=Selector(unit_type="Zealot"),
            task=Task(
                primary_action=Action(
                    verb=Verb.HOLD_POSITION,
                    target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="main_ramp"),
                )
            ),
            persistent=True,
        )
        assert p.persistent is True

    def test_target_kind_building_tag(self) -> None:
        """target.kind 支持 building_tag（M4 e2e schema gap 验证）。"""
        t = TargetSpec(kind=TargetKind.BUILDING_TAG, building_tag=12345)
        assert t.kind == TargetKind.BUILDING_TAG
        assert t.building_tag == 12345

    def test_target_kind_named_spot(self) -> None:
        """守气矿等场景用 named_spot。"""
        t = TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_main_gas")
        assert t.kind == TargetKind.NAMED_SPOT
        assert t.named_spot == "enemy_main_gas"

    def test_selector_no_count_field(self) -> None:
        """UnitSelector 不接 count（extra=forbid 已实现）。"""
        with pytest.raises(ValidationError):
            Selector(unit_type="Probe", count=1)  # type: ignore[call-arg]


# =========================================================================
# TestDoneWhenSchema (P3)
# =========================================================================


from vibecraft.directives import TacticalObjectivePayload  # noqa: E402
from vibecraft.directives.models import (  # noqa: E402
    AllOf,
    AnyOf,
    EnemyKilledInArea,
    ExpansionCount,
    OwnArmySizeRatio,
    TargetDestroyed,
    TechDone,
    TimeElapsedSince,
    UnitCountBuiltSince,
    VisionAcquired,
)


class TestDoneWhenSchema:
    """DoneWhen discriminated union — 8 kind + 2 复合（P3）。"""

    # ------------------------------------------------------------------
    # 8 基本 kind valid cases
    # ------------------------------------------------------------------

    def test_unit_count_built_since_valid(self) -> None:
        c = UnitCountBuiltSince(kind="unit_count_built_since", unit_type="Phoenix", op=">=", value=4)
        assert c.kind == "unit_count_built_since"
        assert c.unit_type == "Phoenix"
        assert c.op == ">="
        assert c.value == 4

    def test_tech_done_valid(self) -> None:
        c = TechDone(kind="tech_done", upgrade_id="Blink")
        assert c.kind == "tech_done"
        assert c.upgrade_id == "Blink"

    def test_expansion_count_valid(self) -> None:
        c = ExpansionCount(kind="expansion_count", op=">=", value=3)
        assert c.kind == "expansion_count"
        assert c.op == ">="
        assert c.value == 3

    def test_target_destroyed_valid(self) -> None:
        c = TargetDestroyed(
            kind="target_destroyed",
            target_kind="natural",
            area="enemy_natural",
        )
        assert c.kind == "target_destroyed"
        assert c.target_kind == "natural"
        assert c.area == "enemy_natural"
        assert c.target_param is None

    def test_own_army_size_ratio_valid(self) -> None:
        c = OwnArmySizeRatio(kind="own_army_size_ratio", op=">=", value=0.8)
        assert c.kind == "own_army_size_ratio"
        assert c.value == 0.8

    def test_vision_acquired_valid(self) -> None:
        c = VisionAcquired(kind="vision_acquired", area="enemy_natural", hold_seconds=3.0)
        assert c.kind == "vision_acquired"
        assert c.hold_seconds == 3.0

    def test_enemy_killed_in_area_valid(self) -> None:
        c = EnemyKilledInArea(
            kind="enemy_killed_in_area",
            area="enemy_natural",
            unit_type="Queen",
            op=">=",
            value=2,
        )
        assert c.kind == "enemy_killed_in_area"
        assert c.unit_type == "Queen"

    def test_time_elapsed_since_valid(self) -> None:
        c = TimeElapsedSince(kind="time_elapsed_since", seconds=60.0)
        assert c.kind == "time_elapsed_since"
        assert c.ref == "directive_issued"  # default

    def test_time_elapsed_since_game_start_ref(self) -> None:
        c = TimeElapsedSince(kind="time_elapsed_since", seconds=300.0, ref="game_start")
        assert c.ref == "game_start"

    # ------------------------------------------------------------------
    # 2 复合 kind valid cases
    # ------------------------------------------------------------------

    def test_any_of_valid(self) -> None:
        c = AnyOf(
            kind="any_of",
            conditions=[
                {"kind": "tech_done", "upgrade_id": "Blink"},
                {"kind": "time_elapsed_since", "seconds": 120.0},
            ],
        )
        assert c.kind == "any_of"
        assert len(c.conditions) == 2
        assert isinstance(c.conditions[0], TechDone)
        assert isinstance(c.conditions[1], TimeElapsedSince)

    def test_all_of_valid(self) -> None:
        c = AllOf(
            kind="all_of",
            conditions=[
                {"kind": "expansion_count", "op": ">=", "value": 3},
                {"kind": "own_army_size_ratio", "op": ">=", "value": 0.9},
            ],
        )
        assert c.kind == "all_of"
        assert len(c.conditions) == 2

    def test_nested_any_of_in_all_of(self) -> None:
        """any_of 嵌套进 all_of（forward ref 解析正确）。"""
        c = AllOf(
            kind="all_of",
            conditions=[
                {
                    "kind": "any_of",
                    "conditions": [
                        {"kind": "tech_done", "upgrade_id": "Blink"},
                        {"kind": "tech_done", "upgrade_id": "Charge"},
                    ],
                },
                {"kind": "expansion_count", "op": ">=", "value": 2},
            ],
        )
        inner = c.conditions[0]
        assert isinstance(inner, AnyOf)
        assert len(inner.conditions) == 2

    # ------------------------------------------------------------------
    # Invalid / missing-field cases
    # ------------------------------------------------------------------

    def test_unit_count_built_since_missing_unit_type(self) -> None:
        """缺 unit_type 应 ValidationError。"""
        with pytest.raises(ValidationError):
            UnitCountBuiltSince(kind="unit_count_built_since", op=">=", value=4)  # type: ignore[call-arg]

    def test_unit_count_built_since_missing_op(self) -> None:
        with pytest.raises(ValidationError):
            UnitCountBuiltSince(kind="unit_count_built_since", unit_type="Stalker", value=2)  # type: ignore[call-arg]

    def test_tech_done_missing_upgrade_id(self) -> None:
        with pytest.raises(ValidationError):
            TechDone(kind="tech_done")  # type: ignore[call-arg]

    def test_vision_acquired_missing_area(self) -> None:
        with pytest.raises(ValidationError):
            VisionAcquired(kind="vision_acquired", hold_seconds=5.0)  # type: ignore[call-arg]

    def test_bad_discriminator_kind(self) -> None:
        """kind 不在白名单 → ValidationError（discriminator 拒绝）。"""

        from pydantic import TypeAdapter

        from vibecraft.directives.models import DoneWhen

        ta = TypeAdapter(DoneWhen)
        with pytest.raises(ValidationError):
            ta.validate_python({"kind": "nonexistent_kind", "value": 1})

    def test_bad_op_value_rejected(self) -> None:
        """op 不在 Literal 白名单 → ValidationError。"""
        with pytest.raises(ValidationError):
            UnitCountBuiltSince(
                kind="unit_count_built_since",
                unit_type="Zealot",
                op="!=",  # type: ignore[arg-type]
                value=1,
            )


# =========================================================================
# TestTacticalObjectivePayload (P3)
# =========================================================================


class TestTacticalObjectivePayload:
    """TacticalObjectivePayload — L2 战术指令 schema。"""

    VALID_VERBS: ClassVar[list[str]] = [
        "attack", "defend", "scout", "expand", "harass",
        "drop", "vision", "raze", "retreat", "regroup", "split",
    ]

    def test_all_valid_verbs_accepted(self) -> None:
        for verb in self.VALID_VERBS:
            p = TacticalObjectivePayload(verb=verb)  # type: ignore[arg-type]
            assert p.verb == verb
            assert p.type == DirectiveType.TACTICAL_OBJECTIVE

    def test_invalid_verb_rejected(self) -> None:
        """非法 verb 拒绝。"""
        invalid_verbs = [
            "charge", "rush", "turtle", "all_in", "cheese",
            "build", "gather", "rally", "ambush", "flank",
            "engage", "disengage",
        ]
        for verb in invalid_verbs:
            with pytest.raises(ValidationError):
                TacticalObjectivePayload(verb=verb)  # type: ignore[arg-type]

    def test_target_area_str(self) -> None:
        p = TacticalObjectivePayload(verb="attack", target_area="enemy_natural")
        assert p.target_area == "enemy_natural"

    def test_target_area_tuple(self) -> None:
        p = TacticalObjectivePayload(verb="defend", target_area=(55.5, 32.0))
        assert p.target_area == (55.5, 32.0)

    def test_target_area_none(self) -> None:
        p = TacticalObjectivePayload(verb="regroup")
        assert p.target_area is None

    def test_defaults(self) -> None:
        p = TacticalObjectivePayload(verb="scout")
        assert p.priority == 50
        assert p.unit_count_hint is None
        assert p.unit_type_hint is None
        assert p.done_when is None
        assert p.timeout_s is None

    def test_unit_hints_accepted(self) -> None:
        p = TacticalObjectivePayload(
            verb="harass",
            unit_count_hint=6,
            unit_type_hint=["Phoenix", "Oracle"],
        )
        assert p.unit_count_hint == 6
        assert p.unit_type_hint == ["Phoenix", "Oracle"]

    def test_done_when_attached(self) -> None:
        """TacticalObjectivePayload 可携带 done_when（来自 _PayloadBase）。"""
        p = TacticalObjectivePayload(
            verb="attack",
            target_area="enemy_main",
            done_when={"kind": "target_destroyed", "target_kind": "main"},
        )
        assert isinstance(p.done_when, TargetDestroyed)

    def test_timeout_s_accepted(self) -> None:
        p = TacticalObjectivePayload(verb="vision", timeout_s=120)
        assert p.timeout_s == 120

    def test_directive_envelope_tactical_objective(self) -> None:
        """TacticalObjectivePayload 进 Directive envelope round-trip。"""
        d = Directive(
            payload=TacticalObjectivePayload(verb="expand", target_area="third_base"),
            issued_at=200.0,
        )
        assert d.type == DirectiveType.TACTICAL_OBJECTIVE
        dumped = d.model_dump(mode="json")
        restored = Directive.model_validate(dumped)
        assert isinstance(restored.payload, TacticalObjectivePayload)
        assert restored.payload.verb == "expand"
        assert restored.payload.target_area == "third_base"

    def test_done_when_on_base_payload_is_optional(self) -> None:
        """_PayloadBase.done_when 对所有 payload 可选（StrategySetPayload 验证）。"""
        from vibecraft.directives import StrategySetPayload
        p = StrategySetPayload(stage="opening", strategy_id="1g_robo")
        assert p.done_when is None
        assert p.timeout_s is None
