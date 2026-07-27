"""WS endpoint：手机 ↔ bot service 的双向 WebSocket 通道。

设计文档 §9.2 / §9.3：
- URL 格式：ws://host:port/ws?room=<token>&player=<昵称>&pid=<设备id>
- 握手时从 query 取 token 验证 + player/pid 解析玩家身份
- WsConnection 持 room_service + player_id（M3：不再持 game_process）
- 帧收发循环：收 JSON → 解析 type → 分发 handler
- 5s 心跳：下行 {"type": "ping", "ts": <game_time>}
- 连接断开：lobby 态调 room.leave + 广播；in_game 态只 detach（重连续命）
- 所有连接事件用 structlog 结构化 log

多人联网 Task 6/7（2026-06-12）：
- 身份握手：URL query player=昵称&pid=设备id；验 token 后 room.join + registry.attach
- lobby 帧：lobby_set_race / lobby_set_team / lobby_ready / lobby_add_computer /
           lobby_remove_slot / lobby_leave / lobby_set_realtime / lobby_start
- 路由：所有 game_process 访问改 self._gp()（= room_service.game_process_for(pid)）
- 帧分发：从 _dispatch_upstream 重构为模块级纯函数 build_downstream_frames
         （供 RoomService._on_player_frame 调用；不再有 per-connection status pump）

评审修订（M2/M3/S4）已全部叠加。
"""

from __future__ import annotations

import array
import asyncio
import base64
import contextlib
import json
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import structlog
from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

from vibecraft.i18n import t
from vibecraft.server.game_process import (
    GameConfig,
    GameStatus,
    _apply_raw_dict,
    _build_game_status_frame_dict,
)
from vibecraft.server.tokens import Connection, RoomRegistry

if TYPE_CHECKING:
    from vibecraft.server.room_service import RoomService

logger = structlog.get_logger(__name__)

# 业务层心跳间隔（秒）。websockets 协议层 keepalive 在 BotService 里设
# ping_interval=None 关掉，心跳全由业务层 ping 帧负责。
_PING_INTERVAL: float = 5.0

# lobby 态断线 → 延迟 N 秒才真正 leave（同 pid 在此期间重连则不动 slot）。
# 2026-06-12 用户实测：客户端重连风暴/手机网络抖动 + 立即 leave = 全房间名单狂闪。
_LOBBY_LEAVE_GRACE_S: float = 10.0

# 在飞的延迟 leave task 引用（RUF006：fire-and-forget task 会被 GC，必须持引用）
_PENDING_LEAVE_TASKS: set[asyncio.Task[None]] = set()


async def _delayed_lobby_leave(room_service: Any, registry: Any, player_id: str) -> None:
    """断线宽限：等 _LOBBY_LEAVE_GRACE_S 后，若该玩家仍没有活跃连接且房间还在
    lobby 态，才释放 slot 并广播。重连（registry 里出现新连接）则什么都不做。"""
    await asyncio.sleep(_LOBBY_LEAVE_GRACE_S)
    try:
        if registry.connection_of(player_id) is not None:
            return  # 已重连，slot 保留
        room = room_service.room
        if room.state != "lobby":
            return  # 期间开局了，slot 保留（对局语义）
        if room.slot_of(player_id) is None:
            return  # 已被踢/已主动退，无事可做
        room.leave(player_id)
        await room_service.broadcast_room_state()
        logger.info("ws_lobby_leave_after_grace", player_id=player_id)
    except Exception:
        logger.exception("delayed_lobby_leave_error", player_id=player_id)


# ------------------------------------------------------------------
# 模块级纯函数：raw dict → 下行帧 JSON 列表
# ------------------------------------------------------------------


def build_downstream_frames(raw: dict[str, Any], gp: Any) -> list[str]:
    """把 GameProcess.raw_events() 的单条 raw dict 转成下行 JSON 帧列表。

    从原 WsConnection._dispatch_upstream 重构而来的纯函数：
    - 只转换，不发送（调用方负责发送）
    - game_status 帧需要 gp._sc2_state / _bot_state / _my_race
    - 从不修改 gp 的状态（只读取）

    供 RoomService._on_player_frame 调用（M2：帧分发归 monitor 管，不在 per-connection pump）。
    """
    kind = raw.get("kind")
    frames: list[str] = []

    if kind == "echo":
        # 指令被 LLM 解析后的 echo → command_echo 帧
        frame = json.dumps(
            {
                "type": "command_echo",
                "user_text": raw.get("user_text", ""),
                "interpretation": raw.get("interpretation", ""),
                "ts": round(time.time(), 3),
            }
        )
        frames.append(frame)

    elif kind == "snapshot":
        # P0-5：snapshot 帧嵌套在 raw["frame"]，直接转发
        snap_frame = raw.get("frame")
        if snap_frame is not None:
            frames.append(json.dumps(snap_frame))

    elif kind == "event":
        # P1-5：event 帧嵌套在 raw["frame"]，直接转发
        ev_frame = raw.get("frame")
        if ev_frame is not None:
            frames.append(json.dumps(ev_frame))

    elif kind == "minimap":
        # minimap 帧嵌套在 raw["frame"]，直接转发
        mm_frame = raw.get("frame")
        if mm_frame is not None:
            frames.append(json.dumps(mm_frame))

    else:
        # game_status 消息（含 sc2/bot 字段，或无 kind 的默认路径）
        sc2, bot, detail = _apply_raw_dict(
            raw,
            getattr(gp, "_sc2_state", "idle"),
            getattr(gp, "_bot_state", "idle"),
        )
        status = GameStatus(
            sc2=sc2,
            bot=bot,
            detail=detail,
            my_race=getattr(gp, "_my_race", "Protoss"),
        )
        frames.append(json.dumps(_build_game_status_frame_dict(status)))

    return frames


def _build_game_status_frame(status: GameStatus) -> str:
    """把 GameStatus 封包成下行 game_status JSON 帧（设计文档 §9.3）。"""
    return json.dumps(_build_game_status_frame_dict(status))


class WsConnection:
    """一个活跃 WS 连接，实现 Connection Protocol（tokens.py）。

    多人联网 Task 6/7：
    - 持 room_service + player_id（不再持 game_process）
    - _gp() = room_service.game_process_for(player_id)（无对局时 None）
    - 帧下发全部由 RoomService._on_player_frame → monitor 负责（M2）
    """

    def __init__(
        self,
        ws: ServerConnection,
        registry: RoomRegistry,
        room_service: RoomService | None = None,
        player_id: str = "default",
        player_name: str = "",  # 握手时从 query player= 取，供 lobby_join 帧使用
        locale: str = "zh",  # 握手时从 query locale= 取（zh/en）；穿透到本方 GameConfig.locale
        default_realtime: bool = True,
        default_my_race: str = "Protoss",
        webrtc_manager: Any = None,
        asr_engine: Any = None,
        # ---- 向后兼容参数（旧测试直接注入 GameProcess；M3 已改用 room_service）----
        game_process: Any = None,
    ) -> None:
        self._ws = ws
        self._registry = registry
        # M3：不再持 game_process；通过 room_service 查。
        # game_process 参数只在旧测试兼容时使用：包装成最小 stub RoomService，
        # 使 _gp() 和 _handle_start_game 的行为与旧接口一致（不建 monitor task）。
        if game_process is not None and room_service is None:
            from vibecraft.server.match import MatchOrchestrator as _MO
            from vibecraft.server.room_service import RoomService as _RS

            _gp_val = game_process

            class _StubOrch:
                """测试兼容 stub orchestrator：直接调 gp.start()，不建 monitor task。"""

                @property
                def processes(self) -> dict[str, Any]:
                    return {"default": _gp_val}

                def process_for(self, _pid: str) -> Any:
                    return _gp_val

                async def start_match(self, room: Any, **_kw: Any) -> dict[str, Any]:
                    # 用真实 build_plan 算 config，调提供的 gp.start 不建后台 task
                    plans = _MO(screen_size=(1920, 1080)).build_plan(room)
                    for plan in plans:
                        _gp_val.start(plan.config)
                    return {p.player_id: _gp_val for p in plans}

                async def stop_match(self) -> None:
                    pass

            room_service = _RS(
                registry,
                orchestrator=_StubOrch(),  # type: ignore[arg-type]
                default_realtime=default_realtime,
            )
            # _compat_gp 供 _gp() 快速返回（无需查 orchestrator.process_for）
            self._compat_gp = _gp_val

        elif room_service is None:
            # 懒初始化：handler 会在握手后注入，这里做兜底
            from vibecraft.server.room_service import RoomService

            room_service = RoomService(registry)

        self._room_service = room_service
        self._player_id = player_id
        # 昵称：握手时从 query player= 存下，lobby_join 帧用此昵称进房（解耦握手与入房）
        self._player_name: str = player_name or player_id
        # 玩家语言（zh/en）：本方开局时写进 GameConfig.locale → 子进程 → IntentParser，决定 interpretation 语言。
        self._locale: str = locale if locale in ("zh", "en") else "zh"
        self._default_realtime = default_realtime
        self._default_my_race = default_my_race
        # 2026-05-24 用户:webrtc signaling 走 WS frame(单端口反代场景也能用)
        self._webrtc_manager = webrtc_manager
        # 2026-06-09 语音输入：进程级 ASR 引擎单例（外部注入），per-连接一个活跃 session
        self._asr_engine: Any = asr_engine
        self._asr_session: Any = None  # AsrSession | None
        # i18n：英文玩家握手即后台预热英文 ASR 模型（SenseVoice 首次下载 ~1GB 较慢，
        # 提前 warm 避免第一句英文语音 finalize 卡在加载上）。fire-and-forget，不阻塞握手。
        if self._locale == "en" and asr_engine is not None:
            with contextlib.suppress(RuntimeError):
                asyncio.ensure_future(self._warmup_en_bg())  # noqa: RUF006
        # 聊天限频：本连接最近发送时刻（~2 条/秒；前端不可信，限频必须在 server）
        self._chat_times: list[float] = []
        # #527 诊断：每段录音的音频幅度统计。真机排查"语音静默失效"用 —— 若一段的
        # peak≈0 说明客户端 track 死了在发静音（坐实 track 失活），有声但 final 空则
        # 是 ASR 侧问题。每段开始(create_session)重置，end/cancel 时落一条 segment_stats。
        self._audio_seg_peak = 0
        self._audio_seg_frames = 0
        self._audio_seg_samples = 0
        self._log = logger.bind(
            remote=str(ws.remote_address),
            player_id=player_id,
        )

    # ------------------------------------------------------------------
    # 内部工具：获取当前 GameProcess（可能 None）
    # ------------------------------------------------------------------

    def _gp(self) -> Any | None:
        """返回当前玩家绑定的 GameProcess，对局未开始则 None。

        M3：只查 orchestrator，无 legacy_gp。
        _compat_gp：旧测试通过 game_process= 注入时，直接返回该值。
        """
        if hasattr(self, "_compat_gp"):
            return self._compat_gp
        return self._room_service.game_process_for(self._player_id)

    @property
    def _game_process(self) -> Any | None:
        """向后兼容属性：旧测试访问 conn._game_process。M3 请改用 _gp()。"""
        return self._gp()

    async def _dispatch_upstream(self, raw: dict[str, Any]) -> None:
        """向后兼容旧测试（M2+已被纯函数 build_downstream_frames 取代）。

        旧测试直接调 conn._dispatch_upstream(raw) 并断言 ws.send 收到下行帧。
        此方法把 build_downstream_frames 结果 send 出去，保留旧行为语义。
        """
        gp = self._gp()
        for frame_str in build_downstream_frames(raw, gp):
            with contextlib.suppress(Exception):
                await self._ws.send(frame_str)

    # ------------------------------------------------------------------
    # Connection Protocol
    # ------------------------------------------------------------------

    async def close(self, reason: str) -> None:
        """关闭连接（被顶旧时由 RoomRegistry 调用方负责调用）。"""
        self._log.info("ws_connection_evicted", reason=reason)
        with contextlib.suppress(Exception):
            await self._ws.close()

    async def send_text(self, frame: str) -> None:
        """向客户端发送一条文本帧（Connection Protocol 要求，供 broadcast 调用）。

        吞 ConnectionClosed：对端已断时安静失败，不向上传播。
        """
        with contextlib.suppress(Exception):
            await self._ws.send(frame)

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """接管握手后的全部生命周期：心跳 + 收帧循环 + 断开清理。

        M2：不再有 per-connection status pump（帧由 monitor 经 _on_player_frame 推送）。
        断线处理：
        - lobby 态：room.leave(pid) + 广播（空出 slot）
        - in_game/starting 态：只 detach（重连续命，对局不中断）
        """
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
            # 断连时清理 ASR session（丢弃未完成的语音段）
            if self._asr_session is not None:
                with contextlib.suppress(Exception):
                    self._asr_session.cancel()
                self._asr_session = None
            # registry.detach：不管哪种状态都要清注册
            self._registry.detach(self)
            # lobby 态断线 → **延迟 10s** 才释放 slot（2026-06-12 用户实测反馈 #1）：
            # 立即 leave 会让"客户端重连风暴/手机网络抖动"表现为 slot 反复清空-恢复,
            # 全房间看到名单狂闪。延迟期内同 pid 重连 → registry 里有新连接 → 不 leave。
            # in_game/starting 态 slot 永远保留（手机掉线不中断对局）。
            room = self._room_service.room
            if room.state == "lobby":
                # RUF006：留引用防 task 被 GC；done 后自清
                _task = asyncio.create_task(
                    _delayed_lobby_leave(self._room_service, self._registry, self._player_id),
                    name=f"delayed-leave-{self._player_id}",
                )
                _PENDING_LEAVE_TASKS.add(_task)
                _task.add_done_callback(_PENDING_LEAVE_TASKS.discard)
                self._log.info("ws_lobby_leave_scheduled", player_id=self._player_id)
            else:
                # in_game/starting：slot 保留，等重连（手机掉线不中断对局）
                self._log.info("ws_in_game_disconnect_slot_kept", player_id=self._player_id)
            self._log.info("ws_session_ended")

    # ------------------------------------------------------------------
    # 心跳
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """每 5s 下行一个业务层 ping 帧（区别于 WS 协议层 keepalive）。

        2026-06-13 用户：对局大厅四个状态灯要定期更新。game_status 原本只随
        GameProcess raw_events 推（对局外一帧都没有 → 灯停在旧态），心跳顺带
        推一帧**当前** game_status：有 gp 用其实时状态，无对局 = idle/idle。
        """
        while True:
            await asyncio.sleep(_PING_INTERVAL)
            frame = json.dumps({"type": "ping", "ts": round(time.time(), 3)})
            gp = self._gp()
            status = GameStatus(
                sc2=getattr(gp, "_sc2_state", "idle") if gp is not None else "idle",
                bot=getattr(gp, "_bot_state", "idle") if gp is not None else "idle",
                detail="",
                my_race=(getattr(gp, "_my_race", "Protoss") if gp is not None else "Protoss"),
            )
            status_frame = json.dumps(_build_game_status_frame_dict(status))
            try:
                await self._ws.send(frame)
                await self._ws.send(status_frame)
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
        + 多人 lobby_* 帧（Task 6）
        """
        if frame_type == "start_game":
            # M3 薄 shim：自动 join→加电脑→ready→start_match（旧 PWA 兼容）
            await self._handle_start_game(frame)
        elif frame_type == "command":
            await self._handle_command(frame)
        elif frame_type == "view_move":
            await self._handle_view_move(frame)
        elif frame_type in {"confirm_recommendation", "dismiss_recommendation"}:
            await self._handle_recommendation_response(frame_type, frame)
        elif frame_type in {"confirm_force_strategy", "cancel_force_strategy"}:
            await self._handle_force_strategy_response(frame_type, frame)
        elif frame_type == "revoke_directive":
            await self._handle_revoke_directive(frame)
        elif frame_type == "tactical_action":
            await self._handle_tactical_action(frame)
        elif frame_type == "macro_action":
            await self._handle_macro_action(frame)
        elif frame_type == "strategy_action":
            await self._handle_strategy_action(frame)
        elif frame_type == "end_game":
            await self._handle_end_game(frame)
        elif frame_type == "surrender":
            await self._handle_surrender(frame)
        elif frame_type in {"confirm_clarification", "cancel_clarification"}:
            await self._handle_clarification_response(frame_type, frame)
        elif frame_type == "webrtc_offer":
            await self._handle_webrtc_offer(frame)
        elif frame_type == "audio_chunk":
            await self._handle_audio_chunk(frame)
        elif frame_type == "audio_end":
            await self._handle_audio_end(frame)
        elif frame_type == "audio_cancel":
            await self._handle_audio_cancel(frame)
        # ---- 多人 lobby 帧（Task 6）----
        elif frame_type == "lobby_set_race":
            await self._handle_lobby_op(
                lambda r: r.set_race(self._player_id, frame.get("race", ""))
            )
        elif frame_type == "lobby_set_team":
            await self._handle_lobby_op(lambda r: r.set_team(self._player_id, frame.get("team", 1)))
        elif frame_type == "lobby_ready":
            await self._handle_lobby_op(
                lambda r: r.set_ready(self._player_id, bool(frame.get("ready", True)))
            )
        elif frame_type == "lobby_add_computer":
            # index 可选：指定空位加电脑（2026-06-12 用户反馈 #3）；缺省第一个空位
            _idx_raw = frame.get("index")
            await self._handle_lobby_op(
                lambda r: r.add_computer(
                    self._player_id,
                    race=str(frame.get("race", "Random")),
                    difficulty=str(frame.get("difficulty", "VeryHard")),
                    index=int(_idx_raw) if _idx_raw is not None else None,
                )
            )
        elif frame_type == "lobby_take_slot":
            # 玩家自由换到空位（2026-06-12 用户反馈 #4）
            await self._handle_lobby_op(
                lambda r: r.take_slot(self._player_id, int(frame.get("index", -1)))
            )
        elif frame_type == "lobby_remove_slot":
            await self._handle_lobby_op(
                lambda r: r.remove_slot(self._player_id, int(frame.get("index", -1)))
            )
        elif frame_type == "lobby_join":
            # 连接与入房解耦：握手只 attach，玩家显式发 lobby_join 才真正进房
            await self._handle_lobby_join(frame)
        elif frame_type == "lobby_leave":
            await self._handle_lobby_leave()
        elif frame_type == "lobby_set_realtime":
            await self._handle_lobby_op(
                lambda r: r.set_realtime(self._player_id, bool(frame.get("realtime", True)))
            )
        elif frame_type == "lobby_start":
            await self._handle_lobby_start(frame)
        elif frame_type == "chat_send":
            await self._handle_chat_send(frame)
        elif frame_type == "chat_history_req":
            await self._handle_chat_history_req()
        elif frame_type in {
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
    # Lobby 操作帮助器
    # ------------------------------------------------------------------

    async def _handle_lobby_op(self, op: Any) -> None:
        """模板：调 Room 方法 → 成功广播 room_state；RoomError → 回 room_error 帧。"""
        from vibecraft.server.room import RoomError

        try:
            op(self._room_service.room)
            await self._room_service.broadcast_room_state()
            self._log.info("ws_lobby_op_ok")
        except RoomError as e:
            # 拒绝必打日志（2026-06-12 排查教训：静默拒绝是诊断盲区）；日志留 zh，回客户端按 locale。
            self._log.info("ws_lobby_op_rejected", reason=str(e))
            await self._send_room_error(e.localized(self._locale))
        except Exception as e:
            self._log.exception("ws_lobby_op_error")
            await self._send_room_error(t("room.err.op_failed", self._locale, detail=str(e)))

    async def _handle_chat_send(self, frame: dict[str, Any]) -> None:
        """玩家发文字聊天 → server 加自增 id + 时间戳 → 广播给所有在线连接（room-global）。

        限频在 server（前端不可信）；文字 strip + 截断 500；昵称取握手 ?player=（可伪造，
        故附 player_id 给客户端做防伪/标本人抓手）。广播经 registry.broadcast（单坏连接被
        其内部 suppress 兜底，不中断整轮）。
        """
        text = str(frame.get("text", "")).strip()[:500]
        if not text:
            return
        now = time.monotonic()
        self._chat_times[:] = [t for t in self._chat_times if now - t < 1.0]
        if len(self._chat_times) >= 2:
            self._log.info("ws_chat_rate_limited", player_id=self._player_id)
            return
        self._chat_times.append(now)
        msg = self._room_service.chat.add(name=self._player_name, pid=self._player_id, text=text)
        await self._registry.broadcast(json.dumps(msg, ensure_ascii=False))
        self._log.info("ws_chat", player_id=self._player_id, name=self._player_name, n=len(text))

    async def _handle_chat_history_req(self) -> None:
        """客户端请求聊天历史 → **玩家一律回空**（2026-06-17 用户:后加入的玩家不推送之前的
        聊天记录,只看得到自己进来之后的实时消息)。

        ChatHub 仍照常累积历史(`chat.add`),只是不再回放给玩家连接;**admin 仍能看完整历史**
        —— admin 走独立 HTTP 路径 `GET /api/admin/chat`(http.py `_serve_admin_chat`,SCRAM 鉴权),
        读同一个 ChatHub.history(),不受这里影响。
        """
        out = {"type": "chat_history", "messages": []}
        with contextlib.suppress(Exception):
            await self._ws.send(json.dumps(out, ensure_ascii=False))

    async def _handle_lobby_join(self, frame: dict[str, Any]) -> None:
        """lobby_join：玩家显式请求进入房间（连接与入房解耦的新入口）。

        - lobby 态：占 slot，广播 room_state；房满/对局中新 pid → room_error。
        - 已有 slot（断线重连场景，任意状态）：幂等更新昵称，广播。
        """
        from vibecraft.server.room import RoomError

        try:
            self._room_service.room.join(self._player_id, self._player_name, self._locale)
            await self._room_service.broadcast_room_state()
            self._log.info("ws_lobby_join_ok", player_id=self._player_id)
        except RoomError as e:
            await self._send_room_error(e.localized(self._locale))
            self._log.info("ws_lobby_join_rejected", player_id=self._player_id, reason=str(e))

    async def _handle_lobby_leave(self) -> None:
        """lobby_leave：离开房间 + 广播。"""
        from vibecraft.server.room import RoomError

        try:
            self._room_service.room.leave(self._player_id)
            await self._room_service.broadcast_room_state()
            self._log.info("ws_lobby_leave", player_id=self._player_id)
        except RoomError as e:
            await self._send_room_error(e.localized(self._locale))

    async def _handle_lobby_start(self, frame: dict[str, Any]) -> None:
        """lobby_start（房主触发）→ room_service.start_match → 启动对局。

        RoomError → 回 room_error 帧（如"不是房主"/"玩家未就绪"）。
        其他异常 → 回 room_error("启动失败:...") + log.exception。
        """
        from vibecraft.server.room import RoomError

        self._log.info("ws_lobby_start_received", player_id=self._player_id)
        # M4：帧里显式带了 realtime 则覆盖房间设置（仅房主；非房主会被 set_realtime 拒）
        if "realtime" in frame:
            with contextlib.suppress(RoomError):
                self._room_service.room.set_realtime(self._player_id, bool(frame["realtime"]))
        try:
            await self._room_service.start_match(self._player_id)
            self._log.info("ws_lobby_start_ok", player_id=self._player_id)
        except RoomError as e:
            # 拒绝必打日志（2026-06-12 排查教训：静默拒绝是诊断盲区）
            self._log.info("ws_lobby_start_rejected", player_id=self._player_id, reason=str(e))
            await self._send_room_error(e.localized(self._locale))
        except Exception as e:
            self._log.exception("ws_lobby_start_failed")
            await self._send_room_error(t("room.err.start_failed", self._locale, detail=str(e)))

    async def _send_room_error(self, message: str) -> None:
        """给本连接回 room_error 帧。"""
        with contextlib.suppress(Exception):
            await self._ws.send(json.dumps({"type": "room_error", "message": message}))

    # ------------------------------------------------------------------
    # 推荐 confirm / dismiss
    # ------------------------------------------------------------------

    async def _handle_recommendation_response(self, frame_type: str, frame: dict[str, Any]) -> None:
        gp = self._gp()
        if gp is None or not gp.is_running:
            self._log.debug("ws_recommendation_no_game_running", frame_type=frame_type)
            return
        gp.send_command({"type": frame_type})
        self._log.info("ws_recommendation_sent", frame_type=frame_type)

    async def _handle_force_strategy_response(self, frame_type: str, frame: dict[str, Any]) -> None:
        gp = self._gp()
        if gp is None or not gp.is_running:
            self._log.debug("ws_force_strategy_no_game_running", frame_type=frame_type)
            return
        gp.send_command({"type": frame_type})
        self._log.info("ws_force_strategy_sent", frame_type=frame_type)

    async def _handle_revoke_directive(self, frame: dict[str, Any]) -> None:
        directive_id = frame.get("directive_id")
        if not directive_id:
            self._log.warning("ws_revoke_directive_missing_id", frame=frame)
            return
        gp = self._gp()
        if gp is None or not gp.is_running:
            self._log.debug("ws_revoke_directive_no_game_running", directive_id=directive_id)
            return
        gp.send_command({"type": "revoke_directive", "directive_id": directive_id})
        self._log.info("ws_revoke_directive_sent", directive_id=directive_id)

    # ------------------------------------------------------------------
    # view_move 处理
    # ------------------------------------------------------------------

    async def _handle_view_move(self, frame: dict[str, Any]) -> None:
        pt = frame.get("target_point")
        if (
            not isinstance(pt, list)
            or len(pt) != 2
            or not all(isinstance(v, (int, float)) for v in pt)
        ):
            self._log.warning("ws_view_move_bad_point", frame=frame)
            return
        gp = self._gp()
        if gp is None or not gp.is_running:
            self._log.debug("ws_view_move_no_game_running")
            return
        gp.send_command(
            {
                "type": "view_move",
                "target_point": [float(pt[0]), float(pt[1])],
            }
        )

    # ------------------------------------------------------------------
    # tactical_action 处理
    # ------------------------------------------------------------------

    _VALID_TACTICAL_VERBS: frozenset[str] = frozenset(
        {"attack", "defend", "retreat", "recon", "scout"}
    )

    async def _handle_tactical_action(self, frame: dict[str, Any]) -> None:
        verb = frame.get("verb")
        if not isinstance(verb, str) or verb not in self._VALID_TACTICAL_VERBS:
            self._log.warning("ws_tactical_action_invalid_verb", verb=verb)
            return
        gp = self._gp()
        if gp is None or not gp.is_running:
            self._log.debug("ws_tactical_action_no_game_running", verb=verb)
            return
        mode = frame.get("mode")
        cmd = {"type": "tactical_action", "verb": verb}
        if mode in ("all_in", "probe"):
            cmd["mode"] = mode
        gp.send_command(cmd)
        self._log.info("ws_tactical_action_sent", verb=verb, mode=mode)

    # ------------------------------------------------------------------
    # macro_action 处理
    # ------------------------------------------------------------------

    # 2026-07-27:开矿封顶(1-5/max/clear)随前端入口下架,只保留「多开一个矿」
    _VALID_EXPAND_VALUES: frozenset[object] = frozenset({"one_more"})
    _VALID_WORKER_VALUES: frozenset[str] = frozenset({"stop", "max", "default"})
    _VALID_MINING_VALUES: frozenset[str] = frozenset({"mineral", "gas", "default"})

    async def _handle_macro_action(self, frame: dict[str, Any]) -> None:
        dim = frame.get("dim")
        value = frame.get("value")
        if dim == "expand":
            if value not in self._VALID_EXPAND_VALUES:
                self._log.warning("ws_macro_action_invalid_expand_value", value=value)
                return
        elif dim == "workers":
            if value not in self._VALID_WORKER_VALUES:
                self._log.warning("ws_macro_action_invalid_worker_value", value=value)
                return
        elif dim == "mining":
            if value not in self._VALID_MINING_VALUES:
                self._log.warning("ws_macro_action_invalid_mining_value", value=value)
                return
        elif dim == "upgrade_target":
            # value = {"family": str, "level": int(0-3) | "auto"}
            if not isinstance(value, dict):
                self._log.warning("ws_macro_action_invalid_upgrade_target_value", value=value)
                return
            family = value.get("family")
            level = value.get("level")
            if not isinstance(family, str) or not family:
                self._log.warning("ws_macro_action_upgrade_target_missing_family", value=value)
                return
            if level != "auto" and level not in (0, 1, 2, 3):
                self._log.warning("ws_macro_action_upgrade_target_invalid_level", level=level)
                return
        else:
            self._log.warning("ws_macro_action_invalid_dim", dim=dim)
            return
        gp = self._gp()
        if gp is None or not gp.is_running:
            self._log.debug("ws_macro_action_no_game_running", dim=dim, value=value)
            return
        cmd = {"type": "macro_action", "dim": dim, "value": value}
        gp.send_command(cmd)
        self._log.info("ws_macro_action_sent", dim=dim, value=value)

    # ------------------------------------------------------------------
    # strategy_action 处理
    # ------------------------------------------------------------------

    async def _handle_strategy_action(self, frame: dict[str, Any]) -> None:
        strategy_id = frame.get("strategy_id")
        if not isinstance(strategy_id, str) or not strategy_id.strip():
            self._log.warning("ws_strategy_action_invalid_id", strategy_id=strategy_id)
            return
        gp = self._gp()
        if gp is None or not gp.is_running:
            self._log.debug("ws_strategy_action_no_game_running", strategy_id=strategy_id)
            return
        gp.send_command({"type": "strategy_action", "strategy_id": strategy_id.strip()})
        self._log.info("ws_strategy_action_sent", strategy_id=strategy_id)

    # ------------------------------------------------------------------
    # start_game 处理（M3 薄 shim）
    # ------------------------------------------------------------------

    async def _handle_start_game(self, frame: dict[str, Any]) -> None:
        """start_game 帧 = 薄 shim（M3），兼容旧 PWA 流程。

        流程（旧 PWA 发 start_game 时的自动适配）：
        1. 玩家未在房间则 join（昵称取 my_race 或 "玩家"）
        2. 若房内尚无电脑且真人=1：按帧 config 加一个电脑
        3. 按帧 config 设置种族 / realtime
        4. set_ready(pid, True)
        5. await room_service.start_match(pid)

        对局已在 starting/in_game 时：记 warning 并忽略（不重复开局）。
        """
        from vibecraft.server.room import RoomError

        raw_config: dict[str, Any] = frame.get("config") or {}
        my_race = str(raw_config.get("my_race", self._default_my_race))
        opponent_race = str(raw_config.get("opponent_race", GameConfig.opponent_race))
        opponent_difficulty = str(
            raw_config.get("opponent_difficulty", GameConfig.opponent_difficulty)
        )
        # realtime：帧里显式传了才用帧值，否则沿用房间当前设置
        realtime_explicit: bool | None = (
            bool(raw_config["realtime"]) if "realtime" in raw_config else None
        )

        self._log.info(
            "ws_start_game_shim",
            map_name=raw_config.get("map", GameConfig.map_name),
            opponent_race=opponent_race,
            opponent_difficulty=opponent_difficulty,
        )

        room = self._room_service.room

        # 对局已在进行中时，忽略（不重复开局）
        if room.state in ("starting", "in_game"):
            self._log.warning("ws_start_game_shim_already_active", state=room.state)
            return

        # 1. 未在房间则 join
        if room.slot_of(self._player_id) is None:
            try:
                room.join(self._player_id, my_race, self._locale)  # 昵称用种族名兜底
            except RoomError as e:
                await self._send_room_error(e.localized(self._locale))
                return

        # 2. 设置种族
        with contextlib.suppress(RoomError):  # 未知种族等错误不阻断
            room.set_race(self._player_id, my_race)

        # 3. realtime（帧显式传了才覆盖）
        if realtime_explicit is not None:
            with contextlib.suppress(RoomError):
                room.set_realtime(self._player_id, realtime_explicit)

        # 4. 若只有 1 个真人且无电脑，加电脑
        computers = [s for s in room.slots if s.kind == "computer"]
        humans = room.bot_slots()
        if len(humans) == 1 and not computers:
            try:
                room.add_computer(
                    self._player_id,
                    race=opponent_race,
                    difficulty=opponent_difficulty,
                )
            except RoomError as e:
                await self._send_room_error(e.localized(self._locale))
                return

        # 5. ready
        try:
            room.set_ready(self._player_id, True)
        except RoomError as e:
            await self._send_room_error(e.localized(self._locale))
            return

        # 6. 开局（通过 room_service，广播 + 启动 orchestrator）
        try:
            await self._room_service.start_match(self._player_id)
        except RoomError as e:
            await self._send_room_error(e.localized(self._locale))
        except Exception as e:
            self._log.exception("ws_start_game_shim_failed")
            await self._send_room_error(t("room.err.start_failed", self._locale, detail=str(e)))

    # ------------------------------------------------------------------
    # command 处理
    # ------------------------------------------------------------------

    async def _handle_command(self, frame: dict[str, Any]) -> None:
        """收到 command 帧 → 发到子进程下行队列。

        若 SC2 对局还没启动（_gp() None 或未 running），静默丢弃并 log warning。
        """
        text = frame.get("text", "")
        if not isinstance(text, str) or not text.strip():
            self._log.warning("ws_command_empty_text", frame=frame)
            return

        gp = self._gp()
        if gp is None or not gp.is_running:
            self._log.warning("ws_command_no_game_running", text=text[:80])
            return

        cmd = {
            "type": "command",
            "text": text.strip(),
            "issued_at": round(time.time(), 3),
        }
        gp.send_command(cmd)
        self._log.info("ws_command_sent", text=text[:80])

        # 立即 ack（文字指令"识别中"反馈）
        ack_frame = json.dumps(
            {
                "type": "command_received",
                "text": text.strip(),
                "ts": cmd["issued_at"],
            }
        )
        try:
            await self._ws.send(ack_frame)
        except Exception:
            self._log.warning("ws_command_ack_failed")

    # ------------------------------------------------------------------
    # end_game 处理
    # ------------------------------------------------------------------

    async def _handle_end_game(self, frame: dict[str, Any]) -> None:
        """结束本局 → room_service.stop_match（停所有进程 + 广播回 lobby）。

        M3：end_game 通过 room_service，不再直接调单个 gp.stop()。
        仅房主可触发（roomState 单人场景房主=唯一玩家，不受影响）。
        """
        room = self._room_service.room
        # 仅房主能结束对局
        if self._player_id != room.host_player_id:
            await self._send_room_error(t("room.err.host_only_end", self._locale))
            return
        if room.state not in ("starting", "in_game"):
            self._log.debug("ws_end_game_no_active_match")
            return
        self._log.info("ws_end_game_requested")
        try:
            await self._room_service.stop_match()
            self._log.info("ws_end_game_done")
        except Exception as exc:
            self._log.warning("ws_end_game_failed", error=str(exc))

    async def _handle_surrender(self, frame: dict[str, Any]) -> None:
        """认输帧（任何在局玩家可发）：让本玩家的进程 leave。

        引擎判对方 Victory → orchestrator monitor 检测到进程退出 → 自动回 lobby 广播。
        非对局中 / 无运行进程 → room_error 拒绝。
        """
        room = self._room_service.room
        if room.state != "in_game":
            await self._send_room_error(t("room.err.surrender_in_game_only", self._locale))
            return
        gp = self._gp()
        if gp is None or not gp.is_running:
            await self._send_room_error(t("room.err.no_active_match", self._locale))
            return
        self._log.info("ws_surrender", player_id=self._player_id)
        await gp.stop()

    # ------------------------------------------------------------------
    # WebRTC 处理（S4）
    # ------------------------------------------------------------------

    async def _handle_webrtc_offer(self, frame: dict[str, Any]) -> None:
        """2026-05-24 WebRTC signaling 走 WS frame。

        S4（Task 6 评审）：把 player_id + sc2_pid 传给 handle_offer，
        支持 per-player per-SC2-实例的视频流（M1）。

        sc2_pid 未就绪（gp 存在但 sc2_pid=None）→ 回 error 帧，PWA 2s 后重试。
        T10 合入前旧签名回退：handle_offer 不接受新 kwargs → try/except TypeError，
        回退 handle_offer(sdp, sdp_type)（注：T10 合入后删此回退）。
        """
        if self._webrtc_manager is None:
            self._log.warning("ws_webrtc_offer_no_manager")
            with contextlib.suppress(Exception):
                await self._ws.send(
                    json.dumps({"type": "webrtc_answer", "error": "WebRTC not enabled on server"})
                )
            return

        sdp = frame.get("sdp", "")
        sdp_type = frame.get("sdp_type", frame.get("type_", "offer"))
        if not sdp:
            self._log.warning("ws_webrtc_offer_missing_sdp")
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"type": "webrtc_answer", "error": "missing sdp"}))
            return

        # S4：sc2_pid 未就绪时提前报错（gp 存在但 sc2_pid 属性 None）
        gp = self._gp()
        sc2_pid: int | None = getattr(gp, "sc2_pid", None) if gp is not None else None
        if gp is not None and sc2_pid is None:
            # gp 在运行但 SC2 进程 PID 尚未可知（T10 还没推上来）
            with contextlib.suppress(Exception):
                await self._ws.send(
                    json.dumps({"type": "webrtc_answer", "error": "sc2 not ready, retry"})
                )
            return

        self._log.info("ws_webrtc_offer_received", player_id=self._player_id)
        try:
            # S4/M1：per-player PC + 按本玩家 SC2 实例 PID 抓屏（T10 已合入）
            answer_sdp, answer_type = await self._webrtc_manager.handle_offer(
                sdp, sdp_type, player_id=self._player_id, sc2_pid=sc2_pid
            )
        except Exception as exc:
            self._log.exception("ws_webrtc_offer_handle_failed")
            with contextlib.suppress(Exception):
                await self._ws.send(
                    json.dumps({"type": "webrtc_answer", "error": f"handle_offer failed: {exc!r}"})
                )
            return

        with contextlib.suppress(Exception):
            await self._ws.send(
                json.dumps({"type": "webrtc_answer", "sdp": answer_sdp, "sdp_type": answer_type})
            )
            self._log.info("ws_webrtc_answer_sent")

    # ------------------------------------------------------------------
    # audio_chunk / audio_end / audio_cancel 处理（2026-06-09 语音输入）
    # ------------------------------------------------------------------

    async def _warmup_en_bg(self) -> None:
        """后台预热英文 ASR 模型（握手见 locale=en 时触发）。失败只记日志，不影响连接。"""
        try:
            ok = await self._asr_engine.warmup_en()
            self._log.info("ws_asr_warmup_en", ok=ok)
        except Exception:
            self._log.warning("ws_asr_warmup_en_failed", exc_info=True)

    async def _handle_audio_chunk(self, frame: dict[str, Any]) -> None:
        # 按玩家语言判定 ASR 是否可用（en 用 SenseVoice，zh 用 paraformer，各自独立加载）。
        if self._asr_engine is None or not self._asr_engine.available_for(self._locale):
            self._log.debug("ws_audio_chunk_asr_unavailable", locale=self._locale)
            return

        if self._asr_session is None:
            self._asr_session = await self._asr_engine.create_session(self._locale)
            if self._asr_session is None:
                self._log.warning("ws_audio_chunk_create_session_failed", locale=self._locale)
                # en 模型加载失败时给玩家一条提示（别静默丢音频，让用户对着麦说半天没反馈）。
                with contextlib.suppress(Exception):
                    await self._ws.send(
                        json.dumps(
                            {
                                "type": "asr_unavailable",
                                "locale": self._locale,
                                "message": t("voice.asrUnavailable", self._locale),
                            }
                        )
                    )
                return
            # 新一段：重置幅度统计（#527 诊断）
            self._audio_seg_peak = 0
            self._audio_seg_frames = 0
            self._audio_seg_samples = 0

        pcm_b64 = frame.get("pcm", "")
        if not pcm_b64:
            self._log.debug("ws_audio_chunk_empty_pcm")
            return

        try:
            pcm_bytes = base64.b64decode(pcm_b64)
        except Exception:
            self._log.warning("ws_audio_chunk_decode_error")
            return

        # #527 诊断：累计本段音频幅度（PCM16 → 峰值绝对值），判客户端是否在发静音。
        try:
            samples = array.array("h")
            samples.frombytes(pcm_bytes)
            if samples:
                self._audio_seg_peak = max(self._audio_seg_peak, max(samples), -min(samples))
                self._audio_seg_samples += len(samples)
            self._audio_seg_frames += 1
        except Exception:
            pass  # 诊断统计永不影响主路径

        partial = await self._asr_session.feed(pcm_bytes)
        if partial:
            try:
                await self._ws.send(
                    json.dumps({"type": "transcript", "text": partial, "is_final": False})
                )
                self._log.debug("ws_transcript_partial_sent", text_len=len(partial))
            except Exception:
                self._log.warning("ws_transcript_partial_send_failed")

    async def _handle_audio_end(self, frame: dict[str, Any]) -> None:
        if self._asr_session is None:
            self._log.debug("ws_audio_end_no_session")
            return

        session = self._asr_session
        self._asr_session = None

        final = await session.finalize()
        # #527 诊断：本段音频幅度统计。peak 归一化 <0.003(≈静音) + final 空 → 客户端
        # track 死了在发静音（坐实 track 失活）；peak 正常但 final 空 → ASR 侧问题。
        peak_norm = round(self._audio_seg_peak / 32768.0, 4)
        self._log.info(
            "ws_audio_segment_stats",
            peak=self._audio_seg_peak,
            peak_norm=peak_norm,
            silent=peak_norm < 0.003,
            frames=self._audio_seg_frames,
            samples=self._audio_seg_samples,
            final_len=len(final),
        )
        try:
            await self._ws.send(json.dumps({"type": "transcript", "text": final, "is_final": True}))
            self._log.info("ws_transcript_final_sent", text_len=len(final))
        except Exception:
            self._log.warning("ws_transcript_final_send_failed")

    async def _handle_audio_cancel(self, frame: dict[str, Any]) -> None:
        if self._asr_session is None:
            self._log.debug("ws_audio_cancel_no_session")
            return

        self._asr_session.cancel()
        self._asr_session = None
        peak_norm = round(self._audio_seg_peak / 32768.0, 4)
        self._log.info(
            "ws_audio_cancel_done",
            peak_norm=peak_norm,
            silent=peak_norm < 0.003,
            frames=self._audio_seg_frames,
        )

    async def _handle_clarification_response(self, frame_type: str, frame: dict[str, Any]) -> None:
        gp = self._gp()
        if gp is None or not gp.is_running:
            self._log.debug("ws_clarification_no_game_running", frame_type=frame_type)
            return
        cmd: dict[str, Any] = {"type": frame_type}
        if frame_type == "confirm_clarification":
            cmd["option_index"] = int(frame.get("option_index", 0))
        gp.send_command(cmd)
        self._log.info(
            "ws_clarification_sent",
            frame_type=frame_type,
            **({"option_index": cmd["option_index"]} if "option_index" in cmd else {}),
        )


# ------------------------------------------------------------------
# 握手钩子（供 BotService 传给 websockets.serve）
# ------------------------------------------------------------------


def make_ws_handler(
    registry: RoomRegistry,
    room_service: RoomService | None = None,
    default_realtime: bool = True,
    default_my_race: str = "Protoss",
    webrtc_manager: Any = None,
    asr_engine: Any = None,
) -> Any:
    """返回 websockets handler coroutine。

    handler 被 websockets.serve 调用，每个新连接进来都会调一次。

    room_service：Task 7 BotService 传入；None 时每条连接自建（单测 fallback）。
    default_realtime：start_game 帧未显式传 realtime 时的 SC2 默认运行模式。
    default_my_race：我方种族，来自 ServiceConfig.default_my_race（CLI --my-race）。
    asr_engine：进程级 AsrEngine 单例（可选）；None 时语音帧静默忽略。
    """
    _room_service = room_service

    async def handler(ws: ServerConnection) -> None:
        """WS 连接 handler：验 token → 解析身份 → join room → attach → run()。"""
        log = logger.bind(remote=str(ws.remote_address))

        request = ws.request
        if request is None:
            log.warning("ws_handshake_no_request")
            await ws.close(1011, "Internal error")
            return

        parsed = urlparse(request.path)
        params = parse_qs(parsed.query)

        # 验 token
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

        # 解析玩家身份：player=昵称（默认"玩家"）, pid=设备id（默认"default"）
        player_name = (params.get("player", ["玩家"])[0]) or "玩家"
        player_id = (params.get("pid", ["default"])[0]) or "default"
        # 玩家语言 locale=zh/en（默认 zh）：白名单校验，避免脏值；穿透到本方 GameConfig.locale。
        _loc = (params.get("locale", ["zh"])[0]) or "zh"
        player_locale = _loc if _loc in ("zh", "en") else "zh"

        # 获取或创建 room_service
        rs = _room_service
        if rs is None:
            from vibecraft.server.room_service import RoomService

            rs = RoomService(registry, default_realtime=default_realtime)

        # 构造连接对象（握手不再自动入房，解耦"连接"与"进房间"）
        # 昵称存入 WsConnection._player_name，等玩家显式发 lobby_join 再用
        conn = WsConnection(
            ws,
            registry,
            room_service=rs,
            player_id=player_id,
            player_name=player_name,
            locale=player_locale,
            default_realtime=default_realtime,
            default_my_race=default_my_race,
            webrtc_manager=webrtc_manager,
            asr_engine=asr_engine,
        )

        # attach（顶旧连接）
        evicted = registry.attach(conn, player_id=player_id)
        if evicted is not None:
            log.info("ws_evicting_old_connection", player_id=player_id)
            await evicted.close("新连接顶旧")

        # 立即给本连接推一次 room_state（预览：玩家尚未入房，slot 名单不含自己）
        with contextlib.suppress(Exception):
            await conn.send_text(json.dumps(rs.room.to_frame()))

        log.info("ws_handshake_ok", player_id=player_id, player_name=player_name)
        await conn.run()

    return handler


# 让 mypy 能验证 WsConnection 真的满足 Connection Protocol
_: Connection = WsConnection.__new__(WsConnection)
