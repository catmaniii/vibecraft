"""WS endpoint：手机 ↔ bot service 的双向 WebSocket 通道。

设计文档 §9.2 / §9.3：
- URL 格式：ws://host:port/ws?room=<token>
- 握手时从 query 取 token，用 RoomRegistry.verify() 验证，失败拒连（WS close 1008）
- attach() 接入，若返回被顶掉的旧连接则 close()（重连顶旧）
- 帧收发循环：收 JSON → 解析 type → 分发 handler（M1.1 阶段全是 stub）
- 5s 心跳：下行 {"type": "ping", "ts": <game_time>}
- 连接断开调 RoomRegistry.detach()
- 所有连接事件用 structlog 结构化 log
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import structlog
from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

from voicecraft.server.tokens import Connection, RoomRegistry

logger = structlog.get_logger(__name__)

# 业务层心跳间隔（秒）。websockets 协议层 keepalive 在 BotService 里设
# ping_interval=None 关掉，心跳全由业务层 ping 帧负责。
_PING_INTERVAL: float = 5.0


class WsConnection:
    """一个活跃 WS 连接，实现 Connection Protocol（tokens.py）。

    M1.1b 阶段：帧业务逻辑全是 stub，只 log。
    M1.4-M1.6 阶段：替换 _dispatch 里的 handler。
    """

    def __init__(self, ws: ServerConnection, registry: RoomRegistry) -> None:
        self._ws = ws
        self._registry = registry
        self._log = logger.bind(
            remote=str(ws.remote_address),
        )

    # ------------------------------------------------------------------
    # Connection Protocol
    # ------------------------------------------------------------------

    async def close(self, reason: str) -> None:
        """关闭连接（被顶旧时由 RoomRegistry 调用方负责调用）。"""
        self._log.info("ws_connection_evicted", reason=reason)
        with contextlib.suppress(Exception):
            await self._ws.close()

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """接管握手后的全部生命周期：心跳 + 收帧循环 + 断开清理。"""
        self._log.info("ws_connected")
        ping_task = asyncio.create_task(self._heartbeat_loop())
        try:
            async for raw in self._ws:
                await self._handle_raw(raw)
        except ConnectionClosed as exc:
            self._log.info(
                "ws_disconnected",
                code=exc.rcvd.code if exc.rcvd else None,
                reason=exc.rcvd.reason if exc.rcvd else "",
            )
        except Exception:
            self._log.exception("ws_recv_error")
        finally:
            ping_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ping_task
            self._registry.detach(self)
            self._log.info("ws_session_ended")

    # ------------------------------------------------------------------
    # 心跳
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """每 5s 下行一个业务层 ping 帧（区别于 WS 协议层 keepalive）。"""
        while True:
            await asyncio.sleep(_PING_INTERVAL)
            frame = json.dumps({"type": "ping", "ts": round(time.time(), 3)})
            try:
                await self._ws.send(frame)
                self._log.debug("ws_ping_sent")
            except ConnectionClosed:
                break
            except Exception:
                self._log.exception("ws_ping_error")
                break

    # ------------------------------------------------------------------
    # 帧分发
    # ------------------------------------------------------------------

    async def _handle_raw(self, raw: str | bytes) -> None:
        """解析原始帧，分发到对应 handler。"""
        if isinstance(raw, bytes):
            raw = raw.decode()
        try:
            frame: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._log.warning("ws_invalid_json", error=str(exc), raw=raw[:200])
            return

        frame_type = frame.get("type")
        if not isinstance(frame_type, str):
            self._log.warning("ws_missing_type", frame=frame)
            return

        await self._dispatch(frame_type, frame)

    async def _dispatch(self, frame_type: str, frame: dict[str, Any]) -> None:
        """帧类型分发。M1.1 阶段全是 stub，只 log。

        帧类型来自设计文档 §9.3 上行 schema：
        start_game / command / recipe / compile_strategy /
        view_move / view_follow / view_zoom /
        confirm_ambiguous / revoke / save_recipe / release_unit
        """
        # M1.1 stub：全部 log，不执行业务逻辑
        # M1.2+ 替换对应分支
        self._log.info("ws_frame_received", frame_type=frame_type, frame=frame)

        if frame_type == "start_game":
            self._log.info("ws_stub_start_game")
        elif frame_type == "command":
            self._log.info("ws_stub_command", text=frame.get("text"))
        elif frame_type in {
            "view_move",
            "view_follow",
            "view_zoom",
        }:
            self._log.info("ws_stub_view", frame_type=frame_type)
        elif frame_type in {
            "recipe",
            "compile_strategy",
            "confirm_ambiguous",
            "revoke",
            "save_recipe",
            "release_unit",
        }:
            self._log.info("ws_stub_other", frame_type=frame_type)
        else:
            self._log.warning("ws_unknown_frame_type", frame_type=frame_type)


# ------------------------------------------------------------------
# 握手钩子（供 BotService 传给 websockets.serve）
# ------------------------------------------------------------------


def make_ws_handler(
    registry: RoomRegistry,
) -> Any:
    """返回 websockets handler coroutine。

    handler 被 websockets.serve 调用，每个新连接进来都会调一次。
    """

    async def handler(ws: ServerConnection) -> None:
        """WS 连接 handler：验 token → 顶旧 → 运行 WsConnection.run()。"""
        log = logger.bind(remote=str(ws.remote_address))

        # 从请求 path 的 query string 取 room token
        request = ws.request
        if request is None:
            log.warning("ws_handshake_no_request")
            await ws.close(1011, "Internal error")
            return
        parsed = urlparse(request.path)
        params = parse_qs(parsed.query)
        token_list = params.get("room", [])
        if not token_list:
            log.warning("ws_handshake_missing_token")
            await ws.close(1008, "Missing room token")
            return

        token = token_list[0]
        if not registry.verify(token):
            log.warning("ws_handshake_invalid_token")
            await ws.close(1008, "Invalid room token")
            return

        conn = WsConnection(ws, registry)
        evicted = registry.attach(conn)
        if evicted is not None:
            log.info("ws_evicting_old_connection")
            await evicted.close("新连接顶旧")

        await conn.run()

    return handler


# 让 mypy 能验证 WsConnection 真的满足 Connection Protocol
_: Connection = WsConnection.__new__(WsConnection)
