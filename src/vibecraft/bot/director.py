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

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from vibecraft.bot.event_bus import EventBus
from vibecraft.bot.facade import Sc2Facade, UnitRole
from vibecraft.directives.board import (
    BoardEvent,
    BoardEventKind,
    DirectiveBoard,
)
from vibecraft.directives.models import (
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
from vibecraft.directives.types import (
    DirectiveType,
    StageKind,
    is_view_directive,
)
from vibecraft.llm.parser import IntentParser
from vibecraft.llm.prompt import ParseContext
from vibecraft.llm.schema import (
    AmbiguousParse,
    IntentParseResult,
    ParseError,
    ParseOutcome,
)
from vibecraft.logging_.session import GameSession
from vibecraft.logging_.types import Event, EventKind, LogStream

if TYPE_CHECKING:
    from vibecraft.bot.task_monitor import TaskMonitor
    from vibecraft.strategy.library import StrategyLibrary


@dataclass
class DirectorConfig:
    """运行时配置。"""

    commit_delay_s: float = 1.5
    recent_command_buffer: int = 3
    snapshot_interval_ticks: int = 45  # ~2s 兜底周期（realtime ~22.4 tick/s × 45 ≈ 2s）


@dataclass
class _RecentCommand:
    text: str
    ts: float


@dataclass(slots=True)
class Recommendation:
    """bot 推荐玩家可以接的下一阶段剧本(等玩家 confirm,不自动 submit)。

    source 标识推荐来源:
      - default: yaml default_transitions[0]
      - abort:   yaml abort_signals 命中
      - llm:     LLM 兜底(yaml 没匹配上时)
    """

    stage: StageKind
    strategy_id: str
    display_name: str
    reason: str
    source: str  # "default" / "abort" / "llm"


@dataclass(slots=True)
class Tactics:
    """bot 当前内部宏观意图(rule-based 推断,非 sharpy 自带概念)。

    stance 取值:
      attacking / defending / expanding / scouting / harassing / sustaining
    """

    stance: str
    label: str  # 中文 + emoji,直接给 UI 显示
    reason: str  # "优势 Overwhelming,4 BG 折跃完"


class Director:
    """主编排器。"""

    def __init__(
        self,
        facade: Sc2Facade,
        parser: IntentParser,
        session: GameSession,
        board: DirectiveBoard | None = None,
        config: DirectorConfig | None = None,
        library: StrategyLibrary | None = None,
        event_bus: EventBus | None = None,
        bot: Any | None = None,
    ) -> None:
        self.facade = facade
        self.parser = parser
        self.session = session
        self.config = config or DirectorConfig()
        self.board = board or DirectiveBoard(commit_delay_s=self.config.commit_delay_s)
        self.library = library
        self._recent_commands: list[_RecentCommand] = []
        self._committed_count = 0
        # P5.C: bot backref（sharpy KnowledgeBot 实例；向后兼容：不传则 None）
        self._bot = bot
        # P3.2: task_monitor（需要 event_bus；不传则为 None，所有调用有 None-guard）
        self.task_monitor: TaskMonitor | None
        if event_bus is not None:
            from vibecraft.bot.task_monitor import TaskMonitor

            self.task_monitor = TaskMonitor(board=self.board, event_bus=event_bus)
        else:
            self.task_monitor = None
        # 跟踪 in-flight directive（submit 后 → committed/revoked 前）。
        # Board 的 strategy_set / unit_release 不会进 overlays，需要这层映射才能在
        # COMMITTED 事件里把 directive 取出来 dispatch。
        self._in_flight: dict[str, Directive] = {}
        # P1.2 L3 standing orders（persistent=True 的 unit_claim 不走 _in_flight）
        self.standing_orders: list[Directive] = []
        # P5.E: standing order directive_id → resolved unit tags（sharpy 让位跟踪）
        self._standing_order_tags: dict[str, set[int]] = {}
        # P2 L4 production overrides（PRODUCTION/TECH/EXPANSION_OVERRIDE 不走 _in_flight）
        self.production_overrides: list[Directive] = []
        # snapshot / event 推送回调（P0 / P1）
        self._snapshot_callback: Callable[[dict[str, Any]], None] | None = None
        self._event_callback: Callable[[dict[str, Any]], None] | None = None
        # snapshot 兜底周期计数器
        self._tick_count: int = 0
        # 当前 bot 推荐(snapshot 透传给 PWA,等玩家 confirm 才 submit;不自动转)
        self._pending_recommendation: Recommendation | None = None
        # 玩家已忽略过的推荐(key=(stage,strategy_id)),不再重复推
        self._dismissed_recommendations: set[tuple[StageKind, str]] = set()
        # 当前 bot 内部意图(rule-based,见 _compute_tactics)
        self._tactics: Tactics | None = None
        # 玩家 voice 切剧本但时机已过 → 拦下来等"硬转"确认;(directive, reasons)
        self._pending_force_strategy: tuple[Directive, list[str]] | None = None

    # ------------------------------------------------------------------
    # snapshot / event 回调注入（P0 / P1）
    # ------------------------------------------------------------------

    def setup_task_monitor(self, event_bus: EventBus) -> None:
        """事后注入 event_bus + 创建 task_monitor（bot on_start 调用，director_factory 不持有 event_bus 时用）。"""
        from vibecraft.bot.task_monitor import TaskMonitor

        self.task_monitor = TaskMonitor(board=self.board, event_bus=event_bus)

    def set_snapshot_callback(self, cb: Callable[[dict[str, Any]], None]) -> None:
        """注入 snapshot 推送回调（game_process 在构造 bot 后调用）。"""
        self._snapshot_callback = cb

    def set_initial_strategy(self, stage: StageKind, strategy_id: str, now: float) -> None:
        """bot 启动时初始化某阶段剧本(反映 ares 选的默认 opening)。

        - bypass board 1.5s commit delay,立即让手机 UI 看到剧本卡片
        - 用 BOT_INTERNAL 来源,玩家 VOICE 指令随时可覆盖
        - 立即 push 一次 snapshot,即使 _snapshot_callback 还没准备好也无害(callback None 时跳过)
        - 幂等:若该 stage slot 已设,不动
        """
        if self.board.set_initial_slot(stage, strategy_id, now):
            self._push_snapshot(now)

    def set_event_callback(self, cb: Callable[[dict[str, Any]], None]) -> None:
        """注入 event 推送回调（P1）。"""
        self._event_callback = cb

    # ------------------------------------------------------------------
    # snapshot 构造（P0-1）
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_current_phase_id(
        phases: list[Any], supply_used: int, game_time: float
    ) -> str | None:
        """根据 supply / time 阈值推断 OpeningBuild 的 current phase id。

        当前 phase = 最后一个"已开始"的 phase。
        phase 已开始 iff (start_at_supply 非 None 且 supply >= 它) 或
                       (start_at_time 非 None 且 time >= 它)。
        都没 phase 满足时返回第一个 phase id(默认开局)。
        """
        if not phases:
            return None
        current: str = str(phases[0].id)  # 默认第一个
        for p in phases:
            started = False
            if p.start_at_supply is not None and supply_used >= p.start_at_supply:
                started = True
            if p.start_at_time is not None and game_time >= p.start_at_time:
                started = True
            if started:
                current = str(p.id)
        return current

    def build_snapshot(self, now: float) -> dict[str, Any]:
        """组装 snapshot 帧 payload（§1.1 MVP 子集：strategy + recent_commands）。

        library 为 None 时，display 字段 fallback 成 id（向后兼容 + 单测用）。

        M5: MidgameStance / LategameDoctrine 带 attack_window / micro_doctrine 文案，
        让手机 UI 显示"当前剧本的进攻时机"，实现信息透明（bot 行为本身不变）。

        phase stepper(2026-05-16): OpeningBuild slot 带 current_phase_id,
        PWA stepper 据此渲染"已完成 ✓ / 当前 ▶ / 未来 ○"。推断依据
        Phase.start_at_supply / start_at_time 阈值,bot.time / supply_used 从 facade.get_state() 取。
        """
        from vibecraft.strategy.models import LategameDoctrine, MidgameStance, OpeningBuild

        # 取一次 bot state 用于 phase tracking(facade 可能 raise,容错)
        try:
            state = self.facade.get_state()
            cur_supply = int(state.supply_used)
            cur_time = float(state.game_time)
        except Exception:
            cur_supply = 0
            cur_time = 0.0

        def _slot_view(stage: StageKind) -> dict[str, Any] | None:
            slot = self.board.slots.get(stage)
            if slot is None:
                return None
            sid = slot.strategy_id
            display = sid  # fallback
            phases: list[dict[str, Any]] | None = None
            attack_window: dict[str, Any] | None = None
            micro_doctrine: list[str] | None = None
            current_phase_id: str | None = None
            if self.library is not None:
                try:
                    strat = self.library.get(sid)
                    display = strat.display_name_zh
                    if isinstance(strat, OpeningBuild):
                        phases = [
                            {"id": p.id, "display": p.display, "subtitle": p.subtitle}
                            for p in strat.phases
                        ]
                        current_phase_id = self._compute_current_phase_id(
                            strat.phases, cur_supply, cur_time
                        )
                    elif isinstance(strat, MidgameStance):
                        # M5: 透传 attack_window / micro_doctrine，供 PWA 剧本卡片展示
                        if strat.attack_window is not None:
                            attack_window = {
                                "open_at": strat.attack_window.open_at,
                                "close_at": strat.attack_window.close_at,
                            }
                        if strat.micro_doctrine:
                            micro_doctrine = list(strat.micro_doctrine)
                    elif isinstance(strat, LategameDoctrine) and strat.engagement_doctrine:
                        # M5: lategame 用 engagement_doctrine 作为 micro_doctrine 展示
                        micro_doctrine = list(strat.engagement_doctrine)
                except Exception:
                    pass
            entry: dict[str, Any] = {
                "id": sid,
                "display": display,
                # 来源标识:voice(玩家) / auto_transition(剧本完成自动) / bot_internal(开局默认)
                # PWA 据此渲染 badge 区分"玩家安排"vs"bot 自选"
                "set_by": slot.set_by.value,
            }
            if phases is not None:
                entry["phases"] = phases
            if current_phase_id is not None:
                entry["current_phase_id"] = current_phase_id
                # phase 全完成标志:current = 最后一个 phase id
                if phases and current_phase_id == phases[-1]["id"]:
                    entry["all_phases_complete"] = True
            if attack_window is not None:
                entry["attack_window"] = attack_window
            if micro_doctrine is not None:
                entry["micro_doctrine"] = micro_doctrine
            return entry

        snapshot: dict[str, Any] = {
            "type": "snapshot",
            "ts": round(now, 3),
            "strategy": {
                "current_stage": self.board.current_stage.value,
                "opening": _slot_view(StageKind.OPENING),
                "midgame": _slot_view(StageKind.MIDGAME),
                "lategame": _slot_view(StageKind.LATEGAME),
            },
            "recent_commands": [
                {"text": c.text, "ts": round(c.ts, 3)} for c in self._recent_commands
            ],
            # P1.3 L3 standing orders 透传
            "standing_orders": [self._standing_order_view(s) for s in self.standing_orders],
            # P2 L4 production overrides 透传
            "production_overrides": [
                self._production_override_view(s) for s in self.production_overrides
            ],
            # P3.5 active tactical objectives（L2 in-flight TACTICAL_OBJECTIVE）
            "active_tactics": [
                self._tactical_view(d)
                for d in self._in_flight.values()
                if d.type == DirectiveType.TACTICAL_OBJECTIVE
            ],
        }
        # bot 推荐(玩家未 confirm 前一直 carry,confirm 后清掉)
        if self._pending_recommendation is not None:
            r = self._pending_recommendation
            snapshot["recommendation"] = {
                "stage": r.stage.value,
                "strategy_id": r.strategy_id,
                "display_name": r.display_name,
                "reason": r.reason,
                "source": r.source,
            }
        # bot 内部意图(进攻/守家/开矿/...)
        if self._tactics is not None:
            t = self._tactics
            snapshot["tactics"] = {
                "stance": t.stance,
                "label": t.label,
                "reason": t.reason,
            }
        # 待玩家确认的"硬转":voice 切剧本但时机已过,被拦下
        if self._pending_force_strategy is not None:
            d, reasons = self._pending_force_strategy
            from vibecraft.directives.models import StrategySetPayload

            payload = d.payload
            if isinstance(payload, StrategySetPayload):
                # 取剧本显示名
                display = payload.strategy_id
                if self.library is not None:
                    try:
                        s = self.library.get(payload.strategy_id)
                        display = getattr(s, "display_name_zh", payload.strategy_id)
                    except Exception:
                        pass
                snapshot["pending_force_strategy"] = {
                    "stage": payload.stage,
                    "strategy_id": payload.strategy_id,
                    "display_name": display,
                    "source_text": d.source_text or "",
                    "reasons": reasons,
                }
        return snapshot

    def _push_snapshot(self, now: float) -> None:
        """推 snapshot（若 callback 已注入）。"""
        if self._snapshot_callback is not None:
            self._snapshot_callback(self.build_snapshot(now))

    def _standing_order_view(self, d: Directive) -> dict[str, Any]:
        """把一条 standing order Directive 转成 snapshot 里的 view dict（P1.3）。

        字段：id / display / issued_at / selector / task_summary。
        """
        payload = d.payload
        display = self._format_standing_order_display(payload)
        view: dict[str, Any] = {
            "id": d.id,
            "display": display,
            "issued_at": d.issued_at,
        }
        if isinstance(payload, UnitClaimPayload):
            view["selector"] = payload.selector.model_dump(mode="json", exclude_none=True)
            view["task_summary"] = payload.task.primary_action.verb.value
        else:
            view["selector"] = {}
            view["task_summary"] = ""
        return view

    def _format_standing_order_display(self, payload: Any) -> str:
        """中文人话格式：'{unit_type} {verb} {target_display}'（P1.3）。

        例：'Phoenix patrol natural' / 'Probe hold_position enemy_main_gas'。
        target_display 优先 named_spot，次 unit_type，fallback '?'。
        """
        if not isinstance(payload, UnitClaimPayload):
            return "未知 standing"
        unit_type = payload.selector.unit_type or "单位"
        verb = payload.task.primary_action.verb.value
        target = payload.task.primary_action.target
        target_display = target.named_spot or target.unit_type or "?"
        return f"{unit_type} {verb} {target_display}"

    # ------------------------------------------------------------------
    # P2 production_overrides snapshot helpers
    # ------------------------------------------------------------------

    # 神族单位 canonical→中文 display 表（从 aliases/protoss.yaml units 节提取）
    _UNIT_ZH: ClassVar[dict[str, str]] = {
        "Probe": "探机",
        "Zealot": "叉子",
        "Stalker": "追猎",
        "Sentry": "哨兵",
        "Adept": "使徒",
        "HighTemplar": "HT",
        "DarkTemplar": "DT",
        "Archon": "白球",
        "Immortal": "不朽",
        "Observer": "OB",
        "WarpPrism": "棱镜",
        "Colossus": "巨像",
        "Disruptor": "干扰者",
        "Phoenix": "凤凰",
        "VoidRay": "虚空",
        "Oracle": "先知",
        "Tempest": "风暴战舰",
        "Carrier": "航母",
        "Mothership": "母舰",
    }

    # ------------------------------------------------------------------
    # P3.5 active_tactics snapshot helpers
    # ------------------------------------------------------------------

    _TACTICAL_VERB_ZH: ClassVar[dict[str, str]] = {
        "attack": "进攻",
        "defend": "守",
        "scout": "探",
        "expand": "开矿",
        "harass": "骚扰",
        "drop": "投放",
        "vision": "探视野",
        "raze": "拆",
        "retreat": "撤退",
        "regroup": "集结",
        "split": "分兵",
    }

    def _format_tactical_display(self, payload: Any) -> str:
        """中文人话格式：'{verb_zh} {target_area}'（P3.5）。

        例：'进攻 enemy_natural' / '探 enemy_main' / '骚扰 (12.5, 34.0)'。
        target_area:named_spot 直显，tuple→坐标，None→'自定'。
        """
        from vibecraft.directives.models import TacticalObjectivePayload

        if not isinstance(payload, TacticalObjectivePayload):
            return "未知战术"
        verb_zh = self._TACTICAL_VERB_ZH.get(payload.verb, payload.verb)
        if payload.target_area is None:
            target_display = "自定"
        elif isinstance(payload.target_area, str):
            target_display = payload.target_area
        else:
            # tuple[float, float]
            target_display = f"({payload.target_area[0]}, {payload.target_area[1]})"
        return f"{verb_zh} {target_display}"

    def _tactical_view(self, d: Directive) -> dict[str, Any]:
        """把一条 TACTICAL_OBJECTIVE Directive 转成 snapshot 里的 view dict（P3.5）。

        字段：id / display / verb / target_area / issued_at。
        """
        from vibecraft.directives.models import TacticalObjectivePayload

        payload = d.payload
        display = self._format_tactical_display(payload)
        verb = payload.verb if isinstance(payload, TacticalObjectivePayload) else ""
        if isinstance(payload, TacticalObjectivePayload):
            if payload.target_area is None:
                target_area: str | None = None
            elif isinstance(payload.target_area, str):
                target_area = payload.target_area
            else:
                target_area = f"({payload.target_area[0]}, {payload.target_area[1]})"
        else:
            target_area = None
        return {
            "id": d.id,
            "display": display,
            "verb": verb,
            "target_area": target_area,
            "issued_at": d.issued_at,
        }

    def _production_override_view(self, d: Directive) -> dict[str, Any]:
        """把一条 production override Directive 转成 snapshot 里的 view dict（P2）。

        字段：id / display / issued_at。
        """
        payload = d.payload
        display = self._format_production_override_display(payload)
        return {
            "id": d.id,
            "display": display,
            "issued_at": d.issued_at,
        }

    def _format_production_override_display(self, payload: Any) -> str:
        """中文 display 格式（P2）：
        - PRODUCTION_OVERRIDE → '出 N <unit_zh>'（alias 翻译，无 alias 用英文）
        - TECH_OVERRIDE       → '研 <upgrade>'
        - EXPANSION_OVERRIDE  → '开 N 矿'
        """
        if isinstance(payload, ProductionOverridePayload):
            unit_zh = self._UNIT_ZH.get(payload.unit_type, payload.unit_type)
            return f"出 {payload.count} {unit_zh}"
        if isinstance(payload, TechOverridePayload):
            return f"研 {payload.upgrade_id}"
        if isinstance(payload, ExpansionOverridePayload):
            return f"开 {payload.target_count} 矿"
        return "未知 override"

    def _push_event(self, event_dict: dict[str, Any]) -> None:
        """推 event 帧（若 callback 已注入）。"""
        if self._event_callback is not None:
            self._event_callback(event_dict)

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

    def _maybe_attach_task_monitor(self, submitted: Directive) -> None:
        """P3.2: done_when 非空时把 directive 注册到 task_monitor。"""
        if self.task_monitor is None:
            return
        dw = submitted.payload.done_when
        if dw is None:
            return
        from pydantic import BaseModel

        done_when_dict: dict[str, Any] = dw.model_dump() if isinstance(dw, BaseModel) else {}
        self.task_monitor.attach_directive(
            directive_id=submitted.id,
            done_when=done_when_dict,
            issued_at=submitted.issued_at,
            timeout_s=submitted.payload.timeout_s,
        )

    def _submit_directives(self, directives: list[Directive], now: float) -> None:
        from vibecraft.directives.types import IssuedBy

        for d in directives:
            d_with_ts = d.model_copy(update={"issued_at": now})
            if is_view_directive(d_with_ts.type):
                self._dispatch_view(d_with_ts, now)
            elif d_with_ts.type == DirectiveType.STRATEGY_CANCEL:
                self._dispatch_cancel(d_with_ts, now)
            elif (
                d_with_ts.type == DirectiveType.STRATEGY_SET
                and d_with_ts.issued_by == IssuedBy.VOICE
            ):
                # VOICE 切剧本前先检测时机;过期 → 拦下来等玩家硬转确认
                reasons = self._check_strategy_obsolete(d_with_ts)
                if reasons:
                    self._pending_force_strategy = (d_with_ts, reasons)
                    self._push_snapshot(now)
                    continue
                submitted = self.board.submit(d_with_ts, now=now)
                self._log_directive(
                    "submitted", submitted, now, effective_at=submitted.effective_at
                )
                self._in_flight[submitted.id] = submitted
                # P3.2: 注册到 task_monitor
                self._maybe_attach_task_monitor(submitted)
            else:
                submitted = self.board.submit(d_with_ts, now=now)
                self._log_directive(
                    "submitted", submitted, now, effective_at=submitted.effective_at
                )
                # P1.2: persistent=True 的 unit_claim 进 standing_orders，不进 _in_flight
                if (
                    isinstance(submitted.payload, UnitClaimPayload)
                    and submitted.payload.persistent
                ):
                    self.standing_orders.append(submitted)
                    # P5.E: 立即 resolve selector + 让 sharpy 让位（set_unit_role）
                    self._assign_standing_order_units(submitted)
                # P2: L4 production/tech/expansion override 进 production_overrides
                elif submitted.type in (
                    DirectiveType.PRODUCTION_OVERRIDE,
                    DirectiveType.TECH_OVERRIDE,
                    DirectiveType.EXPANSION_OVERRIDE,
                ):
                    self.production_overrides.append(submitted)
                else:
                    self._in_flight[submitted.id] = submitted
                # P3.2: 注册到 task_monitor（有 done_when 时才有意义，但 attach 本身 None-safe）
                self._maybe_attach_task_monitor(submitted)

    def _log_directive(self, event: str, d: Directive, now: float, **extra: object) -> None:
        """向 directives.jsonl 写一行 directive 生命周期记录。

        event: "submitted" / "committed" / "released" / "revoked" 等
        d: 对应的 Directive 对象
        extra: 附加字段（effective_at / reason 等）
        """
        record: dict[str, object] = {
            "ts": round(now, 3),
            "event": event,
            "directive_id": d.id,
            "type": d.type.value,
            "issued_by": d.issued_by.value,
            "issued_at": d.issued_at,
            **extra,
        }
        self.session.log(LogStream.DIRECTIVES, record)

    def _assign_standing_order_units(self, submitted: Directive) -> None:
        """P5.E: standing order submit 时解析 selector → tags + 通知 sharpy 让位。

        bot 不存在（单测/unit-only 场景）时 hasattr 兜底跳过，不影响现有测试。
        tags 记录到 _standing_order_tags，revoke 时反向 release。
        """
        if not isinstance(submitted.payload, UnitClaimPayload):
            return
        payload = submitted.payload
        tags = self.facade.resolve_selector(
            unit_type=payload.selector.unit_type,
            tag=payload.selector.tag,
            tags=payload.selector.tags,
        )
        if not tags:
            return
        self._standing_order_tags[submitted.id] = set(tags)
        for tag in tags:
            self.facade.set_unit_role(tag, UnitRole.LLM_CONTROLLED)

    def _release_standing_order_units(self, directive_id: str) -> None:
        """P5.E: revoke_standing_order 时归还 sharpy 让位（set_unit_role 的反向）。

        调用 facade.release_unit_role 把单位从 LLM_CONTROLLED 移出，
        让 sharpy Manager 在下一轮重新接管。
        """
        tags = self._standing_order_tags.pop(directive_id, set())
        for tag in tags:
            if hasattr(self.facade, "release_unit_role"):
                self.facade.release_unit_role(tag)

    # ------------------------------------------------------------------
    # 剧本时机偏差检测(自动从 yaml phase + steps 推断)
    # ------------------------------------------------------------------

    # 神族 tech 建筑全集(只看科技建筑,不含 Pylon/Nexus/Assimilator/兵营 BG)
    _PROTOSS_TECH_STRUCTURES: frozenset[str] = frozenset(
        {
            "ROBOTICSFACILITY",
            "ROBOTICSBAY",
            "STARGATE",
            "FLEETBEACON",
            "TWILIGHTCOUNCIL",
            "TEMPLARARCHIVES",
            "DARKSHRINE",
            "FORGE",
        }
    )

    def _check_strategy_obsolete(self, directive: Directive) -> list[str]:
        """检测剧本时机偏差。返回偏差原因列表(空 → 没过期)。

        判定:
        1. 建筑偏差:已造科技建筑中,有"该剧本 build steps 不需要的"
           (4bg 只需 CyberneticsCore → 若已有 RoboticsFacility,偏差)
        2. 时间/supply 偏差:当前 supply 超过该剧本最后一个 build step supply + 10
           (整个 build 早就应该跑完,玩家现在再切来不及)

        只对 OpeningBuild 检测(midgame / lategame 没有"必须前置建筑"语义)。
        """
        from vibecraft.directives.models import StrategySetPayload
        from vibecraft.strategy.models import OpeningBuild

        payload = directive.payload
        if not isinstance(payload, StrategySetPayload):
            return []
        if self.library is None:
            return []
        try:
            strat = self.library.get(payload.strategy_id)
        except Exception:
            return []
        if not isinstance(strat, OpeningBuild):
            return []  # midgame/lategame 不检测

        reasons: list[str] = []
        try:
            state = self.facade.get_state()
        except Exception:
            return []

        # 1. 建筑偏差
        allowed = {step.obj.upper() for step in strat.parsed_steps() if step.verb == "build"}
        forbidden = self._PROTOSS_TECH_STRUCTURES - allowed
        actual_forbidden = state.structures_built & forbidden
        if actual_forbidden:
            names = "/".join(sorted(actual_forbidden))
            reasons.append(f"已造 {names},该剧本走的是不同科技路线")

        # 2. supply 偏差
        max_step_supply = max((step.supply for step in strat.parsed_steps()), default=0)
        threshold = max_step_supply + 10
        if state.supply_used > threshold > 0:
            reasons.append(
                f"当前 supply {state.supply_used},该剧本 build 应在 supply {max_step_supply} 前完成"
            )

        return reasons

    def confirm_force_strategy(self, now: float) -> None:
        """玩家在 PWA 点 [硬转] → 强制 submit 之前被拦的 STRATEGY_SET。"""
        if self._pending_force_strategy is None:
            return
        directive, _reasons = self._pending_force_strategy
        self._pending_force_strategy = None
        directive = directive.model_copy(
            update={
                "issued_at": now,
                "source_text": (directive.source_text or "voice") + " (force)",
            }
        )
        submitted = self.board.submit(directive, now=now)
        self._in_flight[submitted.id] = submitted
        self._push_snapshot(now)

    def cancel_force_strategy(self) -> None:
        """玩家在 PWA 点 [取消] → drop 被拦的 directive。"""
        self._pending_force_strategy = None

    def revoke_standing_order(self, directive_id: str, now: float) -> bool:
        """玩家通过 revoke_directive 上行帧撤销 standing order（P1.2）。

        从 standing_orders 列表移除，释放 sharpy 让位（P5.E），
        通知 board（P5 已支持 committed overlay 撤销），并推一次 snapshot。
        向后兼容保留；P1.4+ 的新代码改用 revoke_directive。
        """
        before = len(self.standing_orders)
        self.standing_orders = [s for s in self.standing_orders if s.id != directive_id]
        if len(self.standing_orders) < before:
            # P5.E: 归还 sharpy 让位（LLM_CONTROLLED → sharpy 重新接管）
            self._release_standing_order_units(directive_id)
            # 通知 board（P5: board.revoke 现已支持 committed overlays）
            self.board.revoke(directive_id, now)
            self._push_snapshot(now)
            return True
        return False

    def revoke_production_override(self, directive_id: str, now: float) -> bool:
        """从 production_overrides 列表移除指定 directive（P2）。

        通知 board + 推 snapshot，语义镜像 revoke_standing_order。
        """
        before = len(self.production_overrides)
        self.production_overrides = [s for s in self.production_overrides if s.id != directive_id]
        if len(self.production_overrides) < before:
            self.board.revoke(directive_id, now)
            self._push_snapshot(now)
            return True
        return False

    def revoke_directive(self, directive_id: str, now: float) -> bool:
        """统一撤销接口（P2）：先尝试 standing_orders，再尝试 production_overrides。

        ws.py 和 bot._tick_view_channel 的 revoke_directive 分支改调此方法，
        不再直接调用 revoke_standing_order。
        """
        if self.revoke_standing_order(directive_id, now):
            return True
        return self.revoke_production_override(directive_id, now)

    def _dispatch_cancel(self, directive: Directive, now: float) -> None:
        """处理 STRATEGY_CANCEL:清掉 board 对应 slot + 切 sustain plan。

        玩家说"取消" / "停下" / "等等" → bot 切到 Sustain plan(macro + 守家,不出门),
        等下个剧本指令。
        """
        from vibecraft.directives.models import StrategyCancelPayload

        payload = directive.payload
        if not isinstance(payload, StrategyCancelPayload):
            return
        cleared_stages: list[StageKind] = []
        targets: list[StageKind] = (
            list(StageKind) if payload.stage == "all" else [StageKind(payload.stage)]
        )
        for stage in targets:
            if self.board.slots.get(stage) is not None:
                self.board.slots[stage] = None
                cleared_stages.append(stage)
        # 切 sustain plan(facade.set_build 即时生效)
        import contextlib

        with contextlib.suppress(Exception):
            self.facade.set_build("sustain")
        # 清掉推荐(也许之前推荐是基于刚被清的 opening 算的)
        self._pending_recommendation = None
        # log 事件,触发 snapshot 刷新
        self.session.log_event(
            Event(
                ts=now,
                kind=EventKind.STRATEGY_SET,  # 复用,payload 标记是 cancel
                payload={
                    "action": "cancel",
                    "cleared_stages": [s.value for s in cleared_stages],
                    "directive_id": directive.id,
                },
                priority="medium",
                caused_by=directive.source_text or "voice",
            )
        )
        # 主动推 snapshot
        self._push_snapshot(now)

    def _remember_command(self, text: str, now: float) -> None:
        self._recent_commands.append(_RecentCommand(text=text, ts=now))
        if len(self._recent_commands) > self.config.recent_command_buffer:
            self._recent_commands.pop(0)

    # ------------------------------------------------------------------
    # 每 tick
    # ------------------------------------------------------------------

    def on_tick(self, now: float) -> list[BoardEvent]:
        events = self.board.tick(now)
        need_snapshot = False
        for ev in events:
            self._dispatch_event(ev)
            # 变化推：strategy 变化时立即推 snapshot（P0-2）
            if ev.kind in (BoardEventKind.STRATEGY_CHANGED, BoardEventKind.PHASE_TRANSITIONED):
                need_snapshot = True

        # P3.2: task_monitor 检查 done
        if self.task_monitor is not None:
            game_state = getattr(self, "_bot", None)
            completed_ids = self.task_monitor.tick(now, game_state=game_state)
            for did in completed_ids:
                self.board.complete(did, now)
                self.task_monitor.detach(did)
                # 从各列表清理
                self._in_flight.pop(did, None)
                self.production_overrides = [
                    d for d in self.production_overrides if d.id != did
                ]
                need_snapshot = True

        # 不再自动 submit transition directive;只更新 self._pending_recommendation,
        # 等玩家 confirm_recommendation 才真正 submit(见 game_process 上行帧)。
        prev_reco = self._pending_recommendation
        self._update_recommendation(now)
        # 推荐变化时也推一次 snapshot(否则用户要等下次兜底周期)
        if prev_reco != self._pending_recommendation:
            need_snapshot = True

        # 兜底周期推（P0-2）
        self._tick_count += 1
        if self._tick_count >= self.config.snapshot_interval_ticks:
            self._tick_count = 0
            need_snapshot = True

        if need_snapshot:
            self._push_snapshot(now)

        return events

    def _update_recommendation(self, now: float) -> None:
        """计算 self._pending_recommendation(opening 完成 → 推荐 midgame)。

        判定:opening 已设 + 当前 phase = last phase + midgame 空 → 推荐
        来源优先级:abort_signals 命中 > default_transitions[0] > LLM 兜底(留 TODO)

        不再自动 submit;玩家 confirm 后才走 submit_directives。
        如果当前推荐被玩家"忽略"(暂时没实现忽略状态,clear 由玩家点其它剧本或 voice 切覆盖)
        """
        if self.library is None:
            self._pending_recommendation = None
            return

        # 当前 stage 已有 slot 时不推荐(玩家已经决策)
        # opening → midgame
        if self.board.slots.get(StageKind.MIDGAME) is not None:
            self._pending_recommendation = None
            return
        opening_slot = self.board.slots.get(StageKind.OPENING)
        if opening_slot is None:
            self._pending_recommendation = None
            return

        try:
            from vibecraft.strategy.models import OpeningBuild

            strat = self.library.get(opening_slot.strategy_id)
            if not isinstance(strat, OpeningBuild) or not strat.phases:
                self._pending_recommendation = None
                return
            state = self.facade.get_state()
            current_phase = self._compute_current_phase_id(
                strat.phases, int(state.supply_used), float(state.game_time)
            )
            if current_phase != strat.phases[-1].id:
                self._pending_recommendation = None
                return

            # opening 完成 → 找推荐
            # TODO: abort_signals 需要 enemy state context 才能 eval,M5+ 接入
            #       现在只走 default_transitions 路径
            if strat.default_transitions:
                target_mid = strat.default_transitions[0].midgame_id
                # 玩家已忽略过这条推荐 → 不再推
                if (StageKind.MIDGAME, target_mid) in self._dismissed_recommendations:
                    self._pending_recommendation = None
                    return
                target_strat = self.library.get(target_mid)
                display = getattr(target_strat, "display_name_zh", target_mid)
                self._pending_recommendation = Recommendation(
                    stage=StageKind.MIDGAME,
                    strategy_id=target_mid,
                    display_name=display,
                    reason=f"{strat.display_name_zh} 完成 → yaml 默认转 {display}",
                    source="default",
                )
                return

            # TODO: LLM 兜底(default 也没匹配 → 让 LLM 推荐)
            # 异步触发,完成回写 self._pending_recommendation,需要 _llm_recommendation_task 保护
            # 不阻塞 on_tick

            self._pending_recommendation = None
        except Exception:
            self._pending_recommendation = None

    def confirm_recommendation(self, now: float) -> None:
        """玩家在 PWA 点 [确认] → 把 self._pending_recommendation submit 成 VOICE directive。

        用 VOICE 来源(不用 AUTO_TRANSITION):玩家显式认可了,等价 voice 命令。
        Submit 后立即 clear self._pending_recommendation,避免下个 snapshot 还推荐这一个。
        """
        reco = self._pending_recommendation
        if reco is None:
            return
        from vibecraft.directives.models import Directive, StrategySetPayload
        from vibecraft.directives.types import IssuedBy

        directive = Directive(
            payload=StrategySetPayload(stage=reco.stage.value, strategy_id=reco.strategy_id),
            issued_at=now,
            issued_by=IssuedBy.VOICE,  # 玩家确认 → 等价 voice
            source_text=f"confirm_recommendation:{reco.stage.value}→{reco.strategy_id}",
        )
        self._pending_recommendation = None
        self._submit_directives([directive], now)

    def dismiss_recommendation(self) -> None:
        """玩家在 PWA 点 [忽略] → 清掉当前推荐,并记入 dismissed 黑名单。

        后续 _update_recommendation 重新计算时跳过同 (stage, strategy_id),
        不再重复推这条。如果换了别的推荐(不同 strategy_id),仍会推新的。
        """
        if self._pending_recommendation is not None:
            r = self._pending_recommendation
            self._dismissed_recommendations.add((r.stage, r.strategy_id))
        self._pending_recommendation = None

    def _dispatch_event(self, ev: BoardEvent) -> None:
        # log 每个事件到 events.jsonl
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

        # 同步写 directives.jsonl —— directive 生命周期全量（submitted 在 _submit_directives 写）
        _directive_lifecycle_kinds = (
            BoardEventKind.COMMITTED,
            BoardEventKind.RELEASED,
            BoardEventKind.REJECTED,
            BoardEventKind.REVOKED,
        )
        if ev.kind in _directive_lifecycle_kinds and ev.directive_id is not None:
            record: dict[str, object] = {
                "ts": round(ev.ts, 3),
                "event": ev.kind.value.split(".")[-1],  # "committed" / "released" / etc.
                "directive_id": ev.directive_id,
                **ev.payload,
            }
            if ev.reason is not None:
                record["reason"] = ev.reason
            self.session.log(LogStream.DIRECTIVES, record)

        # P1-1：A 组埋点 —— BoardEvent → event 帧 dict → _event_callback
        self._maybe_push_event_frame(ev)

        # 仅在 COMMITTED 时下发 facade 调用
        if ev.kind == BoardEventKind.COMMITTED and ev.directive_id is not None:
            self._dispatch_committed_to_facade(ev.directive_id, ev.ts)

    def _maybe_push_event_frame(self, ev: BoardEvent) -> None:
        """把 BoardEvent 转译成设计文档 §9.4 的 event 帧，推到手机（P1-1 A 组）。

        只转译有意义的 kind；SUBMITTED/REVOKED/SUPERSEDED 不推（信息量低）。
        """
        # §9.4 taxonomy 映射
        ws_kind_map: dict[BoardEventKind, str] = {
            BoardEventKind.STRATEGY_CHANGED: "strategy.set",
            BoardEventKind.PHASE_TRANSITIONED: "strategy.phase_change",
            BoardEventKind.COMMITTED: "directive.committed",
            BoardEventKind.RELEASED: "directive.released",
            BoardEventKind.REJECTED: "directive.rejected",
        }
        ws_kind = ws_kind_map.get(ev.kind)
        if ws_kind is None:
            return

        payload: dict[str, Any] = dict(ev.payload)
        if ev.directive_id is not None:
            payload["directive_id"] = ev.directive_id

        # strategy.set / strategy.phase_change：补 display（§2.5）
        if ev.kind == BoardEventKind.STRATEGY_CHANGED:
            sid = payload.get("strategy_id", "")
            if self.library is not None and isinstance(sid, str) and sid:
                try:
                    strat = self.library.get(sid)
                    payload["display"] = strat.display_name_zh
                except Exception:
                    pass

        event_dict = {
            "type": "event",
            "kind": ws_kind,
            "ts": round(ev.ts, 3),
            "payload": payload,
        }
        self._push_event(event_dict)

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
