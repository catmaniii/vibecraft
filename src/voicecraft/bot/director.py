"""Director：串起 IntentParser + DirectiveBoard + Sc2Facade。

每个 tick：
1. board.tick(now) → 收到 BoardEvent[]
2. 对每个 COMMITTED 事件，分派到 facade 调用
3. 把 events log 到 GameSession

玩家话语：
1. parse → IntentParseResult / Ambiguous / Error
2. 成功的话，每个 directive 赋当前 issued_at 后 board.submit
3. VIEW directive 不进 board，直接调 facade
"""

from __future__ import annotations

from dataclasses import dataclass

from voicecraft.bot.facade import Sc2Facade, UnitRole
from voicecraft.directives.board import (
    BoardEvent,
    BoardEventKind,
    DirectiveBoard,
)
from voicecraft.directives.models import (
    BuildAtPayload,
    Directive,
    EngagementConstraintPayload,
    ExpansionOverridePayload,
    MovePayload,
    ProductionOverridePayload,
    ScoutPayload,
    StrategySetPayload,
    TechOverridePayload,
    UnitClaimPayload,
    UnitReleasePayload,
    ViewFollowPayload,
    ViewMovePayload,
    ViewZoomPayload,
)
from voicecraft.directives.types import (
    DirectiveType,
    StageKind,
    is_view_directive,
)
from voicecraft.llm.parser import IntentParser
from voicecraft.llm.prompt import ParseContext
from voicecraft.llm.schema import (
    AmbiguousParse,
    IntentParseResult,
    ParseError,
    ParseOutcome,
)
from voicecraft.logging_.session import GameSession
from voicecraft.logging_.types import Event, EventKind


@dataclass
class DirectorConfig:
    """运行时配置。"""

    commit_delay_s: float = 1.5
    recent_command_buffer: int = 3


@dataclass
class _RecentCommand:
    text: str
    ts: float


class Director:
    """主编排器。"""

    def __init__(
        self,
        facade: Sc2Facade,
        parser: IntentParser,
        session: GameSession,
        board: DirectiveBoard | None = None,
        config: DirectorConfig | None = None,
    ) -> None:
        self.facade = facade
        self.parser = parser
        self.session = session
        self.config = config or DirectorConfig()
        self.board = board or DirectiveBoard(commit_delay_s=self.config.commit_delay_s)
        self._recent_commands: list[_RecentCommand] = []
        self._committed_count = 0
        # 跟踪 in-flight directive（submit 后 → committed/revoked 前）。
        # Board 的 strategy_set / unit_release 不会进 overlays，需要这层映射才能在
        # COMMITTED 事件里把 directive 取出来 dispatch。
        self._in_flight: dict[str, Directive] = {}

    # ------------------------------------------------------------------
    # 玩家话语入口
    # ------------------------------------------------------------------

    async def on_player_command(self, text: str, now: float) -> ParseOutcome:
        ctx = self.build_parse_context(now)
        outcome = await self.parser.parse(text, ctx)

        if isinstance(outcome, IntentParseResult):
            self._submit_directives(outcome.directives, now)
            self._remember_command(text, now)
        elif isinstance(outcome, AmbiguousParse):
            # 暂不 submit；UI 层等玩家二次确认后再 confirm_ambiguous
            self._remember_command(text, now)
        elif isinstance(outcome, ParseError):
            self.session.log_event(
                Event(
                    ts=now,
                    kind=EventKind.DIRECTIVE_FAILED,
                    payload={
                        "user_text": text,
                        "error_kind": outcome.kind.value,
                        "error_message": outcome.message,
                    },
                    priority="low",
                    caused_by=f"voice:{text[:30]}",
                )
            )

        return outcome

    def confirm_ambiguous(self, ambiguous: AmbiguousParse, now: float, accepted: bool) -> None:
        """玩家二次确认 ambiguous parse。"""
        if accepted:
            self._submit_directives(ambiguous.result.directives, now)

    def _submit_directives(self, directives: list[Directive], now: float) -> None:
        for d in directives:
            d_with_ts = d.model_copy(update={"issued_at": now})
            if is_view_directive(d_with_ts.type):
                self._dispatch_view(d_with_ts, now)
            else:
                submitted = self.board.submit(d_with_ts, now=now)
                self._in_flight[submitted.id] = submitted

    def _remember_command(self, text: str, now: float) -> None:
        self._recent_commands.append(_RecentCommand(text=text, ts=now))
        if len(self._recent_commands) > self.config.recent_command_buffer:
            self._recent_commands.pop(0)

    # ------------------------------------------------------------------
    # 每 tick
    # ------------------------------------------------------------------

    def on_tick(self, now: float) -> list[BoardEvent]:
        events = self.board.tick(now)
        for ev in events:
            self._dispatch_event(ev)
        return events

    def _dispatch_event(self, ev: BoardEvent) -> None:
        # log 每个事件
        kind_map = {
            BoardEventKind.STRATEGY_CHANGED: EventKind.STRATEGY_SET,
            BoardEventKind.PHASE_TRANSITIONED: EventKind.STRATEGY_PHASE_CHANGE,
            BoardEventKind.RELEASED: EventKind.DIRECTIVE_RELEASED,
            BoardEventKind.REJECTED: EventKind.DIRECTIVE_FAILED,
            BoardEventKind.COMMITTED: EventKind.DIRECTIVE_COMMITTED,
            BoardEventKind.SUPERSEDED: EventKind.DIRECTIVE_RELEASED,
        }
        if ev.kind in kind_map:
            self.session.log_event(
                Event(
                    ts=ev.ts,
                    kind=kind_map[ev.kind],
                    payload={**ev.payload, "directive_id": ev.directive_id},
                    caused_by=ev.reason,
                )
            )

        # 仅在 COMMITTED 时下发 facade 调用
        if ev.kind == BoardEventKind.COMMITTED and ev.directive_id is not None:
            self._dispatch_committed_to_facade(ev.directive_id, ev.ts)

    def _dispatch_committed_to_facade(self, directive_id: str, now: float) -> None:
        d = self._in_flight.pop(directive_id, None)
        if d is None:
            # 已被 revoke / supersede；忽略
            return
        self._committed_count += 1
        self._apply_to_facade(d, now)

    def _apply_to_facade(self, d: Directive, now: float) -> None:
        payload = d.payload
        t = d.type

        if t == DirectiveType.STRATEGY_SET:
            assert isinstance(payload, StrategySetPayload)
            self.facade.set_build(payload.strategy_id)
            return

        if t == DirectiveType.PRODUCTION_OVERRIDE:
            assert isinstance(payload, ProductionOverridePayload)
            self.facade.set_production_override(
                unit_type=payload.unit_type,
                count=payload.count,
                building_tag=payload.building_tag,
            )
            return

        if t == DirectiveType.TECH_OVERRIDE:
            assert isinstance(payload, TechOverridePayload)
            self.facade.set_tech_override(
                upgrade_id=payload.upgrade_id, building_tag=payload.building_tag
            )
            return

        if t == DirectiveType.EXPANSION_OVERRIDE:
            assert isinstance(payload, ExpansionOverridePayload)
            self.facade.set_expansion_override(payload.target_count)
            return

        if t == DirectiveType.ENGAGEMENT_CONSTRAINT:
            assert isinstance(payload, EngagementConstraintPayload)
            self.facade.set_engagement_stance(payload.stance)
            return

        if t == DirectiveType.UNIT_CLAIM:
            assert isinstance(payload, UnitClaimPayload)
            self._apply_unit_claim(d, payload, now)
            return

        if t == DirectiveType.UNIT_RELEASE:
            assert isinstance(payload, UnitReleasePayload)
            self._apply_unit_release(payload)
            return

        if t == DirectiveType.BUILD_AT:
            assert isinstance(payload, BuildAtPayload)
            self.facade.set_build_location_override(payload.structure_type, payload.point)
            return

        if t == DirectiveType.MOVE:
            assert isinstance(payload, MovePayload)
            tags = self.facade.resolve_selector(
                unit_type=payload.selector.unit_type,
                tag=payload.selector.tag,
                tags=payload.selector.tags,
            )
            for tag in tags:
                self.facade.execute_unit_action(
                    unit_tag=tag, verb="move_to", target=payload.target.model_dump(mode="json")
                )
            return

        if t == DirectiveType.SCOUT:
            assert isinstance(payload, ScoutPayload)
            tags = self.facade.resolve_selector(
                unit_type=(payload.selector.unit_type if payload.selector else None),
                tag=(payload.selector.tag if payload.selector else None),
                tags=(payload.selector.tags if payload.selector else None),
            )
            if not tags:
                # 让 facade 自选 idle probe（fallback：调一次 execute_unit_action with tag=0 占位）
                self.facade.execute_unit_action(
                    unit_tag=0,
                    verb="scout",
                    target=payload.target.model_dump(mode="json"),
                )
            else:
                for tag in tags:
                    self.facade.execute_unit_action(
                        unit_tag=tag,
                        verb="scout",
                        target=payload.target.model_dump(mode="json"),
                    )
            return

    def _apply_unit_claim(self, d: Directive, payload: UnitClaimPayload, now: float) -> None:
        tags = self.facade.resolve_selector(
            unit_type=payload.selector.unit_type,
            tag=payload.selector.tag,
            tags=payload.selector.tags,
        )
        for tag in tags:
            self.facade.set_unit_role(tag, UnitRole.LLM_CONTROLLED)
            # 立即下发首条 primary_action（reaction 留给 M1+）
            self.facade.execute_unit_action(
                unit_tag=tag,
                verb=payload.task.primary_action.verb.value,
                target=payload.task.primary_action.target.model_dump(mode="json"),
                ability_id=payload.task.primary_action.ability_id,
            )

    def _apply_unit_release(self, payload: UnitReleasePayload) -> None:
        sel = payload.selector
        tags = self.facade.resolve_selector(unit_type=sel.unit_type, tag=sel.tag, tags=sel.tags)
        if not tags and sel.claimed is True:
            # 释放所有已 claim
            tags = list(self.board.unit_claims.keys())
        target_role = UnitRole.IDLE if payload.return_to_role == "IDLE" else UnitRole.ARMY
        for tag in tags:
            self.facade.set_unit_role(tag, target_role)

    def _dispatch_view(self, d: Directive, now: float) -> None:
        if isinstance(d.payload, ViewMovePayload):
            self.facade.move_camera(d.payload.target_point)
        elif isinstance(d.payload, ViewFollowPayload):
            self.facade.follow_unit(d.payload.unit_tag)
        elif isinstance(d.payload, ViewZoomPayload):
            self.facade.set_camera_zoom(d.payload.level)
        self.session.log_event(
            Event(
                ts=now,
                kind=EventKind.CAMERA_MOVED,
                payload={"directive_type": d.type.value},
                priority="low",
            )
        )

    # ------------------------------------------------------------------
    # ParseContext 构造（从 facade.get_state + board 当前快照）
    # ------------------------------------------------------------------

    def build_parse_context(self, now: float) -> ParseContext:
        state = self.facade.get_state()
        active: dict[StageKind, str | None] = {}
        for stage in StageKind:
            slot = self.board.slots[stage]
            active[stage] = slot.strategy_id if slot is not None else None
        standing_orders = [
            f"{d.type.value}@{d.id[:6]}"
            for d in self.board.overlays
            if d.type == DirectiveType.UNIT_CLAIM
        ]
        return ParseContext(
            game_time=now,
            current_stage=self.board.current_stage,
            active_strategies=active,
            minerals=state.minerals,
            gas=state.gas,
            supply_used=state.supply_used,
            supply_cap=state.supply_cap,
            expansion_count=state.expansion_count,
            army_summary=dict(state.army_summary),
            enemy_summary=dict(state.enemy_summary),
            standing_orders=standing_orders,
            recent_commands=[c.text for c in self._recent_commands],
        )

    # ------------------------------------------------------------------
    # 内省（单测用）
    # ------------------------------------------------------------------

    @property
    def committed_count(self) -> int:
        return self._committed_count
