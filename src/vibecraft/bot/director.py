"""Director：串起 IntentParser + DirectiveBoard + Sc2Facade。

每个 tick：
1. board.tick(now) → 收到 BoardEvent[]
2. 对每个 COMMITTED 事件，分派到 facade 调用
3. 把 events log 到 GameSession

玩家话语：
1. parse → IntentParseResult / Ambiguous / Error
2. 成功的话，每个 directive 赋当前 issued_at 后 board.submit
3. 视角控制不走 directive 系统 —— PWA 拖小地图直接经 WS frame view_move 调
   bot.facade.move_camera（见 server/ws.py + bot/auto_combat/protoss/bot.py）
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from vibecraft.bot.event_bus import EventBus

logger = logging.getLogger(__name__)
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
    StrategyCancelPayload,
    StrategySetPayload,
    StructureOverridePayload,
    TacticalObjectivePayload,
    TechOverridePayload,
    UnitClaimPayload,
    UnitReleasePayload,
)
from vibecraft.directives.types import (
    DirectiveType,
    StageKind,
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
    # B 摘要式局内 memory(2026-05-17):parse 完成后回填本条话的 outcome 摘要。
    # 下次 build_parse_context 时一起传给 LLM,让它看到自己上次输出过什么(摘要,
    # 不是完整 JSON;C 完整 multi-turn 才传 JSON)。None = 还没 parse 完(罕见
    # 中途异常) / 历史 buffer 在 parse 前先 push 这条 text。
    outcome_summary: str | None = None


@dataclass
class TacticalSquad:
    """B 类 L2 squad 抢占状态（harass / scout）。"""

    directive_id: str
    unit_tags: set[int]
    target: Any  # Point2 or None
    move_type: Any  # sharpy MoveType or None
    verb: str
    n_wanted: int
    n_locked: int


# A 类 verb（全军 override flag 路径）
_A_VERBS: frozenset[str] = frozenset({"attack", "defend", "retreat", "vision"})
# B 类 verb（squad 抢占路径）；raze/regroup/split/drop MVP 留 on_hold
_B_VERBS: frozenset[str] = frozenset({"harass", "scout"})


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
        # M3 L4 status tracking: directive_id → {"status": "pending"|"active"|"on_hold",
        # "reason": str}。pending = 刚 commit;active = bot.train/research/expand 已生效;
        # on_hold = prereq 缺失或 affordability 不够,等条件满足再 active。
        # snapshot.production_overrides[*].status 透传给 PWA;状态变化时 emit
        # directive.status_changed event。
        self._override_status: dict[str, dict[str, str]] = {}
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
        # P0b Task 12: L2 tactical_objective 状态
        self._tactical_squads: dict[str, TacticalSquad] = {}
        self._tactical_overrides: dict[str, str] = {}
        self._current_l2_global_id: str | None = None
        self._current_l2_global_directive: Directive | None = None

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
        snapshot["command_cards"] = self._build_command_cards(now)
        return snapshot

    def _build_command_cards(self, now: float) -> list[dict[str, Any]]:
        """统一 command_cards array，透传 4 层 directive 给 PWA（P0f Task 10）。

        每张卡片 8 字段：id / layer / type / display / issued_at / status /
        status_reason / revokable。

        来源：
        - L1  board.slots（strategy slots）
        - L2  _in_flight 中的 TACTICAL_OBJECTIVE（ephemeral）
        - L3  standing_orders（persistent unit_claim）
        - L4  production_overrides（production / tech / expansion / structure）
        """
        cards: list[dict[str, Any]] = []

        # L1 strategy slots
        for stage, slot in self.board.slots.items():
            if slot is None:
                continue
            sid = slot.strategy_id
            display = sid
            if self.library is not None:
                try:
                    strat = self.library.get(sid)
                    display = getattr(strat, "display_name_zh", sid)
                except Exception:
                    pass
            cards.append(
                {
                    "id": f"l1_{stage.value}",
                    "layer": "L1",
                    "type": "strategy_set",
                    "display": f"{stage.value}: {display}",
                    "issued_at": slot.set_at,
                    "status": "active",
                    "status_reason": "",
                    "revokable": True,
                }
            )

        # L2 active tactics (TACTICAL_OBJECTIVE in _in_flight)
        for d in self._in_flight.values():
            if d.type == DirectiveType.TACTICAL_OBJECTIVE:
                from vibecraft.directives.models import TacticalObjectivePayload

                payload = d.payload
                if isinstance(payload, TacticalObjectivePayload):
                    target = payload.target_area
                    if isinstance(target, (list, tuple)):
                        target_str = f"({target[0]}, {target[1]})"
                    elif target is not None:
                        target_str = str(target)
                    else:
                        target_str = ""
                    display = f"{payload.verb} {target_str}".strip()
                else:
                    display = "tactical"
                st = self._override_status.get(d.id, {})
                cards.append(
                    {
                        "id": d.id,
                        "layer": "L2",
                        "type": "tactical_objective",
                        "display": display,
                        "issued_at": d.issued_at,
                        "status": st.get("status", "active"),
                        "status_reason": st.get("reason", ""),
                        "revokable": True,
                    }
                )
            elif d.type == DirectiveType.ENGAGEMENT_CONSTRAINT:
                from vibecraft.directives.models import EngagementConstraintPayload

                payload = d.payload
                stance = payload.stance if isinstance(payload, EngagementConstraintPayload) else ""
                cards.append(
                    {
                        "id": d.id,
                        "layer": "L2",
                        "type": "engagement_constraint",
                        "display": f"stance: {stance}",
                        "issued_at": d.issued_at,
                        "status": "active",
                        "status_reason": "",
                        "revokable": True,
                    }
                )

        # L3 standing orders (persistent unit_claim)
        for d in self.standing_orders:
            display = self._format_standing_order_display(d.payload)
            cards.append(
                {
                    "id": d.id,
                    "layer": "L3",
                    "type": "unit_claim",
                    "display": display,
                    "issued_at": d.issued_at,
                    "status": "active",
                    "status_reason": "",
                    "revokable": True,
                }
            )

        # L4 production_overrides (production / tech / expansion / structure)
        for d in self.production_overrides:
            st = self._override_status.get(d.id, {})
            display = self._format_production_override_display(d.payload)
            cards.append(
                {
                    "id": d.id,
                    "layer": "L4",
                    "type": d.type.value,
                    "display": display,
                    "issued_at": d.issued_at,
                    "status": st.get("status", "pending"),
                    "status_reason": st.get("reason", ""),
                    "revokable": True,
                }
            )

        return cards

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

    # 神族单位 canonical→中文 display 表（从 docs/aliases/protoss.yaml units 节提取）
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

        字段：id / display / issued_at / status / status_reason(M3 加)。
        status 取值: pending / active / on_hold。PWA 卡片按此染色。
        """
        payload = d.payload
        display = self._format_production_override_display(payload)
        status_info = self._override_status.get(d.id, {})
        view: dict[str, Any] = {
            "id": d.id,
            "display": display,
            "issued_at": d.issued_at,
            "status": status_info.get("status", "pending"),
        }
        reason = status_info.get("reason", "")
        if reason:
            view["status_reason"] = reason
        return view

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

        # B 局内 memory:所有 outcome(含 ParseError) 都记进 _recent_commands +
        # 回填摘要。这样 LLM 下次 parse 看到的不仅是上次说了什么,还看到上次解出了什么。
        self._remember_command(text, now, outcome=outcome)

        if isinstance(outcome, IntentParseResult):
            self._submit_directives(outcome.directives, now)
        elif isinstance(outcome, AmbiguousParse):
            # 暂不 submit；UI 层等玩家二次确认后再 confirm_ambiguous
            pass
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
            if (
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
                # P2: L4 production/tech/expansion/structure override 进 production_overrides
                elif submitted.type in (
                    DirectiveType.PRODUCTION_OVERRIDE,
                    DirectiveType.TECH_OVERRIDE,
                    DirectiveType.EXPANSION_OVERRIDE,
                    DirectiveType.STRUCTURE_OVERRIDE,
                ):
                    self.production_overrides.append(submitted)
                    # M3: L4 wire — emit "已加入生产队列" event 给 PWA(玩家反馈)
                    self._emit_production_queued_event(submitted, now)
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
            self._override_status.pop(directive_id, None)
            self._push_snapshot(now)
            return True
        return False

    def revoke_directive(self, directive_id: str, now: float) -> bool:
        """统一撤销接口（P2/P0g Task 11）：L3 standing → L4 production → L2 tactical → L1 strategy。

        ws.py 和 bot._tick_view_channel 的 revoke_directive 分支改调此方法，
        不再直接调用 revoke_standing_order。
        """
        if self.revoke_standing_order(directive_id, now):
            return True
        if self.revoke_production_override(directive_id, now):
            return True
        if self.revoke_tactical(directive_id, now):
            return True
        return self.revoke_strategy(directive_id, now)

    def revoke_tactical(self, directive_id: str, now: float) -> bool:
        """L2 撤销：清 override flag (A 类) 或释放 squad unit (B 类)。

        A 类（attack/defend/retreat 等）：清 facade override flag，重置 _current_l2_global_id。
        B 类（harass/scout）：遍历 squad.unit_tags，调 facade.release_unit_role 还给 sharpy，
        然后从 _tactical_squads 移除。
        两类可共存（同一 directive 极罕见，但防御处理）。
        """
        cleared = False

        # A 类：override flag 路径
        if directive_id in self._tactical_overrides:
            self._tactical_overrides.pop(directive_id, None)
            if self._current_l2_global_id == directive_id:
                try:
                    self.facade.set_attack_target_override(None)
                    self.facade.set_combat_intent_override(None)
                except Exception as exc:  # pragma: no cover
                    logger.debug("revoke_tactical facade clear fail: %s", exc)
                self._current_l2_global_id = None
            cleared = True

        # B 类：squad 路径
        if directive_id in self._tactical_squads:
            squad = self._tactical_squads.pop(directive_id)
            for tag in squad.unit_tags:
                try:
                    self.facade.release_unit_role(tag)
                except Exception as exc:  # pragma: no cover
                    logger.debug("revoke_tactical release_unit_role(%s) fail: %s", tag, exc)
            cleared = True

        if cleared:
            self._override_status.pop(directive_id, None)
            # board.revoke 若找不到此 id 也不报错（tactical 可能未经 board.submit）
            try:
                self.board.revoke(directive_id, now)
            except Exception as exc:  # pragma: no cover
                logger.debug("revoke_tactical board.revoke fail: %s", exc)
            self._push_event(
                {
                    "kind": "directive.revoked",
                    "ts": now,
                    "payload": {"directive_id": directive_id, "reason": "player_x"},
                }
            )
            self._push_snapshot(now)

        return cleared

    def revoke_strategy(self, directive_id: str, now: float) -> bool:
        """L1 撤销：清 board.slots[stage] + facade.set_build("sustain")。

        接受两种 directive_id 形式：
        - "l1_{stage.value}" 占位 id（Task 10 约定），如 "l1_midgame"
        - 无前缀时尝试按 slot 匹配（当前 StrategySlot 无 directive_id 字段，不支持）
        """
        import contextlib

        target_stage: StageKind | None = None

        if directive_id.startswith("l1_"):
            suffix = directive_id[3:]
            try:
                target_stage = StageKind(suffix)
            except Exception:
                return False
        else:
            # StrategySlot 当前没有 directive_id 字段，无法按真实 id 匹配
            return False

        if target_stage is None:
            return False

        if self.board.slots.get(target_stage) is None:
            return False

        self.board.slots[target_stage] = None

        with contextlib.suppress(Exception):
            self.facade.set_build("sustain")

        self._push_event(
            {
                "kind": "directive.revoked",
                "ts": now,
                "payload": {"directive_id": directive_id, "reason": "player_x"},
            }
        )
        self._push_snapshot(now)
        return True

    def _dispatch_cancel(self, directive: Directive, now: float) -> None:
        """兼容入口（已废弃旁路）。转发到 _apply_strategy_cancel。

        原来直接旁路调用，现在走 board.submit → _apply_to_facade → _apply_strategy_cancel。
        此方法保留防止外部仍有调用；内部不再从 _submit_directives 调用。
        """
        if not isinstance(directive.payload, StrategyCancelPayload):
            return
        self._apply_strategy_cancel(directive.payload, now, directive_id=directive.id)

    def _apply_strategy_cancel(
        self, payload: StrategyCancelPayload, now: float, directive_id: str
    ) -> None:
        """STRATEGY_CANCEL commit 后执行：清 board slot + 切 sustain + log + push snapshot。

        由 _apply_to_facade 调用（board.submit → commit → 这里），不再是旁路直调。
        """
        import contextlib

        cleared_stages: list[StageKind] = []
        targets: list[StageKind] = (
            list(StageKind) if payload.stage == "all" else [StageKind(payload.stage)]
        )
        for stage in targets:
            if self.board.slots.get(stage) is not None:
                self.board.slots[stage] = None
                cleared_stages.append(stage)
        # commit 后把 STRATEGY_CANCEL directive 从 board.overlays 移出（它已执行，不需持续活跃）
        self.board.overlays = [d for d in self.board.overlays if d.id != directive_id]
        # 切 sustain plan（facade.set_build 即时生效）
        with contextlib.suppress(Exception):
            self.facade.set_build("sustain")
        # 清掉推荐
        self._pending_recommendation = None
        # log 事件，触发 snapshot 刷新
        self.session.log_event(
            Event(
                ts=now,
                kind=EventKind.STRATEGY_SET,
                payload={
                    "action": "cancel",
                    "cleared_stages": [s.value for s in cleared_stages],
                    "directive_id": directive_id,
                },
                priority="medium",
                caused_by="voice",
            )
        )
        self._push_snapshot(now)

    # ------------------------------------------------------------------
    # M3 L4 sharpy 真出兵 wire (production_override → bot.train)
    # ------------------------------------------------------------------

    def _emit_production_queued_event(self, directive: Directive, now: float) -> None:
        """L4 directive 入 production_overrides 时 emit 一条 PWA event 告诉玩家已收到。

        语义:"将加入生产队列",1.5s commit 后实际开始 train。
        """
        from vibecraft.directives.models import (
            ExpansionOverridePayload,
            ProductionOverridePayload,
            TechOverridePayload,
        )

        p = directive.payload
        if isinstance(p, ProductionOverridePayload):
            display = f"{p.unit_type} × {p.count} 已加入生产队列"
        elif isinstance(p, TechOverridePayload):
            display = f"升级 {p.upgrade_id} 已加入生产队列"
        elif isinstance(p, ExpansionOverridePayload):
            display = f"开矿 → {p.target_count} 矿 已加入生产队列"
        else:
            display = f"{directive.type.value} 已加入生产队列"
        event_dict = {
            "type": "event",
            "kind": "directive.queued",
            "ts": round(now, 3),
            "payload": {"directive_id": directive.id, "display": display},
        }
        self._push_event(event_dict)

    async def execute_overrides_step(self, now: float) -> None:
        """每 sharpy bot step 调用,**async**(expand_now 是 async)。

        分发 L4 三类 override 到对应 handler:
        - production_override → bot.train(unit_id) 抢 building action slot
        - tech_override       → bot.research(upgrade_id)
        - expansion_override  → await bot.expand_now()

        增量语义:不重复 train/research(用 bot.already_pending 防 spam)。
        done 判定由 task_monitor (counter / tech_done flag / expansion_count
        checker) 自动 mark + board.complete pop overrides list。
        """
        from vibecraft.directives.models import (
            ExpansionOverridePayload,
            ProductionOverridePayload,
            StructureOverridePayload,
            TechOverridePayload,
        )

        if not self.production_overrides or self._bot is None:
            return
        # 用 list copy 防迭代时 board.complete pop 改 list
        for d in list(self.production_overrides):
            payload = d.payload
            if isinstance(payload, ProductionOverridePayload):
                self._exec_production_override(d, payload)
            elif isinstance(payload, TechOverridePayload):
                self._exec_tech_override(d, payload)
            elif isinstance(payload, ExpansionOverridePayload):
                await self._exec_expansion_override(d, payload)
            elif isinstance(payload, StructureOverridePayload):
                await self._exec_structure_override(d, payload)

    def _check_prereq_ready(self, item_canonical_name: str) -> tuple[bool, str]:
        """检查 unit/upgrade 的 prereq structure 是否 ready。

        返回 (ready: bool, missing_name: str)。ready=True 时 missing_name=''。
        item_canonical_name 是 _REQUIRED_STRUCTURE 表的 key(已 UPPER)。
        """
        required = self._REQUIRED_STRUCTURE.get(item_canonical_name)
        if required is None:
            return (True, "")  # 表里 None 或不在表 = 无 prereq
        if self._bot is None:
            return (False, required)
        try:
            from sc2.ids.unit_typeid import UnitTypeId

            structure_id = UnitTypeId[required]
            ready_count = len(self._bot.structures(structure_id).ready)
            if ready_count > 0:
                return (True, "")
            # 检查 pending(在建)
            pending = float(self._bot.already_pending(structure_id))
            if pending > 0:
                return (False, f"{required}(建造中)")
            return (False, required)
        except (ImportError, KeyError, Exception):
            return (False, required)

    def _set_override_status(
        self, d: Directive, status: str, reason: str = ""
    ) -> None:
        """更新 directive 的 status。**只 status 切换时**才 emit event(防 spam):
        active 阶段 reason 可能高频变化("研究中 2% / 4% / 6%"),不让每次都 emit。
        snapshot 字段照样透传最新 reason(PWA 可看到 progress % 但不被 event 刷屏)。
        """
        cur = self._override_status.get(d.id, {})
        prev_status = cur.get("status")
        self._override_status[d.id] = {"status": status, "reason": reason}
        if prev_status == status:
            return  # status 没变,reason 变化不 emit event
        # status 真切换 → emit event 让 PWA 卡片 update color
        self._push_event({
            "type": "event",
            "kind": "directive.status_changed",
            "ts": 0,  # PWA 自己用接收时间
            "payload": {
                "directive_id": d.id,
                "status": status,
                "reason": reason,
            },
        })

    def _exec_production_override(self, d: Directive, payload: Any) -> None:
        """L4 unit 出兵: bot.train(unit_id)。带 prereq check + status tracking。"""
        unit_id = self._resolve_unit_type_id(payload.unit_type)
        if unit_id is None:
            logger.warning("resolve unit_type_id fail: %r", payload.unit_type)
            return
        # prereq check(canonical UPPER name)
        ready, missing = self._check_prereq_ready(payload.unit_type.upper())
        if not ready:
            self._set_override_status(d, "on_hold", f"需要 {missing}")
            return
        # 已造数 + 已下单数(in-flight): 用 bot.already_pending 防 spam
        try:
            in_flight = float(self._bot.already_pending(unit_id))
        except Exception:
            in_flight = 0.0
        already_done = self._production_override_built_count(d)
        remaining = payload.count - already_done - int(in_flight)
        if remaining <= 0:
            # 已下满 = active(等 task_monitor 判 done 后 board.complete 自动 pop)
            self._set_override_status(d, "active", "已下单等完成")
            return
        try:
            n_trained = self._bot.train(
                unit_id, amount=remaining, train_only_idle_buildings=False
            )
            if n_trained > 0:
                logger.info(
                    "production_override TRAIN %s ×%d (count=%d, done=%d, in_flight=%.0f, id=%s)",
                    unit_id, n_trained, payload.count, already_done, in_flight, d.id[:8],
                )
                self._set_override_status(d, "active", "")
            else:
                # train 失败可能是资源不够或 building 都 busy
                self._set_override_status(d, "on_hold", "资源/building 不足")
        except Exception as exc:
            logger.debug("production_override train fail: %s", exc)

    def _exec_tech_override(self, d: Directive, payload: Any) -> None:
        """L4 科技: bot.research(upgrade_id)。带 prereq check + status tracking。"""
        upgrade_id = self._resolve_upgrade_id(payload.upgrade_id)
        if upgrade_id is None:
            logger.warning("resolve upgrade_id fail: %r", payload.upgrade_id)
            return
        # prereq check —— upgrade 的 _REQUIRED_STRUCTURE key 是 enum name(已 upper)
        ready, missing = self._check_prereq_ready(upgrade_id.name)
        if not ready:
            self._set_override_status(d, "on_hold", f"需要 {missing}")
            return
        # already_pending_upgrade(u) 返回研究进度 [0, 1]
        try:
            progress = float(self._bot.already_pending_upgrade(upgrade_id))
        except Exception:
            progress = 0.0
        if progress > 0.0:
            # 在研究中(或已完成) = active
            self._set_override_status(d, "active", f"研究中 {progress * 100:.0f}%")
            return
        try:
            success = self._bot.research(upgrade_id)
            if success:
                logger.info(
                    "tech_override RESEARCH %s (id=%s)", upgrade_id, d.id[:8]
                )
                self._set_override_status(d, "active", "")
            else:
                # 资源不够 / 没 idle research building
                self._set_override_status(d, "on_hold", "资源/building 不足")
        except Exception as exc:
            # sharpy do() override 不让 BotAI.research 调(传 bool 报错);
            # 只第一次 log,sharpy plan 自带的 research 路径会接管(如果 plan 包含该 upgrade)
            dbg = f"_dbg_research_exc_{d.id}"
            if not getattr(self, dbg, False):
                setattr(self, dbg, True)
                logger.warning(
                    "tech_override BotAI.research(%s) 不可用(sharpy 限制),由 sharpy plan 自带 research 路径接管: %s",
                    upgrade_id, exc,
                )
            # 走 fallback:如果 sharpy plan 已经在研究(progress > 0),仍 set active
            try:
                progress = float(self._bot.already_pending_upgrade(upgrade_id))
                if progress > 0.0:
                    self._set_override_status(d, "active", f"研究中 {progress * 100:.0f}%")
                    return
            except Exception:
                pass
            self._set_override_status(d, "on_hold", "等 sharpy plan 研究")

    async def _exec_expansion_override(self, d: Directive, payload: Any) -> None:
        """L4 开矿: await bot.expand_now()。带 status tracking(expand 无 prereq)。"""
        try:
            from sc2.ids.unit_typeid import UnitTypeId

            nexus_id = UnitTypeId.NEXUS
            current = len(self._bot.townhalls.ready) + int(
                self._bot.already_pending(nexus_id)
            )
            target = payload.target_count
        except Exception:
            return
        if current >= target:
            self._set_override_status(d, "active", f"{current}/{target} 已达成")
            return
        # 资源 / mineral check
        try:
            if self._bot.minerals < 400:  # Nexus 需要 400 mineral
                self._set_override_status(
                    d, "on_hold", f"资源不足({self._bot.minerals}/400 矿)"
                )
                return
        except Exception:
            pass
        try:
            await self._bot.expand_now()
            logger.info(
                "expansion_override EXPAND (target=%d current=%d, id=%s)",
                target, current, d.id[:8],
            )
            self._set_override_status(d, "active", f"{current + 1}/{target}")
        except Exception as exc:
            logger.debug("expansion_override fail: %s", exc)
            self._set_override_status(d, "on_hold", "expand 失败")

    async def _exec_structure_override(self, d: Directive, payload: Any) -> None:
        """L4 建筑目标: bot.build(structure_id, near=location)。

        prereq check → current count check → bot.build → status 透传。
        done 判定由 task_monitor 的 structure_count checker 接管（Task 6 already done）。
        """
        from sc2.ids.unit_typeid import UnitTypeId

        type_name = payload.structure_type.upper()
        try:
            type_id = UnitTypeId[type_name]
        except (ImportError, KeyError):
            logger.warning("structure_override 未知 structure %r", payload.structure_type)
            self._set_override_status(d, "on_hold", f"未知建筑 {payload.structure_type}")
            return
        try:
            current = (
                self._bot.structures(type_id).amount
                + int(self._bot.already_pending(type_id))
            )
        except Exception:
            current = 0
        if current >= payload.target_count:
            self._set_override_status(
                d, "active", f"{current}/{payload.target_count} 已达成"
            )
            return
        # prereq check — _REQUIRED_STRUCTURE key 是 canonical UPPER name
        ready, missing = self._check_prereq_ready(type_name)
        if not ready:
            self._set_override_status(d, "on_hold", f"需要 {missing}")
            return
        pos = self._resolve_location_hint(payload.location_hint, type_id)
        try:
            await self._bot.build(type_id, near=pos)
            logger.info(
                "structure_override BUILD %s near=%s (current=%d, target=%d, id=%s)",
                type_id, pos, current, payload.target_count, d.id[:8],
            )
            self._set_override_status(
                d, "active",
                f"造 {payload.structure_type} ({current + 1}/{payload.target_count})",
            )
        except Exception as exc:
            logger.debug("structure_override build fail: %s", exc)
            self._set_override_status(d, "on_hold", f"build 失败: {exc}")

    def _resolve_location_hint(self, hint: str | None, type_id: Any) -> Any:
        """hint(main/natural/ramp/front) → Point2 via sharpy expansion_zones。

        None → None（让 bot 自选 placement）。
        任何 lookup 失败都 fallback None 而不是抛异常（bot.build(near=None) 会自选）。
        """
        if hint is None:
            return None
        try:
            zones = self._bot.knowledge.zone_manager.expansion_zones
        except Exception:
            return None
        if hint == "main":
            return zones[0].center_location if zones else None
        if hint == "natural":
            return zones[1].center_location if len(zones) > 1 else (zones[0].center_location if zones else None)
        if hint == "ramp":
            try:
                return self._bot.main_base_ramp.top_center
            except Exception:
                return None
        if hint == "front":
            try:
                return self._bot.knowledge.enemy_main_base_ramp.top_center
            except Exception:
                return None
        return None  # 未知 hint → None

    @staticmethod
    def _resolve_unit_type_id(name: str) -> Any:
        """字符串 'Sentry' → UnitTypeId.SENTRY。失败返回 None。"""
        try:
            from sc2.ids.unit_typeid import UnitTypeId

            return UnitTypeId[name.upper()]
        except (ImportError, KeyError):
            return None

    # L4 override 的 prereq structure 表(canonical unit/upgrade name → required structure)。
    # train Sentry 前 Cybernetics Core 要 ready;研究 Blink 前 Twilight Council 要 ready。
    # None 表示无 prereq(如 Zealot / Archon 合成)。
    # 不在表里的 unit(如 Probe)默认无 prereq。
    _REQUIRED_STRUCTURE: ClassVar[dict[str, str | None]] = {
        # ---- Structures (prereq for build) ----
        "GATEWAY": "NEXUS",
        "FORGE": "NEXUS",
        "PHOTONCANNON": "FORGE",
        "CYBERNETICSCORE": "GATEWAY",
        "ROBOTICSFACILITY": "CYBERNETICSCORE",
        "STARGATE": "CYBERNETICSCORE",
        "TWILIGHTCOUNCIL": "CYBERNETICSCORE",
        "ROBOTICSBAY": "ROBOTICSFACILITY",
        # ---- Units ----
        "ZEALOT": None,
        "SENTRY": "CYBERNETICSCORE",
        "STALKER": "CYBERNETICSCORE",
        "ADEPT": "CYBERNETICSCORE",
        "HIGHTEMPLAR": "TEMPLARARCHIVES",
        "DARKTEMPLAR": "DARKSHRINE",
        "ARCHON": None,  # 合成,不需 structure
        "IMMORTAL": "ROBOTICSFACILITY",
        "OBSERVER": "ROBOTICSFACILITY",
        "WARPPRISM": "ROBOTICSFACILITY",
        "COLOSSUS": "ROBOTICSBAY",
        "DISRUPTOR": "ROBOTICSBAY",
        "PHOENIX": "STARGATE",
        "VOIDRAY": "STARGATE",
        "ORACLE": "STARGATE",
        "TEMPEST": "FLEETBEACON",
        "CARRIER": "FLEETBEACON",
        "MOTHERSHIP": "FLEETBEACON",
        # ---- Upgrades(已经 UpgradeId 名) ----
        "WARPGATERESEARCH": "CYBERNETICSCORE",
        "BLINKTECH": "TWILIGHTCOUNCIL",
        "CHARGE": "TWILIGHTCOUNCIL",
        "ADEPTPIERCINGATTACK": "TWILIGHTCOUNCIL",
        "PSISTORMTECH": "TEMPLARARCHIVES",
        "PROTOSSGROUNDWEAPONSLEVEL1": "FORGE",
        "PROTOSSGROUNDARMORSLEVEL1": "FORGE",
        "PROTOSSSHIELDSLEVEL1": "FORGE",
        "PROTOSSAIRWEAPONSLEVEL1": "CYBERNETICSCORE",
        "PROTOSSAIRARMORSLEVEL1": "CYBERNETICSCORE",
        "TEMPESTRANGEUPGRADE": "FLEETBEACON",
        "TEMPESTGROUNDATTACKUPGRADE": "FLEETBEACON",
    }

    # LLM payload upgrade_id (跟 strategies/aliases yaml 一致) → sc2 UpgradeId enum
    # python-sc2 enum 名比 strategies yaml canonical id 多带 "TECH" / "LEVEL" 等后缀。
    _UPGRADE_NAME_MAP: ClassVar[dict[str, str]] = {
        # Twilight
        "BLINK": "BLINKTECH",
        "CHARGE": "CHARGE",
        "RESONATINGGLAIVES": "ADEPTPIERCINGATTACK",
        "GLAIVE": "ADEPTPIERCINGATTACK",
        # Templar Archives
        "PSISTORM": "PSISTORMTECH",
        # Cybernetics
        "WARPGATERESEARCH": "WARPGATERESEARCH",
        "WARPGATE": "WARPGATERESEARCH",
        # Forge — 分 3 级,默认 level1
        "PROTOSSGROUNDWEAPONS": "PROTOSSGROUNDWEAPONSLEVEL1",
        "PROTOSSGROUNDARMOR": "PROTOSSGROUNDARMORSLEVEL1",
        "PROTOSSSHIELDS": "PROTOSSSHIELDSLEVEL1",
        # Fleet Beacon / Cybernetics
        "PROTOSSAIRWEAPONS": "PROTOSSAIRWEAPONSLEVEL1",
        "PROTOSSAIRARMOR": "PROTOSSAIRARMORSLEVEL1",
        # Tempest
        "TEMPESTRANGE": "TEMPESTRANGEUPGRADE",
        "TEMPESTGROUND": "TEMPESTGROUNDATTACKUPGRADE",
    }

    @classmethod
    def _resolve_upgrade_id(cls, name: str) -> Any:
        """字符串 → UpgradeId enum。先查 _UPGRADE_NAME_MAP,fallback 直接 enum["NAME"]。"""
        try:
            from sc2.ids.upgrade_id import UpgradeId
        except ImportError:
            return None
        up = name.upper()
        mapped = cls._UPGRADE_NAME_MAP.get(up, up)
        try:
            return UpgradeId[mapped]
        except KeyError:
            return None

    def _production_override_built_count(self, directive: Directive) -> int:
        """查 task_monitor 累计的 unit_count_built_since counter。

        task_monitor._unit_built_counts[directive_id] 由 EventBus UNIT_CREATED
        handler 维护。L4 production_override 通常带 done_when=unit_count_built_since,
        counter 自然累加。没 done_when 时 fallback 返回 0(每 tick 都试 train)。
        """
        if self.task_monitor is None:
            return 0
        try:
            return int(self.task_monitor._unit_built_counts.get(directive.id, 0))
        except Exception:
            return 0

    def _remember_command(
        self, text: str, now: float, outcome: ParseOutcome | None = None
    ) -> None:
        summary = self._summarize_outcome(outcome) if outcome is not None else None
        self._recent_commands.append(_RecentCommand(text=text, ts=now, outcome_summary=summary))
        if len(self._recent_commands) > self.config.recent_command_buffer:
            self._recent_commands.pop(0)

    def _summarize_outcome(self, outcome: ParseOutcome) -> str:
        """把 ParseOutcome 压成一行摘要(给 LLM 看的局内 memory)。

        例:
          - "strategy_set(stage=midgame, strategy_id=iac_2base) id=d_a3f1c2"
          - "unit_claim(Probe patrol natural, persistent=true) id=d_8b2d4e"
          - "[parse error: schema validation]"
          - "[ambiguous: 哪个剧本?]"
        """
        if isinstance(outcome, ParseError):
            return f"[parse error: {outcome.message[:60]}]"
        if isinstance(outcome, AmbiguousParse):
            interp = outcome.result.interpretation_zh[:60]
            return f"[ambiguous: {interp}]"
        if isinstance(outcome, IntentParseResult):
            if not outcome.directives:
                return "[empty: no directives]"
            return " | ".join(self._brief_directive(d) for d in outcome.directives)
        return "[unknown outcome]"

    def _brief_directive(self, d: Directive) -> str:
        """单条 directive 关键字段摘要(给 LLM 看,不是 JSON)。"""
        p = d.payload
        t = d.type.value
        sid = d.id[:8]
        parts: list[str] = []
        if isinstance(p, StrategySetPayload):
            parts.append(f"stage={p.stage} id={p.strategy_id}")
        elif isinstance(p, ProductionOverridePayload):
            parts.append(f"{p.unit_type}×{p.count}")
        elif isinstance(p, TechOverridePayload):
            parts.append(f"upgrade={p.upgrade_id}")
        elif isinstance(p, ExpansionOverridePayload):
            parts.append(f"target_count={p.target_count}")
        elif isinstance(p, EngagementConstraintPayload):
            parts.append(f"stance={p.stance}")
        elif isinstance(p, UnitClaimPayload):
            unit = p.selector.unit_type or "?"
            verb = p.task.primary_action.verb.value
            target = p.task.primary_action.target
            tgt = target.named_spot or target.unit_type or "?"
            persist = ", persistent=true" if p.persistent else ""
            parts.append(f"{unit} {verb} {tgt}{persist}")
        elif isinstance(p, ScoutPayload):
            unit = p.selector.unit_type if p.selector else "?"
            tgt = p.target.named_spot or "?"
            parts.append(f"{unit}→{tgt}")
        elif isinstance(p, MovePayload):
            unit = p.selector.unit_type or "?"
            tgt = p.target.named_spot or "?"
            parts.append(f"{unit}→{tgt}")
        elif isinstance(p, BuildAtPayload):
            parts.append(f"{p.structure_type}@{p.point}")
        elif isinstance(p, UnitReleasePayload):
            unit = p.selector.unit_type or "?"
            parts.append(f"release {unit}")
        body = ", ".join(parts) if parts else ""
        return f"{t}({body}) id={sid}"

    # ------------------------------------------------------------------
    # 每 tick
    # ------------------------------------------------------------------

    def on_tick(self, now: float) -> list[BoardEvent]:
        # 顶部 import 避免局部 scope 撞掉 module-level reference
        from vibecraft.directives.board import BoardEvent, BoardEventKind

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
                if self.board.complete(did, now):
                    # board.complete fires RELEASED into board._events,
                    # 但 board.tick() return 已经过去(只含本 tick produced),
                    # board._events 累积要等下次 tick 才被 drain。
                    # 直接 dispatch RELEASED 让 events/directives.jsonl 立即落盘。
                    self._dispatch_event(BoardEvent(
                        kind=BoardEventKind.RELEASED,
                        ts=now,
                        directive_id=did,
                        reason="task_monitor_done",
                    ))
                self.task_monitor.detach(did)
                # 从各列表清理
                self._in_flight.pop(did, None)
                self.production_overrides = [
                    d for d in self.production_overrides if d.id != did
                ]
                self._override_status.pop(did, None)
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

        if t == DirectiveType.STRATEGY_CANCEL:
            assert isinstance(payload, StrategyCancelPayload)
            self._apply_strategy_cancel(payload, now, directive_id=d.id)
            return

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

        if t == DirectiveType.STRUCTURE_OVERRIDE:
            assert isinstance(payload, StructureOverridePayload)
            # production_overrides list 的路由已在 _submit_directives 做；
            # _apply_to_facade 不需额外 facade 调用（UI 透传走 snapshot 路径）。
            return

        if t == DirectiveType.TACTICAL_OBJECTIVE:
            assert isinstance(payload, TacticalObjectivePayload)
            self._exec_tactical_objective(d, payload)
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

    # ------------------------------------------------------------------
    # L2 tactical_objective executor（P0b Task 12）
    # ------------------------------------------------------------------

    def _exec_tactical_objective(
        self, d: Directive, payload: TacticalObjectivePayload
    ) -> None:
        """L2 分流入口：A 类（override flag）/ B 类（squad 抢占）/ 其他（on_hold）。"""
        verb = payload.verb
        if verb in _A_VERBS:
            self._exec_l2_global(d, payload)
        elif verb in _B_VERBS:
            self._exec_l2_squad(d, payload)
        else:
            logger.warning("L2 verb %r MVP 未支持 (id=%s)", verb, d.id[:8])
            self._set_override_status(d, "on_hold", f"verb {verb} 未支持")

    def _exec_l2_global(
        self, d: Directive, payload: TacticalObjectivePayload
    ) -> None:
        """A 类：attack/defend/retreat/vision → facade override flag。"""
        # 清前一条 active L2 global；把旧 directive 标 done（被新指令覆盖）
        if self._current_l2_global_id and self._current_l2_global_id != d.id:
            old_id = self._current_l2_global_id
            self._tactical_overrides.pop(old_id, None)
            old_d = self._current_l2_global_directive
            if old_d is not None and old_d.id == old_id:
                self._set_override_status(old_d, "done", "被新指令覆盖")
        point = self._resolve_target_area(payload.target_area)
        try:
            self.facade.set_attack_target_override(point)
            self.facade.set_combat_intent_override(payload.verb)  # type: ignore[arg-type]
        except Exception as exc:
            logger.debug("L2 global override fail: %s", exc)
            self._set_override_status(d, "on_hold", f"facade 失败: {exc}")
            return
        self._tactical_overrides[d.id] = payload.verb
        self._current_l2_global_id = d.id
        self._current_l2_global_directive = d
        target_desc = payload.target_area or ""
        self._set_override_status(d, "active", f"{payload.verb} {target_desc}".strip())

    def _exec_l2_squad(
        self, d: Directive, payload: TacticalObjectivePayload
    ) -> None:
        """B 类：harass/scout → 抢占 free unit → set_unit_role LLM_CONTROLLED。"""
        if payload.unit_count_hint is None:
            self._set_override_status(d, "on_hold", "缺 unit_count_hint")
            return
        n_wanted = payload.unit_count_hint
        if not payload.unit_type_hint:
            self._set_override_status(d, "on_hold", "缺 unit_type_hint")
            return
        unit_type = payload.unit_type_hint[0]
        free_tags = self.facade.resolve_selector(unit_type=unit_type)
        tags = free_tags[:n_wanted]
        if not tags:
            self._set_override_status(d, "on_hold", f"无空闲 {unit_type}")
            return
        for tag in tags:
            self.facade.set_unit_role(tag, UnitRole.LLM_CONTROLLED)
        target_pt = self._resolve_target_area(payload.target_area)
        # sharpy MoveType lazy import（防 e2e import 路径错误）
        try:
            from sharpy.combat.move_type import MoveType

            move_type: Any = MoveType.Harass if payload.verb == "harass" else MoveType.Assault
        except Exception:
            move_type = None
        squad = TacticalSquad(
            directive_id=d.id,
            unit_tags=set(tags),
            target=target_pt,
            move_type=move_type,
            verb=payload.verb,
            n_wanted=n_wanted,
            n_locked=len(tags),
        )
        self._tactical_squads[d.id] = squad
        if len(tags) == n_wanted:
            msg = f"已接管 {len(tags)} 个 {unit_type}"
        else:
            msg = f"已接管 {len(tags)}/{n_wanted} 个 {unit_type}（短缺）"
        self._set_override_status(d, "active", msg)

    def _resolve_target_area(self, area: Any) -> Any:
        """area: str (named_spot) / (x,y) tuple / None → Point2 或 None。"""
        if area is None:
            return None
        try:
            from sc2.position import Point2
        except Exception:
            return None
        if isinstance(area, (tuple, list)) and len(area) == 2:
            try:
                return Point2((float(area[0]), float(area[1])))
            except Exception:
                return None
        if self._bot is None:
            return None
        try:
            if area == "enemy_main":
                return self._bot.enemy_start_locations[0]
            if area == "enemy_natural":
                try:
                    return self._bot.knowledge.zone_manager.enemy_expansion_zones[1].center_location
                except Exception:
                    return self._bot.enemy_start_locations[0]
            if area == "own_main":
                return self._bot.knowledge.zone_manager.expansion_zones[0].center_location
            if area == "own_natural":
                zones = self._bot.knowledge.zone_manager.expansion_zones
                return zones[1].center_location if len(zones) > 1 else zones[0].center_location
        except Exception:
            return None
        return None

    def _cached_combat_manager(self) -> Any:
        """缓存 sharpy combat_manager 引用（lazy lookup once）。

        真 sharpy 路径：knowledge.combat_manager（knowledge.py:59）。
        """
        if hasattr(self, "_cm_cache"):
            return self._cm_cache
        cm = None
        try:
            cm = self._bot.knowledge.combat_manager  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("combat_manager 不可用 (sharpy 接口不一致?): %s", exc)
        self._cm_cache: Any = cm
        return cm

    async def execute_tactics_step(self, now: float) -> None:
        """每 sharpy step 调，给 active squad 派活（GroupCombatManager）。

        真 sharpy 签名：cm.add_units(units: Units)，然后 cm.execute(target, move_type)。
        add_units 每 tick 都要调（execute 内部会 clear _tags）。
        """
        if not self._tactical_squads:
            return
        if self._bot is None:
            return
        cm = self._cached_combat_manager()
        if cm is None:
            return
        for squad in list(self._tactical_squads.values()):
            try:
                units = self._bot.units.tags_in(squad.unit_tags)
                if not units:
                    continue
                cm.add_units(units)
                cm.execute(squad.target, squad.move_type)
            except Exception as exc:
                logger.debug(
                    "execute_tactics_step squad %s fail: %s", squad.directive_id[:8], exc
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
            recent_outcomes=[
                c.outcome_summary or "(未解析)" for c in self._recent_commands
            ],
        )

    # ------------------------------------------------------------------
    # 内省（单测用）
    # ------------------------------------------------------------------

    @property
    def committed_count(self) -> int:
        return self._committed_count
