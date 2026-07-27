"""WebRTC 多人化 + SC2 PID 过滤单测（Task 10 / 2026-06-12 M1）。

覆盖：
- _find_sc2_window(pid_filter=...)：pid_filter 命中 / 不命中 / None 透传 3 条
- WebRtcManager.handle_offer 按 player_id supersede：
    同 player 二次 offer 旧 PC 被关、不同 player 互不影响（aiortc mock 掉）
- GameProcess.sc2_pid 记账：
    fake up_q 塞 {"kind":"sc2_pid","pid":123} → raw_events 不外漏且 gp.sc2_pid==123
"""

from __future__ import annotations

import queue
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibecraft.server.webrtc import (
    WebRtcManager,
    _find_sc2_window,
)

# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------


def _mock_window(
    title: str = "",
    left: int = 0,
    top: int = 0,
    width: int = 1920,
    height: int = 1080,
    hwnd: int = 1,
) -> Any:
    """构造假窗口对象（pygetwindow Win32Window 替身，与 test_webrtc.py 同款）。"""
    w = MagicMock()
    w.title = title
    w.left = left
    w.top = top
    w.width = width
    w.height = height
    w._hWnd = hwnd
    return w


def _make_fake_proc(alive: bool = False, exitcode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.is_alive.return_value = alive
    proc.exitcode = exitcode
    proc.pid = 99999
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.join = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# _find_sc2_window pid_filter 过滤（3 条）
# ---------------------------------------------------------------------------


class TestFindSc2WindowPidFilter:
    """2026-06-12 M1：_find_sc2_window(pid_filter=...) 多实例 PID 过滤。"""

    def test_pid_filter_none_passes_all_matching_windows(self) -> None:
        """pid_filter=None → 不过滤 PID，与现有行为完全一致（标题匹配即返回）。"""
        win = _mock_window(title="StarCraft II", hwnd=100)
        mock_gw = MagicMock()
        mock_gw.getAllWindows.return_value = [win]
        # psutil=None → 走标题匹配回退路径；pid_filter=None → 不调 _window_pid
        with patch.dict("sys.modules", {"pygetwindow": mock_gw, "psutil": None}):
            result = _find_sc2_window(pid_filter=None)
        assert result is win

    def test_pid_filter_match_returns_window(self) -> None:
        """pid_filter=100，窗口 PID=100 → PID 命中，返回该窗口。"""
        win = _mock_window(title="StarCraft II", hwnd=100)
        mock_gw = MagicMock()
        mock_gw.getAllWindows.return_value = [win]
        with (
            patch.dict("sys.modules", {"pygetwindow": mock_gw, "psutil": None}),
            # 模拟 _window_pid 返回 100（与 pid_filter 匹配）
            patch("vibecraft.server.webrtc._window_pid", return_value=100),
        ):
            result = _find_sc2_window(pid_filter=100)
        assert result is win

    def test_pid_filter_no_match_returns_none(self) -> None:
        """pid_filter=999，窗口 PID=100 → PID 不命中，返回 None。"""
        win = _mock_window(title="StarCraft II", hwnd=100)
        mock_gw = MagicMock()
        mock_gw.getAllWindows.return_value = [win]
        with (
            patch.dict("sys.modules", {"pygetwindow": mock_gw, "psutil": None}),
            # 模拟 _window_pid 返回 100（与 pid_filter=999 不匹配）
            patch("vibecraft.server.webrtc._window_pid", return_value=100),
        ):
            result = _find_sc2_window(pid_filter=999)
        assert result is None


# ---------------------------------------------------------------------------
# WebRtcManager.handle_offer 按 player_id supersede（2 条）
# ---------------------------------------------------------------------------


def _setup_mock_pc(MockPc: MagicMock) -> MagicMock:
    """配置 RTCPeerConnection mock 的标准属性（减少测试重复）。"""
    pc = MockPc.return_value
    pc.setRemoteDescription = AsyncMock()
    pc.createAnswer = AsyncMock()
    pc.setLocalDescription = AsyncMock()
    pc.localDescription.sdp = "v=0\r\nm=video 9 UDP\r\nm=audio 9 UDP\r\n"
    pc.localDescription.type = "answer"
    pc.iceGatheringState = "complete"
    return pc


_FAKE_SDP = "v=0\r\nm=video 9 UDP\r\nm=audio 9 UDP\r\n"


class TestWebRtcManagerMultiplayer:
    """2026-06-12 M1：per-player_id WebRTC 生命周期（多玩家互不影响）。"""

    @pytest.mark.asyncio
    async def test_same_player_second_offer_closes_old_pc(self) -> None:
        """同一 player_id 的新 offer 关闭旧 PC，不影响其他玩家的 PC。"""
        mgr = WebRtcManager()
        closed: list[Any] = []

        old_pc = MagicMock()

        async def fake_old_close() -> None:
            closed.append(old_pc)

        old_pc.close = fake_old_close

        # bob 的 PC 预置（不应受影响）
        bob_pc = MagicMock()

        async def bob_close() -> None:
            closed.append(bob_pc)

        bob_pc.close = bob_close

        mgr._pcs["alice"] = old_pc  # type: ignore[assignment]
        mgr._pcs["bob"] = bob_pc  # type: ignore[assignment]

        with (
            patch("vibecraft.server.webrtc.SC2ScreenTrack"),
            patch("vibecraft.server.webrtc.SC2ScreenCapture"),
            patch("vibecraft.server.webrtc.SC2AudioTrack"),
            patch("vibecraft.server.webrtc.SystemAudioGrabber"),
            patch("vibecraft.server.webrtc.RTCPeerConnection") as MockPc,
            patch.object(mgr, "_wait_ice_gathering", new=AsyncMock()),
        ):
            _setup_mock_pc(MockPc)
            # alice 发第二次 offer → 旧 alice PC 应被关
            await mgr.handle_offer(_FAKE_SDP, "offer", player_id="alice")

        # alice 的旧 PC 被关闭
        assert old_pc in closed, "alice 的旧 PC 应被 supersede 关闭"
        # bob 的 PC 未受影响
        assert bob_pc not in closed, "bob 的 PC 不应被 alice 的 offer 影响"
        # alice 的 slot 有新 PC（不再是旧的 old_pc）
        assert "alice" in mgr._pcs
        assert mgr._pcs["alice"] is not old_pc
        # bob 的 slot 仍在
        assert "bob" in mgr._pcs
        # 总共 2 个 PC（alice 的新 + bob 的旧）
        assert len(mgr._pcs) == 2

    @pytest.mark.asyncio
    async def test_different_players_dont_interfere(self) -> None:
        """不同 player_id 的 offer 互不影响：alice 有旧 PC，bob 新 offer 不应关 alice。"""
        mgr = WebRtcManager()
        closed: list[Any] = []

        alice_pc = MagicMock()

        async def alice_close() -> None:
            closed.append(alice_pc)

        alice_pc.close = alice_close
        mgr._pcs["alice"] = alice_pc  # type: ignore[assignment]

        with (
            patch("vibecraft.server.webrtc.SC2ScreenTrack"),
            patch("vibecraft.server.webrtc.SC2ScreenCapture"),
            patch("vibecraft.server.webrtc.SC2AudioTrack"),
            patch("vibecraft.server.webrtc.SystemAudioGrabber"),
            patch("vibecraft.server.webrtc.RTCPeerConnection") as MockPc,
            patch.object(mgr, "_wait_ice_gathering", new=AsyncMock()),
        ):
            _setup_mock_pc(MockPc)
            # bob 发 offer → 不应影响 alice
            await mgr.handle_offer(_FAKE_SDP, "offer", player_id="bob")

        # alice 的 PC 没有被关闭
        assert alice_pc not in closed, "alice 的 PC 不应被 bob 的 offer 影响"
        # alice 和 bob 各有一个 PC
        assert len(mgr._pcs) == 2
        assert "alice" in mgr._pcs
        assert "bob" in mgr._pcs


# ---------------------------------------------------------------------------
# GameProcess.sc2_pid 记账（1 条）
# ---------------------------------------------------------------------------


class TestGameProcessSc2Pid:
    """2026-06-12 M1 T10：sc2_pid 上行消息内部记账，不向外 yield。"""

    async def test_sc2_pid_not_yielded_and_recorded(self) -> None:
        """raw_events 拦截 kind=='sc2_pid'：不向外 yield，但 gp.sc2_pid 被记录。"""
        from vibecraft.server.game_process import GameProcess

        gp = GameProcess()
        q: queue.Queue[dict[str, Any]] = queue.Queue()
        # sc2_pid 消息 + 正常终止消息
        q.put_nowait({"kind": "sc2_pid", "pid": 123})
        q.put_nowait({"sc2": "ended", "bot": "idle"})

        gp._up_q = q  # type: ignore[assignment]
        gp._proc = _make_fake_proc(alive=False, exitcode=0)  # type: ignore[assignment]

        events: list[dict[str, Any]] = []
        async for raw in gp.raw_events():
            events.append(raw)

        # sc2_pid 被记录在属性上
        assert gp.sc2_pid == 123, f"gp.sc2_pid 应为 123，实际为 {gp.sc2_pid}"
        # sc2_pid 消息不应出现在 raw_events 的 yield 流中
        sc2_pid_events = [e for e in events if e.get("kind") == "sc2_pid"]
        assert sc2_pid_events == [], f"sc2_pid 不应向外 yield，实际 events={events}"
        # 正常的 game_status 消息仍然出现
        status_events = [e for e in events if e.get("sc2") == "ended"]
        assert status_events, "ended 状态消息应正常 yield"
