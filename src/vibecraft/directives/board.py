"""DirectiveBoard：directive 生命周期 + 状态机。

职责（设计文档 §5.1 / §5.5 / §5.6）：
- 收 directive → 计算 effective_at = max(now, issued_at + 1.5)
- pending → 到 effective_at 时 commit（emit board event）
- 维护 active_opening / midgame / lategame 三槽（任一时刻最多一）
- 维护 overlay 列表（叠加层）
- 维护 unit_claims 账本（互斥）
- 阶段切换：opening → midgame → lategame 严格单向
- emit 一组 BoardEvent，供日志层和上游消费

**不在本类里实现**：
- 仲裁 resolve_for_unit / resolve_production（依赖运行时 game_state）
- 阶段转移条件 (default_transitions / lategame_transitions) 求值
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vibecraft.directives.models import (
    Directive,
    StrategySetPayload,
    UnitClaimPayload,
    UnitReleasePayload,
)
from vibecraft.directives.scope import (
    ClaimRecord,
    ScopeKind,
)
from vibecraft.directives.types import (
    DirectiveType,
    IssuedBy,
    StageKind,
    is_view_directive,
    issued_by_priority,
)

# ---------------------------------------------------------------------------
# Board events（emit 出去给 logger / WS server）
# ---------------------------------------------------------------------------


class BoardEventKind(str, Enum):
    SUBMITTED = "directive.submitted"
    COMMITTED = "directive.committed"
    REVOKED = "directive.revoked"
    RELEASED = "directive.released"
    REJECTED = "directive.rejected"
    SUPERSEDED = "directive.superseded"
    STRATEGY_CHANGED = "strategy.changed"
    PHASE_TRANSITIONED = "strategy.transitioned"


class BoardEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: BoardEventKind
    ts: float
    directive_id: str | None = None
    reason: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Strategy slot：Board 只持 strategy id；剧本对象由 StrategyLibrary 提供
# ---------------------------------------------------------------------------


class StrategySlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: StageKind
    strategy_id: str
    set_at: float
    set_by: IssuedBy


_STAGE_ORDER = [StageKind.OPENING, StageKind.MIDGAME, StageKind.LATEGAME]


def _stage_index(stage: StageKind) -> int:
    return _STAGE_ORDER.index(stage)


# ---------------------------------------------------------------------------
# DirectiveBoard
# ---------------------------------------------------------------------------


class DirectiveBoardError(Exception):
    """Board 操作非法（如逆向阶段切换、重复 claim）。"""


_DEFAULT_DELAY_S = 1.5


class DirectiveBoard:
    """单局对应一个 Board 实例。"""

    def __init__(self, *, commit_delay_s: float = _DEFAULT_DELAY_S) -> None:
        self.commit_delay_s = commit_delay_s

        self.slots: dict[StageKind, StrategySlot | None] = dict.fromkeys(StageKind)
        self.current_stage: StageKind = StageKind.OPENING

        self.overlays: list[Directive] = []  # 已 commit 的活跃 overlay
        self.pending: list[Directive] = []  # 等待 commit (未到 effective_at)
        self.unit_claims: dict[int, ClaimRecord] = {}

        self._events: list[BoardEvent] = []

    # ----- 写入 ---------------------------------------------------------

    def set_initial_slot(self, stage: StageKind, strategy_id: str, now: float) -> bool:
        """初始化某阶段 slot,bypass 1.5s commit delay。

        用途:bot 启动时把 ares 选的默认 opening 直接落入 board.slots[OPENING],
        让手机 UI 立即(下一次 snapshot)显示当前剧本,而不是空着等 1.5s 后才有。

        幂等:若该阶段 slot 已存在(可能来自玩家语音切换)则不动,避免覆盖玩家意图。
        发出 STRATEGY_CHANGED 事件,触发 Director 主动 push snapshot。

        来源标记为 BOT_INTERNAL(优先级最低),玩家任何 VOICE 指令都能覆盖。
        """
        if self.slots[stage] is not None:
            return False
        self.slots[stage] = StrategySlot(
            stage=stage,
            strategy_id=strategy_id,
            set_at=now,
            set_by=IssuedBy.BOT_INTERNAL,
        )
        self._emit(
            BoardEventKind.STRATEGY_CHANGED,
            now,
            None,
            payload={
                "stage": stage.value,
                "strategy_id": strategy_id,
                "set_by": IssuedBy.BOT_INTERNAL.value,
            },
        )
        return True

    def submit(self, directive: Directive, now: float) -> Directive:
        """玩家话语解析后落 Board。

        - 设置 effective_at = max(now, issued_at + delay)
        - VIEW_* 不进 Board（保留接口未来若需 hook 可改）
        """
        if is_view_directive(directive.type):
            raise DirectiveBoardError(f"VIEW directive 不应进入 Board：{directive.type.value}")

        effective = max(now, directive.issued_at + self.commit_delay_s)
        # Pydantic v2 model_copy 保留其他字段
        d = directive.model_copy(update={"effective_at": effective})
        self.pending.append(d)
        self._emit(BoardEventKind.SUBMITTED, now, d.id, payload={"effective_at": effective})
        return d

    def revoke(self, directive_id: str, now: float) -> bool:
        """撤销 pending（未 commit）的 directive。"""
        for i, d in enumerate(self.pending):
            if d.id == directive_id:
                self.pending.pop(i)
                self._emit(BoardEventKind.REVOKED, now, directive_id)
                return True
        return False

    # ----- tick：推进生命周期 ------------------------------------------

    def tick(self, now: float) -> list[BoardEvent]:
        """推进时间。返回本次 tick emit 的事件列表（也累积进 self._events）。"""
        produced: list[BoardEvent] = []

        # commit 到期的 pending
        still_pending: list[Directive] = []
        to_commit: list[Directive] = []
        for d in self.pending:
            assert d.effective_at is not None
            if now >= d.effective_at:
                to_commit.append(d)
            else:
                still_pending.append(d)
        self.pending = still_pending
        for d in to_commit:
            self._commit(d, now, produced)

        # 释放过期 overlay
        kept: list[Directive] = []
        for d in self.overlays:
            if self._overlay_expired(d, now):
                self._emit(
                    BoardEventKind.RELEASED,
                    now,
                    d.id,
                    reason="scope_expired",
                    sink=produced,
                )
                # claim 同时释放
                if d.type == DirectiveType.UNIT_CLAIM:
                    self._release_claims_for(d.id)
            else:
                kept.append(d)
        self.overlays = kept

        return produced

    # ----- commit 单条 directive ---------------------------------------

    def _commit(self, d: Directive, now: float, sink: list[BoardEvent]) -> None:
        """根据 type 分派到对应的状态变更。"""
        if d.type == DirectiveType.STRATEGY_SET:
            self._apply_strategy_set(d, now, sink)
            return
        if d.type == DirectiveType.UNIT_CLAIM:
            self._apply_unit_claim(d, now, sink)
            return
        if d.type == DirectiveType.UNIT_RELEASE:
            self._apply_unit_release(d, now, sink)
            return
        # 其余 directive 直接进 overlays（仲裁层负责使用）
        self.overlays.append(d)
        self._emit(BoardEventKind.COMMITTED, now, d.id, sink=sink)

    def _apply_strategy_set(self, d: Directive, now: float, sink: list[BoardEvent]) -> None:
        assert isinstance(d.payload, StrategySetPayload)
        new_stage = StageKind(d.payload.stage)
        prev = self.slots[new_stage]

        # 阶段单向：不能从 lategame 退回 midgame
        if (
            d.issued_by == IssuedBy.AUTO_TRANSITION
            and _stage_index(new_stage) <= _stage_index(self.current_stage)
            and prev is not None
        ):
            self._emit(
                BoardEventKind.REJECTED,
                now,
                d.id,
                reason="auto_transition_cannot_regress",
                sink=sink,
            )
            return

        # 同 stage 冲突 → 按 issued_by 优先级仲裁
        if prev is not None:
            cur_prio = issued_by_priority(prev.set_by)
            new_prio = issued_by_priority(d.issued_by)
            if new_prio < cur_prio:
                self._emit(
                    BoardEventKind.REJECTED,
                    now,
                    d.id,
                    reason=f"lower_priority_than_{prev.set_by.value}",
                    sink=sink,
                )
                return
            self._emit(
                BoardEventKind.SUPERSEDED,
                now,
                None,
                payload={"superseded_strategy_id": prev.strategy_id, "stage": new_stage.value},
                sink=sink,
            )

        self.slots[new_stage] = StrategySlot(
            stage=new_stage,
            strategy_id=d.payload.strategy_id,
            set_at=now,
            set_by=d.issued_by,
        )

        # current_stage 提升到 new_stage（若它前进）
        if _stage_index(new_stage) > _stage_index(self.current_stage):
            old_stage = self.current_stage
            self.current_stage = new_stage
            self._emit(
                BoardEventKind.PHASE_TRANSITIONED,
                now,
                None,
                payload={"from": old_stage.value, "to": new_stage.value},
                sink=sink,
            )

        self._emit(
            BoardEventKind.STRATEGY_CHANGED,
            now,
            d.id,
            payload={"stage": new_stage.value, "strategy_id": d.payload.strategy_id},
            sink=sink,
        )
        self._emit(BoardEventKind.COMMITTED, now, d.id, sink=sink)

    def _apply_unit_claim(self, d: Directive, now: float, sink: list[BoardEvent]) -> None:
        assert isinstance(d.payload, UnitClaimPayload)
        # Board 不解析 selector → tag 列表（需要 game_state）。
        # 调用方应在 submit 前把 selector 解析为 tag 集合并以 payload.selector.tags 传入；
        # 或者上层在 commit 后再调 attach_tags() 把 tag 关联到 directive。
        # M0 阶段：若 selector.tag 或 .tags 已显式提供，直接登记
        tags: list[int] = []
        if d.payload.selector.tag is not None:
            tags.append(d.payload.selector.tag)
        if d.payload.selector.tags:
            tags.extend(d.payload.selector.tags)

        for tag in tags:
            prev = self.unit_claims.get(tag)
            if prev is not None:
                # 同 tag 冲突 → 后下达 + 同/更高优先级 supersede
                if d.priority < self._claim_priority(prev):
                    self._emit(
                        BoardEventKind.REJECTED,
                        now,
                        d.id,
                        reason=f"unit_{tag}_already_claimed_by_{prev.directive_id}",
                        sink=sink,
                    )
                    continue
                self._emit(
                    BoardEventKind.SUPERSEDED,
                    now,
                    prev.directive_id,
                    payload={"unit_tag": tag, "by": d.id},
                    sink=sink,
                )
            expires_at: float | None = None
            if d.scope.kind == ScopeKind.DURATION:
                assert d.scope.duration_s is not None
                expires_at = now + d.scope.duration_s
            self.unit_claims[tag] = ClaimRecord(
                unit_tag=tag,
                directive_id=d.id,
                claimed_at=now,
                scope=d.scope,
                expires_at=expires_at,
            )

        # claim directive 同时作为 overlay 存活，仲裁层需要它的 Task
        self.overlays.append(d)
        self._emit(BoardEventKind.COMMITTED, now, d.id, sink=sink)

    def _apply_unit_release(self, d: Directive, now: float, sink: list[BoardEvent]) -> None:
        assert isinstance(d.payload, UnitReleasePayload)
        sel = d.payload.selector

        released_tags: list[int] = []
        # M0：仅支持 selector.tag / .tags / claimed=True 三种 release 形式
        if sel.tag is not None and sel.tag in self.unit_claims:
            released_tags.append(sel.tag)
        if sel.tags:
            released_tags.extend(t for t in sel.tags if t in self.unit_claims)
        if sel.claimed is True and sel.tag is None and not sel.tags:
            released_tags.extend(self.unit_claims.keys())

        affected_directives: set[str] = set()
        for tag in released_tags:
            rec = self.unit_claims.pop(tag)
            affected_directives.add(rec.directive_id)

        # 检查每个原 claim directive 是否还有 tag → 否则 release 整条
        for dir_id in affected_directives:
            if not any(rec.directive_id == dir_id for rec in self.unit_claims.values()):
                self.overlays = [o for o in self.overlays if o.id != dir_id]
                self._emit(
                    BoardEventKind.RELEASED,
                    now,
                    dir_id,
                    reason="released_by_user",
                    sink=sink,
                )
        self._emit(
            BoardEventKind.COMMITTED,
            now,
            d.id,
            payload={"released_tags": released_tags},
            sink=sink,
        )

    # ----- 过期判定 ----------------------------------------------------

    def _overlay_expired(self, d: Directive, now: float) -> bool:
        scope = d.scope
        if scope.kind == ScopeKind.PERSISTENT:
            return False
        if scope.kind == ScopeKind.DURATION:
            assert d.effective_at is not None and scope.duration_s is not None
            return now >= d.effective_at + scope.duration_s
        if scope.kind == ScopeKind.EPHEMERAL:
            # ephemeral 由 LLMControlBehavior 完成后主动 release，Board 不主动过期
            return False
        if scope.kind == ScopeKind.UNTIL:
            # UNTIL 需要 game_state → 由仲裁层调 release，不在此判定
            return False
        # 未覆盖的 scope kind（不应发生）
        return False  # type: ignore[unreachable]

    def _release_claims_for(self, directive_id: str) -> None:
        self.unit_claims = {
            tag: rec for tag, rec in self.unit_claims.items() if rec.directive_id != directive_id
        }

    def _claim_priority(self, rec: ClaimRecord) -> int:
        # 取生成该 claim 的 directive 的 priority；找不到 fallback 50
        for d in self.overlays:
            if d.id == rec.directive_id:
                return d.priority
        return 50

    # ----- 查询 --------------------------------------------------------

    def active_strategy(self, stage: StageKind) -> StrategySlot | None:
        return self.slots[stage]

    def all_strategies(self) -> dict[StageKind, StrategySlot | None]:
        return dict(self.slots)

    def overlays_of(self, t: DirectiveType) -> list[Directive]:
        return [d for d in self.overlays if d.type == t]

    def is_claimed(self, unit_tag: int) -> bool:
        return unit_tag in self.unit_claims

    # ----- 事件 stream -------------------------------------------------

    def consume_events(self) -> list[BoardEvent]:
        """取出累积事件并清空。"""
        out = self._events
        self._events = []
        return out

    def _emit(
        self,
        kind: BoardEventKind,
        ts: float,
        directive_id: str | None,
        *,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
        sink: list[BoardEvent] | None = None,
    ) -> None:
        ev = BoardEvent(
            kind=kind,
            ts=ts,
            directive_id=directive_id,
            reason=reason,
            payload=payload or {},
        )
        self._events.append(ev)
        if sink is not None:
            sink.append(ev)

    # ----- 反向工具：dump（debug 用） ---------------------------------

    def __repr__(self) -> str:
        return (
            f"<DirectiveBoard stage={self.current_stage.value} "
            f"slots={ {k.value: (v.strategy_id if v else None) for k, v in self.slots.items()} } "
            f"overlays={len(self.overlays)} pending={len(self.pending)} "
            f"claims={len(self.unit_claims)}>"
        )

    def all_events(self) -> Iterable[BoardEvent]:
        """已 emit（不消费）。供单测断言。"""
        return list(self._events)
