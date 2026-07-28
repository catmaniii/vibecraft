"""cockpit-sync P0/P1 单测（2026-05-15）。

覆盖：
P0:
- Director.build_snapshot 组装 snapshot 帧
- Director.set_snapshot_callback + on_tick 触发（变化推 + 兜底周期）
- ws._dispatch_upstream snapshot/event 分支不污染 sc2_state（S5）
P1:
- Director._maybe_push_event_frame A 组埋点
- autopilot 阶段二边沿检测 B 组埋点
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

from vibecraft.bot import BotState, FakeFacade
from vibecraft.bot.director import Director, _RecentCommand
from vibecraft.directives.board import DirectiveBoard
from vibecraft.directives.models import Directive, StrategySetPayload
from vibecraft.directives.types import StageKind
from vibecraft.llm import IntentParser, MockLLMProvider
from vibecraft.logging_ import GameSession, GameSessionConfig
from vibecraft.strategy.library import StrategyLibrary
from vibecraft.strategy.models import LategameDoctrine, MidgameStance, OpeningBuild

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

    def test_phase_start_at_event_triggers_current(self) -> None:
        """Phase.start_at_event 触发后,_compute_current_phase_id 把它算 current。

        用户 spec (2026-05-23):野水晶真建好才能 phase 起,不能按 supply 估。
        """
        from vibecraft.strategy.models import Phase

        phases = [
            Phase(id="opening", display="开局", start_at_supply=14),
            Phase(id="tech", display="科技", start_at_supply=21),
            Phase(
                id="forward",
                display="野水晶就绪",
                start_at_event="dt_rush_forward_pylon_ready",
            ),
        ]
        # 事件未触发 → current=tech(最后一个 supply 阈值过的)
        assert Director._compute_current_phase_id(phases, supply_used=25, game_time=200.0) == "tech"
        # 事件触发 → current=forward(更后的 phase started)
        assert (
            Director._compute_current_phase_id(
                phases,
                supply_used=25,
                game_time=200.0,
                events={"dt_rush_forward_pylon_ready"},
            )
            == "forward"
        )

    def test_phase_events_unknown_event_ignored(self) -> None:
        """events 里有不相关事件 → 不影响推断。"""
        from vibecraft.strategy.models import Phase

        phases = [
            Phase(id="opening", display="开局", start_at_supply=14),
            Phase(
                id="forward",
                display="forward",
                start_at_event="dt_rush_forward_pylon_ready",
            ),
        ]
        assert (
            Director._compute_current_phase_id(
                phases,
                supply_used=20,
                game_time=100.0,
                events={"some_unrelated_event"},
            )
            == "opening"
        )

    def test_notify_phase_event_latches(self) -> None:
        """notify_phase_event 累积事件到 director._phase_events,不清空。"""
        d = _make_director()
        assert d._phase_events == set()
        d.notify_phase_event("dt_rush_forward_pylon_ready")
        assert "dt_rush_forward_pylon_ready" in d._phase_events
        d.notify_phase_event("dt_rush_dt_killed_worker")
        assert d._phase_events == {
            "dt_rush_forward_pylon_ready",
            "dt_rush_dt_killed_worker",
        }
        # 重复触发同事件 → no-op
        d.notify_phase_event("dt_rush_forward_pylon_ready")
        assert len(d._phase_events) == 2

    def test_build_snapshot_uses_phase_events(self) -> None:
        """build_snapshot 传 _phase_events 给 _compute_current_phase_id,事件触发后
        snapshot 的 current_phase_id 应反映 event-triggered phase。"""
        from vibecraft.strategy.models import OpeningBuild

        opening = OpeningBuild.model_validate(
            {
                "kind": "opening_build",
                "id": "dt_rush",
                "display_name_zh": "速隐刀",
                "phases": [
                    {"id": "opening", "display": "开局", "start_at_supply": 14},
                    {
                        "id": "forward",
                        "display": "野水晶就绪",
                        "start_at_event": "dt_rush_forward_pylon_ready",
                    },
                ],
                "steps": ["14 build Pylon"],
                "default_transitions": [{"midgame_id": "iac_2base", "when": "default"}],
            }
        )
        lib = StrategyLibrary(
            openings=[opening],
            midgames=[
                MidgameStance.model_validate(
                    {
                        "kind": "midgame_stance",
                        "id": "iac_2base",
                        "display_name_zh": "IAC",
                    }
                )
            ],
            lategames=[],
        )
        d = _make_director(library=lib)
        board = DirectiveBoard(commit_delay_s=0.0)
        d.board = board
        board.submit(_make_strategy_directive("dt_rush"), now=0.0)
        board.tick(1.0)

        # 事件未触发 → current_phase_id = "opening"
        snap = d.build_snapshot(1.0)
        assert snap["strategy"]["opening"]["current_phase_id"] == "opening"

        # 触发事件 → current_phase_id = "forward"
        d.notify_phase_event("dt_rush_forward_pylon_ready")
        snap = d.build_snapshot(2.0)
        assert snap["strategy"]["opening"]["current_phase_id"] == "forward"

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

    # M5: attack_window / micro_doctrine 字段穿透

    def test_midgame_slot_includes_attack_window(self) -> None:
        """M5: MidgameStance 带 attack_window → snapshot midgame slot 包含 attack_window。"""
        midgame_with_window = MidgameStance.model_validate(
            {
                "kind": "midgame_stance",
                "id": "iac_window",
                "display_name_zh": "IAC 进攻窗",
                "attack_window": {
                    "open_at": "9:30",
                    "close_at": "11:30",
                    "target_priority": ["army"],
                },
            }
        )
        lib = StrategyLibrary(openings=[], midgames=[midgame_with_window], lategames=[])
        d = _make_director(library=lib)
        board = DirectiveBoard(commit_delay_s=0.0)
        d.board = board

        board.submit(_make_strategy_directive("iac_window", StageKind.MIDGAME), now=0.0)
        board.tick(1.0)

        snap = d.build_snapshot(1.0)
        midgame = snap["strategy"]["midgame"]
        assert midgame is not None
        assert "attack_window" in midgame
        assert midgame["attack_window"]["open_at"] == "9:30"
        assert midgame["attack_window"]["close_at"] == "11:30"

    def test_midgame_slot_includes_micro_doctrine(self) -> None:
        """M5: MidgameStance 带 micro_doctrine → snapshot midgame slot 包含 micro_doctrine。"""
        midgame_with_doctrine = MidgameStance.model_validate(
            {
                "kind": "midgame_stance",
                "id": "iac_doctrine",
                "display_name_zh": "IAC 微操",
                "micro_doctrine": [
                    "archon focus_fire bio_clumps",
                    "immortal target high_hp_armored",
                ],
            }
        )
        lib = StrategyLibrary(openings=[], midgames=[midgame_with_doctrine], lategames=[])
        d = _make_director(library=lib)
        board = DirectiveBoard(commit_delay_s=0.0)
        d.board = board

        board.submit(_make_strategy_directive("iac_doctrine", StageKind.MIDGAME), now=0.0)
        board.tick(1.0)

        snap = d.build_snapshot(1.0)
        midgame = snap["strategy"]["midgame"]
        assert midgame is not None
        assert "micro_doctrine" in midgame
        assert len(midgame["micro_doctrine"]) == 2
        assert "archon focus_fire bio_clumps" in midgame["micro_doctrine"]

    def test_midgame_slot_no_attack_window_omitted(self) -> None:
        """M5: MidgameStance 无 attack_window → snapshot midgame slot 不含 attack_window 字段。"""
        lib = _make_library()
        d = _make_director(library=lib)
        board = DirectiveBoard(commit_delay_s=0.0)
        d.board = board

        board.submit(_make_strategy_directive("iac_2base", StageKind.MIDGAME), now=0.0)
        board.tick(1.0)

        snap = d.build_snapshot(1.0)
        midgame = snap["strategy"]["midgame"]
        assert midgame is not None
        # _make_library() 里的 iac_2base 无 attack_window，不应出现在 snapshot
        assert "attack_window" not in midgame

    def test_lategame_slot_includes_engagement_doctrine_as_micro_doctrine(self) -> None:
        """M5: LategameDoctrine 带 engagement_doctrine → snapshot lategame slot 含 micro_doctrine。"""
        lategame_with_doctrine = LategameDoctrine.model_validate(
            {
                "kind": "lategame_doctrine",
                "id": "skytoss_doctrine",
                "display_name_zh": "Skytoss 航母",
                "target_composition": {"CARRIER": 6},
                "engagement_doctrine": [
                    "carrier_kite max_dist=12",
                    "mass_recall when=fleet_total_hp<40%",
                ],
            }
        )
        lib = StrategyLibrary(openings=[], midgames=[], lategames=[lategame_with_doctrine])
        d = _make_director(library=lib)
        board = DirectiveBoard(commit_delay_s=0.0)
        d.board = board

        board.submit(_make_strategy_directive("skytoss_doctrine", StageKind.LATEGAME), now=0.0)
        board.tick(1.0)

        snap = d.build_snapshot(1.0)
        lategame = snap["strategy"]["lategame"]
        assert lategame is not None
        assert "micro_doctrine" in lategame
        assert "carrier_kite max_dist=12" in lategame["micro_doctrine"]


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
        from vibecraft.server.game_process import GameProcess
        from vibecraft.server.tokens import RoomRegistry
        from vibecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()
        gp._sc2_state = "playing"
        gp._bot_state = "running"

        conn = WsConnection(ws_mock, registry, game_process=gp)

        # 真实管线结构：snapshot 帧嵌套在 "frame" 里
        snapshot_raw = {
            "kind": "snapshot",
            "frame": {
                "type": "snapshot",
                "ts": 120.0,
                "strategy": {
                    "current_stage": "opening",
                    "opening": None,
                    "midgame": None,
                    "lategame": None,
                },
                "recent_commands": [],
            },
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
        from vibecraft.server.game_process import GameProcess
        from vibecraft.server.tokens import RoomRegistry
        from vibecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()
        gp._sc2_state = "playing"
        gp._bot_state = "running"

        conn = WsConnection(ws_mock, registry, game_process=gp)

        # 真实管线结构：event 帧嵌套在 "frame" 里，且 event 帧自身带 kind
        # （strategy.set）—— 回归测试守住「内层 kind 不会让外层认错消息类型」
        event_raw = {
            "kind": "event",
            "frame": {
                "type": "event",
                "kind": "strategy.set",
                "ts": 200.0,
                "payload": {"stage": "midgame", "strategy_id": "iac_2base"},
            },
        }
        await conn._dispatch_upstream(event_raw)

        assert gp._sc2_state == "playing"
        assert gp._bot_state == "running"

        ws_mock.send.assert_called_once()
        sent = json.loads(ws_mock.send.call_args[0][0])
        assert sent["type"] == "event"
        # 内层 event 帧的 kind 正确转发（不是被外层 "event" 覆盖）
        assert sent["kind"] == "strategy.set"

    async def test_snapshot_forwards_inner_frame(self) -> None:
        """转发的下行帧 = raw["frame"]（外层消息类型 kind 不进下行帧）。"""
        from vibecraft.server.game_process import GameProcess
        from vibecraft.server.tokens import RoomRegistry
        from vibecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()
        conn = WsConnection(ws_mock, registry, game_process=gp)

        await conn._dispatch_upstream(
            {
                "kind": "snapshot",
                "frame": {
                    "type": "snapshot",
                    "ts": 50.0,
                    "strategy": {
                        "current_stage": "opening",
                        "opening": None,
                        "midgame": None,
                        "lategame": None,
                    },
                    "recent_commands": [],
                },
            }
        )

        sent = json.loads(ws_mock.send.call_args[0][0])
        # 下行帧 = 内层 frame；外层标识消息类型的 kind 不出现在下行帧
        assert sent["type"] == "snapshot"
        assert sent["ts"] == 50.0
        assert "kind" not in sent


# ---------------------------------------------------------------------------
# P1-2：autopilot 边沿检测 + event 推送
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_sharpy() -> Any:
    _prefixes = ("sharpy", "vibecraft.bot.sharpy_adapter", "vibecraft.bot.auto_combat")
    for key in list(sys.modules.keys()):
        if any(key == p or key.startswith(p + ".") for p in _prefixes):
            del sys.modules[key]
    yield
    for key in list(sys.modules.keys()):
        if any(key == p or key.startswith(p + ".") for p in _prefixes):
            del sys.modules[key]


def _inject_fake_sharpy_minimal() -> type:
    """注入最小伪 sharpy，返回 FakeKnowledgeBot 类。"""
    import enum

    class _FakeUnitTask(enum.IntEnum):
        Idle = 0
        Reserved = 8

    fake_unit_task_mod = ModuleType("sharpy.managers.core.roles.unit_task")
    fake_unit_task_mod.UnitTask = _FakeUnitTask  # type: ignore[attr-defined]
    fake_roles_mod = ModuleType("sharpy.managers.core.roles")
    fake_roles_mod.UnitTask = _FakeUnitTask  # type: ignore[attr-defined]

    class FakeBuildOrder:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

    fake_plans_mod = ModuleType("sharpy.plans")
    fake_plans_mod.BuildOrder = FakeBuildOrder  # type: ignore[attr-defined]

    class FakeKnowledge:
        def __init__(self) -> None:
            self.roles = MagicMock()
            self.unit_cache = MagicMock()

        def pre_start(self, *a: Any, **kw: Any) -> None:
            pass

        async def start(self) -> None:
            pass

        async def update(self, iteration: int) -> None:
            pass

        async def post_update(self) -> None:
            pass

        async def on_unit_destroyed(self, unit_tag: int) -> None:
            pass

        async def on_end(self, result: Any) -> None:
            pass

        def print(self, *a: Any, **kw: Any) -> None:
            pass

    class FakeKnowledgeBot:
        """sharpy KnowledgeBot 极简 stub。"""

        def __init__(self, name: str = "fake") -> None:
            self.name = name
            self.knowledge = FakeKnowledge()
            self.time = 0.0
            self.minerals = 0
            self.vespene = 0
            self.supply_used = 0
            self.supply_cap = 0
            self.townhalls: list[Any] = []
            self.units = MagicMock()
            self.client = MagicMock()
            self.client.move_camera = AsyncMock()
            self.state = MagicMock()
            self.last_game_loop = -1
            self.realtime = False
            self.active_recipe = ""

        async def on_start(self) -> None:
            pass

        async def on_step(self, iteration: int) -> None:
            pass

        async def on_unit_destroyed(self, unit_tag: int) -> None:
            await self.knowledge.on_unit_destroyed(unit_tag)

        async def on_end(self, result: Any) -> None:
            await self.knowledge.on_end(result)

    fake_sharpy_mod = ModuleType("sharpy")
    fake_knowledges_mod = ModuleType("sharpy.knowledges")
    fake_knowledges_mod.KnowledgeBot = FakeKnowledgeBot  # type: ignore[attr-defined]
    fake_knowledges_mod.BuildOrder = FakeBuildOrder  # type: ignore[attr-defined]
    fake_kb_mod = ModuleType("sharpy.knowledges.knowledge_bot")
    fake_kb_mod.KnowledgeBot = FakeKnowledgeBot  # type: ignore[attr-defined]

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

    sys.modules["sharpy"] = fake_sharpy_mod
    sys.modules["sharpy.knowledges"] = fake_knowledges_mod
    sys.modules["sharpy.knowledges.knowledge_bot"] = fake_kb_mod
    sys.modules["sharpy.plans"] = fake_plans_mod
    sys.modules["sharpy.managers"] = ModuleType("sharpy.managers")
    sys.modules["sharpy.managers.core"] = ModuleType("sharpy.managers.core")
    sys.modules["sharpy.managers.core.roles"] = fake_roles_mod
    sys.modules["sharpy.managers.core.roles.unit_task"] = fake_unit_task_mod
    sys.modules["sharpy.managers.extensions"] = ModuleType("sharpy.managers.extensions")

    return FakeKnowledgeBot


# ---------------------------------------------------------------------------
# status_events 过滤 snapshot/event
# ---------------------------------------------------------------------------


class TestStatusEventsFiltersNewKinds:
    """status_events() 过滤 snapshot/event 消息（不当 GameStatus 返回）。"""

    async def test_status_events_filters_snapshot_and_event(self) -> None:
        from vibecraft.server.game_process import GameProcess

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


# ---------------------------------------------------------------------------
# P1.3：Snapshot standing_orders 字段
# ---------------------------------------------------------------------------


def _make_unit_claim_directive_persistent() -> Directive:
    """构造一个 persistent=True 的 UNIT_CLAIM Directive（凤凰 patrol natural）。"""
    from vibecraft.directives.models import UnitClaimPayload
    from vibecraft.directives.scope import Selector, TargetKind, TargetSpec
    from vibecraft.directives.task import Action, Task, Verb

    payload = UnitClaimPayload(
        selector=Selector(unit_type="Phoenix"),
        task=Task(
            primary_action=Action(
                verb=Verb.PATROL,
                target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="natural"),
            )
        ),
        persistent=True,
    )
    return Directive(payload=payload, issued_at=10.0)


class TestSnapshotStandingOrders:
    """P1.3 build_snapshot 加 standing_orders 字段。"""

    def test_snapshot_includes_standing_orders_field(self) -> None:
        """build_snapshot 返回 dict 包含 standing_orders 且是 list。"""
        d = _make_director()
        snap = d.build_snapshot(now=10.0)
        assert "standing_orders" in snap
        assert isinstance(snap["standing_orders"], list)

    def test_snapshot_standing_orders_empty_by_default(self) -> None:
        """没有 standing order 时，standing_orders 是空 list。"""
        d = _make_director()
        snap = d.build_snapshot(now=10.0)
        assert snap["standing_orders"] == []

    def test_snapshot_standing_order_view_fields(self) -> None:
        """submit persistent=True 的 unit_claim 后，snapshot 含 standing_orders 条目，
        含 id / display / issued_at 三个核心字段。
        """
        d = _make_director()
        directive = _make_unit_claim_directive_persistent()
        d._submit_directives([directive], now=10.0)
        snap = d.build_snapshot(now=11.0)
        assert len(snap["standing_orders"]) == 1
        view = snap["standing_orders"][0]
        assert view["id"] == directive.id
        # #5 i18n:display 用中文名（Phoenix→凤凰、patrol→巡逻），不露内置 id
        assert view["display"]
        assert "凤凰" in view["display"]
        assert "巡逻" in view["display"]
        assert "natural" in view["display"]
        assert view["issued_at"] == 10.0


# ---------------------------------------------------------------------------
# P2: Snapshot production_overrides 字段
# ---------------------------------------------------------------------------


def _make_production_override_for_snap() -> Directive:
    """构造一个 PRODUCTION_OVERRIDE Directive（出 2 哨兵）。"""
    from vibecraft.directives.models import ProductionItem, ProductionOverridePayload

    payload = ProductionOverridePayload(items=[ProductionItem(unit_type="Sentry", count=2)])
    return Directive(payload=payload, issued_at=20.0)


def _make_tech_override_for_snap() -> Directive:
    """构造一个 TECH_OVERRIDE Directive（研 Blink）。"""
    from vibecraft.directives.models import TechOverridePayload

    payload = TechOverridePayload(upgrade_id="Blink")
    return Directive(payload=payload, issued_at=20.0)


def _make_expansion_override_for_snap() -> Directive:
    """构造一个 EXPANSION_OVERRIDE Directive（开 3 矿）。"""
    from vibecraft.directives.models import ExpansionOverridePayload

    payload = ExpansionOverridePayload(target_count=3)
    return Directive(payload=payload, issued_at=20.0)


class TestSnapshotProductionOverrides:
    """P2 build_snapshot 加 production_overrides 字段。"""

    def test_snapshot_includes_production_overrides_field(self) -> None:
        """build_snapshot 返回 dict 包含 production_overrides 且是 list。"""
        d = _make_director()
        snap = d.build_snapshot(now=10.0)
        assert "production_overrides" in snap
        assert isinstance(snap["production_overrides"], list)

    def test_snapshot_production_overrides_view_fields(self) -> None:
        """submit PRODUCTION_OVERRIDE 后，snapshot 含 production_overrides 条目，
        含 id / display / issued_at 三个核心字段。
        """
        d = _make_director()
        directive = _make_production_override_for_snap()
        d._submit_directives([directive], now=20.0)
        snap = d.build_snapshot(now=21.0)
        assert len(snap["production_overrides"]) == 1
        view = snap["production_overrides"][0]
        assert view["id"] == directive.id
        assert view["issued_at"] == 20.0
        # display "新增 2 个 哨兵"(alias 或 Sentry 英文)
        assert view["display"]
        assert "新增" in view["display"] and "2 个" in view["display"]
        assert "哨兵" in view["display"] or "Sentry" in view["display"]

    def test_snapshot_production_override_display_formats(self) -> None:
        """三种 directive type 的 display 格式正确：
        PRODUCTION_OVERRIDE → '出 <unit>×N[ / <unit>×N ...]'（多 item join），
        TECH_OVERRIDE → '研 <upgrade>',
        EXPANSION_OVERRIDE → '开 N 矿'。
        """
        d = _make_director()
        prod_d = _make_production_override_for_snap()
        tech_d = _make_tech_override_for_snap()
        exp_d = _make_expansion_override_for_snap()
        d._submit_directives([prod_d, tech_d, exp_d], now=20.0)
        snap = d.build_snapshot(now=21.0)

        by_id = {v["id"]: v for v in snap["production_overrides"]}
        # PRODUCTION_OVERRIDE: "新增 2 个 哨兵"（alias）或 "新增 2 个 Sentry"（fallback）
        prod_view = by_id[prod_d.id]
        assert prod_view["display"].startswith("新增 ")
        assert "2" in prod_view["display"] and "个" in prod_view["display"]
        # TECH_OVERRIDE: "研 闪烁" 或 "研 Blink"
        tech_view = by_id[tech_d.id]
        assert "研 " in tech_view["display"]
        # EXPANSION_OVERRIDE: "开 3 矿"
        exp_view = by_id[exp_d.id]
        assert "开 3 矿" in exp_view["display"]


# ---------------------------------------------------------------------------
# P3.5: Snapshot active_tactics 字段
# ---------------------------------------------------------------------------


def _make_tactical_objective_directive(
    verb: str = "attack",
    target_area: str | tuple[float, float] | None = "enemy_natural",
) -> Directive:
    """构造一个 TACTICAL_OBJECTIVE Directive。"""
    from vibecraft.directives.models import TacticalObjectivePayload

    payload = TacticalObjectivePayload(verb=verb, target_area=target_area)  # type: ignore[arg-type]
    return Directive(payload=payload, issued_at=30.0)


class TestSnapshotActiveTactics:
    """P3.5 build_snapshot 加 active_tactics 字段。"""

    def test_snapshot_includes_active_tactics_field(self) -> None:
        """build_snapshot 返回 dict 包含 active_tactics 且是 list（空 list 也 OK）。"""
        d = _make_director()
        snap = d.build_snapshot(now=10.0)
        assert "active_tactics" in snap
        assert isinstance(snap["active_tactics"], list)

    def test_snapshot_active_tactics_empty_by_default(self) -> None:
        """没有 TACTICAL_OBJECTIVE in-flight 时，active_tactics 是空 list。"""
        d = _make_director()
        snap = d.build_snapshot(now=10.0)
        assert snap["active_tactics"] == []

    def test_snapshot_active_tactics_contains_one_after_submit(self) -> None:
        """TACTICAL_OBJECTIVE 进 _in_flight 后，snapshot.active_tactics 含 1 条。"""
        d = _make_director()
        board = DirectiveBoard(commit_delay_s=10.0)  # 不立即 commit，保持 in-flight
        d.board = board
        directive = _make_tactical_objective_directive("attack", "enemy_natural")
        d._submit_directives([directive], now=30.0)
        # directive 在 board pending 中，但已在 _in_flight
        snap = d.build_snapshot(now=30.5)
        assert len(snap["active_tactics"]) == 1

    def test_snapshot_active_tactics_view_fields_correct(self) -> None:
        """active_tactics 条目含正确字段：id / display / verb / target_area / issued_at。"""
        d = _make_director()
        board = DirectiveBoard(commit_delay_s=10.0)
        d.board = board
        directive = _make_tactical_objective_directive("attack", "enemy_natural")
        d._submit_directives([directive], now=30.0)
        snap = d.build_snapshot(now=30.5)
        assert len(snap["active_tactics"]) == 1
        view = snap["active_tactics"][0]
        assert view["id"] == directive.id
        assert view["verb"] == "attack"
        assert view["target_area"] == "enemy_natural"
        assert view["issued_at"] == 30.0
        # display 应为中文 "进攻 enemy_natural"
        assert view["display"] == "进攻 enemy_natural"

    def test_snapshot_active_tactics_display_chinese(self) -> None:
        """display 格式是中文动词 + target_area（e.g., '进攻 enemy_natural'）。"""
        d = _make_director()
        board = DirectiveBoard(commit_delay_s=10.0)
        d.board = board
        directive = _make_tactical_objective_directive("harass", "enemy_main")
        d._submit_directives([directive], now=30.0)
        snap = d.build_snapshot(now=30.5)
        view = snap["active_tactics"][0]
        assert "骚扰" in view["display"]
        assert "enemy_main" in view["display"]


# ---------------------------------------------------------------------------
# 2026-05-25 bug: persistent L2 global retreat 提交 → commit 后卡片消失
# (active_tactics 只从 _in_flight 取,commit 后 pop → snapshot 看不到)
# +
# attack_mode_override 残留:玩家先按 attack all_in 再按 retreat,mode 没清
# → PlanZoneAttack._should_retreat 仍按 all_in 不撤
# ---------------------------------------------------------------------------


def _make_persistent_tactical_directive(verb: str, target_area: str | None = None) -> Directive:
    """构造 persistent=True 的 L2 global directive(UI 按钮路径产物)。"""
    from vibecraft.directives.models import TacticalObjectivePayload

    payload = TacticalObjectivePayload(
        verb=verb,
        target_area=target_area,
        persistent=True,  # type: ignore[arg-type]
    )
    return Directive(payload=payload, issued_at=30.0)


class TestPersistentL2GlobalCommitBehavior:
    """玩家按 UI 战术按钮(persistent=True)的 commit 后行为修复。"""

    def _commit_directive(self, d: Director, did: str, commit_at: float) -> None:
        """触发 board commit + dispatch event(让 _exec_l2_global 跑)。"""
        events = d.board.tick(commit_at)
        for ev in events:
            d._dispatch_event(ev)

    def test_persistent_retreat_card_visible_after_commit(self) -> None:
        """bug A: persistent retreat commit 后,active_tactics 仍含该 directive
        (不能因为 _in_flight pop 就让卡片消失;否则玩家看不到也无法 ×)。"""
        d = _make_director()
        board = DirectiveBoard(commit_delay_s=1.5)
        d.board = board
        retreat = _make_persistent_tactical_directive("retreat")
        d._submit_directives([retreat], now=30.0)
        # commit 前可见(in_flight 路径,既有行为)
        snap_before = d.build_snapshot(now=30.5)
        assert any(v["id"] == retreat.id for v in snap_before["active_tactics"])
        # 触发 commit
        self._commit_directive(d, retreat.id, commit_at=32.0)
        # commit 后仍可见(新行为:从 _current_l2_global_directive 补)
        snap_after = d.build_snapshot(now=32.5)
        ids = [v["id"] for v in snap_after["active_tactics"]]
        assert retreat.id in ids, f"commit 后 active_tactics 应含 persistent retreat 卡,得到 {ids}"

    def test_attack_all_in_then_retreat_clears_mode_override(self) -> None:
        """bug B: 玩家先按 attack all_in → 再按 retreat,attack_mode_override
        应被清(否则 PlanZoneAttack._should_retreat 仍按 all_in 不撤)。
        """
        d = _make_director()
        board = DirectiveBoard(commit_delay_s=1.5)
        d.board = board
        # 模拟 _submit_tactical_action 路径:attack all_in 提交前先 set mode
        d.facade.set_attack_mode_override("all_in")  # type: ignore[union-attr]
        attack = _make_persistent_tactical_directive("attack", target_area="enemy_main")
        d._submit_directives([attack], now=30.0)
        self._commit_directive(d, attack.id, commit_at=32.0)
        # 此时 attack_mode_override 应是 "all_in"
        assert d.facade.attack_mode_overrides[-1] == "all_in"  # type: ignore[union-attr]

        # 玩家切 retreat(没传 mode)
        retreat = _make_persistent_tactical_directive("retreat")
        d._submit_directives([retreat], now=40.0)
        self._commit_directive(d, retreat.id, commit_at=42.0)
        # 关键:retreat commit 后 attack_mode_override 必须被清为 None
        assert d.facade.attack_mode_overrides[-1] is None, (
            f"切 retreat 后 attack_mode_override 应清,实际 {d.facade.attack_mode_overrides}"  # type: ignore[union-attr]
        )

    def test_submit_directives_pushes_snapshot_immediately(self) -> None:
        """玩家动作即时反馈:submit 后立即 push snapshot,不等 2s 兜底周期。"""
        d = _make_director()
        snapshots: list[dict[str, Any]] = []
        d.set_snapshot_callback(lambda snap: snapshots.append(snap))
        retreat = _make_persistent_tactical_directive("retreat")
        d._submit_directives([retreat], now=30.0)
        # submit 应触发至少 1 次 snapshot push,且最后一次 snapshot 含 retreat
        assert len(snapshots) >= 1, "submit 后必须立刻 push 1 次 snapshot"
        last = snapshots[-1]
        assert any(v["id"] == retreat.id for v in last["active_tactics"])

    def test_strategy_set_voice_calls_facade_set_build(self) -> None:
        """contract: STRATEGY_SET commit → facade.set_build(strategy_id) 被调。"""
        from vibecraft.directives.board import DirectiveBoard

        d = _make_director(library=_make_library())
        d.board = DirectiveBoard(commit_delay_s=0.0)
        directive = _make_strategy_directive("iac_2base", stage=StageKind.MIDGAME)
        d._submit_directives([directive], now=10.0)
        for ev in d.board.tick(10.5):
            d._dispatch_event(ev)
        assert "iac_2base" in d.facade.builds, (
            f"STRATEGY_SET commit 应调 facade.set_build,builds={d.facade.builds}"
        )

    def test_scout_directive_dispatches_execute_unit_action(self) -> None:
        """contract: SCOUT commit → facade.execute_unit_action(verb='scout')。"""
        from vibecraft.directives.board import DirectiveBoard
        from vibecraft.directives.models import Directive, ScoutPayload
        from vibecraft.directives.scope import Selector, TargetKind, TargetSpec

        d = _make_director()
        d.facade.selector_stub["Probe"] = [6001, 6002]
        d.board = DirectiveBoard(commit_delay_s=0.0)
        payload = ScoutPayload(
            selector=Selector(unit_type="Probe"),
            target=TargetSpec(kind=TargetKind.NAMED_SPOT, named_spot="enemy_main"),
        )
        directive = Directive(payload=payload, issued_at=10.0)
        d._submit_directives([directive], now=10.0)
        for ev in d.board.tick(10.5):
            d._dispatch_event(ev)
        scouts = [a for a in d.facade.unit_actions if a["verb"] == "scout"]
        assert len(scouts) == 1, (
            f"SCOUT commit 应只派 1 个 Probe(unit_type only 默认 cap 1),实际 {len(scouts)}"
        )

    def test_build_at_calls_facade_set_build_location_override(self) -> None:
        """contract: BUILD_AT commit → facade.set_build_location_override 被调。"""
        from vibecraft.directives.board import DirectiveBoard
        from vibecraft.directives.models import BuildAtPayload, Directive

        d = _make_director()
        d.board = DirectiveBoard(commit_delay_s=0.0)
        payload = BuildAtPayload(
            structure_type="Pylon",
            point=(50.0, 30.0),
        )
        directive = Directive(payload=payload, issued_at=10.0)
        d._submit_directives([directive], now=10.0)
        for ev in d.board.tick(10.5):
            d._dispatch_event(ev)
        assert len(d.facade.build_location_overrides) == 1, (
            f"BUILD_AT commit 应调 set_build_location_override,"
            f"实际 {d.facade.build_location_overrides}"
        )
        structure_type, point = d.facade.build_location_overrides[0]
        assert structure_type == "Pylon"
        assert point == (50.0, 30.0)

    def test_build_at_with_named_spot_only_does_not_crash_snapshot(self) -> None:
        """regression: BuildAtPayload(point=None, named_spot=...) 渲染命令卡时,
        _format_unit_directive_display 不应炸在 `x, y = payload.point`。

        2026-05-27 真实 crash: 玩家说"前线去个农民刷个水晶方便折跃追猎",LLM
        解析 named_spot=enemy_main / point=None → snapshot 渲染 unpack None
        → TypeError → 整 SC2 child process 退出。
        """
        from vibecraft.directives.board import DirectiveBoard
        from vibecraft.directives.models import BuildAtPayload, Directive

        d = _make_director()
        d.board = DirectiveBoard(commit_delay_s=0.0)
        payload = BuildAtPayload(
            structure_type="Pylon",
            named_spot="enemy_main",
        )
        directive = Directive(payload=payload, issued_at=10.0)
        d._submit_directives([directive], now=10.0)
        for ev in d.board.tick(10.5):
            d._dispatch_event(ev)
        snap = d.build_snapshot(10.5)
        cards = snap["command_cards"]
        assert any(
            "Pylon" in c.get("display", "") and "enemy_main" in c.get("display", "") for c in cards
        ), f"命令卡应含 named_spot 信息,实际 cards={cards}"

    def test_pending_activation_directive_shows_as_waiting_card(self) -> None:
        """2026-06-02 用户:等 activate_when 激活的 directive 显示成卡片,status=waiting
        (前端灰显"未激活")。修前 _pending_activation 的 directive 不显示成卡。"""
        from vibecraft.directives.models import Directive, TacticalObjectivePayload

        d = _make_director()
        directive = Directive(
            payload=TacticalObjectivePayload(verb="attack", target_area="enemy_natural"),
            issued_at=5.0,
        )
        # 模拟 _dispatch_committed_to_facade 把 activate_when 未满足的 directive 挂起
        d._pending_activation[directive.id] = directive
        d._override_status[directive.id] = {"status": "waiting", "reason": "等激活条件"}
        snap = d.build_snapshot(now=6.0)
        card = next((c for c in snap["command_cards"] if c["id"] == directive.id), None)
        assert card is not None, "等激活 directive 应显示成卡片"
        assert card["status"] == "waiting"

    def test_unit_release_with_count_caps(self) -> None:
        """contract bug 4: UNIT_RELEASE 也尊重 selector.count(走 helper)。
        玩家"释放一个农民"应只释放 1 个,不能全释放。
        """
        from vibecraft.bot.facade import UnitRole
        from vibecraft.directives.board import DirectiveBoard
        from vibecraft.directives.models import Directive, UnitReleasePayload
        from vibecraft.directives.scope import Selector

        d = _make_director()
        # 预先 set 5 个 Probe role=LLM_CONTROLLED
        tags = list(range(7100, 7105))
        for tag in tags:
            d.facade.set_unit_role(tag, UnitRole.LLM_CONTROLLED)
        d.facade.selector_stub["Probe"] = tags
        d.board = DirectiveBoard(commit_delay_s=0.0)
        payload = UnitReleasePayload(
            selector=Selector(unit_type="Probe", count=1),
            return_to_role="IDLE",
        )
        directive = Directive(payload=payload, issued_at=10.0)
        d._submit_directives([directive], now=10.0)
        for ev in d.board.tick(10.5):
            d._dispatch_event(ev)
        # 应该只 1 个 tag 被改成 IDLE,其余 4 个保持 LLM_CONTROLLED
        idle_count = sum(1 for t in tags if d.facade.unit_roles.get(t) == UnitRole.IDLE)
        llm_count = sum(1 for t in tags if d.facade.unit_roles.get(t) == UnitRole.LLM_CONTROLLED)
        assert idle_count == 1, f"count=1 应只释放 1 个,实际 {idle_count} 个变 IDLE"
        assert llm_count == 4, f"其余 4 个应保持 LLM_CONTROLLED,实际 {llm_count}"

    def test_ui_button_recon_assigns_default_squad(self) -> None:
        """2026-05-25 bug 10:UI button recon 不传 hint → _exec_l2_squad 应按
        _B_VERB_DEFAULT_HINTS(recon: 4 Stalker)派 squad,不能 on_hold 不动。
        """
        from vibecraft.bot.facade import UnitRole
        from vibecraft.directives.board import DirectiveBoard

        d = _make_director()
        d.facade.selector_stub["Stalker"] = [9101, 9102, 9103, 9104, 9105]
        d.board = DirectiveBoard(commit_delay_s=0.0)
        # UI button recon 路径产物:persistent=True, 无 hint, 无 target_area
        from vibecraft.directives.models import TacticalObjectivePayload

        payload = TacticalObjectivePayload(verb="recon", persistent=True)  # type: ignore[arg-type]
        d_recon = Directive(payload=payload, issued_at=30.0)
        d._submit_directives([d_recon], now=30.0)
        for ev in d.board.tick(30.5):
            d._dispatch_event(ev)
        # 预期 default 派 4 Stalker → set_unit_role 各调一次
        llm_controlled = [
            tag for tag, role in d.facade.unit_roles.items() if role == UnitRole.LLM_CONTROLLED
        ]
        assert len(llm_controlled) == 4, (
            f"UI button recon 应按 default(4 Stalker)派单位,实际接管 {len(llm_controlled)} 个"
        )

    # bug 9 contract test (auto_prereq dedup) 留 audit session (task #301) 补:
    # _auto_build_prereqs_for 是大方法,要 mock bot.race / prereq_chain / equivalent_structures
    # 等;最好 refactor 抽 helper _has_user_pending_override(struct_name) 再单测。
    # fix 本身已生效(line 2107 pending_override_types 检测),实战能验。
