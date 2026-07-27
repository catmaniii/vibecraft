"""RoomService：Room + MatchOrchestrator 的聚合根（阶段 0 多人联网）。

评审修订（2026-06-12 Opus）M3 版本：
- 无 legacy_gp 双轨：GameProcess 唯一 owner = MatchOrchestrator
- solo (1 bot slot) 也走 orchestrator（mp_role="" → 原单人路径）
- game_process_for(pid) 只查 orchestrator._procs

"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import structlog

from vibecraft.server.chat import ChatHub
from vibecraft.server.match import MatchOrchestrator
from vibecraft.server.room import Room, RoomError  # noqa: F401 — re-export for WS layer

if TYPE_CHECKING:
    from vibecraft.server.tokens import RoomRegistry

logger = structlog.get_logger(__name__)


class RoomService:
    """Room + MatchOrchestrator 的聚合根；WsConnection 通过它解析"我的 GameProcess"。

    设计约定（M3）：
    - 所有 GameProcess 都由 orchestrator 管；不存在独立的 legacy_gp。
    - solo 局（1 bot + 电脑）走 orchestrator 单人路径（mp_role=""）。
    - game_process_for(pid) = orchestrator.process_for(pid)（None 表示对局未开始）。

    不变量（S7）：
    - A 的指令绝不进 B 的 down_q（路由在 ws.py 层保证，RoomService 这里不直接发指令）
    - 每 GameProcess 的 raw_events 恰一个消费者（orchestrator 内的 monitor task）
    """

    def __init__(
        self,
        registry: RoomRegistry,
        orchestrator: MatchOrchestrator | None = None,
        map_name: str = "DaybreakLE",
        # 默认 2 个位（2026-06-12 用户 #8）：引擎多 agent 仅 1v1、单人+多电脑 FFA 未实测，
        # 现阶段 >2 个位纯属 UI 噪音；以后验证了 FFA 再放开。
        max_slots: int = 2,
        default_realtime: bool = True,
    ) -> None:
        self.room = Room(map_name=map_name, max_slots=max_slots, realtime=default_realtime)
        self.orchestrator: MatchOrchestrator = orchestrator or MatchOrchestrator()
        self._registry = registry
        # 全局文字聊天 hub（内存历史 + 自增 id）；经 registry.broadcast 推所有在线连接
        self.chat = ChatHub()
        # _last_room_state：用于 _on_player_frame 检测状态变化触发广播
        self._last_room_state: str = self.room.state
        self._log = logger.bind(component="room_service")

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #

    def game_process_for(self, player_id: str) -> Any | None:
        """返回该玩家绑定的 GameProcess，对局未开始或找不到则 None（M3：只查 orchestrator）。"""
        return self.orchestrator.process_for(player_id)

    # ------------------------------------------------------------------ #
    # 开局 / 结局
    # ------------------------------------------------------------------ #

    async def start_match(self, requester: str) -> None:
        """开局：校验 + 状态→starting → 广播 starting → 启动 orchestrator。

        S8：任一 gp.start() 抛错 → orchestrator 已回滚 room(mark_ended) + 广播 lobby 状态。
        抛出的异常由调用方（ws.py lobby_start 分支）转成 room_error 帧。
        """
        # 校验 + state → starting（抛 RoomError 由调用方转 room_error 帧）
        self.room.start(requester)
        self._last_room_state = self.room.state
        # 先广播 starting（PWA 显示进度）
        await self.broadcast_room_state()
        self._log.info("match_starting", requester=requester, match_id=self.room.match_id)
        try:
            await self.orchestrator.start_match(
                self.room,
                on_player_frame=self._on_player_frame,
                on_match_ended=self._on_match_ended,
            )
        except Exception:
            # S8：orchestrator.start_match 内已调 room.mark_ended()，这里只广播
            self._last_room_state = self.room.state
            await self.broadcast_room_state()
            raise

    async def stop_match(self) -> None:
        """玩家按"结束本局"或测试清场：停所有进程 → room 回 lobby → 广播。"""
        await self.orchestrator.stop_match()
        self.room.mark_ended()
        self._last_room_state = self.room.state
        self._log.info("match_stopped_by_service")
        await self.broadcast_room_state()

    # ------------------------------------------------------------------ #
    # monitor 回调（由 MatchOrchestrator._monitor_player 调用）
    # ------------------------------------------------------------------ #

    async def _on_player_frame(self, player_id: str, raw: dict[str, Any]) -> None:
        """per-player monitor 回调：整体 try/except 兜底，绝不向上抛（M2）。

        职责：
        1. 检测 room.state 变化 → 广播（in_game 时自动触发；ended 收场也覆盖到）
        2. 把 raw dict 转成下行帧，经 registry 推给对应玩家（没连着就丢弃）

        注：room.mark_in_game() 由 _monitor_player 在调本回调之前完成，
        所以回调执行时 room.state 已经是最新值，可直接对比 _last_room_state。
        """
        try:
            # 1. 检测状态变化 → 广播（所有状态变化都会触发：starting→in_game, in_game→lobby 等）
            current = self.room.state
            if current != self._last_room_state:
                self._last_room_state = current
                await self.broadcast_room_state()

            # 2. 把帧推给对应玩家
            # S6：直接用 orchestrator.process_for 查 gp（每次回调都可能有新的 gp 状态）
            gp = self.orchestrator.process_for(player_id)
            if gp is None:
                return

            # 延迟导入避免循环依赖（ws.py 导入 room_service，room_service 导入 ws）
            from vibecraft.server.ws import build_downstream_frames

            frames = build_downstream_frames(raw, gp)
            conn = self._registry.connection_of(player_id)
            if conn is None:
                # 玩家暂时断线（手机刷新等），丢弃帧；重连后会收到最新状态
                return
            for frame_str in frames:
                await conn.send_text(frame_str)

        except Exception:
            # 兜底：任何异常都不能往上抛，否则会杀死 monitor（M2 约束）
            self._log.exception("on_player_frame_error", player_id=player_id)

    async def _on_match_ended(self) -> None:
        """对局全部结束（任一方 ended/crashed）→ 广播 room 回 lobby 状态。

        此时 room.mark_ended() 已由 _monitor_player 调过，state 已是 "lobby"。
        广播完毕后：对每个 bot slot，若玩家当前无活跃连接（离线），
        调度延迟踢出任务（宽限 10s，期间重连则取消踢出）。
        """
        self._last_room_state = self.room.state
        self._log.info("match_ended_broadcast")
        await self.broadcast_room_state()

        # 延迟导入避免循环依赖（与 build_downstream_frames 同样的模式）
        from vibecraft.server.ws import _PENDING_LEAVE_TASKS, _delayed_lobby_leave

        for slot in self.room.bot_slots():
            pid = slot.player_id
            if self._registry.connection_of(pid) is None:
                # 离线玩家：宽限期后踢 slot（期间重连则保留）
                _task = asyncio.create_task(
                    _delayed_lobby_leave(self, self._registry, pid),
                    name=f"match-ended-leave-{pid}",
                )
                _PENDING_LEAVE_TASKS.add(_task)
                _task.add_done_callback(_PENDING_LEAVE_TASKS.discard)
                self._log.info("match_ended_offline_player_leave_scheduled", player_id=pid)

    # ------------------------------------------------------------------ #
    # 广播工具
    # ------------------------------------------------------------------ #

    async def broadcast_room_state(self) -> None:
        """向所有活跃连接广播当前 room_state 帧。"""
        await self._registry.broadcast(json.dumps(self.room.to_frame()))
