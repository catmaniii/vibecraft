"""WS endpoint：手机 ↔ bot service 的双向 WebSocket 通道。

设计文档 §9.2 / §9.3：
- URL 格式：ws://host:port/ws?room=<token>
- 握手时从 query 取 token，用 RoomRegistry.verify() 验证，失败拒连（WS close 1008）
- attach() 接入，若返回被顶掉的旧连接则 close()（重连顶旧）
- 帧收发循环：收 JSON → 解析 type → 分发 handler
- 5s 心跳：下行 {"type": "ping", "ts": <game_time>}
- 连接断开调 RoomRegistry.detach()
- 所有连接事件用 structlog 结构化 log

M1.2 变更：
- start_game handler 从 stub 换成真的 → GameProcess.start() + status_events() 推上行
- WsConnection 增加 game_process 参数
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

from voicecraft.server.game_process import (
    GameConfig,
    GameProcess,
    GameStatus,
    _build_game_status_frame_dict,
)
from voicecraft.server.tokens import Connection, RoomRegistry

logger = structlog.get_logger(__name__)

# 业务层心跳间隔（秒）。websockets 协议层 keepalive 在 BotService 里设
# ping_interval=None 关掉，心跳全由业务层 ping 帧负责。
_PING_INTERVAL: float = 5.0


def _build_game_status_frame(status: GameStatus) -> str:
    """把 GameStatus 封包成下行 game_status JSON 帧（设计文档 §9.3）。"""
    return json.dumps(_build_game_status_frame_dict(status))


class WsConnection:
    """一个活跃 WS 连接，实现 Connection Protocol（tokens.py）。

    M1.2：start_game handler 真实实现；新增 game_process 参数。
    M1.4-M1.6 阶段：替换 _dispatch 里的其他 handler。
    """

    def __init__(
        self,
        ws: ServerConnection,
        registry: RoomRegistry,
        game_process: GameProcess | None = None,
    ) -> None:
        self._ws = ws
        self._registry = registry
        self._game_process = game_process or GameProcess()
        self._status_pump_task: asyncio.Task[None] | None = None
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
        """接管握手后的全部生命周期：心跳 + 收帧循环 + 断开清理。

        新连接落地时若 game_process 已经在跑（例如 PWA 刷新 / 重连），
        立即推一次当前 game_status 并启动 status pump —— 否则手机端会一直
        停在 sc2=idle/bot=idle 默认状态，因为状态推送是事件驱动的（变化才推）。
        """
        self._log.info("ws_connected")
        ping_task = asyncio.create_task(self._heartbeat_loop())

        # 重连兜底：如果游戏已经在跑，主动推一次当前状态 + 启动 pump
        if self._game_process.is_running:
            await self._send_game_status(self._game_process.status)
            self._status_pump_task = asyncio.create_task(
                self._status_pump_loop(),
                name="status-pump-resume",
            )
            self._log.info(
                "ws_resume_pushed_current_status",
                sc2=self._game_process.status.sc2,
                bot=self._game_process.status.bot,
            )

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
            # 停 status pump（如果 start_game 启动了）
            if self._status_pump_task is not None:
                self._status_pump_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._status_pump_task
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
        """帧类型分发。

        帧类型来自设计文档 §9.3 上行 schema：
        start_game / command / recipe / compile_strategy /
        view_move / view_follow / view_zoom /
        confirm_ambiguous / revoke / save_recipe / release_unit
        """
        # view_move 拖拽时高频（10Hz），降到 debug 避免刷屏；其它帧保持 info
        if frame_type == "view_move":
            self._log.debug("ws_frame_received", frame_type=frame_type)
        else:
            self._log.info("ws_frame_received", frame_type=frame_type, frame=frame)

        if frame_type == "start_game":
            await self._handle_start_game(frame)
        elif frame_type == "command":
            # Gap 2：stub → 真实转发到下行队列
            await self._handle_command(frame)
        elif frame_type == "view_move":
            # minimap 拖拽 → 切 SC2 大屏视野
            await self._handle_view_move(frame)
        elif frame_type in {"confirm_recommendation", "dismiss_recommendation"}:
            # 玩家点 [确认] / [忽略] bot 推荐
            await self._handle_recommendation_response(frame_type, frame)
        elif frame_type in {"confirm_force_strategy", "cancel_force_strategy"}:
            # 玩家点 [硬转] / [取消] (voice 切剧本但时机已过 → 拦下来等确认)
            await self._handle_force_strategy_response(frame_type, frame)
        elif frame_type in {
            "view_follow",
            "view_zoom",
        }:
            # M3+ 实现
            self._log.info("ws_stub_view", frame_type=frame_type)
        elif frame_type in {
            "recipe",
            "compile_strategy",
            "confirm_ambiguous",
            "revoke",
            "save_recipe",
            "release_unit",
        }:
            # 后续里程碑实现
            self._log.info("ws_stub_other", frame_type=frame_type)
        else:
            self._log.warning("ws_unknown_frame_type", frame_type=frame_type)

    # ------------------------------------------------------------------
    # 推荐 confirm / dismiss
    # ------------------------------------------------------------------

    async def _handle_recommendation_response(
        self, frame_type: str, frame: dict[str, Any]
    ) -> None:
        """玩家在 PWA 点 [确认] / [忽略] bot 推荐 → 转发到子进程。

        Director.confirm_recommendation / dismiss_recommendation 处理。
        """
        if not self._game_process.is_running:
            self._log.debug("ws_recommendation_no_game_running", frame_type=frame_type)
            return
        self._game_process.send_command({"type": frame_type})
        self._log.info("ws_recommendation_sent", frame_type=frame_type)

    async def _handle_force_strategy_response(
        self, frame_type: str, frame: dict[str, Any]
    ) -> None:
        """玩家在 PWA 点 [硬转] / [取消硬转] → 转发到子进程。

        Director.confirm_force_strategy / cancel_force_strategy 处理。
        """
        if not self._game_process.is_running:
            self._log.debug("ws_force_strategy_no_game_running", frame_type=frame_type)
            return
        self._game_process.send_command({"type": frame_type})
        self._log.info("ws_force_strategy_sent", frame_type=frame_type)

    # ------------------------------------------------------------------
    # view_move 处理（minimap 拖拽切视野）
    # ------------------------------------------------------------------

    async def _handle_view_move(self, frame: dict[str, Any]) -> None:
        """收到 view_move 帧 → 校验 → 经 down_q 发到子进程。

        若 SC2 对局未在 playing 状态，静默丢弃（避免无效 move_camera 调用）。
        校验：target_point 必须是 [number, number] 列表。
        """
        pt = frame.get("target_point")
        if (
            not isinstance(pt, list)
            or len(pt) != 2
            or not all(isinstance(v, (int, float)) for v in pt)
        ):
            self._log.warning("ws_view_move_bad_point", frame=frame)
            return
        if not self._game_process.is_running:
            self._log.debug("ws_view_move_no_game_running")
            return
        self._game_process.send_command(
            {
                "type": "view_move",
                "target_point": [float(pt[0]), float(pt[1])],
            }
        )
        # debug 级别：拖拽时高频，info 会刷屏
        self._log.debug("ws_view_move_sent", point=pt)

    # ------------------------------------------------------------------
    # start_game 处理（M1.2）
    # ------------------------------------------------------------------

    async def _handle_start_game(self, frame: dict[str, Any]) -> None:
        """收到 start_game 帧 → 解析 config → GameProcess.start() → 开 status pump。

        设计文档 §9.3：config 可省略，缺省用默认值。
        """
        raw_config: dict[str, Any] = frame.get("config") or {}
        config = GameConfig(
            map_name=str(raw_config.get("map", GameConfig.map_name)),
            opponent_race=str(raw_config.get("opponent_race", GameConfig.opponent_race)),
            opponent_difficulty=str(
                raw_config.get("opponent_difficulty", GameConfig.opponent_difficulty)
            ),
            realtime=bool(raw_config.get("realtime", GameConfig.realtime)),
        )

        self._log.info(
            "ws_start_game",
            map_name=config.map_name,
            opponent_race=config.opponent_race,
            opponent_difficulty=config.opponent_difficulty,
            realtime=config.realtime,
        )

        # 若已有 status pump 在跑（上一局还没退干净），先取消
        if self._status_pump_task is not None and not self._status_pump_task.done():
            self._status_pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._status_pump_task

        self._game_process.start(config)

        # 立即推 launching 状态给手机
        launching_status = self._game_process.status
        await self._send_game_status(launching_status)

        # 启动 status pump task：持续把上行队列的消息转发给手机
        self._status_pump_task = asyncio.create_task(
            self._status_pump_loop(),
            name="status-pump",
        )

    # ------------------------------------------------------------------
    # command 处理（M1.6）
    # ------------------------------------------------------------------

    async def _handle_command(self, frame: dict[str, Any]) -> None:
        """收到 command 帧 → 发到子进程下行队列。

        若 SC2 对局还没启动（game_process 没有活跃子进程），
        静默丢弃并 log warning（不抛异常，保持 WS 连接活跃）。
        """
        text = frame.get("text", "")
        if not isinstance(text, str) or not text.strip():
            self._log.warning("ws_command_empty_text", frame=frame)
            return

        if not self._game_process.is_running:
            self._log.warning("ws_command_no_game_running", text=text[:80])
            return

        cmd = {
            "type": "command",
            "text": text.strip(),
            "issued_at": round(time.time(), 3),
        }
        self._game_process.send_command(cmd)
        self._log.info("ws_command_sent", text=text[:80])

    # ------------------------------------------------------------------
    # status pump
    # ------------------------------------------------------------------

    async def _status_pump_loop(self) -> None:
        """持续把 GameProcess.status_events() 转发成下行帧。

        上行队列消息有两种：
        - game_status 消息（sc2/bot 字段）→ game_status 帧
        - echo 消息（kind="echo"）→ command_echo 帧给手机
        """
        try:
            async for raw in self._game_process.raw_events():
                await self._dispatch_upstream(raw)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._log.exception("status_pump_error")

    async def _dispatch_upstream(self, raw: dict[str, Any]) -> None:
        """把上行队列的单条消息转发为对应下行帧。

        kind 分支（显式，不落到 else，避免污染 _sc2_state/_bot_state）：
        - "echo"     → command_echo 帧
        - "snapshot" → snapshot 帧（直接转发，子进程已组好）
        - "event"    → event 帧（直接转发，子进程已组好）
        - 无 kind    → game_status 帧（含 sc2/bot 字段）
        """
        kind = raw.get("kind")
        if kind == "echo":
            # 基础 echo：告诉手机指令已被解析
            frame = json.dumps(
                {
                    "type": "command_echo",
                    "user_text": raw.get("user_text", ""),
                    "interpretation": raw.get("interpretation", ""),
                    "ts": round(time.time(), 3),
                }
            )
            try:
                await self._ws.send(frame)
                self._log.info(
                    "ws_echo_sent",
                    interpretation=str(raw.get("interpretation", ""))[:80],
                )
            except Exception:
                self._log.warning("ws_echo_send_failed")
        elif kind == "snapshot":
            # P0-5：snapshot 帧嵌套在 raw["frame"]，子进程已组好，直接转发
            snap_frame = raw["frame"]
            try:
                await self._ws.send(json.dumps(snap_frame))
                self._log.debug("ws_snapshot_sent", ts=snap_frame.get("ts"))
            except Exception:
                self._log.warning("ws_snapshot_send_failed")
        elif kind == "event":
            # P1-5：event 帧嵌套在 raw["frame"]（event 帧自身带 kind 字段），直接转发
            ev_frame = raw["frame"]
            try:
                await self._ws.send(json.dumps(ev_frame))
                self._log.debug("ws_event_sent", event_kind=ev_frame.get("kind"))
            except Exception:
                self._log.warning("ws_event_send_failed")
        elif kind == "minimap":
            # minimap 帧嵌套在 raw["frame"]，子进程已组好，直接转发
            mm_frame = raw["frame"]
            try:
                await self._ws.send(json.dumps(mm_frame))
                self._log.debug("ws_minimap_sent", ts=mm_frame.get("ts"))
            except Exception:
                self._log.warning("ws_minimap_send_failed")
        else:
            # game_status 消息处理（sc2/bot 字段）
            from voicecraft.server.game_process import _apply_raw_dict

            sc2, bot, detail = _apply_raw_dict(
                raw,
                self._game_process._sc2_state,
                self._game_process._bot_state,
            )
            from voicecraft.server.game_process import GameStatus

            status = GameStatus(sc2=sc2, bot=bot, detail=detail)
            await self._send_game_status(status)

    async def _send_game_status(self, status: GameStatus) -> None:
        """封包 game_status 帧并推给手机。连接已断时静默忽略。"""
        frame = _build_game_status_frame(status)
        try:
            await self._ws.send(frame)
            self._log.debug(
                "ws_game_status_sent",
                sc2=status.sc2,
                bot=status.bot,
                detail=status.detail,
            )
        except ConnectionClosed:
            self._log.info("ws_game_status_dropped_connection_closed")
        except Exception:
            self._log.exception("ws_game_status_send_error")


# ------------------------------------------------------------------
# 握手钩子（供 BotService 传给 websockets.serve）
# ------------------------------------------------------------------


def make_ws_handler(
    registry: RoomRegistry,
    game_process: GameProcess | None = None,
) -> Any:
    """返回 websockets handler coroutine。

    handler 被 websockets.serve 调用，每个新连接进来都会调一次。

    game_process：可注入，用于测试；None 时每条连接自建一个（服务端默认行为应传入共享实例）。
    M1.2 阶段 BotService 传入 service 级共享 GameProcess（一个 service 实例 = 一局）。
    """
    # 若外部没注入，handler 每次新建一个（MVP 一 token 一连接，一局一 GameProcess）
    _gp = game_process

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

        conn = WsConnection(ws, registry, game_process=_gp)
        evicted = registry.attach(conn)
        if evicted is not None:
            log.info("ws_evicting_old_connection")
            await evicted.close("新连接顶旧")

        await conn.run()

    return handler


# 让 mypy 能验证 WsConnection 真的满足 Connection Protocol
_: Connection = WsConnection.__new__(WsConnection)
