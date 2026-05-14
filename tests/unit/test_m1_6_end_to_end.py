"""M1.6 端到端串通单测。

测试策略：不需要真实 SC2 / ares，全部 mock。验证：
1. Gap 2：WS command handler → send_command
2. Gap 2：_VoiceCraftBot.on_step 消费下行队列
3. Gap 3：on_player_command 用 create_task fire-and-forget（不阻塞 on_step）
4. Gap 5：status_callback 在 on_start/on_end 调用
5. 基础 echo：parse 完成后调 echo_callback
6. raw_events()：上行队列含 echo 消息时正确 yield
7. status_events()（向后兼容）：echo 消息被过滤
"""

from __future__ import annotations

import asyncio
import json
import queue
import sys
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# fake ares 注入辅助（复用 test_ares_adapter.py 的模式）
# ---------------------------------------------------------------------------


def _inject_fake_ares() -> tuple[type, type]:
    """向 sys.modules 注入伪 ares 模块。"""
    fake_ares_consts = ModuleType("ares.consts")
    FakeUnitRole = MagicMock()
    FakeUnitRole.CONTROL_GROUP_ONE = "CONTROL_GROUP_ONE"
    FakeUnitRole.IDLE = "IDLE"
    FakeUnitRole.ATTACKING = "ATTACKING"
    FakeUnitRole.DEFENDING = "DEFENDING"
    FakeUnitRole.HARASSING = "HARASSING"
    FakeUnitRole.SCOUTING = "SCOUTING"
    fake_ares_consts.UnitRole = FakeUnitRole  # type: ignore[attr-defined]

    class FakeAresBot:
        def __init__(self) -> None:
            self.config: dict[str, Any] = {}
            self.build_order_runner = MagicMock()
            # 默认 opening 未跑完 → auto-pilot 只走阶段一（Mining/AutoSupply）
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

    # ares.behaviors.macro —— auto-pilot 用的 7 个 macro behavior（伪类即可）
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

    return FakeAresBot, FakeUnitRole


@pytest.fixture(autouse=True)
def _clean_ares_modules() -> Any:
    for key in list(sys.modules.keys()):
        if key.startswith("ares"):
            del sys.modules[key]
    for mod in ["voicecraft.bot.ares_adapter"]:
        sys.modules.pop(mod, None)
    yield
    for key in list(sys.modules.keys()):
        if key.startswith("ares"):
            del sys.modules[key]
    for mod in ["voicecraft.bot.ares_adapter"]:
        sys.modules.pop(mod, None)


# ---------------------------------------------------------------------------
# Gap 2：WS command handler → send_command
# ---------------------------------------------------------------------------


class TestWsCommandHandler:
    """WS _dispatch command 帧 → GameProcess.send_command（Gap 2）。"""

    async def test_command_sent_to_game_process(self) -> None:
        """command 帧里的 text 应通过 send_command 发到子进程。"""
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

        # 模拟子进程在跑
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = True
        gp._proc = fake_proc

        sent_cmds: list[dict[str, Any]] = []
        gp.send_command = lambda cmd: sent_cmds.append(cmd)  # type: ignore[method-assign]

        conn = WsConnection(ws_mock, registry, game_process=gp)
        await conn._handle_command({"type": "command", "text": "切1门Robo"})

        assert len(sent_cmds) == 1
        assert sent_cmds[0]["type"] == "command"
        assert sent_cmds[0]["text"] == "切1门Robo"
        assert "issued_at" in sent_cmds[0]

    async def test_command_dropped_when_no_game(self) -> None:
        """对局没开（is_running=False）时，command 应被静默丢弃。"""
        from voicecraft.server.game_process import GameProcess
        from voicecraft.server.tokens import RoomRegistry
        from voicecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()  # _proc=None → is_running=False

        sent_cmds: list[Any] = []
        gp.send_command = lambda cmd: sent_cmds.append(cmd)  # type: ignore[method-assign]

        conn = WsConnection(ws_mock, registry, game_process=gp)
        await conn._handle_command({"type": "command", "text": "切1门Robo"})

        assert sent_cmds == []

    async def test_empty_text_ignored(self) -> None:
        """text 为空时不发送。"""
        from voicecraft.server.game_process import GameProcess
        from voicecraft.server.tokens import RoomRegistry
        from voicecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = True
        gp._proc = fake_proc

        sent_cmds: list[Any] = []
        gp.send_command = lambda cmd: sent_cmds.append(cmd)  # type: ignore[method-assign]

        conn = WsConnection(ws_mock, registry, game_process=gp)
        await conn._handle_command({"type": "command", "text": "   "})

        assert sent_cmds == []

    async def test_dispatch_command_calls_handle_command(self) -> None:
        """_dispatch 收到 command 帧时调用 _handle_command（不再是 stub）。"""
        from voicecraft.server.game_process import GameProcess
        from voicecraft.server.tokens import RoomRegistry
        from voicecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()

        conn = WsConnection(ws_mock, registry, game_process=gp)

        handle_command_called: list[dict[str, Any]] = []
        orig = conn._handle_command

        async def _capture(frame: dict[str, Any]) -> None:
            handle_command_called.append(frame)
            await orig(frame)

        conn._handle_command = _capture  # type: ignore[method-assign]

        await conn._dispatch("command", {"type": "command", "text": "切1门Robo"})

        assert len(handle_command_called) == 1


# ---------------------------------------------------------------------------
# Gap 2 + Gap 3：_VoiceCraftBot.on_step 消费下行队列 + fire-and-forget
# ---------------------------------------------------------------------------


class TestBotOnStepConsumesQueue:
    """bot on_step 消费 down_q（Gap 2）+ create_task fire-and-forget（Gap 3）。"""

    async def test_on_step_creates_task_for_command(self) -> None:
        """on_step 里收到 command 消息，应 create_task 调 director.on_player_command。"""
        FakeAresBot, _ = _inject_fake_ares()
        from voicecraft.bot.ares_adapter import make_bot_class

        parse_calls: list[tuple[str, float]] = []

        async def fake_on_player_command(text: str, now: float) -> Any:
            parse_calls.append((text, now))
            # 返回一个 ParseError 模拟 LLM 完成
            from voicecraft.llm.schema import ParseError, ParseErrorKind

            return ParseError(kind=ParseErrorKind.PROVIDER_ERROR, message="test")

        director_mock = MagicMock()
        director_mock.on_player_command = fake_on_player_command
        director_mock.on_tick = MagicMock()

        down_q: queue.Queue[dict[str, Any]] = queue.Queue()
        down_q.put_nowait({"type": "command", "text": "切1门Robo", "issued_at": 42.0})

        BotClass = make_bot_class(
            director_factory=lambda facade: director_mock,
            down_q=down_q,
        )

        instance = object.__new__(BotClass)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []
        instance.director = director_mock
        instance.facade = MagicMock()

        # 调 on_step
        await instance.on_step(0)

        # task 已创建（create_task fire-and-forget）
        # 等待所有任务完成
        if instance._cmd_tasks:
            await asyncio.gather(*instance._cmd_tasks, return_exceptions=True)

        assert len(parse_calls) == 1
        assert parse_calls[0][0] == "切1门Robo"
        assert parse_calls[0][1] == 42.0

    async def test_on_step_does_not_await_command(self) -> None:
        """on_step 在 task 完成前就返回（不阻塞 realtime loop）。"""
        FakeAresBot, _ = _inject_fake_ares()
        from voicecraft.bot.ares_adapter import make_bot_class

        parse_started = asyncio.Event()
        parse_can_finish = asyncio.Event()

        async def slow_parse(text: str, now: float) -> Any:
            parse_started.set()
            await parse_can_finish.wait()  # 模拟 LLM 慢调用
            from voicecraft.llm.schema import ParseError, ParseErrorKind

            return ParseError(kind=ParseErrorKind.TIMEOUT, message="slow")

        director_mock = MagicMock()
        director_mock.on_player_command = slow_parse
        director_mock.on_tick = MagicMock()

        down_q: queue.Queue[dict[str, Any]] = queue.Queue()
        down_q.put_nowait({"type": "command", "text": "test"})

        BotClass = make_bot_class(
            director_factory=lambda facade: director_mock,
            down_q=down_q,
        )

        instance = object.__new__(BotClass)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []
        instance.director = director_mock
        instance.facade = MagicMock()

        # on_step 应在 slow_parse 完成前就返回
        on_step_task = asyncio.create_task(instance.on_step(0))
        # 给足够时间让 on_step 跑完（但 slow_parse 还没完）
        await asyncio.sleep(0.02)

        assert on_step_task.done(), "on_step 应该已经完成（不等 LLM 返回）"
        assert parse_started.is_set(), "parse 应已开始（fire-and-forget）"
        assert not parse_can_finish.is_set(), "parse 尚未完成（验证 fire-and-forget）"

        # 允许 parse 完成，清理
        parse_can_finish.set()
        if instance._cmd_tasks:
            await asyncio.gather(*instance._cmd_tasks, return_exceptions=True)

    async def test_cmd_task_exception_is_logged_not_raised(self) -> None:
        """后台 cmd task 异常时 log 不向上传播（不崩 bot）。"""
        FakeAresBot, _ = _inject_fake_ares()
        from voicecraft.bot.ares_adapter import make_bot_class

        async def failing_parse(text: str, now: float) -> Any:
            raise RuntimeError("parse 崩了")

        director_mock = MagicMock()
        director_mock.on_player_command = failing_parse
        director_mock.on_tick = MagicMock()

        down_q: queue.Queue[dict[str, Any]] = queue.Queue()
        down_q.put_nowait({"type": "command", "text": "test"})

        BotClass = make_bot_class(
            director_factory=lambda facade: director_mock,
            down_q=down_q,
        )

        instance = object.__new__(BotClass)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []
        instance.director = director_mock
        instance.facade = MagicMock()

        # 调 on_step 不应抛
        await instance.on_step(0)

        # 等 task 完成（应有异常但不传播）
        if instance._cmd_tasks:
            results = await asyncio.gather(*instance._cmd_tasks, return_exceptions=True)
            assert any(isinstance(r, RuntimeError) for r in results)

        # _cmd_tasks 应清空（done callback 已运行）
        await asyncio.sleep(0.01)  # 让 done callback 有机会执行
        assert instance._cmd_tasks == []

    async def test_empty_queue_does_not_crash(self) -> None:
        """下行队列空时 on_step 正常跑。"""
        FakeAresBot, _ = _inject_fake_ares()
        from voicecraft.bot.ares_adapter import make_bot_class

        director_mock = MagicMock()
        director_mock.on_tick = MagicMock()

        down_q: queue.Queue[dict[str, Any]] = queue.Queue()  # 空队列

        BotClass = make_bot_class(
            director_factory=lambda facade: director_mock,
            down_q=down_q,
        )

        instance = object.__new__(BotClass)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []
        instance.director = director_mock
        instance.facade = MagicMock()

        await instance.on_step(0)
        director_mock.on_tick.assert_called_once()


# ---------------------------------------------------------------------------
# Gap 5：status_callback 在 on_start / on_end 调用
# ---------------------------------------------------------------------------


class TestStatusCallback:
    """status_callback 在 on_start 推 in_game → playing，on_end 推 ended（Gap 5）。"""

    async def test_status_callback_on_start(self) -> None:
        """on_start 应调 status_callback("in_game", ...) 和 status_callback("playing", ...)。"""
        FakeAresBot, _ = _inject_fake_ares()
        from voicecraft.bot.ares_adapter import make_bot_class

        calls: list[tuple[str, str, str]] = []

        def status_cb(sc2: str, bot: str, detail: str = "") -> None:
            calls.append((sc2, bot, detail))

        BotClass = make_bot_class(
            director_factory=lambda facade: MagicMock(),
            status_callback=status_cb,
        )

        instance = object.__new__(BotClass)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []

        with patch.object(FakeAresBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        sc2_states = [c[0] for c in calls]
        assert "in_game" in sc2_states
        assert "playing" in sc2_states

    async def test_status_callback_on_end(self) -> None:
        """on_end 应调 status_callback("ended", ...)。"""
        FakeAresBot, _ = _inject_fake_ares()
        from voicecraft.bot.ares_adapter import make_bot_class

        calls: list[tuple[str, str, str]] = []

        def status_cb(sc2: str, bot: str, detail: str = "") -> None:
            calls.append((sc2, bot, detail))

        BotClass = make_bot_class(
            director_factory=lambda facade: MagicMock(),
            status_callback=status_cb,
        )

        instance = object.__new__(BotClass)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []

        await instance.on_end("Defeat")

        assert any(c[0] == "ended" for c in calls)

    async def test_no_status_callback_is_backward_compatible(self) -> None:
        """status_callback=None 时，on_start / on_end 不抛异常（向后兼容）。"""
        FakeAresBot, _ = _inject_fake_ares()
        from voicecraft.bot.ares_adapter import make_bot_class

        BotClass = make_bot_class(
            director_factory=lambda facade: MagicMock(),
            # status_callback=None（默认）
        )

        instance = object.__new__(BotClass)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []

        with patch.object(FakeAresBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()  # 不应抛

        await instance.on_end("Victory")  # 不应抛


# ---------------------------------------------------------------------------
# 基础 echo：echo_callback 调用
# ---------------------------------------------------------------------------


class TestEchoCallback:
    """parse 完成后 echo_callback 被调用（基础 echo）。"""

    async def test_echo_on_successful_parse(self) -> None:
        """IntentParseResult → echo_callback(text, interpretation_zh)。"""
        FakeAresBot, _ = _inject_fake_ares()
        from voicecraft.bot.ares_adapter import make_bot_class
        from voicecraft.llm.schema import IntentParseResult

        echo_calls: list[tuple[str, str]] = []

        async def fake_parse(text: str, now: float) -> Any:
            return IntentParseResult(
                interpretation_zh="切到1门Robo不朽流",
                confidence=0.9,
                directives=[],
            )

        director_mock = MagicMock()
        director_mock.on_player_command = fake_parse
        director_mock.on_tick = MagicMock()

        down_q: queue.Queue[dict[str, Any]] = queue.Queue()
        down_q.put_nowait({"type": "command", "text": "切1门Robo", "issued_at": 10.0})

        BotClass = make_bot_class(
            director_factory=lambda facade: director_mock,
            down_q=down_q,
            echo_callback=lambda t, i: echo_calls.append((t, i)),
        )

        instance = object.__new__(BotClass)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []
        instance.director = director_mock
        instance.facade = MagicMock()

        await instance.on_step(0)
        if instance._cmd_tasks:
            await asyncio.gather(*instance._cmd_tasks, return_exceptions=True)

        assert len(echo_calls) == 1
        assert echo_calls[0][0] == "切1门Robo"
        assert echo_calls[0][1] == "切到1门Robo不朽流"

    async def test_echo_on_parse_error(self) -> None:
        """ParseError → echo_callback(text, '[解析失败] ...')。"""
        FakeAresBot, _ = _inject_fake_ares()
        from voicecraft.bot.ares_adapter import make_bot_class
        from voicecraft.llm.schema import ParseError, ParseErrorKind

        echo_calls: list[tuple[str, str]] = []

        async def fake_parse(text: str, now: float) -> Any:
            return ParseError(kind=ParseErrorKind.TIMEOUT, message="LLM 超时")

        director_mock = MagicMock()
        director_mock.on_player_command = fake_parse
        director_mock.on_tick = MagicMock()

        down_q: queue.Queue[dict[str, Any]] = queue.Queue()
        down_q.put_nowait({"type": "command", "text": "乱说", "issued_at": 5.0})

        BotClass = make_bot_class(
            director_factory=lambda facade: director_mock,
            down_q=down_q,
            echo_callback=lambda t, i: echo_calls.append((t, i)),
        )

        instance = object.__new__(BotClass)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []
        instance.director = director_mock
        instance.facade = MagicMock()

        await instance.on_step(0)
        if instance._cmd_tasks:
            await asyncio.gather(*instance._cmd_tasks, return_exceptions=True)

        assert len(echo_calls) == 1
        assert "[解析失败]" in echo_calls[0][1]

    async def test_no_echo_callback_is_fine(self) -> None:
        """echo_callback=None 时，parse 完成后不抛异常。"""
        FakeAresBot, _ = _inject_fake_ares()
        from voicecraft.bot.ares_adapter import make_bot_class
        from voicecraft.llm.schema import IntentParseResult

        async def fake_parse(text: str, now: float) -> Any:
            return IntentParseResult(
                interpretation_zh="测试",
                confidence=0.9,
                directives=[],
            )

        director_mock = MagicMock()
        director_mock.on_player_command = fake_parse
        director_mock.on_tick = MagicMock()

        down_q: queue.Queue[dict[str, Any]] = queue.Queue()
        down_q.put_nowait({"type": "command", "text": "test", "issued_at": 1.0})

        BotClass = make_bot_class(
            director_factory=lambda facade: director_mock,
            down_q=down_q,
            # echo_callback=None（默认）
        )

        instance = object.__new__(BotClass)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []
        instance.director = director_mock
        instance.facade = MagicMock()

        await instance.on_step(0)
        if instance._cmd_tasks:
            await asyncio.gather(*instance._cmd_tasks, return_exceptions=True)
        # 不应抛异常


# ---------------------------------------------------------------------------
# GameProcess.raw_events() 和 status_events() 向后兼容
# ---------------------------------------------------------------------------


def _make_q(*messages: dict[str, Any]) -> queue.Queue[dict[str, Any]]:
    q: queue.Queue[dict[str, Any]] = queue.Queue()
    for msg in messages:
        q.put_nowait(msg)
    return q


def _make_fake_proc(alive: bool = False, exitcode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.is_alive.return_value = alive
    proc.exitcode = exitcode
    return proc


class TestRawEvents:
    """raw_events() 上行流：game_status + echo 消息都 yield（Gap 2 上行）。"""

    async def test_raw_events_yields_game_status(self) -> None:
        from voicecraft.server.game_process import GameProcess

        gp = GameProcess()
        gp._up_q = _make_q({"sc2": "playing", "bot": "running"})
        gp._proc = _make_fake_proc(alive=False, exitcode=0)

        raws: list[dict[str, Any]] = []
        async for raw in gp.raw_events():
            raws.append(raw)

        assert any(r.get("sc2") == "playing" for r in raws)

    async def test_raw_events_yields_echo_messages(self) -> None:
        """echo 消息也应被 raw_events() yield 出来。"""
        from voicecraft.server.game_process import GameProcess

        gp = GameProcess()
        gp._up_q = _make_q(
            {"sc2": "playing", "bot": "running"},
            {"kind": "echo", "user_text": "切1门Robo", "interpretation": "切到1门Robo不朽流"},
        )
        gp._proc = _make_fake_proc(alive=False, exitcode=0)

        raws: list[dict[str, Any]] = []
        async for raw in gp.raw_events():
            raws.append(raw)

        echo_msgs = [r for r in raws if r.get("kind") == "echo"]
        assert len(echo_msgs) == 1
        assert echo_msgs[0]["user_text"] == "切1门Robo"

    async def test_status_events_filters_echo(self) -> None:
        """status_events()（向后兼容）应过滤掉 echo 消息。"""
        from voicecraft.server.game_process import GameProcess, GameStatus

        gp = GameProcess()
        gp._up_q = _make_q(
            {"sc2": "playing", "bot": "running"},
            {"kind": "echo", "user_text": "test", "interpretation": "ok"},
            {"sc2": "ended", "bot": "idle"},
        )
        gp._proc = _make_fake_proc(alive=False, exitcode=0)

        statuses: list[GameStatus] = []
        async for s in gp.status_events():
            statuses.append(s)

        # 只有 game_status 类消息，没有 echo
        sc2_states = [s.sc2 for s in statuses]
        assert "playing" in sc2_states
        assert "ended" in sc2_states
        # 没有 echo 被当 GameStatus 返回
        for s in statuses:
            assert s.sc2 != "echo"


# ---------------------------------------------------------------------------
# WS status pump：echo 消息转 command_echo 帧
# ---------------------------------------------------------------------------


class TestStatusPumpEcho:
    """status pump 收到 echo 消息时下行 command_echo 帧。"""

    async def test_echo_message_sent_as_command_echo_frame(self) -> None:
        """上行 echo 消息 → WS 下行 command_echo 帧。"""
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

        # 直接调 _dispatch_upstream 模拟 echo 消息
        await conn._dispatch_upstream(
            {
                "kind": "echo",
                "user_text": "切1门Robo",
                "interpretation": "切到1门Robo不朽流",
            }
        )

        ws_mock.send.assert_called_once()
        frame = json.loads(ws_mock.send.call_args[0][0])
        assert frame["type"] == "command_echo"
        assert frame["user_text"] == "切1门Robo"
        assert frame["interpretation"] == "切到1门Robo不朽流"
        assert "ts" in frame

    async def test_game_status_message_dispatched_correctly(self) -> None:
        """上行 game_status 消息 → WS 下行 game_status 帧（正常路径不破坏）。"""
        from voicecraft.server.game_process import GameProcess
        from voicecraft.server.tokens import RoomRegistry
        from voicecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()
        gp._sc2_state = "launching"
        gp._bot_state = "idle"

        conn = WsConnection(ws_mock, registry, game_process=gp)

        await conn._dispatch_upstream({"sc2": "playing", "bot": "running", "detail": ""})

        ws_mock.send.assert_called_once()
        frame = json.loads(ws_mock.send.call_args[0][0])
        assert frame["type"] == "game_status"
        assert frame["sc2"] == "playing"


# ---------------------------------------------------------------------------
# Gap 4 向后兼容：make_bot_class 旧用法（无 director）仍能用
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """make_bot_class 旧用法不带 strategy_library / status_callback / down_q 仍能工作。"""

    async def test_old_usage_no_new_params(self) -> None:
        """旧版 make_bot_class(director_factory) 签名不破坏。"""
        FakeAresBot, _ = _inject_fake_ares()
        from voicecraft.bot.ares_adapter import make_bot_class

        BotClass = make_bot_class(lambda facade: MagicMock())

        instance = object.__new__(BotClass)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []

        with patch.object(FakeAresBot, "on_start", new_callable=AsyncMock):
            await instance.on_start()

        # on_step 调 on_tick
        instance.director = MagicMock()
        instance.director.on_tick = MagicMock()
        await instance.on_step(0)
        instance.director.on_tick.assert_called_once()

        await instance.on_end("Victory")


# ---------------------------------------------------------------------------
# on_end 等待 in-flight cmd tasks
# ---------------------------------------------------------------------------


class TestOnEndWaitsForTasks:
    """on_end 时等待所有 in-flight cmd task 完成。"""

    async def test_on_end_awaits_cmd_tasks(self) -> None:
        """on_end 应 gather 所有 _cmd_tasks 才调 status_callback("ended")。"""
        FakeAresBot, _ = _inject_fake_ares()
        from voicecraft.bot.ares_adapter import make_bot_class

        task_finished = asyncio.Event()
        status_calls: list[str] = []

        async def slow_task() -> None:
            await asyncio.sleep(0.02)
            task_finished.set()

        def status_cb(sc2: str, bot: str, detail: str = "") -> None:
            status_calls.append(sc2)

        BotClass = make_bot_class(
            director_factory=lambda facade: MagicMock(),
            status_callback=status_cb,
        )

        instance = object.__new__(BotClass)
        FakeAresBot.__init__(instance)  # type: ignore[arg-type]
        instance._cmd_tasks = []

        # 手动加一个 in-flight task
        t = asyncio.create_task(slow_task())
        instance._cmd_tasks.append(t)

        await instance.on_end("Defeat")

        # task 应已完成
        assert task_finished.is_set()
        # status_callback 应已调
        assert "ended" in status_calls
