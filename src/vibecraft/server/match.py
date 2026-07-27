"""MatchOrchestrator：房间配置 → SC2 启动计划 → 多 GameProcess 生命周期。

评审修订（2026-06-12 Opus）已全部叠加：
- M2：start_match 为每个 (player_id, gp) 创建 connection-无关的 asyncio monitor task；
      每 GameProcess 恰一个消费者（不变量）。
- M3：solo(1 bot slot)也走本 orchestrator（mp_role="" → 原单人路径）；无 legacy_gp 概念。
- M4：realtime 来自 room.realtime，不写死 True。
- S1：build_plan 对 >2 真人直接 raise（Room.start 已拦，这里是第二道防线）。
- S2/#586：屏分辨率 DPI-aware 检测（模块级 _detect_screen_size，每局重测感知改分辨率），失败 fallback (1920,1080)；
      __init__ 支持注入固定值（单测用）。
- S6：monitor 直接持有 gp 引用，不在循环内重新解析。
- S8：start_match 任一 gp.start() 抛错 → 立即 stop 已起进程 + room.mark_ended() + re-raise。
端口：必须用 new_portconfig_json（不用 contiguous_ports）——见 sc2_multiplayer 的 docstring。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from vibecraft.server.game_process import GameConfig, GameProcess
from vibecraft.server.room import Room, RoomError, Slot

logger = structlog.get_logger(__name__)


def _detect_screen_size() -> tuple[int, int]:
    """DPI-aware 检测主屏物理分辨率 (宽, 高)（Win32）。失败 fallback (1920, 1080)。

    先启用 DPI 感知（PROCESS_PER_MONITOR_DPI_AWARE=2），再用 GetSystemMetrics
    (SM_CXSCREEN=0 / SM_CYSCREEN=1) 拿物理像素。取整屏（非 workarea）——SC2 窗口
    横向平铺依据整屏分割（任务栏在底部不影响横向）。

    **关键（#586）：每次 build_plan 重新调此函数**，好让用户中途改桌面分辨率能被感知。
    否则 server 启动时 detect 一次缓存的旧分辨率，会让新开的窗口太大/互相重叠。
    """
    import sys

    if sys.platform != "win32":
        return (1920, 1080)
    try:
        import ctypes

        # 启用 DPI 感知：返回物理像素（否则 high-DPI 屏被 cap 到 scaled 值）
        try:
            # PROCESS_PER_MONITOR_DPI_AWARE = 2（Win 8.1+）
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            with contextlib.suppress(Exception):
                ctypes.windll.user32.SetProcessDPIAware()
        w = ctypes.windll.user32.GetSystemMetrics(0)  # SM_CXSCREEN
        h = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
        return (int(w) if w > 0 else 1920, int(h) if h > 0 else 1080)
    except Exception:
        return (1920, 1080)


# 星际窗口长宽比：**4:3**（用户 2026-07-04 强指定，不是 16:9）。窗口按此比例缩放，不拉伸变形。
_SC2_WINDOW_ASPECT: float = 4 / 3


def _tile_windows(
    screen_w: int, screen_h: int, n: int, aspect: float = _SC2_WINDOW_ASPECT
) -> list[tuple[int, int, int, int]]:
    """N 个 SC2 窗口横向平铺，各自保持 `aspect` 长宽比，**不重叠、不超屏**。

    返回 `[(x, y, w, h), ...]`（物理像素）。每个窗口分到宽 `screen_w // n` 的 slot；
    在 slot 内按 aspect 缩到最大（宽受 slot 限、高受 screen_h 限），水平居中于 slot、
    顶部对齐（y=0，避开底部任务栏）。→ 桌面分辨率变了自适应缩放，绝不重叠、不变形。
    """
    n = max(1, n)
    slot_w = max(1, screen_w // n)
    # 保持 aspect：宽不超 slot、高不超屏
    w = min(slot_w, int(screen_h * aspect))
    w = max(320, w)  # 下限防极端小屏算出病态尺寸
    h = int(w / aspect)
    x_off = max(0, (slot_w - w) // 2)  # slot 内水平居中
    return [(i * slot_w + x_off, 0, w, h) for i in range(n)]


@dataclass
class PlayerPlan:
    """单个玩家的启动计划：player_id 对应一个 GameConfig。"""

    player_id: str
    config: GameConfig


class MatchOrchestrator:
    """房间配置 → 多 GameProcess 编排 + 生命周期管理。

    职责：
    1. build_plan：Room → list[PlayerPlan]（纯函数，无副作用）
    2. start_match：spawn 各进程 + 创建 per-player monitor task
    3. stop_match：cancel 其他 monitor → stop 所有进程

    不变量（来自设计评审 M2/S6/S7）：
    - 每个 GameProcess 的 raw_events 恰一个消费者（该玩家的 monitor task）
    - A 的指令绝不进 B 的 down_q（路由在 ws.py / room_service.py 层保证）
    """

    def __init__(
        self,
        game_process_factory: Callable[[], Any] = GameProcess,
        screen_size: tuple[int, int] | None = None,
    ) -> None:
        self._factory = game_process_factory
        # #586：None → **每次 build_plan** 重新 DPI-aware detect（感知用户中途改分辨率，
        # 不再 server 启动时缓存一次）；单测注入固定 (w, h) 避免依赖 OS API。
        self._screen_size_override: tuple[int, int] | None = screen_size
        self._procs: dict[str, Any] = {}
        self._monitors: list[asyncio.Task[None]] = []
        # _stopping 防重入：stop_match 执行中，其他 monitor 不再触发第二次 stop
        self._stopping: bool = False
        self._log = logger.bind(component="match_orchestrator")

    # ------------------------------------------------------------------ #
    # 计划生成（纯函数，单测主战场）
    # ------------------------------------------------------------------ #

    def build_plan(self, room: Room) -> list[PlayerPlan]:
        """Room 快照 → 各玩家启动计划（不改 room 状态）。

        S1（第二道防线）：Room.start 已拦截 3+ 真人，这里再拦一次以防直接调用。
        M4：realtime 来自 room.realtime，不写死。
        端口：new_portconfig_json（散点端口，不用 contiguous_ports）。
        窗口：横向均分主屏宽，首个 bot 抢焦点（声音）。
        """
        bots: list[Slot] = room.bot_slots()

        # S1：第二道防线（Room.start 已拦，这里防直接调 build_plan 绕过）
        if len(bots) > 2:
            raise RoomError(
                "3+ 真人玩家暂未支持(spike 仅实测 2 真人)", key="room.err.tooManyHumans"
            )

        computers = [
            {"race": s.race, "difficulty": s.difficulty} for s in room.slots if s.kind == "computer"
        ]
        match_id = room.match_id or f"match_{time.strftime('%Y%m%d_%H%M%S')}"

        # 整局参战方 roster（写进每个 player 的 game_start telemetry → admin 对局记录显示全部
        # 参战方：真人名+种族 / 电脑种族+难度。admin_games 按 match_id 去重，一局一条）。
        match_roster_json = json.dumps(
            [{"name": b.name, "race": b.race, "kind": "human"} for b in bots]
            + [
                {"race": c["race"], "difficulty": c["difficulty"], "kind": "computer"}
                for c in computers
            ],
            ensure_ascii=False,
        )

        if len(bots) == 1:
            # ---- 单人路径：原 run_multiple_games（电脑作 opponent），零变化 ----
            # mp_role="" → 子进程走原来的 run_multiple_games 分支
            comp = computers[0] if computers else {"race": "Random", "difficulty": "VeryHard"}
            if len(computers) > 1:
                # 多电脑 + 单玩家是未来增强，今天只用第一个
                self._log.warning("solo_path_extra_computers_ignored", count=len(computers) - 1)
            cfg = GameConfig(
                map_name=room.map_name,
                my_race=bots[0].race,
                opponent_race=comp["race"],
                opponent_difficulty=comp["difficulty"],
                realtime=room.realtime,  # M4
                game_id=f"{match_id}_p0",
                focus_window=True,
                player_name=bots[0].name,  # admin 对局记录 + telemetry game_start
                locale=bots[0].locale,  # 玩家语言 → IntentParser interpretation 语言
                match_roster_json=match_roster_json,
            )
            return [PlayerPlan(bots[0].player_id, cfg)]

        # ---- 多人路径：共享 portconfig + 窗口横向平铺 ----
        # new_portconfig_json：散点 Portconfig() 而非 contiguous_ports()
        # 原因见 sc2_multiplayer.new_portconfig_json 的 docstring（spike 实锤的坑）
        from vibecraft.server.sc2_multiplayer import new_portconfig_json

        pc_json = new_portconfig_json(guests=len(bots) - 1)
        # #586：每局重新 detect 当前分辨率（不缓存）→ 用户中途改分辨率也能自适应。
        # 按 4:3 平铺算显式 (x,y,w,h)：各窗保持 4:3、缩到 fit、不重叠、不超屏。
        screen_w, screen_h = self._screen_size_override or _detect_screen_size()
        tiles = _tile_windows(screen_w, screen_h, len(bots))
        plans: list[PlayerPlan] = []

        for i, s in enumerate(bots):
            win_x, win_y, win_w, win_h = tiles[i]
            cfg = GameConfig(
                map_name=room.map_name,
                my_race=s.race,
                realtime=room.realtime,  # M4：来自 room，不写死 True
                game_id=f"{match_id}_p{s.index}",
                mp_role="host" if i == 0 else "join",
                mp_portconfig_json=pc_json,
                mp_player_name=s.name or f"player{s.index}",
                # host 负责创建房间，需要 guest 名单 + 电脑列表；join 方不需要
                mp_guest_names=[g.name for g in bots[1:]] if i == 0 else [],
                mp_computers=computers if i == 0 else [],
                window_x=win_x,
                window_y=win_y,
                window_width=win_w,
                window_height=win_h,  # 显式 4:3 高度（>0 → 子进程不再自动全高）
                focus_window=(i == 0),  # 只有 host 抢焦点（声音 + 玩家看 host 视角）
                player_name=s.name,  # admin 对局记录 + telemetry game_start
                locale=s.locale,  # 每个玩家用自己的语言 → 各自 bot 的 interpretation 语言
                match_roster_json=match_roster_json,
            )
            plans.append(PlayerPlan(s.player_id, cfg))

        return plans

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    async def start_match(
        self,
        room: Room,
        on_player_frame: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        on_match_ended: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Spawn 各玩家进程，为每个 (player_id, gp) 创建独立 monitor task。

        M2：monitor task 是唯一消费者（raw_events 只在 monitor 里消费）。
        S8：任一 gp.start() 抛错 → 立即 stop 已起进程 + room.mark_ended() + re-raise。

        参数：
            on_player_frame: 每条 raw event 带 player_id 回调（Task 6 连接层接线用）。
            on_match_ended:  对局结束（全局 ended/crashed）时回调（广播用）。

        返回：{player_id → GameProcess} 的快照（start_match 时的状态）。
        """
        plans = self.build_plan(room)
        self._procs = {}
        self._monitors = []
        self._stopping = False  # 新局开始，重置防上一局残留的 _stopping 标志

        # ---- spawn 各进程（S8：任一失败立即清场）----
        started: list[tuple[str, Any]] = []
        try:
            for p in plans:
                gp = self._factory()
                gp.start(p.config)  # 同步调用，可能抛 RuntimeError 等
                started.append((p.player_id, gp))
                self._procs[p.player_id] = gp
                self._log.info(
                    "match_player_started",
                    player_id=p.player_id,
                    role=p.config.mp_role or "solo",
                    game_id=p.config.game_id,
                )
        except Exception:
            # S8：stop 已起的进程，room 回 lobby，re-raise（调用方可以告知玩家）
            for _pid, gp in started:
                try:
                    await gp.stop()
                except Exception as stop_exc:
                    self._log.warning("start_match_cleanup_stop_failed", error=str(stop_exc))
            self._procs = {}
            room.mark_ended()  # starting → lobby（slot 保留，ready 清零）
            raise

        # ---- 为每个 bot slot 创建独立 monitor task ----
        # M2 不变量：每 GameProcess 恰一个消费者（就是这个 monitor）
        for p in plans:
            gp = self._procs[p.player_id]
            # S6：把 gp 引用直接传给 monitor，不在循环内重新解析
            task: asyncio.Task[None] = asyncio.create_task(
                self._monitor_player(p.player_id, gp, room, on_player_frame, on_match_ended),
                name=f"match_monitor_{p.player_id}",
            )
            self._monitors.append(task)

        return dict(self._procs)

    async def _monitor_player(
        self,
        player_id: str,
        gp: Any,
        room: Room,
        on_player_frame: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
        on_match_ended: Callable[[], Awaitable[None]] | None,
    ) -> None:
        """单个玩家的 gp raw_events 消费者（connection-无关的后台 task）。

        S6：直接持有 gp 引用，不在循环内重新解析 game_process_for()。
        不变量：每个 GameProcess 的 raw_events 恰一个消费者（本 task）。

        - sc2="playing" 且 room 在 starting → room.mark_in_game()（首次触发）
        - sc2 in ("ended","crashed") → stop_match + room.mark_ended() + on_match_ended
        - 被 stop_match cancel → asyncio.CancelledError 正常传播，task 退出
        """
        try:
            # S6：直接用传入的 gp 引用，不重新 lookup
            async for raw in gp.raw_events():
                sc2 = raw.get("sc2", "")

                # 首个 playing → 房间进入 in_game（只在 starting 态触发，避免重复）
                if sc2 == "playing" and room.state == "starting":
                    room.mark_in_game()
                    self._log.info("match_in_game_triggered", player_id=player_id)

                # on_player_frame 回调：连接层用来把帧推给对应玩家（Task 6 接线）
                if on_player_frame is not None:
                    await on_player_frame(player_id, raw)

                # 任一进程 ended/crashed → 触发全局收场
                # _stopping 防重入：若 stop_match 已在执行中，跳过（另一 monitor 已处理）
                if sc2 in ("ended", "crashed") and not self._stopping:
                    self._log.info("match_player_done", player_id=player_id, sc2=sc2)
                    # stop_match 会 cancel 其他 monitor（排除自己），本 monitor 靠 return 退出
                    # 为什么不 cancel 自己：self-cancel 会在下一个 await 抛 CancelledError，
                    # 打断后续的 mark_ended / on_match_ended 清场逻辑。
                    await self.stop_match()
                    room.mark_ended()
                    if on_match_ended is not None:
                        await on_match_ended()
                    return  # 明确 return，clean exit（不靠 CancelledError）

        except asyncio.CancelledError:
            # stop_match 发起的正常取消，重新抛出让 asyncio task 正常结束
            raise

    def process_for(self, player_id: str) -> Any | None:
        """查当前 match 中该玩家绑定的 GameProcess；不在或 match 未开始则 None。"""
        return self._procs.get(player_id)

    @property
    def processes(self) -> dict[str, Any]:
        """当前 match 所有 {player_id → gp} 的快照。"""
        return dict(self._procs)

    async def stop_match(self) -> None:
        """停止当前 match：先 cancel 其他 monitor，再 stop 所有进程。

        为什么先 cancel monitor 再 stop 进程：
        stop 进程（gp.stop()）会让 gp.raw_events() 迭代器结束，
        monitor 看到 ended/crashed 会再次触发 stop_match（死循环/重入）。
        先 cancel 其他 monitor 切断这条递归触发链；_stopping 标志提供额外重入保护。

        为什么排除当前 task（不 self-cancel）：
        若 stop_match 从 monitor 内部调用（某进程 ended），self-cancel 会在
        下一个 await 抛 CancelledError，打断 on_match_ended 等清场逻辑。
        排除自己，让调用方 monitor 在 stop_match 返回后自然 return 退出。

        为什么 await gather cancelled tasks：
        防止 "Task was destroyed but it is pending!" ResourceWarning
        （filterwarnings=error 下会变测试失败）。
        """
        if self._stopping:
            return
        self._stopping = True

        # cancel 其他 monitor（排除正在调用本函数的 monitor task）
        current = asyncio.current_task()
        tasks_to_cancel = [t for t in self._monitors if t is not current]
        self._monitors.clear()

        for task in tasks_to_cancel:
            task.cancel()

        # 等待被 cancel 的 task 真正退出（防 "Task destroyed but pending" 警告）
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        # stop 所有进程
        for pid, gp in list(self._procs.items()):
            try:
                await gp.stop()
            except Exception as exc:
                self._log.warning("match_player_stop_failed", player_id=pid, error=str(exc))
        self._procs = {}
        self._log.info("match_stopped")
