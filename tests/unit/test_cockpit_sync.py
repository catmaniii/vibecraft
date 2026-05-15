"""cockpit-sync P0/P1 单测（2026-05-15）。

覆盖：
P0:
- Director.build_snapshot 组装 snapshot 帧
- Director.set_snapshot_callback + on_tick 触发（变化推 + 兜底周期）
- ws._dispatch_upstream snapshot/event 分支不污染 sc2_state（S5）
P1:
- Director._maybe_push_event_frame A 组埋点
- ares_adapter autopilot 阶段二边沿检测 B 组埋点
- game_process snapshot/event 消息入上行队列
"""

from __future__ import annotations

import json
import queue
import sys
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from voicecraft.bot import BotState, FakeFacade
from voicecraft.bot.director import Director, _RecentCommand
from voicecraft.directives.board import DirectiveBoard
from voicecraft.directives.models import Directive, StrategySetPayload
from voicecraft.directives.types import StageKind
from voicecraft.llm import IntentParser, MockLLMProvider
from voicecraft.logging_ import GameSession, GameSessionConfig
from voicecraft.strategy.library import StrategyLibrary
from voicecraft.strategy.models import LategameDoctrine, MidgameStance, OpeningBuild

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _make_session() -> GameSession:
    return GameSession(GameSessionConfig(use_null_sinks=True))


def _make_library() -> StrategyLibrary:
    opening = OpeningBuild.model_validate(
        {
            "kind": "opening_build",
            "id": "1g_robo",
            "display_name_zh": "1门Robo 不朽开",
            "phases": [
                {"id": "opening", "display": "开局", "subtitle": "13农BG"},
                {"id": "tech", "display": "上折跃", "subtitle": "WG研究"},
            ],
            "steps": ["13 build Pylon", "14 build Gateway"],
            "default_transitions": [{"midgame_id": "iac_2base", "when": "default"}],
        }
    )
    midgame = MidgameStance.model_validate(
        {
            "kind": "midgame_stance",
            "id": "iac_2base",
            "display_name_zh": "双矿 IAC 重装地面",
        }
    )
    lategame = LategameDoctrine.model_validate(
        {
            "kind": "lategame_doctrine",
            "id": "skytoss",
            "display_name_zh": "Skytoss 航母流",
            "target_composition": {"CARRIER": 6},
        }
    )
    return StrategyLibrary(openings=[opening], midgames=[midgame], lategames=[lategame])


def _make_director(library: StrategyLibrary | None = None) -> Director:
    session = _make_session()
    facade = FakeFacade(state=BotState(game_time=0.0))
    parser = IntentParser(MockLLMProvider(), library or StrategyLibrary(), session=session)
    return Director(facade=facade, parser=parser, session=session, library=library)


def _make_strategy_directive(strategy_id: str, stage: StageKind = StageKind.OPENING) -> Directive:
    """构造一个 STRATEGY_SET directive（issued_at=0，无 delay）。"""
    return Directive(
        payload=StrategySetPayload(stage=stage, strategy_id=strategy_id),
        issued_at=0.0,
    )


# ---------------------------------------------------------------------------
# P0：Director.build_snapshot
# ---------------------------------------------------------------------------


class TestBuildSnapshot:
    def test_snapshot_has_required_fields(self) -> None:
        """build_snapshot 返回的 dict 包含 type/ts/strategy/recent_commands。"""
        d = _make_director()
        snap = d.build_snapshot(100.0)
        assert snap["type"] == "snapshot"
        assert snap["ts"] == 100.0
        assert "strategy" in snap
        assert "recent_commands" in snap

    def test_strategy_fields_present(self) -> None:
        """strategy 包含 current_stage + opening/midgame/lategame 三档。"""
        d = _make_director()
        snap = d.build_snapshot(0.0)
        strat = snap["strategy"]
        assert "current_stage" in strat
        assert "opening" in strat
        assert "midgame" in strat
        assert "lategame" in strat

    def test_no_library_fallback_display_is_id(self) -> None:
        """library=None 时，slot 设置后 display fallback 成 strategy_id。"""
        d = _make_director(library=None)
        board = DirectiveBoard(commit_delay_s=0.0)
        d.board = board

        board.submit(_make_strategy_directive("some_build"), now=0.0)
        board.tick(1.0)  # commit

        snap = d.build_snapshot(1.0)
        opening = snap["strategy"]["opening"]
        assert opening is not None
        assert opening["id"] == "some_build"
        assert opening["display"] == "some_build"  # fallback

    def test_with_library_display_name_zh(self) -> None:
        """library 注入后，display 使用 display_name_zh。"""
        lib = _make_library()
        d = _make_director(library=lib)
        board = DirectiveBoard(commit_delay_s=0.0)
        d.board = board

        board.submit(_make_strategy_directive("1g_robo"), now=0.0)
        board.tick(1.0)

        snap = d.build_snapshot(1.0)
        opening = snap["strategy"]["opening"]
        assert opening is not None
        assert opening["display"] == "1门Robo 不朽开"

    def test_opening_slot_includes_phases(self) -> None:
        """library 注入后，opening slot 包含 phases 列表。"""
        lib = _make_library()
        d = _make_director(library=lib)
        board = DirectiveBoard(commit_delay_s=0.0)
        d.board = board

        board.submit(_make_strategy_directive("1g_robo"), now=0.0)
        board.tick(1.0)

        snap = d.build_snapshot(1.0)
        opening = snap["strategy"]["opening"]
        assert opening is not None
        assert "phases" in opening
        assert len(opening["phases"]) == 2
        assert opening["phases"][0]["id"] == "opening"

    def test_recent_commands_included(self) -> None:
        """recent_commands 包含 _remember_command 里的记录。"""
        d = _make_director()
        d._recent_commands.append(_RecentCommand(text="切 IAC", ts=50.0))
        snap = d.build_snapshot(60.0)
        assert len(snap["recent_commands"]) == 1
        assert snap["recent_commands"][0]["text"] == "切 IAC"

    def test_null_slots_are_none(self) -> None:
        """未设置的 slot 在 snapshot 里是 null（None）。"""
        d = _make_director()
        snap = d.build_snapshot(0.0)
        assert snap["strategy"]["opening"] is None
        assert snap["strategy"]["midgame"] is None
        assert snap["strategy"]["lategame"] is None


# ---------------------------------------------------------------------------
# P0：snapshot callback 触发（变化推 + 兜底）
# ---------------------------------------------------------------------------


class TestSnapshotCallbackTrigger:
    def test_callback_triggered_on_strategy_change(self) -> None:
        """STRATEGY_CHANGED 事件触发 snapshot callback。"""
        d = _make_director()
        pushed: list[dict] = []
        d.set_snapshot_callback(pushed.append)

        board = DirectiveBoard(commit_delay_s=0.0)
        d.board = board

        board.submit(_make_strategy_directive("x"), now=0.0)
        d.on_tick(1.0)  # commit → STRATEGY_CHANGED → 变化推

        assert len(pushed) >= 1
        assert pushed[-1]["type"] == "snapshot"

    def test_callback_triggered_on_periodic_interval(self) -> None:
        """兜底周期（snapshot_interval_ticks）触发 snapshot callback。"""
        d = _make_director()
        d.config.snapshot_interval_ticks = 3
        pushed: list[dict] = []
        d.set_snapshot_callback(pushed.append)

        # 跑 3 tick 触发兜底
        for i in range(3):
            d.on_tick(float(i))

        assert len(pushed) >= 1

    def test_no_callback_no_error(self) -> None:
        """未注入 callback 时，on_tick 不抛异常。"""
        d = _make_director()
        d.on_tick(0.0)  # 不应抛

    def test_set_snapshot_callback_stores_cb(self) -> None:
        """set_snapshot_callback 确实存储了 callback。"""
        d = _make_director()
        cb = MagicMock()
        d.set_snapshot_callback(cb)
        assert d._snapshot_callback is cb


# ---------------------------------------------------------------------------
# P1：event callback 触发（A 组埋点）
# ---------------------------------------------------------------------------


class TestEventCallbackTrigger:
    def test_event_pushed_on_strategy_changed(self) -> None:
        """STRATEGY_CHANGED → 推 strategy.set event 帧。"""
        d = _make_director()
        pushed: list[dict] = []
        d.set_event_callback(pushed.append)

        board = DirectiveBoard(commit_delay_s=0.0)
        d.board = board

        board.submit(_make_strategy_directive("x"), now=0.0)
        d.on_tick(1.0)

        event_frames = [e for e in pushed if e.get("type") == "event"]
        assert any(e["kind"] == "strategy.set" for e in event_frames)

    def test_event_pushed_on_directive_committed(self) -> None:
        """COMMITTED → 推 directive.committed event 帧。"""
        d = _make_director()
        pushed: list[dict] = []
        d.set_event_callback(pushed.append)

        board = DirectiveBoard(commit_delay_s=0.0)
        d.board = board

        board.submit(_make_strategy_directive("x"), now=0.0)
        d.on_tick(1.0)

        event_frames = [e for e in pushed if e.get("type") == "event"]
        assert any(e["kind"] == "directive.committed" for e in event_frames)

    def test_strategy_set_event_has_display_with_library(self) -> None:
        """strategy.set event payload 在有 library 时包含 display 字段。"""
        lib = _make_library()
        d = _make_director(library=lib)
        pushed: list[dict] = []
        d.set_event_callback(pushed.append)

        board = DirectiveBoard(commit_delay_s=0.0)
        d.board = board

        board.submit(_make_strategy_directive("1g_robo"), now=0.0)
        d.on_tick(1.0)

        strategy_events = [e for e in pushed if e.get("kind") == "strategy.set"]
        assert len(strategy_events) >= 1
        assert strategy_events[0]["payload"].get("display") == "1门Robo 不朽开"

    def test_set_event_callback_stores_cb(self) -> None:
        """set_event_callback 确实存储了 callback。"""
        d = _make_director()
        cb = MagicMock()
        d.set_event_callback(cb)
        assert d._event_callback is cb


# ---------------------------------------------------------------------------
# S5：ws._dispatch_upstream snapshot/event 不污染 sc2_state
# ---------------------------------------------------------------------------


class TestDispatchUpstreamNoStatePollution:
    """S5：snapshot/event kind 必须显式处理，不落到 else 分支（不污染内部状态）。"""

    async def test_snapshot_kind_does_not_update_sc2_state(self) -> None:
        """上行 snapshot 消息 → 下行 snapshot 帧，sc2_state/bot_state 不变。"""
        from voicecraft.server.game_process import GameProcess
        from voicecraft.server.tokens import RoomRegistry
        from voicecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()
        gp._sc2_state = "playing"
        gp._bot_state = "running"

        conn = WsConnection(ws_mock, registry, game_process=gp)

        snapshot_raw = {
            "kind": "snapshot",
            "type": "snapshot",
            "ts": 120.0,
            "strategy": {
                "current_stage": "opening",
                "opening": None,
                "midgame": None,
                "lategame": None,
            },
            "recent_commands": [],
        }
        await conn._dispatch_upstream(snapshot_raw)

        # 内部状态不应被修改
        assert gp._sc2_state == "playing"
        assert gp._bot_state == "running"

        # 应下发 snapshot 帧
        ws_mock.send.assert_called_once()
        sent = json.loads(ws_mock.send.call_args[0][0])
        assert sent["type"] == "snapshot"

    async def test_event_kind_does_not_update_sc2_state(self) -> None:
        """上行 event 消息 → 下行 event 帧，sc2_state/bot_state 不变。"""
        from voicecraft.server.game_process import GameProcess
        from voicecraft.server.tokens import RoomRegistry
        from voicecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()
        gp._sc2_state = "playing"
        gp._bot_state = "running"

        conn = WsConnection(ws_mock, registry, game_process=gp)

        event_raw = {
            "kind": "event",
            "type": "event",
            "ts": 200.0,
            "payload": {"stage": "midgame"},
        }
        await conn._dispatch_upstream(event_raw)

        assert gp._sc2_state == "playing"
        assert gp._bot_state == "running"

        ws_mock.send.assert_called_once()
        sent = json.loads(ws_mock.send.call_args[0][0])
        assert sent["type"] == "event"

    async def test_snapshot_kind_stripped_from_forwarded_frame(self) -> None:
        """转发的 snapshot 帧不含 'kind' 键（只保留 snapshot payload）。"""
        from voicecraft.server.game_process import GameProcess
        from voicecraft.server.tokens import RoomRegistry
        from voicecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()
        conn = WsConnection(ws_mock, registry, game_process=gp)

        await conn._dispatch_upstream(
            {
                "kind": "snapshot",
                "type": "snapshot",
                "ts": 50.0,
                "strategy": {
                    "current_stage": "opening",
                    "opening": None,
                    "midgame": None,
                    "lategame": None,
                },
                "recent_commands": [],
            }
        )

        sent = json.loads(ws_mock.send.call_args[0][0])
        # 'kind' 字段应被剥离（不存在于转发帧）
        assert "kind" not in sent


# ---------------------------------------------------------------------------
# P1-2：ares_adapter autopilot 边沿检测 + event 推送
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_ares() -> Any:
    for key in list(sys.modules.keys()):
        if key.startswith("ares"):
            del sys.modules[key]
    sys.modules.pop("voicecraft.bot.ares_adapter", None)
    yield
    for key in list(sys.modules.keys()):
        if key.startswith("ares"):
            del sys.modules[key]
    sys.modules.pop("voicecraft.bot.ares_adapter", None)


def _inject_fake_ares_minimal() -> type:
    """注入最小伪 ares，返回 FakeAresBot 类（内联，不依赖 test_ares_adapter）。"""
    fake_ares_consts = ModuleType("ares.consts")
    FakeUnitRole = MagicMock()
    for attr in ("CONTROL_GROUP_ONE", "IDLE", "ATTACKING", "DEFENDING", "HARASSING", "SCOUTING"):
        setattr(FakeUnitRole, attr, attr)
    fake_ares_consts.UnitRole = FakeUnitRole  # type: ignore[attr-defined]

    class FakeAresBot:
        def __init__(self) -> None:
            self.config: dict[str, Any] = {}
            self.build_order_runner = MagicMock()
            self.build_order_runner.build_completed = False
            self.mediator = MagicMock()
            self.time = 0.0
            self.minerals = 0
            self.vespene = 0
            self.supply_used = 0
            self.supply_cap = 0
            self.townhalls: list[Any] = []
            self.units = MagicMock()
            self.start_location = (0, 0)

        async def on_start(self) -> None:
            pass

        def register_behavior(self, behavior: Any) -> None:
            pass

    fake_ares = ModuleType("ares")
    fake_ares.AresBot = FakeAresBot  # type: ignore[attr-defined]
    fake_ares_behaviors = ModuleType("ares.behaviors")
    fake_ares_behaviors_macro = ModuleType("ares.behaviors.macro")
    for _bname in (
        "AutoSupply",
        "BuildWorkers",
        "ExpansionController",
        "GasBuildingController",
        "Mining",
        "ProductionController",
        "SpawnController",
    ):
        setattr(fake_ares_behaviors_macro, _bname, MagicMock())

    sys.modules["ares"] = fake_ares
    sys.modules["ares.consts"] = fake_ares_consts
    sys.modules["ares.behaviors"] = fake_ares_behaviors
    sys.modules["ares.behaviors.macro"] = fake_ares_behaviors_macro
    if "sc2" not in sys.modules:
        fake_sc2 = ModuleType("sc2")
        fake_sc2_position = ModuleType("sc2.position")
        fake_sc2_position.Point2 = lambda t: t  # type: ignore[attr-defined]
        fake_sc2_ids = ModuleType("sc2.ids")
        fake_sc2_unit_typeid = ModuleType("sc2.ids.unit_typeid")
        fake_sc2_unit_typeid.UnitTypeId = MagicMock()  # type: ignore[attr-defined]
        sys.modules["sc2"] = fake_sc2
        sys.modules["sc2.position"] = fake_sc2_position
        sys.modules["sc2.ids"] = fake_sc2_ids
        sys.modules["sc2.ids.unit_typeid"] = fake_sc2_unit_typeid

    return FakeAresBot


class TestAutopilotEventCallback:
    """P1-2：build_completed false→true 时推一次 decision.autopilot_phase event。"""

    def _make_instance(self, event_cb: Any) -> Any:
        FakeAresBot = _inject_fake_ares_minimal()
        from voicecraft.bot.ares_adapter import make_bot_class

        BotClass = make_bot_class(
            director_factory=lambda facade: MagicMock(),
            event_callback=event_cb,
        )
        instance = object.__new__(BotClass)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        instance._autopilot_started = False
        instance.register_behavior = MagicMock()
        return instance

    def test_event_pushed_on_first_build_completed(self) -> None:
        """build_completed 首次变 True 时推 decision.autopilot_phase。"""
        events: list[dict] = []
        instance = self._make_instance(events.append)

        instance.build_order_runner.build_completed = False
        instance._register_auto_pilot()
        assert events == []

        instance.build_order_runner.build_completed = True
        instance._register_auto_pilot()
        assert len(events) == 1
        assert events[0]["kind"] == "decision.autopilot_phase"
        assert events[0]["type"] == "event"

    def test_event_pushed_only_once(self) -> None:
        """build_completed 保持 True 时，后续 tick 不重复推。"""
        events: list[dict] = []
        instance = self._make_instance(events.append)

        instance.build_order_runner.build_completed = True
        instance._register_auto_pilot()  # 第一次：推
        instance._register_auto_pilot()  # 后续：不重复
        instance._register_auto_pilot()

        assert len(events) == 1

    def test_no_event_callback_is_fine(self) -> None:
        """event_callback=None 时不抛异常。"""
        instance = self._make_instance(None)
        instance.build_order_runner.build_completed = True
        instance._register_auto_pilot()  # 不应抛


# ---------------------------------------------------------------------------
# status_events 过滤 snapshot/event
# ---------------------------------------------------------------------------


class TestStatusEventsFiltersNewKinds:
    """status_events() 过滤 snapshot/event 消息（不当 GameStatus 返回）。"""

    async def test_status_events_filters_snapshot_and_event(self) -> None:
        from voicecraft.server.game_process import GameProcess

        def _make_q(*messages: dict) -> queue.Queue:  # type: ignore[type-arg]
            q: queue.Queue = queue.Queue()  # type: ignore[type-arg]
            for msg in messages:
                q.put_nowait(msg)
            return q

        gp = GameProcess()
        gp._up_q = _make_q(  # type: ignore[assignment]
            {"sc2": "playing", "bot": "running"},
            {
                "kind": "snapshot",
                "type": "snapshot",
                "ts": 50.0,
                "strategy": {},
                "recent_commands": [],
            },
            {
                "kind": "event",
                "type": "event",
                "ts": 60.0,
                "payload": {},
            },
            {"sc2": "ended", "bot": "idle"},
        )
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = False
        fake_proc.exitcode = 0
        gp._proc = fake_proc

        statuses = []
        async for s in gp.status_events():
            statuses.append(s)

        sc2_states = [s.sc2 for s in statuses]
        assert "playing" in sc2_states
        assert "ended" in sc2_states
        for s in statuses:
            assert s.sc2 not in ("snapshot", "event")
