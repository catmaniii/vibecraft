"""SC2 子进程生命周期管理（M1.2）。

设计预研覆盖 4 个架构难点 + spike 结论。

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

    my_race: str = "Protoss"
    """我方种族：Protoss / Zerg / Terran（默认 Protoss）。"""

    opponent_race: str = "Random"
    """内置 AI 种族：Protoss / Terran / Zerg / Random（默认随机）。"""

    opponent_difficulty: str = "VeryHard"
    """内置 AI 难度（sc2.data.Difficulty 全 10 档）：
    VeryEasy / Easy / Medium / MediumHard / Hard / Harder / VeryHard /
    CheatVision（全图视野）/ CheatMoney（白送资源）/ CheatInsane（视野+资源+速度全开）。
    """

    realtime: bool = True
    """是否以实时（1x）速度跑。玩家要看画面，默认 True。"""

    random_seed: int | None = None
    """SC2 引擎随机种子（透传给 python-sc2 GameMatch.random_seed）。

    None = 引擎自选（正常对局）。build 效率评测/进化迭代时显式给定 → 变体 A/B 同 seed
    配对跑，消掉地图/spawn/电脑 AI 的随机变量，使配对差分几乎只反映 build 改动。
    （2026-06-15 build 效率评价系统 Phase 0；现状本无 seed，多局完全非确定。）
    """

    sandbox_macro_only: bool = False
    """纯运营沙盒模式：子进程 on_start 一次性强制 bot 进 defend 姿态（combat_intent_override
    + engagement_stance = defend），全程只 macro 不主动 moveout，隔离战斗损耗噪声。

    仅 build 效率评测对"纯运营 build"用（降噪）；all-in build 关它、自然打。
    （2026-06-15 build 效率评价系统 Phase 0。）
    """

    game_time_limit_s: int = 0
    """solo 局游戏内时限（秒，透传 python-sc2 GameMatch.game_time_limit）。0 = 不限（默认）。

    build 效率评测用：沙盒 forced-defend 下游戏不会自然结束（或拖到 30+ 分钟），靠它把每局
    钉在评测窗口（如 600s）就结束 → 省 wall-clock + 控制评测窗口。（2026-06-15。）
    """

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
    # 2026-06-03 用户:正常开局起 SC2 窗口后要把焦点给它,否则 SC2 失焦静音听不到声音。
    # 仅 server 正常开局 path 设 True;build/override acceptance 并行多窗口绝不抢焦点
    # (会互相抢成一团 + 测试不需要声音),默认 False。
    focus_window: bool = False
    # 2026-05-23 修并发 race:build_acceptance 并行多 strategy 时,
    # 用 os.environ["VIBECRAFT_FORCE_INITIAL_OPENING"] 传 strategy_id 会被
    # 后写的覆盖 — 5 个并行 game active_recipe 全成最后写的那个。
    # 改成 GameConfig 字段(picklable,每个子进程独立),子进程入口在 setup 前
    # 设到自己 os.environ —— 各子进程 env 独立,不互相覆盖。
    forced_opening: str = ""  # 非空时子进程 set VIBECRAFT_FORCE_INITIAL_OPENING
    game_id: str = ""  # 非空时子进程 set VIBECRAFT_GAME_ID(同 race 修复)
    locale: str = "zh"  # 玩家语言(zh/en)；子进程 set VIBECRAFT_LOCALE → 喂 IntentParser 决定 interpretation 语言
    # 玩家覆盖 e2e (Task #311) 用:由 override_acceptance runner 把 spec
    # player_actions 序列化成 list[dict] 传进来,子进程入口写到 director
    # `_scheduled_player_actions`,Director.on_tick 到点 submit_directive
    # 模拟玩家按 UI 战术按钮。
    # dict 形状:{"at_s": float, "verb": str, "mode": str|None, "target_area": str|None}
    player_actions: list[dict[str, Any]] = field(default_factory=list)
    # Task #350: persistent_doctrine build_acceptance 用。
    # 非空时子进程在 opening_completed + auto_switch_delay_s 秒后自动
    # set_build(auto_switch_to)（模拟玩家 PWA toast confirm 切 doctrine）。
    # 透传到 director._auto_switch_to；空串 = 不切（生产 / 普通 opening 验收）。
    auto_switch_to: str = ""
    auto_switch_delay_s: float = 10.0

    # ---- 阶段 0 多人联网（2026-06-12 设计）----
    # mp_role: "" = 单人（原 run_multiple_games 路径，完全不变）
    #          "host" = 本进程 create_game + 打 players[0]
    #          "join" = 本进程加入 host 创建的局
    mp_role: str = ""
    mp_portconfig_json: str = ""  # Portconfig.as_json，全部参与进程共享同一份
    mp_player_name: str = "VibeCraft"  # 本方 bot 显示名（lobby 用户名）
    mp_guest_names: list[str] = field(default_factory=list)  # host 用：guest 占位名
    mp_computers: list[dict[str, str]] = field(default_factory=list)  # host 用：内置 AI 列表
    mp_game_time_limit: int = 7200  # 多人局兜底时限（秒），防双方挂机永不结束

    # 玩家昵称（admin 对局记录 + telemetry game_start 字段）。
    # match.py build_plan 从 Room.slot.name 填入；solo/multi 均透传。
    # 子进程入口写到 VIBECRAFT_PLAYER_NAME 环境变量，common_bot on_start 读取后落进
    # telemetry game_start record → admin dashboard 对局列表显示玩家名。
    # 默认空串 = 未知（旧局 / build_acceptance 沙盒无玩家）。
    player_name: str = ""
    # 整局参战方 roster JSON（[{name,race,kind} 真人 / {race,difficulty,kind:computer} 电脑]）。
    # 写进 game_start telemetry → admin 对局记录显示全部参战方（一局含两人 / 玩家+电脑种族）。
    match_roster_json: str = ""


@dataclass
class GameStatus:
    """一个状态快照，从上行队列取出后封包成 game_status 帧。"""

    sc2: Sc2State
    bot: BotState
    ts: float = field(default_factory=time.time)
    detail: str = ""
    my_race: str = "Protoss"  # PWA 用它过滤剧本列表(只显示当前种族剧本)


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


def _window_pid_allowed(pid: int, whitelist: set[int] | None) -> bool:
    """窗口 PID 白名单过滤（纯函数，供单测）。

    whitelist=None 表示不过滤（允许所有 PID），非 None 时只允许 whitelist 内的 PID。
    用于 _focus_sc2_window 多实例场景——只 focus 本子进程的 SC2 窗口，不抢别的实例。
    """
    if whitelist is None:
        return True
    return pid in whitelist


def _poll_own_sc2_pids(timeout_s: float) -> set[int] | None:
    """轮询当前进程的 SC2 子孙 PID（供 focus 线程 / sc2_pid 上报线程共用）。

    返回找到的 SC2 子孙 PID 集合；psutil 未装或超时时返回 None（调用方按需回退）。
    timeout_s：最长轮询时长（focus 线程建议 60s；sc2_pid 上报线程建议 120s）。
    2026-06-12 M1 T10：从 _focus_thread_with_pid_filter 抽出，两个 daemon 线程共用。
    """
    import os as _os_poll
    import time as _t_poll

    try:
        import psutil as _psutil_poll

        self_pid = _os_poll.getpid()
        deadline = _t_poll.monotonic() + timeout_s
        while _t_poll.monotonic() < deadline:
            try:
                children = _psutil_poll.Process(self_pid).children(recursive=True)
                sc2_pids = {p.pid for p in children if "sc2" in p.name().lower()}
                if sc2_pids:
                    return sc2_pids
            except Exception:
                pass
            _t_poll.sleep(0.5)
        return None
    except ImportError:
        return None
    except Exception:
        return None


def _focus_sc2_window(
    child_log: Any,
    *,
    title_substr: str = "StarCraft II",
    wait_timeout_s: float = 120.0,
    poll_interval_s: float = 0.5,
    settle_delay_s: float = 1.5,
    refocus_attempts: int = 3,
    refocus_interval_s: float = 1.5,
    pid_whitelist: set[int] | None = None,
) -> None:
    """轮询等 SC2 窗口出现后把焦点抢过来(否则 SC2 失焦默认静音)。

    在子进程的后台 daemon 线程里跑(run_multiple_games / asyncio.run 是阻塞的,只能旁路起线程)。
    Windows-only;非 win32 直接返回。找到窗口后 settle 一下再 focus,并重试几次,
    盖掉 SC2 loading→对局阶段窗口重建导致的焦点丢失。**只 focus 几次就停**,
    不长期轮询抢焦点(玩家用手机操作,PC 当显示器,一次抢稳即可)。

    pid_whitelist: 非 None 时只 focus PID 在白名单内的 SC2 窗口（多实例防抢错）。
    None = 老行为（按标题找第一个 StarCraft II 窗口）。
    """
    import sys
    import time as _time

    if sys.platform != "win32":
        return

    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_RESTORE = 9

    def _find_hwnd() -> int | None:
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)  # type: ignore[untyped-decorator]
        def _enum(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if title_substr in buf.value:
                # PID 白名单过滤：多实例时只认属于本子进程的 SC2 窗口
                if pid_whitelist is not None:
                    _win_pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(_win_pid))
                    if not _window_pid_allowed(_win_pid.value, pid_whitelist):
                        return True  # 继续枚举，跳过此窗口
                found.append(hwnd)
                return False  # 停止枚举
            return True

        user32.EnumWindows(_enum, 0)
        return found[0] if found else None

    def _do_focus(hwnd: int) -> None:
        # SetForegroundWindow 有"只有前台进程能设前台"限制;经典绕法:把当前线程
        # input 附到目标窗口线程,再 BringWindowToTop + SetForegroundWindow。
        try:
            user32.ShowWindow(hwnd, SW_RESTORE)
            fg = user32.GetForegroundWindow()
            target_tid = user32.GetWindowThreadProcessId(hwnd, None)
            fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            cur_tid = kernel32.GetCurrentThreadId()
            attached = []
            for other in (fg_tid, cur_tid):
                if (
                    other
                    and other != target_tid
                    and user32.AttachThreadInput(other, target_tid, True)
                ):
                    attached.append(other)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
            for other in attached:
                user32.AttachThreadInput(other, target_tid, False)
        except Exception as exc:
            child_log.warning("focus_sc2_window_set_failed: %s", exc)

    deadline = _time.monotonic() + wait_timeout_s
    hwnd: int | None = None
    while _time.monotonic() < deadline:
        hwnd = _find_hwnd()
        if hwnd:
            break
        _time.sleep(poll_interval_s)

    if not hwnd:
        child_log.warning("focus_sc2_window: 等 %.0fs 没找到 SC2 窗口,放弃", wait_timeout_s)
        return

    # settle 一下(窗口刚建可能还在 loading),然后 focus + 重试几次抢稳
    _time.sleep(settle_delay_s)
    child_log.info("focus_sc2_window: 找到 SC2 窗口 hwnd=%s,抢焦点", hwnd)
    for _ in range(max(1, refocus_attempts)):
        cur = _find_hwnd() or hwnd  # loading→对局窗口可能重建,重查一次
        _do_focus(cur)
        _time.sleep(refocus_interval_s)


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
    # 子进程接力 server log 捕获:父进程通过 env VIBECRAFT_SERVER_LOG_PATH 传过来,
    # 让子进程 stdout/stderr 和 logging 都镜像到同一文件 — 早期 traceback 不再只
    # 落到 service terminal。失败不阻塞子进程启动。
    try:
        from vibecraft.logging_.server_log import init_from_env

        init_from_env()
    except Exception:
        pass

    # 2026-05-23 修并发 race:每个子进程从 config.forced_opening 写自己的
    # VIBECRAFT_FORCE_INITIAL_OPENING(不读父进程 os.environ,父并发时会被覆盖)。
    # spawn 子进程 env 独立(继承自父 fork 时刻的快照,后续父修改不影响子)—— 但
    # build_acceptance 父进程并发起 5 个子进程时,后写覆盖前写,前 4 个子进程拿到的
    # env 是最后一个 strategy 的。改用 GameConfig 字段是 picklable 安全。
    import os as _os

    if config.forced_opening:
        _os.environ["VIBECRAFT_FORCE_INITIAL_OPENING"] = config.forced_opening
    if config.game_id:
        _os.environ["VIBECRAFT_GAME_ID"] = config.game_id
    if config.locale:
        _os.environ["VIBECRAFT_LOCALE"] = config.locale
    if config.player_name:
        _os.environ["VIBECRAFT_PLAYER_NAME"] = config.player_name
    if config.match_roster_json:
        _os.environ["VIBECRAFT_MATCH_ROSTER"] = config.match_roster_json

    # 子进程需要重新配置日志（spawn 后父进程 logging state 不继承）
    logging.basicConfig(level=log_level)
    # vibecraft 自己的模块用 INFO(生产可观察 directive 流转 / production_override
    # TRAIN / standing order release 等),其他模块(sharpy/sc2/anthropic 等)仍走
    # log_level(WARNING),避免噪音。
    logging.getLogger("vibecraft").setLevel(logging.INFO)
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

    # 玩家/对手选 Random → 解析成具体种族（#585）。VibeCraft bot 有种族专属 build，
    # 无法用 "Random" 构造 plan（make_bot_class 抛 NotImplementedError → 整局 crashed）。
    # **在此一处**解析，喂给下游 make_bot_class 的 plan **和** SC2 Bot(Race[config.my_race])
    # 的种族，两边一致 → 避免"plan A 族 / in-game B 族"。
    if config.my_race == "Random":
        import random as _rnd

        _resolved_race = _rnd.choice(["Protoss", "Terran", "Zerg"])
        child_log.info("random_race_resolved: my_race Random -> %s", _resolved_race)
        config.my_race = _resolved_race

    try:
        bot_class = _build_bot_class(
            _put,
            down_q,
            _put_echo,
            _put_snapshot,
            _put_event,
            _put_minimap,
            my_race=config.my_race,
            player_actions=list(config.player_actions),
            auto_switch_to=config.auto_switch_to,
            auto_switch_delay_s=config.auto_switch_delay_s,
            sandbox_macro_only=config.sandbox_macro_only,
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

    # 2026-06-03 用户:正常开局起 SC2 窗口后抢焦点(失焦静音)。后台 daemon 线程旁路
    # 跑(run_multiple_games / asyncio.run 阻塞);仅 config.focus_window=True(server 正常 path)。
    # 2026-06-12 S3 修订:先轮询子进程拿 SC2 PID 集,再带白名单 focus(多实例不抢错窗口)。
    # 拿不到 PID 集(超时/psutil 异常)时回退 pid_whitelist=None(老行为)。
    # 2026-06-12 M1 T10：threading 供 focus + sc2_pid 两个 daemon 线程共用
    import threading as _thr

    if config.focus_window:

        def _focus_thread_with_pid_filter() -> None:
            """轮询本子进程 SC2 子孙 PID，再带白名单 focus（S3 多实例防抢错窗口）。

            使用共用工具函数 _poll_own_sc2_pids（60s 超时），拿不到时回退 None（老行为）。
            """
            pid_whitelist = _poll_own_sc2_pids(60.0)
            _focus_sc2_window(child_log, pid_whitelist=pid_whitelist)

        _thr.Thread(
            target=_focus_thread_with_pid_filter,
            daemon=True,
            name="vibecraft-focus-sc2",
        ).start()

    # 2026-06-12 M1 T10：无条件上报 SC2 子孙 PID（WebRTC 按 PID 分流用）
    # 不限 focus_window=True，单人/多人路径都要上报，父进程记到 GameProcess.sc2_pid。
    def _sc2_pid_report() -> None:
        """轮询本子进程 SC2 子孙 PID，找到即上报父进程（最多等 120s）。"""
        pid_set = _poll_own_sc2_pids(120.0)
        if pid_set:
            pid = next(iter(pid_set))
            try:
                up_q.put_nowait({"kind": "sc2_pid", "pid": pid})
                child_log.info("sc2_pid_reported: pid=%d", pid)
            except Exception as exc:
                child_log.warning("sc2_pid_report_failed: %s", exc)

    _thr.Thread(target=_sc2_pid_report, daemon=True, name="vibecraft-sc2-pid").start()

    if config.mp_role:
        # ---- 多人分支：host/join 跨进程联机（sc2_multiplayer runner）----
        import asyncio as _asyncio

        from sc2.player import Bot as _Bot  # type: ignore[import-untyped]
        from sc2.portconfig import Portconfig as _Portconfig  # type: ignore[import-untyped]

        from vibecraft.server.sc2_multiplayer import (
            build_host_players,
            host_game,
            join_game,
        )

        try:
            _put("launching", "running")
            portconfig = _Portconfig.from_json(config.mp_portconfig_json)
            if config.mp_role == "host":
                players = build_host_players(
                    config.my_race,
                    config.mp_player_name,
                    guest_names=list(config.mp_guest_names),
                    computers=list(config.mp_computers),
                    my_ai=bot_instance,
                )
                _asyncio.run(
                    host_game(
                        sc2_map,
                        players,
                        config.realtime,
                        portconfig,
                        resolution=(config.window_width, config.window_height),
                        placement=(config.window_x, config.window_y),
                        game_time_limit=config.mp_game_time_limit,
                    )
                )
            else:
                me = _Bot(Race[config.my_race], bot_instance, name=config.mp_player_name)
                _asyncio.run(
                    join_game(
                        me,
                        config.realtime,
                        portconfig,
                        resolution=(config.window_width, config.window_height),
                        placement=(config.window_x, config.window_y),
                        game_time_limit=config.mp_game_time_limit,
                    )
                )
            _put("ended", "idle")
        except Exception as exc:
            _put(
                "crashed",
                "error",
                detail=f"多人对局失败: {type(exc).__name__}: {exc}",
            )
    else:
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
                            Bot(Race[config.my_race], bot_instance, name="VibeCraft"),
                            Computer(
                                Race[config.opponent_race],
                                Difficulty[config.opponent_difficulty],
                            ),
                        ],
                        realtime=config.realtime,
                        random_seed=config.random_seed,
                        game_time_limit=(
                            config.game_time_limit_s if config.game_time_limit_s > 0 else None
                        ),
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
    my_race: str = "Protoss",
    player_actions: list[dict[str, Any]] | None = None,
    auto_switch_to: str = "",
    auto_switch_delay_s: float = 10.0,
    sandbox_macro_only: bool = False,
) -> type:
    """在子进程内构造 bot 类（M1.6：改用真 VibeCraftBot）。

    put_status：子进程内的 _put 闭包（不跨进程边界传递）。
    down_q：下行队列，传给 make_bot_class（Gap 2）。
    put_echo：echo 回调，让 director 结果能推给父进程（基础 echo）。
    put_snapshot：snapshot 推送回调（P0-4）。None 时忽略。
    put_event：event 推送回调（P1-4）。None 时忽略。
    put_minimap：minimap 推送回调（5Hz 下行流）。None 时忽略。
    my_race：我方种族（Protoss/Zerg/Terran），默认 Protoss。
    auto_switch_to：Task #350 — opening_completed + auto_switch_delay_s 秒后自动
      set_build 到此 doctrine。空串 = 不切（普通 build_acceptance 路径）。
    auto_switch_delay_s：auto_switch_to 生效延迟（秒，默认 10）。

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
    import os as _os
    from pathlib import Path

    from vibecraft.bot.director import Director
    from vibecraft.bot.sharpy_adapter import make_bot_class
    from vibecraft.llm.config import LLMConfig
    from vibecraft.llm.parser import IntentParser
    from vibecraft.logging_.session import GameSession, GameSessionConfig
    from vibecraft.strategy.library import StrategyLibrary

    # --- GameSession（日志落盘，logs/<game_id>/）---
    # 读环境变量 VIBECRAFT_GAME_ID：由 build_acceptance runner 在 start() 前设置，
    # 保证并发局各自落到独立目录，父进程可直接按 game_id 读 telemetry.jsonl。
    # 普通 server / smoke 场景不设此变量 → GameSessionConfig 自动生成唯一 id。
    _game_id_from_env: str | None = _os.environ.get("VIBECRAFT_GAME_ID") or None
    session = GameSession(GameSessionConfig(game_id=_game_id_from_env))

    # --- StrategyLibrary（从 strategies/ + aliases/ 加载）---
    # 路径推算：本文件在 src/vibecraft/server/game_process.py
    # 项目根 = 上溯 4 层
    _pkg_dir = Path(__file__).parent  # server/
    _src_vc_dir = _pkg_dir.parent  # vibecraft/
    _src_dir = _src_vc_dir.parent  # src/
    _project_root = _src_dir.parent  # 项目根
    strategies_dir = _project_root / "strategies"
    aliases_path = _project_root / "docs" / "aliases" / f"{my_race.lower()}.yaml"

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
    # my_race 传进 parser：(1) Strategy Catalog 只列当前种族剧本，
    # (2) strategy_set 校验拒绝跨种族 id（神族玩家说"切 12pool"会被拦下不切）
    # 玩家语言:子进程 env VIBECRAFT_LOCALE(父进程从 GameConfig.locale 设)→ 决定 interpretation 等语言
    _locale = _os.environ.get("VIBECRAFT_LOCALE", "zh")
    parser = IntentParser(
        provider=provider,
        library=strategy_library,
        session=session,
        my_race=my_race,
        locale=_locale,
    )

    # --- director_factory（在 on_start 时拿到真实 facade 再构造）---
    # 玩家覆盖 e2e (Task #311): 闭包捕获 player_actions,创建 Director 后
    # 写到 _scheduled_player_actions,让 Director.on_tick 到点触发。空 list
    # 时 Director 什么都不做,生产 / 普通 build_acceptance 路径完全不受影响。
    _scheduled_actions: list[dict[str, Any]] = list(player_actions or [])
    # Task #350: persistent_doctrine 验收 — 捕获 auto_switch 参数到闭包。
    _auto_switch_to: str = auto_switch_to or ""
    _auto_switch_delay_s: float = auto_switch_delay_s

    def director_factory(facade: Any) -> Director:
        director = Director(facade=facade, parser=parser, session=session, library=strategy_library)
        if _scheduled_actions:
            director._scheduled_player_actions = list(_scheduled_actions)
        # build 效率沙盒：强制全程 defend（纯运营 build 评测降噪）
        director._sandbox_macro_only = sandbox_macro_only
        # Task #350: 透传 auto_switch 到 director（参考 _scheduled_player_actions 模式）
        if _auto_switch_to:
            director._auto_switch_to = _auto_switch_to
            director._auto_switch_delay_s = _auto_switch_delay_s
        return director

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
        race=my_race,
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
        "my_race": status.my_race,
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
        self._my_race: str = "Protoss"  # 上次 start 用的种族,push 给 PWA 过滤剧本列表
        # 2026-05-24 用户:点结束本局 → stop() 设此标志 → raw_events 看到子进程
        # 退出(exit_code=-15 即 SIGTERM)时不当 crashed,而是 ended,UI 不显示
        # "子进程非正常退出"错误。
        self._user_stopped: bool = False
        # 2026-06-12 M1 T10：子进程 SC2 窗口 PID（首次上报后记录，WebRTC pid_filter 用）。
        # None = 尚未上报；父进程通过 raw_events 拦截 kind=="sc2_pid" 更新。
        self.sc2_pid: int | None = None
        self._log = logger.bind(component="game_process")

    @property
    def status(self) -> GameStatus:
        """当前状态快照（同步读取，不阻塞）。"""
        return GameStatus(sc2=self._sc2_state, bot=self._bot_state, my_race=self._my_race)

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

        # 玩家选 Random → 在此解析成具体种族（#585）。既喂给子进程 config（下游 make_bot_class
        # 的 plan + SC2 Bot(Race[...]) 两边一致），也让 PWA 状态/剧本面板按具体种族过滤
        # （否则面板按 "Random" 过滤不出任何剧本）。子进程 _build_bot_class 里另有一处兜底，
        # 覆盖 build_acceptance 等直接 spawn 子进程、不走 start() 的路径。
        if config.my_race == "Random":
            import random as _rnd

            config.my_race = _rnd.choice(["Protoss", "Terran", "Zerg"])
            self._log.info("random_race_resolved: my_race Random -> %s", config.my_race)

        self._sc2_state = "launching"
        self._bot_state = "idle"
        self._my_race = config.my_race  # 记下本局种族用于 status frame
        self._user_stopped = False  # 新局重置(防上一局 stop 标志残留)
        self.sc2_pid = None  # 新局重置，等子进程上报（2026-06-12 M1 T10）

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
                # 2026-06-12 M1 T10：sc2_pid 内部记账，不向外 yield（ws.py 不收到）
                if raw.get("kind") == "sc2_pid":
                    self.sc2_pid = raw.get("pid")
                    continue
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
                    # 2026-05-24 用户:用户主动停(_user_stopped=True)时不当 crashed,
                    # exit_code=-15(SIGTERM) 是 stop() 强杀的预期结果。
                    if self._user_stopped:
                        self._sc2_state = "ended"
                        self._bot_state = "idle"
                        yield {"sc2": "ended", "bot": "idle"}
                    else:
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
                # 2026-06-12 M1 T10：sc2_pid 内部记账，不向外 yield
                if result.get("kind") == "sc2_pid":
                    self.sc2_pid = result.get("pid")
                else:
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

        # 2026-05-24 用户:标记用户主动停 → raw_events 看到 exit_code=-15 时不当 crashed
        self._user_stopped = True
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
        # 必须在 terminate 之前 scoped-kill 本局 SC2 子孙：terminate 后父进程
        # 消失，psutil.Process(parent).children() 就拿不到子孙树了 —— SC2_x64.exe
        # 沦为孤儿、scoped kill 全漏。趁父进程还活着先抓子孙树杀掉。
        child_pid = self._proc.pid
        if child_pid is not None:
            try:
                from vibecraft.bot.watchdog import kill_sc2_by_parent_pid

                killed = kill_sc2_by_parent_pid(child_pid)
                if killed:
                    self._log.info(
                        "game_process_killed_sc2_orphans_scoped",
                        count=killed,
                        parent_pid=child_pid,
                    )
            except Exception as exc:
                self._log.warning("game_process_kill_sc2_error", error=str(exc))
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
