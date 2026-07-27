"""audio_grab 子进程入口单测 —— 只测参数解析与模式分派，不碰真音频/COM。

真实 per-PID process loopback 链路由 scripts/spike_process_loopback.py 验证
（spawn 有声/安静子进程，断言分得开），不进 CI。
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from vibecraft.server import audio_grab
from vibecraft.server.audio_grab import _parse_args


def test_parse_args_default_no_pid() -> None:
    assert _parse_args([]).pid is None


def test_parse_args_with_pid() -> None:
    assert _parse_args(["--pid", "777"]).pid == 777


def test_main_pid_mode_falls_back_to_device_on_failure(monkeypatch: Any) -> None:
    """pid 模式初始化失败（老系统 / PID 已死）→ 回退 device loopback，不直接 exit(1)。"""
    monkeypatch.setattr(sys, "argv", ["audio_grab", "--pid", "999"])
    calls: list[str] = []

    def boom(pid: int, out: Any) -> int:
        calls.append(f"pid:{pid}")
        raise OSError("activation failed 模拟")

    monkeypatch.setattr(audio_grab, "_run_process_loopback", boom)
    monkeypatch.setattr(audio_grab, "_run_device_loopback", lambda: calls.append("device") or 0)

    assert audio_grab.main() == 0
    assert calls == ["pid:999", "device"]


def test_main_without_pid_goes_straight_to_device(monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["audio_grab"])
    calls: list[str] = []

    def no_call(pid: int, out: Any) -> int:
        pytest.fail("无 --pid 不应进 process loopback 路径")

    monkeypatch.setattr(audio_grab, "_run_process_loopback", no_call)
    monkeypatch.setattr(audio_grab, "_run_device_loopback", lambda: calls.append("device") or 0)

    assert audio_grab.main() == 0
    assert calls == ["device"]
