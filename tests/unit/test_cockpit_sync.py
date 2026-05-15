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
                "micro_doctrine": ["archon focus_fire bio_clumps", "immortal target high_hp_armored"],
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

        board.submit(
            _make_strategy_directive("skytoss_doctrine", StageKind.LATEGAME), now=0.0
        )
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
# P1-2：ares_adapter autopilot 边沿检测 + event 推送
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_ares() -> Any:
    _prefixes = ("sharpy", "voicecraft.bot.sharpy_adapter", "voicecraft.bot.auto_combat")
    for key in list(sys.modules.keys()):
        if any(key == p or key.startswith(p + ".") for p in _prefixes):
            del sys.modules[key]
    yield
    for key in list(sys.modules.keys()):
        if any(key == p or key.startswith(p + ".") for p in _prefixes):
            del sys.modules[key]


def _inject_fake_ares_minimal() -> type:
    """注入最小伪 sharpy，返回 FakeKnowledgeBot 类（M1 sharpy 迁移后替代原 FakeAresBot）。"""
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


class TestAutopilotEventCallback:
    """P1-2：build_completed false→true 时推一次 decision.autopilot_phase event。

    S2 后：_VoiceCraftProtossBot 继承 Aristaeus MyBot，_register_auto_pilot 已移除。
    auto-pilot event 逻辑需要在 Aristaeus 框架内重新设计；当前测试 skip 保留历史。
    """

    def _make_instance(self, event_cb: Any) -> Any:
        FakeKnowledgeBot = _inject_fake_ares_minimal()
        from voicecraft.bot.sharpy_adapter import make_bot_class

        BotClass = make_bot_class(
            director_factory=lambda facade: MagicMock(),
            event_callback=event_cb,
        )
        instance = object.__new__(BotClass)
        FakeKnowledgeBot.__init__(instance)  # type: ignore[arg-type]
        instance.register_behavior = MagicMock()
        return instance

    @pytest.mark.skip(reason="S2 后 _register_auto_pilot 已移至 Aristaeus；需重设计")
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

    @pytest.mark.skip(reason="S2 后 _register_auto_pilot 已移至 Aristaeus；需重设计")
    def test_event_pushed_only_once(self) -> None:
        """build_completed 保持 True 时，后续 tick 不重复推。"""
        events: list[dict] = []
        instance = self._make_instance(events.append)

        instance.build_order_runner.build_completed = True
        instance._register_auto_pilot()  # 第一次：推
        instance._register_auto_pilot()  # 后续：不重复
        instance._register_auto_pilot()

        assert len(events) == 1

    @pytest.mark.skip(reason="S2 后 _register_auto_pilot 已移至 Aristaeus；需重设计")
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
