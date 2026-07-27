"""WebRTC 模块单测。

覆盖：
- _find_sc2_window：pygetwindow 缺席 / 无窗口 → None；中英文标题 + 进程名匹配
- _window_rect：尺寸非法（最小化）返回 None
- _make_placeholder_bgra：占位帧形状 + dtype + alpha
- SC2ScreenCapture.grab_frame：
    - SC2 找不到时返回占位帧（形状 + dtype 正确）
    - mss 抛异常时 fallback 到占位帧
- SC2ScreenCapture._locate：无窗口时全量搜索限频；命中缓存不再搜索
- SC2AudioTrack：构造 / stop 幂等；recv 从 grabber 拉 PCM → s16 立体声帧
  （soundcard 版采集已换 SystemAudioGrabber 子进程隔离，采集逻辑测在 test_audio_capture）
- WebRtcManager.close_all：空集合 OK；close_all 清空集合
- _read_http_request：POST 请求解析（method / path / headers / body）
- _http_response：响应字节格式（状态行 / Content-Type / CORS header）
- WebRtcSignalServer.__init__：端口 / host 正确设置

WebRTC 端到端（PeerConnection + 真浏览器 + SC2）不在单测范围内。
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from vibecraft.server.audio_capture import SystemAudioGrabber
from vibecraft.server.webrtc import (
    SC2AudioTrack,
    SC2ScreenCapture,
    WebRtcManager,
    WebRtcSignalServer,
    _find_sc2_window,
    _http_response,
    _make_placeholder_bgra,
    _read_http_request,
    _select_window_rect,
    _summarize_candidates,
    _window_rect,
)

# ---------------------------------------------------------------------------
# _summarize_candidates —— ICE 候选诊断(2026-06-09 视频连不上排查)
# ---------------------------------------------------------------------------


class TestSummarizeCandidates:
    def test_extracts_ip_and_type_deduped(self) -> None:
        """抽 a=candidate 的 'ip typ',去重(同 ip+typ 的多 component 只留一条)。"""
        sdp = (
            "v=0\r\n"
            "a=candidate:1 1 udp 2130706431 192.168.8.4 54321 typ host\r\n"
            "a=candidate:2 1 udp 2130706431 100.94.239.76 54322 typ host\r\n"
            "a=candidate:2 2 udp 2130706431 100.94.239.76 54322 typ host\r\n"
            "a=candidate:3 1 udp 1694498815 1.2.3.4 54323 typ srflx\r\n"
        )
        assert _summarize_candidates(sdp) == [
            "192.168.8.4 host",
            "100.94.239.76 host",
            "1.2.3.4 srflx",
        ]

    def test_no_candidates_returns_empty(self) -> None:
        assert _summarize_candidates("v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n") == []

    def test_none_safe(self) -> None:
        assert _summarize_candidates("") == []


def _mock_window(
    title: str = "",
    left: int = 0,
    top: int = 0,
    width: int = 1920,
    height: int = 1080,
    hwnd: int = 1,
) -> Any:
    """构造一个假窗口对象（pygetwindow Win32Window 替身）。"""
    w = MagicMock()
    w.title = title
    w.left = left
    w.top = top
    w.width = width
    w.height = height
    w._hWnd = hwnd
    return w


# ---------------------------------------------------------------------------
# _find_sc2_window
# ---------------------------------------------------------------------------


class TestFindSc2Window:
    def test_returns_none_when_pygetwindow_unavailable(self) -> None:
        """pygetwindow 不可用时返回 None，不抛异常。"""
        with patch.dict("sys.modules", {"pygetwindow": None}):
            assert _find_sc2_window() is None

    def test_returns_none_when_no_windows(self) -> None:
        """没有任何窗口时返回 None。"""
        mock_gw = MagicMock()
        mock_gw.getAllWindows.return_value = []
        with patch.dict("sys.modules", {"pygetwindow": mock_gw}):
            assert _find_sc2_window() is None

    def test_matches_english_title(self) -> None:
        """英文版客户端标题 StarCraft II 能被识别。"""
        win = _mock_window(title="StarCraft II")
        mock_gw = MagicMock()
        mock_gw.getAllWindows.return_value = [win]
        with patch.dict("sys.modules", {"pygetwindow": mock_gw}):
            assert _find_sc2_window() is win

    def test_matches_chinese_title(self) -> None:
        """中文版客户端标题《星际争霸II》能被识别（这是本次黑屏 bug 根因）。"""
        win = _mock_window(title="《星际争霸II》")
        mock_gw = MagicMock()
        mock_gw.getAllWindows.return_value = [win]
        with patch.dict("sys.modules", {"pygetwindow": mock_gw}):
            assert _find_sc2_window() is win

    def test_skips_zero_size_windows(self) -> None:
        """宽高为 0（最小化）的窗口被过滤，找不到时返回 None。"""
        win = _mock_window(title="StarCraft II", width=0, height=0)
        mock_gw = MagicMock()
        mock_gw.getAllWindows.return_value = [win]
        with patch.dict("sys.modules", {"pygetwindow": mock_gw}):
            assert _find_sc2_window() is None

    def test_matches_by_process_name(self) -> None:
        """标题不含已知子串，但进程名是 SC2_x64.exe → 仍能定位。"""
        win = _mock_window(title="some localized title", hwnd=4242)
        mock_gw = MagicMock()
        mock_gw.getAllWindows.return_value = [win]
        mock_proc = MagicMock()
        mock_proc.name.return_value = "SC2_x64.exe"
        with (
            patch.dict("sys.modules", {"pygetwindow": mock_gw}),
            patch("vibecraft.server.webrtc._window_pid", return_value=4242),
            patch("psutil.Process", return_value=mock_proc),
        ):
            assert _find_sc2_window() is win

    def test_returns_none_on_exception(self) -> None:
        """pygetwindow 抛异常时返回 None，不向上传播。"""
        mock_gw = MagicMock()
        mock_gw.getAllWindows.side_effect = RuntimeError("oops")
        with patch.dict("sys.modules", {"pygetwindow": mock_gw}):
            assert _find_sc2_window() is None


# ---------------------------------------------------------------------------
# _window_rect
# ---------------------------------------------------------------------------


class TestWindowRect:
    def test_returns_rect_for_valid_window(self) -> None:
        win = _mock_window(left=10, top=20, width=800, height=600)
        assert _window_rect(win) == (10, 20, 800, 600)

    def test_returns_none_for_zero_size(self) -> None:
        win = _mock_window(width=0, height=0)
        assert _window_rect(win) is None

    def test_returns_none_on_bad_attrs(self) -> None:
        """属性读取抛异常（窗口已销毁）时返回 None。"""
        win = MagicMock()
        type(win).left = property(lambda self: (_ for _ in ()).throw(RuntimeError()))
        assert _window_rect(win) is None


# ---------------------------------------------------------------------------
# _make_placeholder_bgra
# ---------------------------------------------------------------------------


class TestMakePlaceholderBgra:
    def test_shape_and_dtype(self) -> None:
        arr = _make_placeholder_bgra(640, 360)
        assert arr.shape == (360, 640, 4)
        assert arr.dtype == np.uint8

    def test_alpha_fully_opaque(self) -> None:
        """所有像素 alpha = 255（不透明）。"""
        arr = _make_placeholder_bgra(320, 180)
        assert (arr[:, :, 3] == 255).all()

    def test_background_mostly_black(self) -> None:
        """背景主体为黑色（RGB 三通道 = 0）。"""
        arr = _make_placeholder_bgra(640, 360)
        # 只检角落区域（不含中线）
        corner = arr[:10, :10, :3]
        assert (corner == 0).all()

    def test_custom_size(self) -> None:
        arr = _make_placeholder_bgra(128, 64)
        assert arr.shape == (64, 128, 4)


# ---------------------------------------------------------------------------
# SC2ScreenCapture
# ---------------------------------------------------------------------------


class TestEnsureTopmost:
    """SC2 窗口置顶（防遮挡污染视频）的限频 + 开关逻辑。"""

    def test_ensure_topmost_calls_setwindowpos_then_ratelimits(self) -> None:
        import vibecraft.server.webrtc as wm

        cap = SC2ScreenCapture()
        cap._win = _mock_window(hwnd=4242)
        with (
            patch.object(wm, "_SC2_TOPMOST", True),
            patch.object(wm, "_set_window_topmost", return_value=True) as mock_set,
        ):
            cap._ensure_topmost()  # 首次 → 调用
            cap._ensure_topmost()  # 紧接着 → 限频不调用
            assert mock_set.call_count == 1
            assert mock_set.call_args[0][0] == 4242

    def test_ensure_topmost_gated_off_by_flag(self) -> None:
        import vibecraft.server.webrtc as wm

        cap = SC2ScreenCapture()
        cap._win = _mock_window(hwnd=1)
        with (
            patch.object(wm, "_SC2_TOPMOST", False),
            patch.object(wm, "_set_window_topmost") as mock_set,
        ):
            cap._ensure_topmost()
            mock_set.assert_not_called()


class TestSC2ScreenCapture:
    def test_grab_frame_returns_placeholder_when_no_window(self) -> None:
        """SC2 窗口找不到时返回占位帧，形状正确，不抛异常。"""
        cap = SC2ScreenCapture()
        with patch.object(SC2ScreenCapture, "_locate", return_value=None):
            frame = cap.grab_frame()
        assert frame.ndim == 3
        assert frame.shape[2] == 4  # BGRA
        assert frame.dtype == np.uint8

    def test_grab_frame_returns_placeholder_on_mss_error(self) -> None:
        """mss.grab 抛异常时 fallback 到占位帧，不向上传播。"""
        cap = SC2ScreenCapture()
        mock_rect = (0, 0, 1920, 1080)
        with patch.object(SC2ScreenCapture, "_locate", return_value=mock_rect):
            # mss 模块整体 mock 成会抛 RuntimeError 的版本
            mock_mss_mod = MagicMock()
            mock_mss_mod.mss.return_value.__enter__.side_effect = RuntimeError("mss fail")
            with patch.dict("sys.modules", {"mss": mock_mss_mod}):
                frame = cap.grab_frame()
        assert frame.ndim == 3
        assert frame.shape[2] == 4
        assert frame.dtype == np.uint8

    def test_placeholder_cached(self) -> None:
        """占位帧只创建一次（缓存）。"""
        cap = SC2ScreenCapture()
        with patch.object(SC2ScreenCapture, "_locate", return_value=None):
            f1 = cap.grab_frame()
            f2 = cap.grab_frame()
        assert f1 is f2  # 同一对象

    def test_grab_frame_uses_mss_when_window_found(self) -> None:
        """找到 SC2 窗口时调用 mss.grab，返回正确形状的 numpy 数组。"""
        cap = SC2ScreenCapture()
        mock_rect = (100, 200, 800, 600)

        # 构造一个假的 ScreenShot 对象
        fake_bgra = np.zeros((600, 800, 4), dtype=np.uint8).tobytes()
        mock_img = MagicMock()
        mock_img.bgra = fake_bgra
        mock_img.height = 600
        mock_img.width = 800

        mock_sct = MagicMock()
        mock_sct.grab.return_value = mock_img
        mock_mss_instance = MagicMock()
        mock_mss_instance.__enter__ = MagicMock(return_value=mock_sct)
        mock_mss_instance.__exit__ = MagicMock(return_value=False)

        mock_mss_mod = MagicMock()
        mock_mss_mod.mss.return_value = mock_mss_instance

        with (
            patch.object(SC2ScreenCapture, "_locate", return_value=mock_rect),
            patch.dict("sys.modules", {"mss": mock_mss_mod}),
        ):
            frame = cap.grab_frame()

        assert frame.shape == (600, 800, 4)
        assert frame.dtype == np.uint8
        # 确认用了正确的 monitor dict
        mock_sct.grab.assert_called_once_with(
            {"left": 100, "top": 200, "width": 800, "height": 600}
        )


# ---------------------------------------------------------------------------
# SC2ScreenCapture._locate（缓存 + 限频）
# ---------------------------------------------------------------------------


class TestLocate:
    def test_search_throttled_when_no_window(self) -> None:
        """无窗口时全量搜索被限频：区间内第二次调用不再搜索。"""
        cap = SC2ScreenCapture()
        with patch("vibecraft.server.webrtc._find_sc2_window", return_value=None) as mock_find:
            assert cap._locate() is None
            assert cap._locate() is None  # 仍在 _SEARCH_INTERVAL 区间内
        assert mock_find.call_count == 1

    def test_cached_window_avoids_research(self) -> None:
        """命中缓存且窗口存活时直接读坐标，不再全量搜索。"""
        cap = SC2ScreenCapture()
        win = _mock_window(left=5, top=6, width=800, height=600)
        with (
            patch("vibecraft.server.webrtc._find_sc2_window", return_value=win) as mock_find,
            patch("vibecraft.server.webrtc._window_alive", return_value=True),
        ):
            assert cap._locate() == (5, 6, 800, 600)
            assert cap._locate() == (5, 6, 800, 600)  # 第二次走缓存
        assert mock_find.call_count == 1


# ---------------------------------------------------------------------------
# SC2AudioTrack（采集换 SystemAudioGrabber 子进程，本处只测轨的取帧/停止）
# ---------------------------------------------------------------------------


def _silent_grabber() -> MagicMock:
    """假 grabber：read(n) → (n,2) int16 静音；start/stop 可断言。"""
    g = MagicMock(spec=SystemAudioGrabber)
    g.restart_count = 0
    g.read.side_effect = lambda n: np.zeros((n, 2), dtype=np.int16)
    return g


class TestSc2AudioTrack:
    @pytest.mark.asyncio
    async def test_construct_and_stop_idempotent(self) -> None:
        """构造正常；stop 不抛异常且可重复调用；stop 会停 grabber。"""
        grabber = _silent_grabber()
        track = SC2AudioTrack(grabber)
        assert track.kind == "audio"
        track.stop()
        track.stop()  # 二次 stop 幂等
        grabber.stop.assert_called()

    @pytest.mark.asyncio
    async def test_recv_produces_frame_and_logs_diag(self) -> None:
        """recv() 从 grabber 拉 PCM → 合法 s16 立体声帧；首帧记诊断日志 silent=True。"""
        grabber = _silent_grabber()
        track = SC2AudioTrack(grabber)
        with patch.object(track, "_log") as mock_log:
            frame = await track.recv()
        track.stop()
        assert frame.samples == 960
        assert frame.format.name == "s16"
        assert frame.layout.name == "stereo"
        assert frame.sample_rate == 48000
        grabber.read.assert_called_with(960)
        # 首帧应记一条 sc2_audio_frame 诊断日志，标记 silent=True
        mock_log.info.assert_called_once()
        args, kwargs = mock_log.info.call_args
        assert args[0] == "sc2_audio_frame"
        assert kwargs["silent"] is True

    @pytest.mark.asyncio
    async def test_recv_passes_through_nonzero_pcm(self) -> None:
        """grabber 给出非零 PCM 时，帧不静音（peak 反映出来）。"""
        grabber = _silent_grabber()
        grabber.read.side_effect = lambda n: np.full((n, 2), 100, dtype=np.int16)
        track = SC2AudioTrack(grabber)
        with patch.object(track, "_log") as mock_log:
            frame = await track.recv()
        track.stop()
        assert frame.samples == 960
        _, kwargs = mock_log.info.call_args
        assert kwargs["silent"] is False
        assert kwargs["peak"] == 100

    @pytest.mark.asyncio
    async def test_recv_applies_audio_gain(self, monkeypatch) -> None:
        """音量衰减（_AUDIO_GAIN）作用于**送出的帧**；诊断 peak 仍是采集原值（不受 gain）。"""
        import vibecraft.server.webrtc as webrtc_mod

        monkeypatch.setattr(webrtc_mod, "_AUDIO_GAIN", 0.5)
        grabber = _silent_grabber()
        grabber.read.side_effect = lambda n: np.full((n, 2), 100, dtype=np.int16)
        track = SC2AudioTrack(grabber)
        with patch.object(track, "_log") as mock_log:
            frame = await track.recv()
        track.stop()
        # 诊断 peak = 采集原值 100（衰减前，判采集有无声音用）
        _, kwargs = mock_log.info.call_args
        assert kwargs["peak"] == 100
        # 送出帧被衰减：100 * 0.5 = 50
        out = frame.to_ndarray()
        assert int(np.max(np.abs(out))) == 50


# ---------------------------------------------------------------------------
# WebRtcManager
# ---------------------------------------------------------------------------


class TestWebRtcManager:
    @pytest.mark.asyncio
    async def test_close_all_on_empty_set(self) -> None:
        """空集合时 close_all 不报错。"""
        mgr = WebRtcManager()
        await mgr.close_all()  # 不应抛异常
        assert len(mgr._pcs) == 0

    @pytest.mark.asyncio
    async def test_close_all_clears_pcs(self) -> None:
        """close_all 关闭并清除所有 PeerConnection。"""
        mgr = WebRtcManager()
        mock_pc = MagicMock()

        async def fake_close() -> None:
            pass

        mock_pc.close = fake_close
        # 2026-06-12 M1：_pcs 改 dict[player_id, PC]
        mgr._pcs["player1"] = mock_pc  # type: ignore[assignment]
        await mgr.close_all()
        assert len(mgr._pcs) == 0

    @pytest.mark.asyncio
    async def test_new_offer_supersedes_old_pcs(self) -> None:
        """同一 player_id 的新 offer 关掉旧 PeerConnection（单客户端刷新场景，防泄漏）。

        2026-06-12 M1：_pcs 改 dict[player_id, PC]，新 offer 只 supersede 同 player_id
        的旧 PC，不影响其他玩家。此测试用默认 player_id="default" 验证旧有行为保持不变。
        """
        mgr = WebRtcManager()

        # 预置一个"旧" PC：模拟上一次连接遗留、卡在 connecting 没被清理
        old_pc = MagicMock()
        closed: list[Any] = []

        async def fake_old_close() -> None:
            closed.append(old_pc)

        old_pc.close = fake_old_close
        # 2026-06-12 M1：_pcs 改 dict，以 "default" 为 key 模拟已有旧 PC
        mgr._pcs["default"] = old_pc  # type: ignore[assignment]

        # handle_offer 全程要真 aiortc 信令，把重型构造 mock 掉，
        # 只验证"旧 PC 被 supersede"这一行为
        with (
            patch("vibecraft.server.webrtc.SC2ScreenTrack"),
            patch("vibecraft.server.webrtc.SC2ScreenCapture"),
            patch("vibecraft.server.webrtc.SC2AudioTrack"),
            patch("vibecraft.server.webrtc.SystemAudioGrabber") as MockGrabber,
            patch("vibecraft.server.webrtc.RTCPeerConnection") as MockPc,
            patch.object(mgr, "_wait_ice_gathering", new=AsyncMock()),
        ):
            new_pc = MockPc.return_value
            new_pc.setRemoteDescription = AsyncMock()
            new_pc.createAnswer = AsyncMock()
            new_pc.setLocalDescription = AsyncMock()
            new_pc.localDescription.sdp = "v=0\r\nm=video 9 UDP\r\nm=audio 9 UDP\r\n"
            new_pc.localDescription.type = "answer"
            new_pc.iceGatheringState = "complete"

            await mgr.handle_offer("v=0\r\nm=video 9 UDP\r\nm=audio 9 UDP\r\n", "offer")

        # 旧 PC 被关；"default" slot 被新 PC 占用
        assert old_pc in closed
        # 2026-06-12 M1：dict 版断言——旧 PC 不再是 "default" slot 的值
        assert mgr._pcs.get("default") is not old_pc
        assert len(mgr._pcs) == 1
        # 音频默认开（2026-06-13 #516 per-PID 修复后）：每轨独享 grabber 创建并
        # start，addTrack 两次（视频 + 音频）
        MockGrabber.return_value.start.assert_called_once()
        assert new_pc.addTrack.call_count == 2


# ---------------------------------------------------------------------------
# _read_http_request
# ---------------------------------------------------------------------------


class TestReadHttpRequest:
    @pytest.mark.asyncio
    async def test_parse_post_request_with_body(self) -> None:
        """标准 POST 请求（含 Content-Length + body）。"""
        body_bytes = b'{"sdp": "test", "type": "offer"}'
        raw = (
            f"POST /webrtc/offer HTTP/1.1\r\n"
            f"Host: localhost:8081\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body_bytes)}\r\n"
            f"\r\n"
        ).encode() + body_bytes

        reader = asyncio.StreamReader()
        reader.feed_data(raw)
        reader.feed_eof()

        method, path, headers, body = await _read_http_request(reader)

        assert method == "POST"
        assert path == "/webrtc/offer"
        assert headers["content-type"] == "application/json"
        assert headers["content-length"] == str(len(body_bytes))
        assert body == body_bytes

    @pytest.mark.asyncio
    async def test_parse_options_request(self) -> None:
        """OPTIONS preflight 请求（无 body）。"""
        raw = b"OPTIONS /webrtc/offer HTTP/1.1\r\nOrigin: http://localhost:5173\r\n\r\n"

        reader = asyncio.StreamReader()
        reader.feed_data(raw)
        reader.feed_eof()

        method, path, _headers, body = await _read_http_request(reader)

        assert method == "OPTIONS"
        assert path == "/webrtc/offer"
        assert body == b""

    @pytest.mark.asyncio
    async def test_parse_get_request(self) -> None:
        """GET 请求（无 Content-Length → body 为空）。"""
        raw = b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n"

        reader = asyncio.StreamReader()
        reader.feed_data(raw)
        reader.feed_eof()

        method, path, _headers, body = await _read_http_request(reader)

        assert method == "GET"
        assert path == "/health"
        assert body == b""


# ---------------------------------------------------------------------------
# _http_response
# ---------------------------------------------------------------------------


class TestHttpResponse:
    def test_contains_status_line(self) -> None:
        resp = _http_response(200, "OK", "application/json", b"{}")
        assert b"HTTP/1.1 200 OK" in resp

    def test_contains_cors_header(self) -> None:
        resp = _http_response(200, "OK", "application/json", b"{}")
        assert b"Access-Control-Allow-Origin: *" in resp

    def test_contains_content_type(self) -> None:
        resp = _http_response(200, "OK", "application/json", b"{}")
        assert b"Content-Type: application/json" in resp

    def test_contains_body(self) -> None:
        body = b'{"sdp": "answer"}'
        resp = _http_response(200, "OK", "application/json", body)
        assert body in resp

    def test_404_status(self) -> None:
        resp = _http_response(404, "Not Found", "text/plain", b"Not Found")
        assert b"HTTP/1.1 404 Not Found" in resp


# ---------------------------------------------------------------------------
# WebRtcSignalServer
# ---------------------------------------------------------------------------


class TestWebRtcSignalServer:
    def test_port_and_host_stored(self) -> None:
        mgr = WebRtcManager()
        srv = WebRtcSignalServer(manager=mgr, port=8081, host="0.0.0.0")
        assert srv._port == 8081
        assert srv._host == "0.0.0.0"

    def test_default_host(self) -> None:
        mgr = WebRtcManager()
        srv = WebRtcSignalServer(manager=mgr, port=9000)
        assert srv._host == "0.0.0.0"


# ---------------------------------------------------------------------------
# _select_window_rect（客户区优先 + 回退；2026-06-12 #4）
# ---------------------------------------------------------------------------


class TestSelectWindowRect:
    def test_client_rect_used_when_available(self) -> None:
        """hwnd 可用且 _get_hwnd_client_rect 成功 → 使用客户区矩形，忽略外接矩形。

        模拟场景：窗口外接矩形含 31px 标题栏（top=0→31），
        客户区从屏幕 (8, 31) 起、宽 1904 高 1041（去掉标题栏和边框）。
        """
        win = _mock_window(left=0, top=0, width=1920, height=1080, hwnd=42)
        client_rect = (8, 31, 1904, 1041)
        with patch("vibecraft.server.webrtc._get_hwnd_client_rect", return_value=client_rect):
            result = _select_window_rect(win)
        assert result == client_rect

    def test_falls_back_to_outer_rect_when_client_rect_fails(self) -> None:
        """_get_hwnd_client_rect 返回 None（API 失败 / 最小化） → 回退 pygetwindow 外接矩形。

        回退后与改造前行为完全一致，不影响已有截屏逻辑。
        """
        win = _mock_window(left=10, top=20, width=1920, height=1080, hwnd=42)
        with patch("vibecraft.server.webrtc._get_hwnd_client_rect", return_value=None):
            result = _select_window_rect(win)
        # 回退到外接矩形：(left, top, width, height)
        assert result == (10, 20, 1920, 1080)


# ---------------------------------------------------------------------------
# 音频开关（2026-06-13 #516：默认开，VIBECRAFT_WEBRTC_AUDIO=0 可关）
# ---------------------------------------------------------------------------


class TestAudioEnabledSwitch:
    @pytest.mark.asyncio
    async def test_handle_offer_no_audio_track_when_disabled(self) -> None:
        """_AUDIO_ENABLED=False（VIBECRAFT_WEBRTC_AUDIO=0）时，handle_offer 不创建
        音频轨：SystemAudioGrabber 不被实例化，PC.addTrack 只调一次（仅视频轨）。
        answer SDP 照常返回，连接不受影响（只是无音频 m-line）。
        """
        mgr = WebRtcManager()
        with (
            patch("vibecraft.server.webrtc._AUDIO_ENABLED", False),
            patch("vibecraft.server.webrtc.SC2ScreenTrack"),
            patch("vibecraft.server.webrtc.SC2ScreenCapture"),
            patch("vibecraft.server.webrtc.SC2AudioTrack") as MockAudioTrack,
            patch("vibecraft.server.webrtc.SystemAudioGrabber") as MockGrabber,
            patch("vibecraft.server.webrtc.RTCPeerConnection") as MockPc,
            patch.object(mgr, "_wait_ice_gathering", new=AsyncMock()),
        ):
            pc = MockPc.return_value
            pc.setRemoteDescription = AsyncMock()
            pc.createAnswer = AsyncMock()
            pc.setLocalDescription = AsyncMock()
            pc.localDescription.sdp = "v=0\r\nm=video 9 UDP\r\n"
            pc.localDescription.type = "answer"
            pc.iceGatheringState = "complete"

            await mgr.handle_offer("v=0\r\nm=video 9 UDP\r\n", "offer")

        # 音频关闭：grabber 和 AudioTrack 均不被创建
        MockGrabber.assert_not_called()
        MockAudioTrack.assert_not_called()
        # addTrack 只调一次（视频轨）
        assert pc.addTrack.call_count == 1

    @pytest.mark.asyncio
    async def test_handle_offer_audio_grabber_per_pid(self) -> None:
        """_AUDIO_ENABLED=True（默认）：每个 offer 创建**独享** grabber，
        并把 sc2_pid 透传给它（process loopback 按局分音，2026-06-13 #516）。
        """
        mgr = WebRtcManager()
        with (
            patch("vibecraft.server.webrtc.SC2ScreenTrack"),
            patch("vibecraft.server.webrtc.SC2ScreenCapture"),
            patch("vibecraft.server.webrtc.SC2AudioTrack") as MockAudioTrack,
            patch("vibecraft.server.webrtc.SystemAudioGrabber") as MockGrabber,
            patch("vibecraft.server.webrtc.RTCPeerConnection") as MockPc,
            patch.object(mgr, "_wait_ice_gathering", new=AsyncMock()),
        ):
            pc = MockPc.return_value
            pc.setRemoteDescription = AsyncMock()
            pc.createAnswer = AsyncMock()
            pc.setLocalDescription = AsyncMock()
            pc.localDescription.sdp = "v=0\r\nm=video 9 UDP\r\nm=audio 9 UDP\r\n"
            pc.localDescription.type = "answer"
            pc.iceGatheringState = "complete"

            await mgr.handle_offer(
                "v=0\r\nm=video 9 UDP\r\nm=audio 9 UDP\r\n",
                "offer",
                player_id="alice",
                sc2_pid=4242,
            )

        # grabber 按 sc2_pid 创建（per-PID process loopback）并 start
        MockGrabber.assert_called_once_with(pid=4242)
        MockGrabber.return_value.start.assert_called_once()
        # 音频轨创建，release_fn = 该 grabber 自己的 stop（独享，无共享池）
        MockAudioTrack.assert_called_once()
        _, kwargs = MockAudioTrack.call_args
        assert kwargs["release_fn"] is MockGrabber.return_value.stop
        # addTrack 两次（视频 + 音频）
        assert pc.addTrack.call_count == 2
