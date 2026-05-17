"""SC2 子进程生命周期管理（M1.2）。

设计预研 `docs/plans/2026-05-14-m1.2-sc2-lifecycle.md`（4 个架构难点 + spike 结论）。

架构难点解决方案（spike 结论）：
- 难点 1：`run_game()` 阻塞 → 方案 B：独立 `multiprocessing` spawn 子进程。
  Windows 用 spawn，父进程只传 picklable 的 GameConfig，子进程自己构造 bot。
  spike 确认：GameConfig 全是基本类型，可 pickle；子进程 import sharpy + 构造 bot
  在自己的进程空间完成，不跨进程传 bot 对象。
- 难点 2：阶段检测 → bot 回调（on_start / on_step / on_end）往上行队列 put 状态事件。
- 难点 3：崩溃捕获 → try/except 包 run_game()；父进程轮询 exitcode 兜底。
- 难点 4：双向通信 → 两个 multiprocessing.Queue（上行 / 下行）；asyncio 侧用
  loop.run_in_executor 桥接阻塞 Queue.get()。

ADR：见 `docs/adr/0002-game-process-multiprocessing-spawn.md`。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing
import multiprocessing.queues
import queue
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast

import structlog

logger = structlog.get_logger(__name__)

# -----------------------------------------------------------------------
# 进程间消息类型
# -----------------------------------------------------------------------

# 子进程 → 父进程（上行）的消息 dict 格式：
#   {"sc2": <Sc2State>, "bot": <BotState>, "detail": <str>}
# 其中 sc2 / bot 取下列字面量字符串（对应 §9.3 game_status 下行帧字段）。

Sc2State = str  # "idle" | "launching" | "in_game" | "playing" | "ended" | "crashed"
BotState = str  # "idle" | "running" | "error"

# -----------------------------------------------------------------------
# 数据类
# -----------------------------------------------------------------------


@dataclass
class GameConfig:
    """拉起一局游戏所需的配置。

    必须 picklable —— 跨 spawn 子进程边界传递。
    全部使用标准 Python 基本类型。
    """

    map_name: str = "DaybreakLE"
    """地图文件名（去掉 .SC2Map），传给 sc2.maps.get()。

    默认 DaybreakLE —— 用户环境 `<SC2PATH>/Maps/` 下实际就位的地图（M0c 用的同一张）。
    M2+ 可做成 PWA 可选 / 配置项。"""

    opponent_race: str = "Random"
    """内置 AI 种族：Protoss / Terran / Zerg / Random（默认随机）。"""

    opponent_difficulty: str = "VeryHard"
    """内置 AI 难度（sc2.data.Difficulty 全 10 档）：
    VeryEasy / Easy / Medium / MediumHard / Hard / Harder / VeryHard /
    CheatVision（全图视野）/ CheatMoney（白送资源）/ CheatInsane（视野+资源+速度全开）。
    """

    realtime: bool = True
    """是否以实时（1x）速度跑。玩家要看画面，默认 True。"""

    llm_controlled_probes: int = 0
    """预留：开局置入 LLM 控制 role 的探机数（M1.5 用，M1.2 暂 0）。"""

    # SC2 客户端窗口设置(windowed mode)。
    # window_y=0 贴顶,window_x=0 贴左。
    # window_height=0 时子进程入口自动取 workarea 高度(屏幕减任务栏,不遮)。
    # window_width 默认 1720(用户实测合适的尺寸)。
    # 用户屏 3440×1440 + DPI 150% 缩放,子进程会用 DPI-aware API 拿物理像素值。
    fullscreen: bool = False
    window_x: int = 0
    window_y: int = 0
    window_width: int = 1720
    window_height: int = 0  # 0 = workarea 全高(自动 detect)


@dataclass
class GameStatus:
    """一个状态快照，从上行队列取出后封包成 game_status 帧。"""

    sc2: Sc2State
    bot: BotState
    ts: float = field(default_factory=time.time)
    detail: str = ""


# -----------------------------------------------------------------------
# 子进程入口（顶层函数，multiprocessing spawn 要求 picklable）
# -----------------------------------------------------------------------

# 启动超时（秒）：子进程 spawn 到 on_start 被调用前，最多等这么久。
# M0c 观察：SC2 冷启动到进对局约 5-6s；留出充足余量。
_LAUNCH_TIMEOUT: float = 120.0

# watchdog 间隔（秒）：父进程轮询子进程存活的频率。
_WATCHDOG_INTERVAL: float = 1.0

# 父进程兜底 watchdog 阈值（秒）：子进程仍 alive 但 wall-clock N 秒无任何上行
# 消息 → 判定子进程卡死（子进程内 watchdog 自己挂了 / hang_watchdog 没生效 /
# multiprocessing queue 卡死等），强制 terminate。
#
# 阈值选 120s 而非 30s/60s：sharpy bot 正常每秒推 snapshot/event 多条，
# 30s 无消息 = 子进程 hang_watchdog 已触发的窗口，120s = 给子进程一切自救
# 路径都失败后的硬兜底。launching 阶段 SC2 启动可能 ~60s 无消息（_put 只在
# 初始发 launching + 进 in_game 后才频繁），所以阈值要宽。
_PARENT_WATCHDOG_STALE_S: float = 120.0


def _child_entry(
    config: GameConfig,
    up_q: multiprocessing.Queue,  # type: ignore[type-arg]
    down_q: multiprocessing.Queue,  # type: ignore[type-arg]
    log_level: int,
) -> None:
    """子进程入口：在子进程内构造 bot、调 run_game()，往 up_q 推状态事件。

    父进程只传 picklable 的 GameConfig（基本类型），此函数负责 import ares 并
    构造真 _VibeCraftBot（含 director + IntentParser + GameSession）。

    M1.6 变更：
    - 参数 `_down_q` 改为 `down_q`（激活下行队列，Gap 2）
    - 改用 make_bot_class 造真 bot（Gap 1），同时传 status_callback 和 down_q（Gap 5+2）
    - 子进程内装配 GameSession / StrategyLibrary / LLMProvider / IntentParser（Gap 4）
    """
    # 子进程需要重新配置日志（spawn 后父进程 logging state 不继承）
    logging.basicConfig(level=log_level)
    child_log = logging.getLogger(__name__)

    # window_height = 0 → 自动取 workarea 高度(屏幕减任务栏);window_y = 0 贴顶
    # DPI-aware:用户屏 3440×1440 + 150% 缩放,默认 GetSystemMetrics 返回 scaled (2293×960)
    # SetProcessDpiAwareness(2)= PerMonitor V2,拿到物理像素(3440×1440)
    if config.window_height <= 0:
        detected_h = 1440
        try:
            import ctypes
            import sys

            if sys.platform == "win32":
                # 启用 DPI 感知:返回物理像素(否则在 high-DPI 屏被 cap 到 scaled 值)
                import contextlib

                try:
                    # PROCESS_PER_MONITOR_DPI_AWARE = 2(Win 8.1+)
                    ctypes.windll.shcore.SetProcessDpiAwareness(2)
                except Exception:
                    # fallback: 老 Windows
                    with contextlib.suppress(Exception):
                        ctypes.windll.user32.SetProcessDPIAware()

                # SPI_GETWORKAREA:屏幕减任务栏的可用区域
                class _RECT(ctypes.Structure):
                    _fields_ = [
                        ("left", ctypes.c_long),
                        ("top", ctypes.c_long),
                        ("right", ctypes.c_long),
                        ("bottom", ctypes.c_long),
                    ]

                rect = _RECT()
                if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                    detected_h = int(rect.bottom - rect.top)
        except Exception as exc:
            child_log.warning("workarea_detect_failed, fallback 1440: %s", exc)
        config.window_height = detected_h
        config.window_y = 0
    child_log.info(
        "sc2_window: x=%d y=%d w=%d h=%d (windowed, DPI-aware, workarea height)",
        config.window_x,
        config.window_y,
        config.window_width,
        config.window_height,
    )

    def _put(sc2: Sc2State, bot: BotState, detail: str = "") -> None:
        """向上行队列推一条状态消息。queue 是进程安全的。"""
        try:
            up_q.put_nowait({"sc2": sc2, "bot": bot, "detail": detail})
        except Exception as exc:
            child_log.warning("up_queue_put_failed: %s", exc)

    def _put_echo(text: str, result: str) -> None:
        """向上行队列推一条 echo 消息（基础版 echo，Gap §5 基础 echo）。

        消息格式：{"kind": "echo", "user_text": ..., "interpretation": ...}
        WS 层在收到此消息时把它转发给手机（设计文档 §9.3 echo 帧）。
        """
        try:
            up_q.put_nowait({"kind": "echo", "user_text": text, "interpretation": result})
        except Exception as exc:
            child_log.warning("up_queue_echo_failed: %s", exc)

    def _put_snapshot(d: dict[str, Any]) -> None:
        """向上行队列推一条 snapshot 消息（P0-4）。

        消息格式：{"kind": "snapshot", "frame": <snapshot 帧 dict>}。
        用嵌套 "frame" 而非展开 —— 与 event 保持一致，且不会被帧内字段覆盖外层 kind。
        WS 层 _dispatch_upstream 取 raw["frame"] 转发给手机。
        """
        try:
            up_q.put_nowait({"kind": "snapshot", "frame": d})
        except Exception as exc:
            child_log.warning("up_queue_snapshot_failed: %s", exc)

    def _put_event(d: dict[str, Any]) -> None:
        """向上行队列推一条 event 消息（P1-4）。

        消息格式：{"kind": "event", "frame": <event 帧 dict>}。
        event 帧自身有 "kind" 字段（strategy.set / directive.committed 等），
        必须嵌套在 "frame" 里，否则 `{"kind": "event", **d}` 会被 d 的 kind 覆盖，
        导致 _dispatch_upstream 认不出是 event、误当 game_status 处理。
        WS 层 _dispatch_upstream 取 raw["frame"] 转发给手机。
        """
        try:
            up_q.put_nowait({"kind": "event", "frame": d})
        except Exception as exc:
            child_log.warning("up_queue_event_failed: %s", exc)

    def _put_minimap(d: dict[str, Any]) -> None:
        """向上行队列推一条 minimap 消息（5Hz 下行流）。

        消息格式：{"kind": "minimap", "frame": <minimap 帧 dict>}。
        与 snapshot / event 保持一致的嵌套模式：frame 字段包含完整帧，
        WS 层 _dispatch_upstream 取 raw["frame"] 直接转发给手机。
        """
        try:
            up_q.put_nowait({"kind": "minimap", "frame": d})
        except Exception as exc:
            child_log.warning("up_queue_minimap_failed: %s", exc)

    _put("launching", "idle")

    try:
        from sc2 import maps
        from sc2.data import Difficulty, Race
        from sc2.main import GameMatch, run_multiple_games
        from sc2.player import Bot, Computer
    except ImportError as exc:
        _put("crashed", "error", detail=f"ImportError: {exc}")
        return

    try:
        bot_class = _build_bot_class(
            _put, down_q, _put_echo, _put_snapshot, _put_event, _put_minimap
        )
    except Exception as exc:
        _put("crashed", "error", detail=f"bot_class构造失败: {type(exc).__name__}: {exc}")
        return

    bot_instance = bot_class()

    try:
        sc2_map = maps.get(config.map_name)
    except Exception as exc:
        _put("crashed", "error", detail=f"地图未找到 '{config.map_name}': {exc}")
        return

    try:
        _put("launching", "running")
        # 走 GameMatch + run_multiple_games：只有这条路径才会把 sc2_config
        # 透传给 SC2Process（run_game 的 **kwargs 进的是 _host_game，不认
        # sc2_config）。窗口模式 + 尺寸/位置：靠左撑满高度，右边留给 bot 状态面板。
        run_multiple_games(
            [
                GameMatch(
                    sc2_map,
                    [
                        Bot(Race.Protoss, bot_instance, name="VibeCraft"),
                        Computer(
                            Race[config.opponent_race],
                            Difficulty[config.opponent_difficulty],
                        ),
                    ],
                    realtime=config.realtime,
                    sc2_config=[
                        {
                            "fullscreen": config.fullscreen,
                            "resolution": (config.window_width, config.window_height),
                            "placement": (config.window_x, config.window_y),
                        }
                    ],
                )
            ]
        )
        _put("ended", "idle")
    except Exception as exc:
        _put("crashed", "error", detail=f"run_game失败: {type(exc).__name__}: {exc}")


def _build_bot_class(
    put_status: Any,
    down_q: Any | None = None,
    put_echo: Any | None = None,
    put_snapshot: Any | None = None,
    put_event: Any | None = None,
    put_minimap: Any | None = None,
) -> type:
    """在子进程内构造 bot 类（M1.6：改用真 VibeCraftBot）。

    put_status：子进程内的 _put 闭包（不跨进程边界传递）。
    down_q：下行队列，传给 make_bot_class（Gap 2）。
    put_echo：echo 回调，让 director 结果能推给父进程（基础 echo）。
    put_snapshot：snapshot 推送回调（P0-4）。None 时忽略。
    put_event：event 推送回调（P1-4）。None 时忽略。
    put_minimap：minimap 推送回调（5Hz 下行流）。None 时忽略。

    fallback 逻辑（向后兼容 M0c smoke / 没有 sc2 的环境）：
    - sc2 装了 → 调 make_bot_class 造真 _VibeCraftProtossBot（sharpy KnowledgeBot 子类）
    - sc2 未装 → 退回 _M12Bot stub（仅推状态，不解析指令）
    """
    # 检查 sc2 是否可用（运行时探测，不引入顶层 import）
    import importlib.util

    if importlib.util.find_spec("sc2") is None:
        # sc2 未装：退到最小 python-sc2 Bot stub（M0c smoke 环境）
        from sc2.bot_ai import BotAI as AresBotFallback

        class _M12Bot(AresBotFallback):  # type: ignore[misc]
            """向后兼容 stub：ares 未装时保留 M1.2 行为（仅推状态）。"""

            async def on_start(self) -> None:
                if hasattr(super(), "on_start"):
                    await super().on_start()
                put_status("in_game", "running")
                put_status("playing", "running")

            async def on_step(self, iteration: int) -> None:
                if hasattr(super(), "on_step"):
                    await super().on_step(iteration)

            async def on_end(self, game_result: Any) -> None:
                put_status("ended", "idle")

        return _M12Bot

    # sc2 + sharpy 装了：装配完整 director 栈（Gap 4 + Gap 1 + Gap 5）
    from pathlib import Path

    from vibecraft.bot.director import Director
    from vibecraft.bot.sharpy_adapter import make_bot_class
    from vibecraft.llm.config import LLMConfig
    from vibecraft.llm.parser import IntentParser
    from vibecraft.logging_.session import GameSession, GameSessionConfig
    from vibecraft.strategy.library import StrategyLibrary

    # --- GameSession（日志落盘，logs/<game_id>/）---
    session = GameSession(GameSessionConfig())

    # --- StrategyLibrary（从 strategies/ + aliases/ 加载）---
    # 路径推算：本文件在 src/vibecraft/server/game_process.py
    # 项目根 = 上溯 4 层
    _pkg_dir = Path(__file__).parent  # server/
    _src_vc_dir = _pkg_dir.parent  # vibecraft/
    _src_dir = _src_vc_dir.parent  # src/
    _project_root = _src_dir.parent  # 项目根
    strategies_dir = _project_root / "strategies"
    aliases_path = _project_root / "aliases" / "protoss.yaml"

    strategy_library: StrategyLibrary
    if strategies_dir.exists() and aliases_path.exists():
        strategy_library = StrategyLibrary.from_directories(strategies_dir, aliases_path)
    else:
        # 没有剧本文件时用空 library（单测 / 无策略环境也能跑）
        strategy_library = StrategyLibrary()

    # --- LLM provider（按 config/llm.yaml 的 provider 读对应 API key 环境变量）---
    llm_config_path = _project_root / "config" / "llm.yaml"
    llm_config = LLMConfig.from_yaml_or_defaults(
        llm_config_path if llm_config_path.exists() else None
    )
    provider = llm_config.build_provider()

    # --- IntentParser ---
    parser = IntentParser(provider=provider, library=strategy_library, session=session)

    # --- director_factory（在 on_start 时拿到真实 facade 再构造）---
    def director_factory(facade: Any) -> Director:
        return Director(facade=facade, parser=parser, session=session, library=strategy_library)

    # echo 由 auto_combat.common.run_command_with_echo 在 task done 时推送，
    # 经 echo_callback 参数（=put_echo）回传父进程。
    return make_bot_class(
        director_factory=director_factory,
        strategy_library=strategy_library,
        status_callback=put_status,
        down_q=down_q,
        echo_callback=put_echo,
        snapshot_callback=put_snapshot,
        event_callback=put_event,
        minimap_callback=put_minimap,
    )


# -----------------------------------------------------------------------
# 辅助函数（公开，供 ws.py 和测试复用）
# -----------------------------------------------------------------------


def _apply_raw_dict(
    raw: dict[str, Any],
    current_sc2: Sc2State,
    current_bot: BotState,
) -> tuple[Sc2State, BotState, str]:
    """从上行队列的 raw dict 提取 (sc2, bot, detail)，缺字段 fallback 到当前值。"""
    sc2 = raw.get("sc2", current_sc2)
    bot = raw.get("bot", current_bot)
    detail = raw.get("detail", "")
    return str(sc2), str(bot), str(detail)


def _build_game_status_frame_dict(status: GameStatus) -> dict[str, object]:
    """把 GameStatus 转成 game_status 帧的 dict（不含 JSON 序列化）。"""
    return {
        "type": "game_status",
        "ts": round(status.ts, 3),
        "link": "connected",
        "sc2": status.sc2,
        "bot": status.bot,
        "detail": status.detail,
    }


# -----------------------------------------------------------------------
# 主类
# -----------------------------------------------------------------------


class GameProcess:
    """管一局游戏子进程的生命周期。bot service 持有一个实例。

    用法::

        gp = GameProcess()
        config = GameConfig(map_name="Goldenaura LE", realtime=True)
        gp.start(config)

        async for status in gp.status_events():
            ws.send(game_status_frame(status))

        await gp.stop()

    线程安全性：start / stop 必须从同一个 asyncio event loop 调。
    """

    def __init__(self) -> None:
        self._proc: multiprocessing.Process | None = None
        self._up_q: multiprocessing.Queue[dict[str, str]] | None = None
        self._down_q: multiprocessing.Queue[dict[str, Any]] | None = None
        self._sc2_state: Sc2State = "idle"
        self._bot_state: BotState = "idle"
        self._log = logger.bind(component="game_process")

    @property
    def status(self) -> GameStatus:
        """当前状态快照（同步读取，不阻塞）。"""
        return GameStatus(sc2=self._sc2_state, bot=self._bot_state)

    @property
    def is_running(self) -> bool:
        """子进程是否还活着。"""
        return self._proc is not None and self._proc.is_alive()

    def start(self, config: GameConfig) -> None:
        """Spawn 子进程，开始拉 SC2。

        如果已有进程在跑，先 stop 再 start（防止孤儿进程）。
        """
        if self._proc is not None and self._proc.is_alive():
            self._log.warning("game_process_already_running_force_stop")
            self._terminate_and_join()

        ctx = multiprocessing.get_context("spawn")
        self._up_q = ctx.Queue()
        self._down_q = ctx.Queue()

        self._sc2_state = "launching"
        self._bot_state = "idle"

        # SpawnContext.Process 返回 SpawnProcess，是 multiprocessing.Process 子类；
        # multiprocessing stubs 的类型窄化不够精确，用 cast 告诉 mypy 这里是 Process。
        proc: multiprocessing.Process = cast(
            multiprocessing.Process,
            ctx.Process(
                target=_child_entry,
                args=(config, self._up_q, self._down_q, logging.WARNING),
                daemon=True,  # daemon：父进程退时自动 kill，防孤儿
                name="vibecraft-sc2",
            ),
        )
        proc.start()
        self._proc = proc
        self._log.info(
            "game_process_started",
            pid=proc.pid,
            map_name=config.map_name,
            realtime=config.realtime,
        )

    async def raw_events(self) -> AsyncIterator[dict[str, Any]]:
        """上行流（M1.6）：持续 yield 原始 dict，直到子进程结束或出错。

        上行消息有两种：
        - game_status 类：含 sc2/bot 字段，_dispatch_upstream 转 game_status 帧
        - echo 类：含 kind="echo"，_dispatch_upstream 转 command_echo 帧

        asyncio 侧用 run_in_executor 桥接阻塞 Queue.get()，不阻塞 event loop。

        父进程兜底 watchdog（与子进程内 HangWatchdog 互补）：上行 queue
        `_PARENT_WATCHDOG_STALE_S` 秒无任何消息 + 子进程仍 alive → 子进程
        卡死（hang_watchdog 也挂了 / queue 死锁），强制 terminate + emit
        crashed。子进程层 30s 是第一道防线，父进程 120s 是兜底。
        """
        proc = self._proc
        q = self._up_q
        if proc is None or q is None:
            return

        loop = asyncio.get_running_loop()
        last_msg_wall = time.monotonic()

        def _blocking_get() -> dict[str, Any] | None:
            """在 executor 线程里阻塞等队列消息（最多 1s timeout 轮一次）。"""
            try:
                return q.get(timeout=_WATCHDOG_INTERVAL)
            except queue.Empty:
                return None

        while True:
            # 非阻塞：先把队列里积压的消息全部处理
            try:
                raw = q.get_nowait()
                last_msg_wall = time.monotonic()
                # 只有 game_status 类消息才更新内部状态
                if "sc2" in raw or "bot" in raw:
                    self._apply_raw(raw)
                yield raw
                continue
            except queue.Empty:
                pass

            # 队列空了，检查进程状态
            if not proc.is_alive():
                # 进程已退出：兜底判定
                exit_code = proc.exitcode
                if exit_code != 0 and self._sc2_state not in ("ended", "crashed"):
                    self._sc2_state = "crashed"
                    self._bot_state = "error"
                    yield {
                        "sc2": "crashed",
                        "bot": "error",
                        "detail": f"子进程非正常退出，exitcode={exit_code}",
                    }
                break

            # 父进程兜底 watchdog：长时间无消息 + 子进程仍 alive → 强制 kill
            stale = time.monotonic() - last_msg_wall
            if stale > _PARENT_WATCHDOG_STALE_S and self._sc2_state not in ("ended", "crashed"):
                self._log.error(
                    "parent_watchdog_timeout",
                    stale_s=round(stale, 1),
                    threshold_s=_PARENT_WATCHDOG_STALE_S,
                    pid=proc.pid,
                )
                self._sc2_state = "crashed"
                self._bot_state = "error"
                yield {
                    "sc2": "crashed",
                    "bot": "error",
                    "detail": f"parent_watchdog: 子进程 {stale:.0f}s 无上行消息，强制 kill",
                }
                self._terminate_and_join()
                break

            # 阻塞等（在 executor 线程，不卡 event loop）
            result: dict[str, Any] | None = await loop.run_in_executor(None, _blocking_get)
            if result is not None:
                last_msg_wall = time.monotonic()
                if "sc2" in result or "bot" in result:
                    self._apply_raw(result)
                yield result

    async def status_events(self) -> AsyncIterator[GameStatus]:
        """上行流（向后兼容）：持续 yield GameStatus，过滤掉 echo / snapshot / event 消息。

        M1.6 新增了 raw_events()；此方法保留向后兼容，
        只 yield game_status 类消息（跳过 echo / snapshot / event）。
        """
        async for raw in self.raw_events():
            # 非 game_status 类消息跳过（echo / snapshot / event / minimap 等）
            if raw.get("kind") in ("echo", "snapshot", "event", "minimap"):
                continue
            sc2, bot, detail = _apply_raw_dict(raw, self._sc2_state, self._bot_state)
            yield GameStatus(sc2=sc2, bot=bot, detail=detail)

    def send_command(self, cmd: dict[str, Any]) -> None:
        """下行通道：发指令到子进程（M1.4+ 用）。"""
        if self._down_q is None:
            self._log.warning("send_command_no_queue")
            return
        try:
            self._down_q.put_nowait(cmd)
        except Exception as exc:
            self._log.warning("send_command_failed", error=str(exc))

    async def stop(self) -> None:
        """善后：先请求 leave，等几秒，再强杀，最后 join。"""
        if self._proc is None:
            return

        self._log.info("game_process_stopping", pid=self._proc.pid)

        # 先发 leave 信号（M1.4+ 实现；M1.2 阶段直接进入强杀）
        self.send_command({"type": "leave"})

        # 给子进程最多 5s 自然退出
        proc = self._proc
        loop = asyncio.get_event_loop()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                loop.run_in_executor(None, lambda: proc.join(timeout=5)),
                timeout=6.0,
            )

        self._terminate_and_join()

    def _terminate_and_join(self) -> None:
        """强杀子进程（terminate + join）+ 清理 grandchild SC2_x64.exe 孤儿。

        Windows 上 multiprocessing.Process.terminate() 走 TerminateProcess 强杀,
        子进程没机会执行 atexit / sc2.kill_switch → python-sc2 spawn 的
        SC2_x64.exe 成孤儿继续跑。显式 psutil kill SC2_x64 兜底,避免 service
        长跑或 e2e 测试堆积一堆 SC2 窗口。
        """
        if self._proc is None:
            return
        try:
            if self._proc.is_alive():
                self._proc.terminate()
                self._proc.join(timeout=3)
                if self._proc.is_alive():
                    self._proc.kill()
                    self._proc.join(timeout=2)
        except Exception as exc:
            self._log.warning("game_process_terminate_error", error=str(exc))
        finally:
            # 兜底 kill SC2_x64 孤儿(grandchild,Python terminate 不到)
            try:
                from vibecraft.bot.watchdog import kill_sc2_processes

                killed = kill_sc2_processes()
                if killed:
                    self._log.info("game_process_killed_sc2_orphans", count=killed)
            except Exception as exc:
                self._log.warning("game_process_kill_sc2_error", error=str(exc))
            self._proc = None
            self._up_q = None
            self._down_q = None
            self._log.info("game_process_terminated")

    def _apply_raw(self, raw: dict[str, str]) -> GameStatus:
        """解析上行队列消息，更新内部状态，返回 GameStatus。"""
        sc2, bot, detail = _apply_raw_dict(raw, self._sc2_state, self._bot_state)
        self._sc2_state = sc2
        self._bot_state = bot
        status = GameStatus(sc2=sc2, bot=bot, detail=detail)
        self._log.debug(
            "game_status_update",
            sc2=sc2,
            bot=bot,
            detail=detail,
        )
        return status
