"""WS endpoint 单测（M1.1b）。

测试策略：不需要真实 TCP 连接，用 mock ServerConnection + 事件队列模拟收帧。
全部 async（pytest asyncio_mode = "auto"）。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from vibecraft.server.game_process import GameStatus
from vibecraft.server.tokens import RoomRegistry
from vibecraft.server.ws import WsConnection, make_ws_handler

# ---------------------------------------------------------------------------
# 测试桩
# ---------------------------------------------------------------------------


def _make_ws_mock(path: str = "/ws?room=test-token") -> MagicMock:
    """构造一个 fake ServerConnection，request.path 指向给定路径。"""
    ws = MagicMock()
    ws.remote_address = ("127.0.0.1", 12345)
    request_mock = MagicMock()
    request_mock.path = path
    ws.request = request_mock
    ws.close = AsyncMock()
    ws.send = AsyncMock()
    return ws


class _FakeConn:
    """实现 Connection Protocol 的测试桩（含 send_text）。"""

    def __init__(self) -> None:
        self.closed_reason: str | None = None
        self.close_called = False

    async def close(self, reason: str) -> None:
        self.closed_reason = reason
        self.close_called = True

    async def send_text(self, frame: str) -> None:
        pass


# ---------------------------------------------------------------------------
# WsConnection.close()
# ---------------------------------------------------------------------------


class TestWsConnectionClose:
    async def test_close_calls_ws_close(self) -> None:
        """close() 应当调用底层 ws.close()。"""
        registry = RoomRegistry(token="tok")
        ws = _make_ws_mock()
        conn = WsConnection(ws, registry)
        await conn.close("test")
        ws.close.assert_called_once()

    async def test_close_swallows_exception(self) -> None:
        """底层 ws.close() 抛异常时不应该向上传播。"""
        registry = RoomRegistry(token="tok")
        ws = _make_ws_mock()
        ws.close = AsyncMock(side_effect=RuntimeError("already closed"))
        conn = WsConnection(ws, registry)
        # 不应抛
        await conn.close("test")


# ---------------------------------------------------------------------------
# WsConnection._handle_raw() 帧解析
# ---------------------------------------------------------------------------


class TestWsConnectionDispatch:
    def _make_conn(self) -> tuple[WsConnection, MagicMock]:
        registry = RoomRegistry(token="tok")
        ws = _make_ws_mock()
        mock_gp = MagicMock()
        mock_gp.is_running = False
        mock_gp.status = GameStatus(sc2="idle", bot="idle")

        async def _empty_events() -> Any:
            return
            yield  # makes this an async generator

        mock_gp.raw_events = _empty_events
        conn = WsConnection(ws, registry, game_process=mock_gp)
        return conn, ws

    async def test_invalid_json_does_not_raise(self) -> None:
        conn, _ = self._make_conn()
        # 不应该抛，只 log warning
        await conn._handle_raw("not-json{{{")

    async def test_missing_type_does_not_raise(self) -> None:
        conn, _ = self._make_conn()
        await conn._handle_raw(json.dumps({"no_type": True}))

    async def test_known_frame_types_dispatch_without_error(self) -> None:
        conn, _ = self._make_conn()
        known_types = [
            {"type": "start_game"},
            {"type": "command", "text": "切 1门Robo"},
            {"type": "view_move", "target_point": [88, 134]},
            {"type": "view_follow", "unit_tag": 1234},
            {"type": "view_zoom", "level": 0.7},
            {"type": "recipe", "recipe_id": "r1"},
            {"type": "compile_strategy", "text": "xxx"},
            {"type": "confirm_ambiguous", "echo_id": "e1", "confirmed": True},
            {"type": "revoke", "echo_id": "e1"},
            {"type": "save_recipe", "echo_id": "e1", "name": "test"},
            {"type": "release_unit", "directive_ids": ["d1"]},
        ]
        for frame in known_types:
            await conn._handle_raw(json.dumps(frame))  # 都不应该抛

    async def test_unknown_frame_type_does_not_raise(self) -> None:
        conn, _ = self._make_conn()
        await conn._handle_raw(json.dumps({"type": "future_unknown_type"}))

    async def test_bytes_frame_decoded(self) -> None:
        conn, _ = self._make_conn()
        # bytes 帧也应该被正常处理
        await conn._handle_raw(json.dumps({"type": "ping"}).encode())


# ---------------------------------------------------------------------------
# WsConnection.run() 生命周期
# ---------------------------------------------------------------------------


class TestWsConnectionRun:
    async def test_run_calls_detach_on_exit(self) -> None:
        """run() 结束后必须调用 registry.detach()。"""
        registry = RoomRegistry(token="tok")
        ws = _make_ws_mock()

        # 模拟 async for ws 不返回任何帧（empty iterator）
        async def _empty_iter() -> Any:
            return
            yield  # 让它是 async generator

        ws.__aiter__ = MagicMock(return_value=_empty_iter())
        conn = WsConnection(ws, registry)
        registry.attach(conn, player_id="default")

        await conn.run()
        assert registry.connection_of("default") is None

    async def test_run_cancels_ping_task_on_exit(self) -> None:
        """run() 结束后 ping task 应当被 cancel（不泄漏 task）。"""
        registry = RoomRegistry(token="tok")
        ws = _make_ws_mock()

        async def _empty_iter() -> Any:
            return
            yield

        ws.__aiter__ = MagicMock(return_value=_empty_iter())
        conn = WsConnection(ws, registry)
        registry.attach(conn, player_id="default")

        # 完成后应当干净退出（ping task 不应该变成"永远挂着的 task"）
        await asyncio.wait_for(conn.run(), timeout=2.0)


# ---------------------------------------------------------------------------
# 心跳
# ---------------------------------------------------------------------------


class TestHeartbeat:
    async def test_heartbeat_sends_ping_frame(self) -> None:
        """心跳 loop 发送合法的 ping 帧（type=ping, ts 字段）。"""
        registry = RoomRegistry(token="tok")
        ws = _make_ws_mock()
        conn = WsConnection(ws, registry)

        # 只跑一次心跳，然后取消
        task = asyncio.create_task(conn._heartbeat_loop())
        await asyncio.sleep(0.05)  # 让 loop 启动
        # 注意：_PING_INTERVAL=5s，第一个 ping 还没发
        # 改用 patch 缩短间隔来测试
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def test_heartbeat_sends_valid_json(self) -> None:
        """心跳帧是合法 JSON，包含 type=ping 和 ts。"""
        registry = RoomRegistry(token="tok")
        ws = _make_ws_mock()
        conn = WsConnection(ws, registry)

        sent: list[str] = []

        async def capture_send(data: str) -> None:
            sent.append(data)

        ws.send = capture_send  # type: ignore[method-assign]

        with patch("vibecraft.server.ws._PING_INTERVAL", 0.01):
            task = asyncio.create_task(conn._heartbeat_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert len(sent) >= 1
        frame = json.loads(sent[0])
        assert frame["type"] == "ping"
        assert isinstance(frame["ts"], float)


# ---------------------------------------------------------------------------
# default_realtime：CLI / ServiceConfig 透传到 _handle_start_game
# ---------------------------------------------------------------------------


class TestDefaultRealtimeFallback:
    """验证 PWA start_game 帧不传 realtime 时回退到 ServiceConfig.default_realtime。"""

    def _make_conn(self, default_realtime: bool) -> tuple[WsConnection, MagicMock]:
        registry = RoomRegistry(token="tok")
        ws = _make_ws_mock()
        mock_gp = MagicMock()
        mock_gp.is_running = False
        mock_gp.start = MagicMock()
        mock_gp.status = GameStatus(sc2="launching", bot="idle")
        conn = WsConnection(ws, registry, game_process=mock_gp, default_realtime=default_realtime)
        return conn, ws

    async def test_pwa_omits_realtime_uses_service_default_true(self) -> None:
        conn, _ = self._make_conn(default_realtime=True)
        await conn._handle_start_game({"type": "start_game", "config": {}})
        gp = conn._game_process
        gp.start.assert_called_once()  # type: ignore[attr-defined]
        passed_config = gp.start.call_args[0][0]  # type: ignore[attr-defined]
        assert passed_config.realtime is True

    async def test_pwa_omits_realtime_uses_service_default_false(self) -> None:
        conn, _ = self._make_conn(default_realtime=False)
        await conn._handle_start_game({"type": "start_game", "config": {}})
        passed_config = conn._game_process.start.call_args[0][0]  # type: ignore[attr-defined]
        assert passed_config.realtime is False

    async def test_pwa_explicit_realtime_overrides_service_default(self) -> None:
        """PWA 显式传 realtime=True 时优先使用 PWA 值（即使 service 默认 False）。"""
        conn, _ = self._make_conn(default_realtime=False)
        await conn._handle_start_game({"type": "start_game", "config": {"realtime": True}})
        passed_config = conn._game_process.start.call_args[0][0]  # type: ignore[attr-defined]
        assert passed_config.realtime is True


# ---------------------------------------------------------------------------
# make_ws_handler：握手逻辑
# ---------------------------------------------------------------------------


class TestMakeWsHandler:
    async def test_missing_token_closes_connection(self) -> None:
        """无 token → close(1008)。"""
        registry = RoomRegistry(token="tok")
        handler = make_ws_handler(registry)

        ws = _make_ws_mock(path="/ws")  # 无 room= 参数
        await handler(ws)
        ws.close.assert_called_once()
        code = ws.close.call_args[0][0]
        assert code == 1008

    async def test_invalid_token_closes_connection(self) -> None:
        """错误 token → close(1008)。"""
        registry = RoomRegistry(token="correct-token")
        handler = make_ws_handler(registry)

        ws = _make_ws_mock(path="/ws?room=wrong-token")
        await handler(ws)
        ws.close.assert_called_once()
        code = ws.close.call_args[0][0]
        assert code == 1008

    async def test_valid_token_starts_session(self) -> None:
        """有效 token → attach() + run() 被调用；run() 正常结束（空 iter）。"""
        registry = RoomRegistry(token="valid-token")
        handler = make_ws_handler(registry)

        ws = _make_ws_mock(path="/ws?room=valid-token")

        async def _empty_iter() -> Any:
            return
            yield

        ws.__aiter__ = MagicMock(return_value=_empty_iter())

        await handler(ws)
        # attach 成功后 run() 结束 → detach → default player 连接为 None
        assert registry.connection_of("default") is None

    async def test_evicts_old_connection(self) -> None:
        """新连接顶旧：旧连接的 close() 必须被调用。"""
        registry = RoomRegistry(token="tok")
        old_conn = _FakeConn()
        registry.attach(old_conn, player_id="default")

        handler = make_ws_handler(registry)
        ws = _make_ws_mock(path="/ws?room=tok")

        async def _empty_iter() -> Any:
            return
            yield

        ws.__aiter__ = MagicMock(return_value=_empty_iter())

        await handler(ws)
        assert old_conn.close_called is True


# ---------------------------------------------------------------------------
# P1.4: revoke_directive 上行帧
# ---------------------------------------------------------------------------


class TestRevokeDirectiveFrame:
    """验证 ws.py 正确处理 revoke_directive 帧 → game_process.send_command。"""

    def _make_conn_with_game(self) -> tuple[WsConnection, MagicMock]:
        """构造 WsConnection，注入一个 running fake game_process。"""
        registry = RoomRegistry(token="tok")
        ws = _make_ws_mock()
        mock_gp = MagicMock()
        mock_gp.is_running = True
        mock_gp.send_command = MagicMock()
        conn = WsConnection(ws, registry, game_process=mock_gp)
        return conn, mock_gp

    async def test_revoke_directive_sent_to_game(self) -> None:
        """有效 revoke_directive 帧 → game_process.send_command 收到完整 dict。"""
        conn, mock_gp = self._make_conn_with_game()
        await conn._handle_raw(json.dumps({"type": "revoke_directive", "directive_id": "d_abc123"}))
        mock_gp.send_command.assert_called_once_with(
            {"type": "revoke_directive", "directive_id": "d_abc123"}
        )

    async def test_revoke_directive_missing_id_rejected(self) -> None:
        """缺少 directive_id 时 send_command 不应被调用。"""
        conn, mock_gp = self._make_conn_with_game()
        await conn._handle_raw(json.dumps({"type": "revoke_directive"}))
        mock_gp.send_command.assert_not_called()


# ---------------------------------------------------------------------------
# Task 3: audio_chunk / audio_end / audio_cancel → AsrSession → transcript
# ---------------------------------------------------------------------------


class TestAudioFrames:
    """验证 audio_chunk/audio_end/audio_cancel 帧 → AsrSession → transcript 回推。

    AsrEngine / AsrSession 全部用 mock 注入，不依赖真实 funasr。
    """

    # ------------------------------------------------------------------
    # 工厂方法
    # ------------------------------------------------------------------

    def _make_mock_session(
        self,
        partial: str | None = "草稿",
        final: str = "最终识别",
    ) -> MagicMock:
        """构造 fake AsrSession。"""
        sess = MagicMock()
        sess.feed = AsyncMock(return_value=partial)
        sess.finalize = AsyncMock(return_value=final)
        sess.cancel = MagicMock()
        return sess

    def _make_mock_engine(
        self,
        available: bool = True,
        session: MagicMock | None = None,
    ) -> MagicMock:
        """构造 fake AsrEngine，create_session 返回给定 session（或 None）。"""
        eng = MagicMock()
        eng.available = available
        # ws.py 现按玩家 locale 用 available_for(locale) 门控（双模型 zh/en）。
        eng.available_for = MagicMock(return_value=available)
        eng.create_session = AsyncMock(return_value=session)
        return eng

    def _make_conn(
        self,
        asr_engine: Any = None,
    ) -> tuple[WsConnection, MagicMock]:
        """构造注入 fake AsrEngine 的 WsConnection。"""
        registry = RoomRegistry(token="tok")
        ws = _make_ws_mock()
        conn = WsConnection(ws, registry, asr_engine=asr_engine)
        return conn, ws

    @staticmethod
    def _pcm_b64(n_samples: int = 100) -> str:
        """生成 n_samples 个假 PCM16 样本的 base64 字符串。"""
        return base64.b64encode(b"\x00\x01" * n_samples).decode()

    # ------------------------------------------------------------------
    # audio_chunk 测试
    # ------------------------------------------------------------------

    async def test_audio_chunk_creates_session_on_first_chunk(self) -> None:
        """首帧 audio_chunk → engine.create_session 被调用一次。"""
        sess = self._make_mock_session()
        eng = self._make_mock_engine(session=sess)
        conn, _ws = self._make_conn(asr_engine=eng)
        await conn._handle_raw(
            json.dumps({"type": "audio_chunk", "seq": 0, "pcm": self._pcm_b64()})
        )
        eng.create_session.assert_awaited_once()

    async def test_audio_chunk_calls_feed(self) -> None:
        """audio_chunk → session.feed 被调用。"""
        sess = self._make_mock_session()
        eng = self._make_mock_engine(session=sess)
        conn, _ws = self._make_conn(asr_engine=eng)
        await conn._handle_raw(
            json.dumps({"type": "audio_chunk", "seq": 0, "pcm": self._pcm_b64()})
        )
        sess.feed.assert_awaited_once()

    async def test_audio_chunk_partial_sends_transcript_is_final_false(self) -> None:
        """feed 返回草稿 → ws.send 一帧 {type:transcript, is_final:false}。"""
        sess = self._make_mock_session(partial="草稿文字")
        eng = self._make_mock_engine(session=sess)
        conn, ws = self._make_conn(asr_engine=eng)
        await conn._handle_raw(
            json.dumps({"type": "audio_chunk", "seq": 0, "pcm": self._pcm_b64()})
        )
        ws.send.assert_awaited_once()
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["type"] == "transcript"
        assert sent["text"] == "草稿文字"
        assert sent["is_final"] is False

    async def test_audio_chunk_no_partial_no_send(self) -> None:
        """feed 返回 None → ws.send 不被调用。"""
        sess = self._make_mock_session(partial=None)
        eng = self._make_mock_engine(session=sess)
        conn, ws = self._make_conn(asr_engine=eng)
        await conn._handle_raw(
            json.dumps({"type": "audio_chunk", "seq": 0, "pcm": self._pcm_b64()})
        )
        ws.send.assert_not_called()

    async def test_audio_chunk_second_chunk_reuses_session(self) -> None:
        """第二帧 audio_chunk → 复用已有 session，create_session 只调一次。"""
        sess = self._make_mock_session()
        eng = self._make_mock_engine(session=sess)
        conn, _ws = self._make_conn(asr_engine=eng)
        pcm = self._pcm_b64()
        await conn._handle_raw(json.dumps({"type": "audio_chunk", "seq": 0, "pcm": pcm}))
        await conn._handle_raw(json.dumps({"type": "audio_chunk", "seq": 1, "pcm": pcm}))
        assert eng.create_session.await_count == 1
        assert sess.feed.await_count == 2

    # ------------------------------------------------------------------
    # audio_end 测试
    # ------------------------------------------------------------------

    async def test_audio_end_calls_finalize(self) -> None:
        """有活跃 session 时 audio_end → session.finalize 被调用。"""
        sess = self._make_mock_session()
        eng = self._make_mock_engine(session=sess)
        conn, ws = self._make_conn(asr_engine=eng)
        # 先建 session
        await conn._handle_raw(
            json.dumps({"type": "audio_chunk", "seq": 0, "pcm": self._pcm_b64()})
        )
        ws.send.reset_mock()
        await conn._handle_raw(json.dumps({"type": "audio_end"}))
        sess.finalize.assert_awaited_once()

    async def test_audio_end_sends_final_transcript_is_final_true(self) -> None:
        """audio_end → ws.send 一帧 {type:transcript, is_final:true}。"""
        sess = self._make_mock_session(final="最终识别文字")
        eng = self._make_mock_engine(session=sess)
        conn, ws = self._make_conn(asr_engine=eng)
        await conn._handle_raw(
            json.dumps({"type": "audio_chunk", "seq": 0, "pcm": self._pcm_b64()})
        )
        ws.send.reset_mock()
        await conn._handle_raw(json.dumps({"type": "audio_end"}))
        ws.send.assert_awaited_once()
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["type"] == "transcript"
        assert sent["text"] == "最终识别文字"
        assert sent["is_final"] is True

    async def test_audio_end_clears_session(self) -> None:
        """audio_end → _asr_session 被清为 None。"""
        sess = self._make_mock_session()
        eng = self._make_mock_engine(session=sess)
        conn, _ws = self._make_conn(asr_engine=eng)
        await conn._handle_raw(
            json.dumps({"type": "audio_chunk", "seq": 0, "pcm": self._pcm_b64()})
        )
        assert conn._asr_session is not None
        await conn._handle_raw(json.dumps({"type": "audio_end"}))
        assert conn._asr_session is None

    async def test_audio_end_without_session_no_crash_no_send(self) -> None:
        """没有活跃 session 时收到 audio_end → 不崩、不发 transcript。"""
        sess = self._make_mock_session()
        eng = self._make_mock_engine(session=sess)
        conn, ws = self._make_conn(asr_engine=eng)
        # 不发 audio_chunk，直接 audio_end
        await conn._handle_raw(json.dumps({"type": "audio_end"}))
        sess.finalize.assert_not_called()
        ws.send.assert_not_called()

    # ------------------------------------------------------------------
    # audio_cancel 测试
    # ------------------------------------------------------------------

    async def test_audio_cancel_calls_cancel(self) -> None:
        """有活跃 session 时 audio_cancel → session.cancel() 被调用。"""
        sess = self._make_mock_session()
        eng = self._make_mock_engine(session=sess)
        conn, ws = self._make_conn(asr_engine=eng)
        await conn._handle_raw(
            json.dumps({"type": "audio_chunk", "seq": 0, "pcm": self._pcm_b64()})
        )
        ws.send.reset_mock()
        await conn._handle_raw(json.dumps({"type": "audio_cancel"}))
        sess.cancel.assert_called_once()

    async def test_audio_cancel_no_transcript_sent(self) -> None:
        """audio_cancel → ws.send 不被调用（不发 transcript）。"""
        sess = self._make_mock_session()
        eng = self._make_mock_engine(session=sess)
        conn, ws = self._make_conn(asr_engine=eng)
        await conn._handle_raw(
            json.dumps({"type": "audio_chunk", "seq": 0, "pcm": self._pcm_b64()})
        )
        ws.send.reset_mock()
        await conn._handle_raw(json.dumps({"type": "audio_cancel"}))
        ws.send.assert_not_called()

    async def test_audio_cancel_clears_session(self) -> None:
        """audio_cancel → _asr_session 被清为 None。"""
        sess = self._make_mock_session()
        eng = self._make_mock_engine(session=sess)
        conn, _ws = self._make_conn(asr_engine=eng)
        await conn._handle_raw(
            json.dumps({"type": "audio_chunk", "seq": 0, "pcm": self._pcm_b64()})
        )
        assert conn._asr_session is not None
        await conn._handle_raw(json.dumps({"type": "audio_cancel"}))
        assert conn._asr_session is None

    async def test_audio_cancel_without_session_no_crash(self) -> None:
        """没有活跃 session 时收到 audio_cancel → 不崩。"""
        sess = self._make_mock_session()
        eng = self._make_mock_engine(session=sess)
        conn, _ws = self._make_conn(asr_engine=eng)
        # 不发 audio_chunk，直接 audio_cancel
        await conn._handle_raw(json.dumps({"type": "audio_cancel"}))
        sess.cancel.assert_not_called()

    # ------------------------------------------------------------------
    # engine 不可用测试
    # ------------------------------------------------------------------

    async def test_engine_unavailable_audio_chunk_no_crash(self) -> None:
        """engine.available=False → audio_chunk 不崩、不调 create_session、不发帧。"""
        sess = self._make_mock_session()
        eng = self._make_mock_engine(available=False, session=sess)
        conn, ws = self._make_conn(asr_engine=eng)
        await conn._handle_raw(
            json.dumps({"type": "audio_chunk", "seq": 0, "pcm": self._pcm_b64()})
        )
        eng.create_session.assert_not_called()
        ws.send.assert_not_called()

    async def test_engine_none_audio_chunk_no_crash(self) -> None:
        """asr_engine=None → audio_chunk 不崩、ws.send 不被调用。"""
        conn, ws = self._make_conn(asr_engine=None)
        await conn._handle_raw(
            json.dumps({"type": "audio_chunk", "seq": 0, "pcm": self._pcm_b64()})
        )
        ws.send.assert_not_called()

    async def test_engine_none_audio_end_no_crash(self) -> None:
        """asr_engine=None → audio_end 不崩。"""
        conn, ws = self._make_conn(asr_engine=None)
        await conn._handle_raw(json.dumps({"type": "audio_end"}))
        ws.send.assert_not_called()

    async def test_engine_none_audio_cancel_no_crash(self) -> None:
        """asr_engine=None → audio_cancel 不崩。"""
        conn, ws = self._make_conn(asr_engine=None)
        await conn._handle_raw(json.dumps({"type": "audio_cancel"}))
        ws.send.assert_not_called()
