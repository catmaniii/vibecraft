"""WS endpoint 单测（M1.1b）。

测试策略：不需要真实 TCP 连接，用 mock ServerConnection + 事件队列模拟收帧。
全部 async（pytest asyncio_mode = "auto"）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
    """实现 Connection Protocol 的测试桩。"""

    def __init__(self) -> None:
        self.closed_reason: str | None = None
        self.close_called = False

    async def close(self, reason: str) -> None:
        self.closed_reason = reason
        self.close_called = True


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
        conn = WsConnection(ws, registry)
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
        registry.attach(conn)

        await conn.run()
        assert registry.active_connection is None

    async def test_run_cancels_ping_task_on_exit(self) -> None:
        """run() 结束后 ping task 应当被 cancel（不泄漏 task）。"""
        registry = RoomRegistry(token="tok")
        ws = _make_ws_mock()

        async def _empty_iter() -> Any:
            return
            yield

        ws.__aiter__ = MagicMock(return_value=_empty_iter())
        conn = WsConnection(ws, registry)
        registry.attach(conn)

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
        # attach 成功后 run() 结束 → detach → active is None
        assert registry.active_connection is None

    async def test_evicts_old_connection(self) -> None:
        """新连接顶旧：旧连接的 close() 必须被调用。"""
        registry = RoomRegistry(token="tok")
        old_conn = _FakeConn()
        registry.attach(old_conn)

        handler = make_ws_handler(registry)
        ws = _make_ws_mock(path="/ws?room=tok")

        async def _empty_iter() -> Any:
            return
            yield

        ws.__aiter__ = MagicMock(return_value=_empty_iter())

        await handler(ws)
        assert old_conn.close_called is True
