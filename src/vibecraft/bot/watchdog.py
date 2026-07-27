"""SC2 卡死 watchdog（自动化 e2e 测试基础设施）。

子进程内 daemon thread，每 _CHECK_INTERVAL_S 检查 bot.time 是否前进。
若 _STALL_THRESHOLD_S 内 bot.time 不变 → 判定 SC2 卡死：

  1. fire 可选的 on_hang 回调（让 bot 在退出前推一条 status_callback 给父进程）
  2. 用 psutil kill 当前用户的 SC2_x64.exe（保险起见也 taskkill 兜底）
  3. os._exit(_HANG_EXIT_CODE) 强制子进程退出（不走 atexit，避免本身就卡）

适用场景：自动化 e2e smoke（headless_smoke + 4 类指令测试）。
生产环境（玩家手机控制）应该用 W2：PWA banner 让玩家点重启。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

# psutil 是可选依赖（不在所有环境都装），顶层 try-import 让模块属性存在，
# 方便单测 patch("vibecraft.bot.watchdog.psutil")。
try:
    import psutil  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    psutil = None

_CHECK_INTERVAL_S = 5.0
_STALL_THRESHOLD_S = 30.0
_HANG_EXIT_CODE = 87  # 任意约定值，父进程可识别


def kill_sc2_processes() -> int:
    """Kill 所有 SC2_x64.exe 进程。返回 kill 数。

    公开 API,driver/GameProcess/watchdog 共用。**孤儿 grandchild 兜底**:
    python-sc2 拉的 SC2_x64.exe 是 GameProcess 子进程的 grandchild,
    `multiprocessing.Process.terminate()` 走 Windows TerminateProcess 强杀,
    子进程没机会执行 atexit / sc2.kill_switch → SC2_x64 成孤儿。所有
    "强制结束 game session" 路径都必须显式 kill 兜底。
    """
    killed = 0
    if psutil is not None:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = proc.info.get("name") or ""
                if "SC2" in name:
                    proc.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    if killed == 0:
        import subprocess

        try:
            result = subprocess.run(
                ["taskkill", "/F", "/IM", "SC2_x64.exe"],
                check=False,
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                killed = 1
        except Exception as exc:
            logger.error("watchdog taskkill failed: %s", exc)
    return killed


def kill_sc2_by_parent_pid(parent_pid: int) -> int:
    """Kill SC2_x64.exe 进程，仅限 parent_pid 子孙树内的进程。返回 kill 数。

    **作用域化 kill**：并发跑多局时，每局只清理自己 GameProcess 子进程
    （parent_pid）名下的 SC2_x64.exe grandchild，不影响其他并行局。

    SC2 进程层级：
        runner / ws → GameProcess._proc (parent_pid)
                      └─ python-sc2 SC2Process
                             └─ SC2_x64.exe  ← 目标

    psutil.Process(parent_pid).children(recursive=True) 拿完整子孙树，
    筛出名字含 "SC2" 的杀掉。parent_pid 不存在时静默返回 0。
    """
    if psutil is None:
        # psutil 未装：fallback 到全局 kill（保守兜底）
        logger.warning("psutil not available, falling back to global SC2 kill")
        return kill_sc2_processes()

    killed = 0
    try:
        parent = psutil.Process(parent_pid)
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        return 0

    for proc in children:
        try:
            name = proc.name() or ""
            if "SC2" in name:
                proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed


class HangWatchdog:
    """监控 bot.time 是否前进的 daemon 线程。

    用法（在子进程内）::

        wd = HangWatchdog(
            get_bot_time=lambda: float(bot.time),
            on_hang=lambda: status_callback("crashed", "error", "hang"),
        )
        wd.start()
        # 对局正常结束:
        wd.stop()
    """

    def __init__(
        self,
        get_bot_time: Callable[[], float],
        stall_threshold_s: float = _STALL_THRESHOLD_S,
        check_interval_s: float = _CHECK_INTERVAL_S,
        on_hang: Callable[[], None] | None = None,
        kill_sc2: Callable[[], int] = kill_sc2_processes,
        exit_fn: Callable[[int], None] | None = None,
    ) -> None:
        self._get_bot_time = get_bot_time
        self._stall_threshold_s = stall_threshold_s
        self._check_interval_s = check_interval_s
        self._on_hang = on_hang
        self._kill_sc2 = kill_sc2
        self._exit_fn = exit_fn if exit_fn is not None else os._exit
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_bot_time: float = 0.0
        self._last_advance_wall: float = time.monotonic()
        # 暴露给外部探测（测试 / 诊断用）
        self.triggered: bool = False

    def start(self) -> None:
        if self._thread is not None:
            return
        try:
            self._last_bot_time = self._get_bot_time()
        except Exception:
            self._last_bot_time = 0.0
        self._last_advance_wall = time.monotonic()

        self._thread = threading.Thread(
            target=self._run, name="vibecraft-hang-watchdog", daemon=True
        )
        self._thread.start()
        logger.info(
            "hang_watchdog_started: stall_threshold=%.1fs check_interval=%.1fs",
            self._stall_threshold_s,
            self._check_interval_s,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self._check_interval_s):
            try:
                bot_t = self._get_bot_time()
            except Exception as exc:
                logger.warning("watchdog: get_bot_time fail: %s", exc)
                continue

            now_wall = time.monotonic()
            if bot_t > self._last_bot_time + 1e-6:
                self._last_bot_time = bot_t
                self._last_advance_wall = now_wall
                continue

            stall = now_wall - self._last_advance_wall
            if stall < self._stall_threshold_s:
                continue

            self.triggered = True
            logger.error(
                "hang_detected: bot.time=%.2f 停在 %.1fs 不前进, kill SC2",
                bot_t,
                stall,
            )
            if self._on_hang is not None:
                try:
                    self._on_hang()
                except Exception as exc:
                    logger.warning("watchdog on_hang callback fail: %s", exc)

            killed = self._kill_sc2()
            logger.error("watchdog killed %d SC2 process(es), exiting child", killed)
            self._exit_fn(_HANG_EXIT_CODE)
            return
