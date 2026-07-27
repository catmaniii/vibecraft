"""HangWatchdog 单测。

不动真实 SC2 / os._exit；exit_fn / kill_sc2 都注入 stub。
验证：
- bot.time 前进时不触发
- bot.time 卡住超阈值 → 触发 on_hang + kill_sc2 + exit_fn
- stop() 干净关停 thread
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from vibecraft.bot.watchdog import HangWatchdog, kill_sc2_by_parent_pid


def test_watchdog_does_not_trigger_when_bot_time_advances() -> None:
    bot_time = [0.0]

    def get_t() -> float:
        return bot_time[0]

    exits: list[int] = []
    kills: list[int] = []

    wd = HangWatchdog(
        get_bot_time=get_t,
        stall_threshold_s=0.5,
        check_interval_s=0.05,
        on_hang=lambda: None,
        kill_sc2=lambda: kills.append(1) or 1,
        exit_fn=lambda code: exits.append(code),
    )
    wd.start()
    # 不断推进 bot.time
    for _ in range(20):
        time.sleep(0.05)
        bot_time[0] += 0.05
    wd.stop()

    assert exits == []
    assert kills == []
    assert wd.triggered is False


def test_watchdog_triggers_on_stall() -> None:
    bot_time = [10.0]
    exits: list[int] = []
    kills: list[int] = []
    hang_fired = threading.Event()

    def get_t() -> float:
        return bot_time[0]

    def on_hang() -> None:
        hang_fired.set()

    wd = HangWatchdog(
        get_bot_time=get_t,
        stall_threshold_s=0.3,
        check_interval_s=0.05,
        on_hang=on_hang,
        kill_sc2=lambda: kills.append(1) or 1,
        exit_fn=lambda code: exits.append(code),
    )
    wd.start()
    # 不动 bot_time,等触发
    deadline = time.monotonic() + 3.0
    while not exits and time.monotonic() < deadline:
        time.sleep(0.05)
    wd.stop()

    assert exits == [87]
    assert kills == [1]
    assert hang_fired.is_set()
    assert wd.triggered is True


def test_watchdog_stop_is_idempotent() -> None:
    wd = HangWatchdog(
        get_bot_time=lambda: 0.0,
        stall_threshold_s=10.0,
        check_interval_s=0.05,
        exit_fn=lambda code: None,
    )
    wd.start()
    wd.stop()
    wd.stop()  # 不报错


def test_watchdog_get_bot_time_exception_recovers() -> None:
    """get_bot_time 抛异常时 watchdog 不挂,下次再尝试。"""
    raised = [False]
    exits: list[int] = []

    def get_t() -> float:
        if not raised[0]:
            raised[0] = True
            raise RuntimeError("boom")
        return 100.0  # 推进 → 重置 baseline

    wd = HangWatchdog(
        get_bot_time=get_t,
        stall_threshold_s=0.5,
        check_interval_s=0.05,
        exit_fn=lambda code: exits.append(code),
    )
    wd.start()
    time.sleep(0.3)
    wd.stop()

    # 抛异常那次跳过,后续 get_t 返回 100 让 baseline 推进,不触发 stall
    assert exits == []


# ---------------------------------------------------------------------------
# kill_sc2_by_parent_pid — scoped kill 单测（mock psutil）
# ---------------------------------------------------------------------------


def test_kill_sc2_by_parent_pid_kills_sc2_children() -> None:
    """kill_sc2_by_parent_pid 只杀 parent_pid 子孙树里的 SC2 进程。"""
    # 构造两个 fake child process：一个是 SC2，一个是无关进程
    sc2_proc = MagicMock()
    sc2_proc.name.return_value = "SC2_x64.exe"

    other_proc = MagicMock()
    other_proc.name.return_value = "python.exe"

    fake_parent = MagicMock()
    fake_parent.children.return_value = [sc2_proc, other_proc]

    with patch("vibecraft.bot.watchdog.psutil") as mock_psutil:
        mock_psutil.Process.return_value = fake_parent
        mock_psutil.NoSuchProcess = Exception
        mock_psutil.AccessDenied = Exception

        killed = kill_sc2_by_parent_pid(99999)

    assert killed == 1
    sc2_proc.kill.assert_called_once()
    other_proc.kill.assert_not_called()


def test_kill_sc2_by_parent_pid_no_such_process() -> None:
    """parent_pid 不存在时静默返回 0，不抛异常。"""

    class _NoSuchProcess(Exception):
        pass

    with patch("vibecraft.bot.watchdog.psutil") as mock_psutil:
        mock_psutil.NoSuchProcess = _NoSuchProcess
        mock_psutil.AccessDenied = Exception
        mock_psutil.Process.side_effect = _NoSuchProcess("gone")

        killed = kill_sc2_by_parent_pid(99998)

    assert killed == 0


def test_kill_sc2_by_parent_pid_no_sc2_children() -> None:
    """子孙树里没有 SC2 进程时返回 0。"""
    fake_parent = MagicMock()
    child = MagicMock()
    child.name.return_value = "python.exe"
    fake_parent.children.return_value = [child]

    with patch("vibecraft.bot.watchdog.psutil") as mock_psutil:
        mock_psutil.Process.return_value = fake_parent
        mock_psutil.NoSuchProcess = Exception
        mock_psutil.AccessDenied = Exception

        killed = kill_sc2_by_parent_pid(88888)

    assert killed == 0
    child.kill.assert_not_called()


def test_kill_sc2_by_parent_pid_multiple_sc2_processes() -> None:
    """子孙树里有多个 SC2 进程时全部杀掉。"""
    sc2_a = MagicMock()
    sc2_a.name.return_value = "SC2_x64.exe"
    sc2_b = MagicMock()
    sc2_b.name.return_value = "SC2_x64.exe"

    fake_parent = MagicMock()
    fake_parent.children.return_value = [sc2_a, sc2_b]

    with patch("vibecraft.bot.watchdog.psutil") as mock_psutil:
        mock_psutil.Process.return_value = fake_parent
        mock_psutil.NoSuchProcess = Exception
        mock_psutil.AccessDenied = Exception

        killed = kill_sc2_by_parent_pid(77777)

    assert killed == 2
    sc2_a.kill.assert_called_once()
    sc2_b.kill.assert_called_once()
