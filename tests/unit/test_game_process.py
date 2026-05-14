"""GameProcess 单测（M1.2）。

测试策略：
- 不拉真实 SC2，用 fake 子进程入口（只按协议吐 game_status 消息的桩）测生命周期
- multiprocessing.Queue 直接注入，绕开 spawn，不需要真 SC2 环境
- 测 GameProcess 的上行状态机 / 下行通道 / 善后逻辑
- 所有 async 测试走 asyncio_mode = "auto"（pyproject.toml 已配）
"""

from __future__ import annotations

import multiprocessing
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from voicecraft.server.game_process import (
    GameConfig,
    GameProcess,
    GameStatus,
    _apply_raw_dict,
    _build_game_status_frame_dict,
)

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _make_up_q(*messages: dict[str, str]) -> multiprocessing.Queue:  # type: ignore[type-arg]
    """构造一个已有若干消息的上行队列（不需要子进程）。"""
    ctx = multiprocessing.get_context("spawn")
    q: multiprocessing.Queue[dict[str, str]] = ctx.Queue()
    for msg in messages:
        q.put_nowait(msg)
    return q


def _make_fake_proc(
    pid: int = 12345,
    alive: bool = True,
    exitcode: int | None = None,
) -> MagicMock:
    """构造一个 fake multiprocessing.Process。"""
    proc = MagicMock()
    proc.pid = pid
    proc.is_alive.return_value = alive
    proc.exitcode = exitcode
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.join = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# GameConfig
# ---------------------------------------------------------------------------


class TestGameConfig:
    def test_defaults(self) -> None:
        cfg = GameConfig()
        assert cfg.map_name == "Goldenaura LE"
        assert cfg.opponent_race == "Random"
        assert cfg.opponent_difficulty == "Easy"
        assert cfg.realtime is True

    def test_picklable(self) -> None:
        """GameConfig 必须跨 spawn 边界传递，需能 pickle。"""
        import pickle

        cfg = GameConfig(map_name="DaybreakLE", opponent_race="Protoss", realtime=False)
        data = pickle.dumps(cfg)
        restored = pickle.loads(data)
        assert restored.map_name == "DaybreakLE"
        assert restored.opponent_race == "Protoss"
        assert restored.realtime is False

    def test_custom_fields(self) -> None:
        cfg = GameConfig(
            map_name="DaybreakLE",
            opponent_race="Terran",
            opponent_difficulty="Hard",
            realtime=False,
        )
        assert cfg.map_name == "DaybreakLE"
        assert cfg.opponent_difficulty == "Hard"


# ---------------------------------------------------------------------------
# GameStatus
# ---------------------------------------------------------------------------


class TestGameStatus:
    def test_fields(self) -> None:
        ts = time.time()
        s = GameStatus(sc2="playing", bot="running", ts=ts, detail="ok")
        assert s.sc2 == "playing"
        assert s.bot == "running"
        assert s.ts == ts
        assert s.detail == "ok"

    def test_default_detail_empty(self) -> None:
        s = GameStatus(sc2="idle", bot="idle")
        assert s.detail == ""


# ---------------------------------------------------------------------------
# 辅助函数（从 game_process 导出，供 ws.py 复用）
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    def test_apply_raw_dict_normal(self) -> None:
        raw = {"sc2": "playing", "bot": "running", "detail": ""}
        sc2, bot, detail = _apply_raw_dict(raw, current_sc2="launching", current_bot="idle")
        assert sc2 == "playing"
        assert bot == "running"
        assert detail == ""

    def test_apply_raw_dict_partial(self) -> None:
        """部分字段缺失时 fallback 到当前值。"""
        raw = {"sc2": "crashed"}
        sc2, bot, detail = _apply_raw_dict(raw, current_sc2="playing", current_bot="running")
        assert sc2 == "crashed"
        assert bot == "running"  # fallback
        assert detail == ""  # fallback

    def test_build_game_status_frame_dict_fields(self) -> None:
        status = GameStatus(sc2="launching", bot="idle", ts=100.0, detail="")
        d = _build_game_status_frame_dict(status)
        assert d["type"] == "game_status"
        assert d["sc2"] == "launching"
        assert d["bot"] == "idle"
        assert d["link"] == "connected"
        assert d["ts"] == 100.0


# ---------------------------------------------------------------------------
# GameProcess.status property
# ---------------------------------------------------------------------------


class TestGameProcessStatus:
    def test_initial_status_idle(self) -> None:
        gp = GameProcess()
        s = gp.status
        assert s.sc2 == "idle"
        assert s.bot == "idle"

    def test_is_running_false_initially(self) -> None:
        gp = GameProcess()
        assert gp.is_running is False


# ---------------------------------------------------------------------------
# GameProcess.start()
# ---------------------------------------------------------------------------


class TestGameProcessStart:
    def test_start_sets_launching(self) -> None:
        """start() 调用后内部状态应变 launching。"""
        gp = GameProcess()
        config = GameConfig(map_name="DaybreakLE", realtime=False)

        # patch multiprocessing.Process.start 让它不真的 spawn
        with patch("voicecraft.server.game_process.multiprocessing") as mock_mp:
            ctx = MagicMock()
            mock_proc = _make_fake_proc()
            ctx.Queue.return_value = MagicMock()
            ctx.Process.return_value = mock_proc
            mock_mp.get_context.return_value = ctx

            gp.start(config)

        assert gp._sc2_state == "launching"

    def test_start_twice_terminates_first(self) -> None:
        """连续两次 start()，第一次的进程应被 terminate。"""
        gp = GameProcess()
        config = GameConfig(realtime=False)

        with patch("voicecraft.server.game_process.multiprocessing") as mock_mp:
            ctx = MagicMock()
            first_proc = _make_fake_proc(pid=1001, alive=True)
            second_proc = _make_fake_proc(pid=1002, alive=True)
            # 队列 mock：两次 start() 各新建一对队列
            ctx.Queue.return_value = MagicMock()
            ctx.Process.side_effect = [first_proc, second_proc]
            mock_mp.get_context.return_value = ctx

            gp.start(config)
            gp._proc = first_proc  # 手动设置，让第二次 start 能感知
            gp.start(config)

        first_proc.terminate.assert_called()


# ---------------------------------------------------------------------------
# GameProcess.status_events() — 核心逻辑测试
# ---------------------------------------------------------------------------


class TestGameProcessStatusEvents:
    async def test_drain_queue_messages(self) -> None:
        """status_events() 应把上行队列里的消息全部 yield 出来。"""
        gp = GameProcess()
        messages = [
            {"sc2": "launching", "bot": "idle", "detail": ""},
            {"sc2": "in_game", "bot": "running", "detail": ""},
            {"sc2": "playing", "bot": "running", "detail": ""},
            {"sc2": "ended", "bot": "idle", "detail": ""},
        ]
        gp._up_q = _make_up_q(*messages)

        # 子进程：已死，exitcode=0（正常退出）
        fake_proc = _make_fake_proc(alive=False, exitcode=0)
        gp._proc = fake_proc

        collected: list[GameStatus] = []
        async for s in gp.status_events():
            collected.append(s)

        sc2_states = [s.sc2 for s in collected]
        assert "launching" in sc2_states
        assert "playing" in sc2_states
        assert "ended" in sc2_states

    async def test_crashed_on_nonzero_exit(self) -> None:
        """子进程以非 0 exitcode 退出 + 没推过 ended → 应补 crashed 状态。"""
        gp = GameProcess()
        gp._up_q = _make_up_q()  # 空队列（子进程没来得及推消息就崩了）

        fake_proc = _make_fake_proc(alive=False, exitcode=-9)
        gp._proc = fake_proc

        collected: list[GameStatus] = []
        async for s in gp.status_events():
            collected.append(s)

        assert any(s.sc2 == "crashed" for s in collected), (
            f"应有 crashed 状态，实际收到：{[s.sc2 for s in collected]}"
        )

    async def test_no_crash_if_already_ended(self) -> None:
        """子进程 exitcode 非 0，但已推过 ended，不再重复推 crashed。"""
        gp = GameProcess()
        gp._up_q = _make_up_q({"sc2": "ended", "bot": "idle", "detail": ""})
        gp._sc2_state = "ended"  # 模拟已收到 ended

        fake_proc = _make_fake_proc(alive=False, exitcode=1)
        gp._proc = fake_proc

        collected: list[GameStatus] = []
        async for s in gp.status_events():
            collected.append(s)

        assert not any(s.sc2 == "crashed" for s in collected), (
            f"不应再出现 crashed，实际收到：{[s.sc2 for s in collected]}"
        )

    async def test_no_proc_returns_immediately(self) -> None:
        """_proc 为 None 时 status_events() 应立即结束（空生成器）。"""
        gp = GameProcess()
        gp._proc = None

        collected: list[GameStatus] = []
        async for s in gp.status_events():
            collected.append(s)

        assert collected == []

    async def test_state_updated_after_events(self) -> None:
        """yield 之后内部 _sc2_state / _bot_state 应已更新。"""
        gp = GameProcess()
        gp._up_q = _make_up_q({"sc2": "in_game", "bot": "running", "detail": ""})
        fake_proc = _make_fake_proc(alive=False, exitcode=0)
        gp._proc = fake_proc

        async for _ in gp.status_events():
            pass

        assert gp._sc2_state in ("in_game", "ended", "crashed")


# ---------------------------------------------------------------------------
# GameProcess.send_command()
# ---------------------------------------------------------------------------


class TestGameProcessSendCommand:
    def test_send_command_no_queue_logs_warning(self, caplog: Any) -> None:
        gp = GameProcess()
        gp._down_q = None
        # 不抛异常
        gp.send_command({"type": "leave"})

    def test_send_command_puts_to_queue(self) -> None:
        gp = GameProcess()
        ctx = multiprocessing.get_context("spawn")
        gp._down_q = ctx.Queue()
        gp.send_command({"type": "leave"})
        msg = gp._down_q.get_nowait()
        assert msg["type"] == "leave"


# ---------------------------------------------------------------------------
# GameProcess.stop()
# ---------------------------------------------------------------------------


class TestGameProcessStop:
    async def test_stop_no_proc_noop(self) -> None:
        """stop() 在没有进程时不抛异常。"""
        gp = GameProcess()
        await gp.stop()  # 不应报错

    async def test_stop_terminates_proc(self) -> None:
        """stop() 应对活着的进程调 terminate。"""
        gp = GameProcess()
        fake_proc = _make_fake_proc(alive=True)
        # join 返回后模拟进程已退出
        fake_proc.join.side_effect = lambda timeout=None: None

        ctx = multiprocessing.get_context("spawn")
        gp._down_q = ctx.Queue()
        gp._proc = fake_proc

        await gp.stop()

        fake_proc.terminate.assert_called()

    async def test_stop_clears_state(self) -> None:
        """stop() 完成后 _proc 应为 None。"""
        gp = GameProcess()
        fake_proc = _make_fake_proc(alive=False)
        fake_proc.join.return_value = None
        gp._proc = fake_proc
        ctx = multiprocessing.get_context("spawn")
        gp._down_q = ctx.Queue()
        gp._up_q = ctx.Queue()

        await gp.stop()

        assert gp._proc is None


# ---------------------------------------------------------------------------
# WS 层集成：start_game 帧 → GameProcess 交互
# ---------------------------------------------------------------------------


class TestWsStartGameIntegration:
    async def test_start_game_frame_triggers_game_process_start(self) -> None:
        """收到 start_game 帧 → GameProcess.start() 被调用，config 正确解析。"""
        from unittest.mock import AsyncMock, patch

        from voicecraft.server.tokens import RoomRegistry
        from voicecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()

        called_config: list[GameConfig] = []

        def fake_start(config: GameConfig) -> None:
            called_config.append(config)

        gp.start = fake_start  # type: ignore[method-assign]
        gp._sc2_state = "launching"
        gp._bot_state = "idle"

        conn = WsConnection(ws_mock, registry, game_process=gp)

        # 模拟 status_events() 立即结束（不真的启 SC2）
        async def _empty_events() -> Any:
            return
            yield  # make it an async generator

        with patch.object(gp, "status_events", return_value=_empty_events()):
            await conn._handle_start_game(
                {
                    "type": "start_game",
                    "config": {
                        "map": "DaybreakLE",
                        "opponent_race": "Terran",
                        "opponent_difficulty": "Hard",
                        "realtime": False,
                    },
                }
            )

        assert len(called_config) == 1
        cfg = called_config[0]
        assert cfg.map_name == "DaybreakLE"
        assert cfg.opponent_race == "Terran"
        assert cfg.opponent_difficulty == "Hard"
        assert cfg.realtime is False

    async def test_start_game_sends_launching_frame(self) -> None:
        """start_game 处理后应立即发一帧 game_status{sc2: launching}。"""
        from voicecraft.server.tokens import RoomRegistry
        from voicecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()
        gp.start = MagicMock()  # type: ignore[method-assign]
        gp._sc2_state = "launching"
        gp._bot_state = "idle"

        conn = WsConnection(ws_mock, registry, game_process=gp)

        async def _empty_events() -> Any:
            return
            yield

        with patch.object(gp, "status_events", return_value=_empty_events()):
            await conn._handle_start_game({"type": "start_game"})

        import json

        # 检查发出的帧
        calls = ws_mock.send.call_args_list
        assert len(calls) >= 1
        first_frame = json.loads(calls[0].args[0])
        assert first_frame["type"] == "game_status"
        assert first_frame["sc2"] == "launching"
        assert first_frame["link"] == "connected"

    async def test_start_game_default_config_when_no_config_field(self) -> None:
        """start_game 帧不带 config 字段 → 使用 GameConfig 默认值。"""
        from voicecraft.server.tokens import RoomRegistry
        from voicecraft.server.ws import WsConnection

        ws_mock = MagicMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)
        ws_mock.send = AsyncMock()

        registry = RoomRegistry(token="tok")
        gp = GameProcess()
        called_config: list[GameConfig] = []
        gp.start = lambda cfg: called_config.append(cfg)  # type: ignore[method-assign]
        gp._sc2_state = "launching"
        gp._bot_state = "idle"

        conn = WsConnection(ws_mock, registry, game_process=gp)

        async def _empty_events() -> Any:
            return
            yield

        with patch.object(gp, "status_events", return_value=_empty_events()):
            await conn._handle_start_game({"type": "start_game"})

        assert called_config[0].map_name == GameConfig.map_name
        assert called_config[0].opponent_race == GameConfig.opponent_race


# ---------------------------------------------------------------------------
# game_status 帧格式
# ---------------------------------------------------------------------------


class TestGameStatusFrameFormat:
    def test_frame_has_required_fields(self) -> None:
        import json

        from voicecraft.server.ws import _build_game_status_frame

        status = GameStatus(sc2="playing", bot="running", ts=500.0)
        frame_str = _build_game_status_frame(status)
        frame = json.loads(frame_str)

        assert frame["type"] == "game_status"
        assert frame["sc2"] == "playing"
        assert frame["bot"] == "running"
        assert frame["link"] == "connected"
        assert frame["ts"] == 500.0
        assert "detail" in frame

    def test_crashed_frame_has_detail(self) -> None:
        import json

        from voicecraft.server.ws import _build_game_status_frame

        status = GameStatus(sc2="crashed", bot="error", detail="地图未找到")
        frame_str = _build_game_status_frame(status)
        frame = json.loads(frame_str)
        assert frame["detail"] == "地图未找到"
