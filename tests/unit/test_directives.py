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

import pytest

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
