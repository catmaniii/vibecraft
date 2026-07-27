"""WebRTC 直播模块：SC2 画面 + 声音 → aiortc 媒体轨 → 浏览器 <video>。

设计：
- SC2ScreenCapture  : 用 mss 抓取 SC2 窗口区域；找不到窗口时产占位帧（黑底灰线）。
- SC2ScreenTrack    : aiortc VideoStreamTrack，每帧调 capture.grab_frame()。
- SystemAudioGrabber: 隔离子进程抓系统输出（SC2 游戏声音），见 audio_capture.py /
                      audio_grab.py。原 soundcard 在 native 层崩整个 server，改方案 A+2
                      （PyAudioWPatch + 子进程隔离，2026-06-03 用户决策）。
- SC2AudioTrack     : aiortc 音频轨，每 20ms 从 grabber 拉一个 48kHz 立体声帧（编 Opus）。
- WebRtcManager     : 维护活跃 PeerConnection 集合，负责生命周期 + 清理。
- WebRtcSignalServer: asyncio.start_server 实现的轻量 HTTP 服务，监听独立端口。
                      POST /webrtc/offer → 交换 SDP → 返回 answer JSON。

信令端点：POST /webrtc/offer（监听在 webrtc_port，默认 port + 1）
  请求体 JSON: {"sdp": "<sdp string>", "type": "offer"}
  响应体 JSON: {"sdp": "<sdp string>", "type": "answer"}

ADR 0013：为什么用独立端口而非共享 websockets process_request？
  websockets process_request 在 HTTP 握手阶段调用，此时 socket 里只有 HTTP
  请求行 + 头，POST body 尚未读取。读 body 需要访问底层 transport，与
  websockets 内部实现深度耦合，升级风险高。独立端口 (port+1) 把信令 I/O
  与 WS/静态文件 I/O 彻底分离，代码更清晰，testable。

ICE：STUN stun.l.google.com:19302（本地 / 远程都能尝试连通）。

按需采集：PeerConnection 建立后才开始截屏；
         连接 closed/failed/disconnected → 服务端停采集，零开销。
前端折叠直播窗 → pc.close() → 服务端 connectionstatechange → 停采集。
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import json
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from fractions import Fraction
from typing import Any

import av
import numpy as np
import numpy.typing as npt
import structlog
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack, VideoStreamTrack

from vibecraft.server.audio_capture import SystemAudioGrabber
from vibecraft.server.turn_config import TurnConfig

logger = structlog.get_logger(__name__)

# STUN server URL（保留备用，当前**不启用**，见下）。
_STUN_URL = "stun:stun.l.google.com:19302"
# 2026-06-03 连接慢根因：配了 STUN 后 aiortc 的 ICE gathering 要等 STUN reflexive
# 候选返回才算 complete；server 这侧到 stun.l.google.com 的 UDP 往往不通 → 每次
# gathering 都卡满 _wait_ice_gathering 的 5s 超时才发 answer（offer→answer 恒定 5s，
# 日志实证）→ 音视频整体延后 ~5s 才连上。本项目玩家恒走 Tailscale / 同 wifi（host
# 候选 100.94.x / 192.168.x，瞬间 gather，不需要 STUN 打洞）→ 干脆不配 STUN：
# gathering 只收 host 候选 → 立即 complete → answer 秒发，连接快 ~5s。
# 未来若要支持「无 Tailscale 的纯外网」再加回 STUN/TURN（需要 TURN 中继才真能连）。
_ICE_SERVERS: list[RTCIceServer] = []

# ICE 候选诊断:从 SDP 抽 a=candidate 行的 "ip typ"(host/srflx/relay),去重。
# 2026-06-09 视频连不上排查:本地(answer)候选应含 Tailnet 100.94.x;远端(offer/手机)
# 候选若**没有** 100.94.x → 手机不在 tailnet(funnel 只代理 WS、不代理 media)→ ICE 无可达
# 候选必 fail。日志打出两侧候选,下次失败一眼定位"是不是手机掉了 tailnet"。
_CAND_RE = re.compile(r"^a=candidate:\S+ \d+ \S+ \d+ (\S+) \d+ typ (\S+)", re.MULTILINE)


def _summarize_candidates(sdp: str) -> list[str]:
    """从 SDP 抽 ICE 候选的 "ip typ" 列表(去重),给诊断日志用。"""
    out: list[str] = []
    seen: set[str] = set()
    for ip, typ in _CAND_RE.findall(sdp or ""):
        key = f"{ip} {typ}"
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


# 目标帧率。默认 30fps（流畅优先）。早先压满 event loop 的根因不是帧率，而是
# 「整窗大帧(1100+px) + 截屏抢共享默认 executor」—— 已靠降采样 + 专属 executor 解决。
#
# 2026-06-06 用户:网络差时画面太糊。aiortc 坏网下保分辨率、压码率,30fps 把有限
# 码率摊薄 → 每帧比特少 → 糊。**画质优先模式**(VIBECRAFT_VIDEO_QUALITY=1):降帧率,
# 同带宽下每帧分到 2× 比特 → 明显变清晰,代价是画面更卡(用户已接受)。
# VIBECRAFT_VIDEO_FPS 显式覆盖始终优先。
_QUALITY_MODE = os.environ.get("VIBECRAFT_VIDEO_QUALITY", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
_TARGET_FPS = int(os.environ.get("VIBECRAFT_VIDEO_FPS", "15" if _QUALITY_MODE else "30"))
_TARGET_FPS = max(5, min(60, _TARGET_FPS))  # 夹到合理区间
# RTP 视频时钟（aiortc 标准 90kHz）；每帧步长 = _VIDEO_CLOCK_RATE / _TARGET_FPS
_VIDEO_CLOCK_RATE = 90000

# 画质优先:抬高 aiortc 编码器码率上限/默认值,中等及以上带宽时用足 → 更清晰。
# 不强抬 MIN_BITRATE —— 带宽真不足时强发高码率会丢包冻结,比糊更糟;坏网清晰靠低帧率。
if _QUALITY_MODE:
    with contextlib.suppress(Exception):
        import aiortc.codecs.vpx as _vpx

        _vpx.DEFAULT_BITRATE = max(_vpx.DEFAULT_BITRATE, 1_200_000)
        _vpx.MAX_BITRATE = max(_vpx.MAX_BITRATE, 2_500_000)
    with contextlib.suppress(Exception):
        import aiortc.codecs.h264 as _h264

        _h264.DEFAULT_BITRATE = max(_h264.DEFAULT_BITRATE, 1_500_000)
        _h264.MAX_BITRATE = max(_h264.MAX_BITRATE, 3_500_000)
    logger.info("video_quality_mode_on", target_fps=_TARGET_FPS)

# 视频帧降采样上限宽度：SC2 窗口常 1100+px，整帧编码太重；
# 超过此宽度按 2× 步长降采样（手机屏看不出差别，编码量降 ~4×）。
_MAX_VIDEO_WIDTH = 960

# 占位帧尺寸（SC2 窗口找不到时）
_PLACEHOLDER_WIDTH = 640
_PLACEHOLDER_HEIGHT = 360

# SC2 客户端进程名 —— 语言无关，最可靠的定位依据
_SC2_PROCESS_NAMES = frozenset({"SC2_x64.exe", "SC2_x64", "SC2.exe", "SC2"})
# 窗口标题子串兜底（进程名拿不到时用）；覆盖中 / 英 / 韩文客户端
_SC2_TITLE_HINTS = ("StarCraft II", "星际争霸", "스타크래프트")
# 无窗口时全量搜索的限频间隔（秒）—— 避免每帧扫描所有窗口
_SEARCH_INTERVAL = 1.0

# SC2 窗口置顶：防止其它窗口（报错框等）盖住 SC2 → mss 按屏幕矩形抓屏会把遮挡物抓进视频流。
# PC 是"只当显示器、不交互"，置顶把遮挡窗口压到 SC2 后面无副作用。SWP_NOACTIVATE 不抢焦点
# （不影响 per-window 音频抓取 / 输入）。周期重断言（其它 topmost 窗口 / 焦点变化会打乱 z 序）。
# 默认开；VIBECRAFT_SC2_TOPMOST=0 关闭。
_SC2_TOPMOST = os.environ.get("VIBECRAFT_SC2_TOPMOST", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
_TOPMOST_INTERVAL = 2.0  # 重断言置顶的最小间隔（秒）

# 音频（系统回环采集 → aiortc Opus 编码）
_AUDIO_SAMPLE_RATE = 48000  # Opus 原生采样率
_AUDIO_CHANNELS = 2  # 立体声
_AUDIO_FRAME_SAMPLES = 960  # 每帧 20ms @ 48kHz —— Opus 原生帧长
# 音频增益（VIBECRAFT_AUDIO_GAIN）：SC2 推流音量整体衰减，默认 0.5（-6dB，用户反馈太吵）。
# 1.0=原样，<1 更小，0=静音。recv() 里对 int16 采样乘此系数并 clip 回 int16。
_AUDIO_GAIN = max(0.0, float(os.environ.get("VIBECRAFT_AUDIO_GAIN", "0.5")))

# 2026-06-13 任务 #516 修复后默认重新开启（VIBECRAFT_WEBRTC_AUDIO=0 可关）。
# 历史：2026-06-12 多人局破音默认关过 —— 根因是共享 SystemAudioGrabber 被多个
# SC2AudioTrack 同时消费、各拿一半采样帧。现每轨独享 grabber（按 sc2_pid
# process loopback 分局采集），破音结构上消失，多人局两手机各听各的。
_AUDIO_ENABLED: bool = os.environ.get("VIBECRAFT_WEBRTC_AUDIO", "1") == "1"


# ---------------------------------------------------------------------------
# SC2 窗口定位
# ---------------------------------------------------------------------------


def _window_pid(hwnd: int) -> int | None:
    """取窗口所属进程 PID（Windows 专用），失败返回 None。"""
    try:
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        return int(pid.value) or None
    except Exception:
        return None


def _window_alive(win: Any) -> bool:
    """缓存的窗口对象是否仍对应一个有效窗口（Win32 IsWindow）。"""
    try:
        hwnd = getattr(win, "_hWnd", None)
        if hwnd is None:
            return False
        return bool(ctypes.windll.user32.IsWindow(wintypes.HWND(hwnd)))
    except Exception:
        return False


# SetWindowPos: HWND_TOPMOST=-1; SWP_NOSIZE=0x1 NOMOVE=0x2 NOACTIVATE=0x10
_HWND_TOPMOST = -1
_SWP_TOPMOST_FLAGS = 0x0001 | 0x0002 | 0x0010


def _set_window_topmost(hwnd: int) -> bool:
    """把窗口置顶（z 序最上，不移动/不缩放/不抢焦点）。失败返回 False。"""
    try:
        return bool(
            ctypes.windll.user32.SetWindowPos(
                wintypes.HWND(hwnd), wintypes.HWND(_HWND_TOPMOST), 0, 0, 0, 0, _SWP_TOPMOST_FLAGS
            )
        )
    except Exception:
        return False


def _window_rect(win: Any) -> tuple[int, int, int, int] | None:
    """读窗口 (left, top, width, height)；尺寸非法（最小化等）返回 None。"""
    try:
        left, top = int(win.left), int(win.top)
        width, height = int(win.width), int(win.height)
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return left, top, width, height


# ---------------------------------------------------------------------------
# DPI 感知声明（一次性，幂等；2026-06-12 #4 客户区抓屏）
# ---------------------------------------------------------------------------

_DPI_AWARE_DONE: bool = False


def _ensure_dpi_aware() -> None:
    """进程级 DPI 感知声明（System-aware DPI mode = 2）。幂等，suppress 全部异常。

    webrtc 跑在 server 父进程；match.py 的 _detect_screen_size 可能已调，
    但调用时序不保证 —— 保险起见在首次取客户区矩形前再调一次。
    重复调用 shcore 会返回 E_ACCESSDENIED，已在 contextlib.suppress 里静默。
    """
    global _DPI_AWARE_DONE
    if _DPI_AWARE_DONE:
        return
    _DPI_AWARE_DONE = True
    with contextlib.suppress(Exception):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)


# ---------------------------------------------------------------------------
# 客户区矩形（去标题栏 / 边框 / 空白；2026-06-12 #4）
# ---------------------------------------------------------------------------


def _get_hwnd_client_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """用 Win32 GetClientRect + ClientToScreen 取窗口客户区的屏幕矩形。

    GetClientRect 返回客户区尺寸（left/top 恒为 0；right = 宽，bottom = 高）。
    ClientToScreen 把客户区左上角 POINT(0, 0) 转换成屏幕坐标。

    返回 (screen_left, screen_top, width, height)；
    API 失败或客户区尺寸为 0（窗口最小化 / 特殊状态）时返回 None，
    调用方负责回退到外接矩形逻辑。
    """
    try:
        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        width = rect.right - rect.left  # GetClientRect 的 left / top 恒为 0
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            # 最小化或客户区为 0 → 通知调用方回退外接矩形
            return None
        pt = wintypes.POINT()
        pt.x = 0
        pt.y = 0
        if not ctypes.windll.user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt)):
            return None
        return int(pt.x), int(pt.y), width, height
    except Exception:
        return None


def _select_window_rect(win: Any) -> tuple[int, int, int, int] | None:
    """取截屏用矩形：优先客户区，失败时回退外接矩形。

    客户区 = 去掉标题栏 / 边框 / 空白后的实际游戏画面区域（Win32 GetClientRect）。
    回退条件：hwnd 不可用 / GetClientRect 失败 / 客户区尺寸为 0（最小化等）。

    纯函数（仅依赖 win 属性 + _get_hwnd_client_rect），便于单测。
    首次调用时触发 _ensure_dpi_aware()（幂等）。
    """
    _ensure_dpi_aware()
    hwnd = getattr(win, "_hWnd", None)
    if hwnd is not None:
        client = _get_hwnd_client_rect(hwnd)
        if client is not None:
            return client
    # 回退：pygetwindow 外接矩形（含标题栏 / 边框）
    return _window_rect(win)


def _find_sc2_window(pid_filter: int | None = None) -> Any:
    """枚举顶层窗口，定位 StarCraft II 客户端窗口对象，找不到返回 None。

    中文版客户端窗口标题是《星际争霸II》、英文版是 StarCraft II —— 故优先
    按进程名匹配（SC2_x64.exe，语言无关），失败再回退到多语言标题子串匹配。
    Windows 专用；非 Windows / 出错时返回 None，上层产占位帧不崩溃。

    pid_filter: 非 None 时只返回属于该 PID 的窗口（2026-06-12 M1 多实例分流）。
                None = 老行为（不过滤 PID，首个匹配窗口即返回）。
    """
    try:
        import pygetwindow as gw  # type: ignore[import-untyped]

        wins = [w for w in gw.getAllWindows() if _window_rect(w) is not None]
    except Exception:
        return None

    # 1) 进程名匹配 —— 不受客户端语言影响，首选
    try:
        import psutil  # type: ignore[import-untyped]

        for w in wins:
            hwnd = getattr(w, "_hWnd", None)
            if hwnd is None:
                continue
            pid = _window_pid(hwnd)
            if pid is None:
                continue
            # 2026-06-12 M1：pid_filter 非 None 时只留 PID 匹配的窗口
            if pid_filter is not None and pid != pid_filter:
                continue
            try:
                pname = psutil.Process(pid).name()
            except Exception:
                continue
            if pname in _SC2_PROCESS_NAMES:
                return w
    except Exception:
        pass

    # 2) 回退：多语言标题子串匹配
    for w in wins:
        if any(hint in (w.title or "") for hint in _SC2_TITLE_HINTS):
            # 2026-06-12 M1：标题匹配路径同样过 pid_filter
            if pid_filter is not None:
                hwnd = getattr(w, "_hWnd", None)
                if hwnd is None:
                    continue
                pid = _window_pid(hwnd)
                if pid != pid_filter:
                    continue
            return w

    return None


# ---------------------------------------------------------------------------
# 占位帧（SC2 窗口不存在时用）
# ---------------------------------------------------------------------------


def _make_placeholder_bgra(
    width: int = _PLACEHOLDER_WIDTH,
    height: int = _PLACEHOLDER_HEIGHT,
) -> npt.NDArray[np.uint8]:
    """生成黑底灰中线占位帧，格式 HxWx4 BGRA uint8。

    不依赖 Pillow；用 numpy 直接操作像素。
    中线让浏览器 <video> 不全黑（全黑时容易误以为流没在传）。
    """
    arr = np.zeros((height, width, 4), dtype=np.uint8)
    arr[:, :, 3] = 255  # alpha = 完全不透明

    # 水平中线（灰色）— 视觉提示"直播已连，等待 SC2 启动"
    cy = height // 2
    arr[cy - 1 : cy + 2, :, :3] = 100  # BGR gray
    arr[cy - 1 : cy + 2, :, 3] = 255

    return arr


# ---------------------------------------------------------------------------
# 截屏器
# ---------------------------------------------------------------------------


class SC2ScreenCapture:
    """SC2 窗口截屏器。

    grab_frame() 每次返回最新的 HxWx4 BGRA numpy 数组；SC2 窗口不存在时
    返回占位帧（黑底灰中线），不抛异常、不崩溃。

    缓存已定位的窗口对象：窗口在时每帧只读一次坐标（便宜，且窗口移动 /
    缩放后坐标自动跟随）；窗口消失后按 _SEARCH_INTERVAL 限频重新全量
    搜索，避免每帧扫描所有窗口。在 aiortc recv() 的 executor 线程里同步调用。
    """

    def __init__(self, pid_filter: int | None = None) -> None:
        self._placeholder: npt.NDArray[np.uint8] | None = None
        self._win: Any = None
        self._last_search: float = 0.0
        self._last_topmost: float = 0.0
        self._on_placeholder: bool = True
        # 2026-06-12 M1：按 SC2 进程 PID 过滤窗口（多实例各抓自己的窗口）。
        # None = 老行为（不过滤，首个匹配窗口即抓）。
        self._pid_filter = pid_filter

    def grab_frame(self) -> npt.NDArray[np.uint8]:
        """抓取 SC2 窗口当前帧（BGRA HxWx4 uint8）。"""
        rect = self._locate()
        if rect is None:
            if not self._on_placeholder:
                logger.warning("sc2_window_lost")
                self._on_placeholder = True
            return self._get_placeholder()

        left, top, width, height = rect
        try:
            import mss

            with mss.mss() as sct:
                mon = {"left": left, "top": top, "width": width, "height": height}
                img = sct.grab(mon)
                # ScreenShot.bgra 是 bytes，转 numpy
                arr: npt.NDArray[np.uint8] = np.frombuffer(img.bgra, dtype=np.uint8)
                frame = arr.reshape((img.height, img.width, 4))
        except Exception:
            logger.warning("sc2_capture_grab_failed")
            self._on_placeholder = True
            return self._get_placeholder()

        # 大帧降采样 + 保证偶数尺寸（H.264/VP8 要求）：1100+px 宽的全帧
        # 高帧率编码会压满 server event loop，整站卡死。
        if frame.shape[1] > _MAX_VIDEO_WIDTH:
            small = frame[::2, ::2]
            h = small.shape[0] - small.shape[0] % 2
            w = small.shape[1] - small.shape[1] % 2
            frame = np.ascontiguousarray(small[:h, :w])

        if self._on_placeholder:
            logger.info(
                "sc2_window_acquired",
                left=left,
                top=top,
                width=width,
                height=height,
            )
            self._on_placeholder = False
        return frame

    def _locate(self) -> tuple[int, int, int, int] | None:
        """定位 SC2 窗口区域；命中缓存便宜，否则限频全量搜索。

        2026-06-12 #4：改用 _select_window_rect（客户区优先）而非 _window_rect
        （外接矩形），去掉截屏范围中的标题栏 / 边框 / 空白。
        """
        # 缓存命中且窗口仍存活 → 直接读最新坐标（最小化时返回 None）
        if self._win is not None and _window_alive(self._win):
            self._ensure_topmost()
            return _select_window_rect(self._win)

        # 缓存失效 → 限频全量搜索（无窗口时避免每帧扫描所有窗口）
        now = time.monotonic()
        if now - self._last_search < _SEARCH_INTERVAL:
            return None
        self._last_search = now

        # 2026-06-12 M1：透传 pid_filter 给 _find_sc2_window（多实例分流）
        self._win = _find_sc2_window(pid_filter=self._pid_filter)
        if self._win is None:
            return None
        self._ensure_topmost()
        return _select_window_rect(self._win)

    def _ensure_topmost(self) -> None:
        """周期把 SC2 窗口置顶（防遮挡污染视频流）。限频 _TOPMOST_INTERVAL。"""
        if not _SC2_TOPMOST or self._win is None:
            return
        now = time.monotonic()
        if now - self._last_topmost < _TOPMOST_INTERVAL:
            return
        self._last_topmost = now
        hwnd = getattr(self._win, "_hWnd", None)
        if hwnd is not None:
            _set_window_topmost(hwnd)

    def _get_placeholder(self) -> npt.NDArray[np.uint8]:
        if self._placeholder is None:
            self._placeholder = _make_placeholder_bgra()
        return self._placeholder


# ---------------------------------------------------------------------------
# aiortc VideoStreamTrack
# ---------------------------------------------------------------------------


class SC2ScreenTrack(VideoStreamTrack):
    """aiortc VideoStreamTrack：每帧从 SC2ScreenCapture.grab_frame() 取图。

    截屏走自带的单线程 executor —— 绝不用默认 executor，否则会跟 HTTP 静态
    服务、game_process status pump 抢同一批线程；截屏一忙，整站请求排不上、
    网页无响应。帧率由 next_timestamp() 节流到 _TARGET_FPS。
    """

    kind = "video"

    def __init__(self, capture: SC2ScreenCapture) -> None:
        super().__init__()
        self._capture = capture
        # 专用单线程 executor：截屏与 HTTP / status pump 彻底隔离
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sc2-video")
        self._vts: int | None = None
        self._vstart: float = 0.0
        self._stopped = False
        self._frame_count = 0

    async def next_timestamp(self) -> tuple[int, Fraction]:
        """按 _TARGET_FPS 节流帧率（覆盖 aiortc 默认 30fps）。"""
        step = _VIDEO_CLOCK_RATE // _TARGET_FPS
        if self._vts is None:
            self._vstart = time.time()
            self._vts = 0
        else:
            self._vts += step
            wait = self._vstart + self._vts / _VIDEO_CLOCK_RATE - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
        return self._vts, Fraction(1, _VIDEO_CLOCK_RATE)

    async def recv(self) -> av.VideoFrame:
        """产出下一帧（供 aiortc RTP 编码器消费）。"""
        if self._stopped:
            raise MediaStreamError
        pts, time_base = await self.next_timestamp()
        # 抓屏走专属 executor —— 不碰默认 executor（见 class docstring）。
        loop = asyncio.get_running_loop()
        t0 = time.monotonic()
        bgra = await loop.run_in_executor(self._executor, self._capture.grab_frame)
        grab_ms = (time.monotonic() - t0) * 1000.0
        frame = av.VideoFrame.from_ndarray(bgra, format="bgra")
        frame.pts = pts
        frame.time_base = time_base
        # 诊断：周期性记录视频帧 —— recv() 一停日志就断，能定位视频轨何时卡。
        self._frame_count += 1
        if self._frame_count <= 3 or self._frame_count % 150 == 0:
            logger.info(
                "sc2_video_frame",
                frame=self._frame_count,
                size=f"{bgra.shape[1]}x{bgra.shape[0]}",
                grab_ms=round(grab_ms, 1),
            )
        return frame

    def stop(self) -> None:
        """停止采集，释放 executor（aiortc 在 pc 关闭时调用）。"""
        super().stop()
        if self._stopped:
            return
        self._stopped = True
        self._executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# 系统回环音频采集（隔离子进程，见 audio_capture.py / audio_grab.py）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# aiortc 音频轨
# ---------------------------------------------------------------------------


class SC2AudioTrack(MediaStreamTrack):
    """aiortc 音频轨：每 20ms 从 SystemAudioGrabber 拉一帧系统声音（SC2 游戏声）。

    采集在隔离子进程里跑（grabber 管，见 audio_capture.py）。本轨只从 grabber
    的 ring buffer 取 PCM —— grabber.read() 不阻塞（缺数据补静音），故实时性完全
    由这里的 wall-clock 节流保证（每 20ms 一帧，按采样点对齐）。run_in_executor
    只是不在事件循环线程里做 numpy 拷贝。
    """

    kind = "audio"

    # 诊断日志节流：前若干帧每帧记，之后每 N 帧记一次
    _DIAG_FIRST_FRAMES = 5
    _DIAG_EVERY = 250  # 250 帧 = 5 秒

    def __init__(
        self,
        grabber: SystemAudioGrabber,
        *,
        release_fn: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._grabber = grabber
        # 2026-06-12 M1：共享 grabber 时通过引用计数释放，不直接 stop()。
        # None = 老行为（独享 grabber，stop() 直接停它，单 PC 场景保持不变）。
        self._release_fn = release_fn
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sc2-audio")
        self._timestamp = 0
        self._start: float | None = None
        self._closed = False
        self._frame_count = 0
        self._log = logger.bind(component="sc2_audio_track")

    async def recv(self) -> av.AudioFrame:
        """产出下一个 20ms 音频帧（供 aiortc Opus 编码器消费）。

        首帧 + 周期性记录帧峰值，落盘后一眼区分：recv 没被调用（轨没协商）/
        一直静音（采集没声音 / 设备不对）/ 有幅度（OK）。
        """
        if self._closed:
            raise MediaStreamError

        # wall-clock 节流：grabber.read() 不阻塞 → 全靠这里按采样点对齐实时
        if self._start is None:
            self._start = time.time()
        else:
            self._timestamp += _AUDIO_FRAME_SAMPLES
            wait = self._start + self._timestamp / _AUDIO_SAMPLE_RATE - time.time()
            if wait > 0:
                await asyncio.sleep(wait)

        loop = asyncio.get_running_loop()
        # data: (samples, 2) int16，缺数据时是补好的静音
        data = await loop.run_in_executor(self._executor, self._grabber.read, _AUDIO_FRAME_SAMPLES)

        self._frame_count += 1
        if (
            self._frame_count <= self._DIAG_FIRST_FRAMES
            or self._frame_count % self._DIAG_EVERY == 0
        ):
            # 诊断 peak 用**采集原值**（音量衰减前）—— 判"采集有没有声音"，不受 gain 影响
            peak = int(np.max(np.abs(data))) if data.size else 0
            self._log.info(
                "sc2_audio_frame",
                frame=self._frame_count,
                peak=peak,
                silent=peak == 0,
                restarts=self._grabber.restart_count,
            )

        # 音量衰减（默认 0.5）：诊断之后、建帧之前对**送出的帧**做衰减。
        # int32 中间态防溢出 → 乘增益 → clip 回 int16。
        if _AUDIO_GAIN != 1.0 and data.size:
            data = np.clip(data.astype(np.int32) * _AUDIO_GAIN, -32768, 32767).astype(np.int16)

        # (samples, 2) int16 行主序展平 = [L,R,L,R,...] 交织 → packed s16 stereo
        frame = av.AudioFrame.from_ndarray(
            np.ascontiguousarray(data).reshape(1, -1), format="s16", layout="stereo"
        )
        frame.pts = self._timestamp
        frame.sample_rate = _AUDIO_SAMPLE_RATE
        frame.time_base = Fraction(1, _AUDIO_SAMPLE_RATE)
        return frame

    def stop(self) -> None:
        """停止采集 + 释放 executor（aiortc 关 pc 时调用）。

        共享 grabber 模式（release_fn 非 None）：通过引用计数通知 WebRtcManager
        释放，不直接 stop grabber（可能还有其他 PC 在用）。
        独享 grabber 模式（release_fn=None，老行为）：直接 stop grabber。
        """
        super().stop()
        if self._closed:
            return
        self._closed = True
        if self._release_fn is not None:
            # 共享 grabber：通知管理器减引用计数，最后一个 PC 关闭时 stop()
            with contextlib.suppress(Exception):
                self._release_fn()
        else:
            # 独享 grabber（向后兼容单 PC 路径）
            with contextlib.suppress(Exception):
                self._grabber.stop()
        self._executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# WebRtcManager：管理活跃 PeerConnection 集合
# ---------------------------------------------------------------------------


class WebRtcManager:
    """管理所有活跃 WebRTC PeerConnection。

    - 每个 offer 建一个 PeerConnection + SC2ScreenTrack（视频）+ SC2AudioTrack（音频）
    - connectionstatechange = failed/closed/disconnected → 自动关闭 + 释放
    - 关闭时显式 stop() 所有 track，释放 mss 截屏 + 音频采集子进程
    - service 关闭时 close_all() 清理全部
    """

    def __init__(self, turn_config: TurnConfig | None = None) -> None:
        # 2026-06-12 M1：改 dict[player_id, ...] 支持多玩家并行 WebRTC
        self._pcs: dict[str, RTCPeerConnection] = {}
        self._tracks: dict[str, list[MediaStreamTrack]] = {}
        # 阶段1：TURN 中继配置（None=无 TURN，纯 P2P/host 候选，行为不变）
        self._turn_config = turn_config
        self._log = logger.bind(component="webrtc_manager")

    def _ice_servers(self) -> list[RTCIceServer]:
        """每个 offer 现签短期凭证组 iceServers。无 turn_config → 空（host 候选，gather 即 complete）。"""
        if self._turn_config is None:
            return list(_ICE_SERVERS)
        from vibecraft.server.turn_config import build_ice_servers

        return [
            RTCIceServer(urls=s["urls"], username=s.get("username"), credential=s.get("credential"))
            for s in build_ice_servers(self._turn_config)
        ]

    async def handle_offer(
        self,
        sdp: str,
        type_: str,
        player_id: str = "default",
        sc2_pid: int | None = None,
    ) -> tuple[str, str]:
        """处理 WebRTC offer，返回 (answer_sdp, "answer")。

        :param sdp: 客户端 offer SDP
        :param type_: 应为 "offer"
        :param player_id: 玩家标识（2026-06-12 M1 新增，默认 "default" 保持单人路径不变）
        :param sc2_pid: 对应 SC2 实例 PID，None = 不过滤（单人路径行为不变）
        :returns: (answer SDP, "answer")

        2026-06-12 M1：新 offer 只 supersede **同一 player_id** 的旧 PC，
        不影响其他玩家的 PeerConnection。
        """
        # 旧 PC 处理：只关闭同一 player_id 的旧 PC（ICE 从未连通会永远卡住）。
        # 不同 player_id 互不干扰，多玩家可并存。
        if player_id in self._pcs:
            self._log.info("webrtc_superseding_old_pc", player_id=player_id)
            await self._close_player_pc(player_id)

        # 视频：按 SC2 窗口 PID 过滤截屏（多实例各抓自己的窗口）。
        # sc2_pid=None = 老行为（任意匹配，单人路径不变）。
        video_track = SC2ScreenTrack(SC2ScreenCapture(pid_filter=sc2_pid))

        # 音频（2026-06-13 任务 #516）：每条音频轨**独享**一个 grabber 子进程，
        # 按 sc2_pid process loopback 只采本局 SC2 的声音 —— 多人局两手机各听
        # 各的；独享也从结构上消灭了旧共享 grabber 的多消费者分帧破音。
        # sc2_pid=None（单人未知 PID）→ 子进程回退整机 device loopback 老行为。
        # grabber.start() 只是后台拉起子进程，不阻塞 handle_offer；
        # 即便采集 native 崩，也只死子进程并自动重启，server 不受影响。
        audio_track: SC2AudioTrack | None = None
        if _AUDIO_ENABLED:
            audio_grabber = SystemAudioGrabber(pid=sc2_pid)
            audio_grabber.start()
            audio_track = SC2AudioTrack(audio_grabber, release_fn=audio_grabber.stop)

        has_audio_mline = "m=audio" in sdp
        has_video_mline = "m=video" in sdp

        # 阶段1：有 turn_config 则现签短期凭证组 iceServers（含 turns:443 中继兜底）；
        # 无则空（只 host 候选，gather 即 complete，行为不变）。coturn 可达时 relay
        # 候选亚秒返回；仅 coturn 不可达才吃满 gather cap（评审已记此权衡）。
        config = RTCConfiguration(iceServers=self._ice_servers())
        pc = RTCPeerConnection(configuration=config)
        self._pcs[player_id] = pc
        tracks: list[MediaStreamTrack] = [video_track]
        if audio_track is not None:
            tracks.append(audio_track)
        self._tracks[player_id] = tracks
        self._log.info(
            "webrtc_pc_created",
            player_id=player_id,
            total=len(self._pcs),
            offer_has_audio=has_audio_mline,
            offer_has_video=has_video_mline,
            audio_enabled=_AUDIO_ENABLED,
        )

        pc.addTrack(video_track)
        if audio_track is not None:
            pc.addTrack(audio_track)

        # 连接状态监控 — 断开时自动清理（只关自己，防 supersede 后误关新 PC）
        @pc.on("connectionstatechange")
        async def _on_state_change() -> None:
            state = pc.connectionState
            self._log.info("webrtc_connection_state", state=state, player_id=player_id)
            # 2026-06-12 M1：只关掉自己（防 supersede 后旧 PC 回调误关新 PC）
            if state in ("failed", "closed", "disconnected") and self._pcs.get(player_id) is pc:
                await self._close_player_pc(player_id)

        # 信令交换
        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=type_))
        # 诊断:远端(手机)offer 的 ICE 候选。没有 100.94.x → 手机不在 tailnet → 视频必 fail。
        self._log.info("webrtc_remote_candidates", candidates=_summarize_candidates(sdp))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        # 等 ICE gathering 完成（把所有 candidate 内联进 SDP，关闭 Trickle ICE）
        await self._wait_ice_gathering(pc)

        # answer SDP 里是否回了音频轨 —— offer 有 m=audio 但 answer 没有，
        # 说明协商在 aiortc 侧出了问题（应为正常情况，留作下次排查锚点）。
        answer_sdp = pc.localDescription.sdp
        self._log.info(
            "webrtc_answer_ready",
            ice_state=pc.iceGatheringState,
            answer_has_audio="m=audio" in answer_sdp,
            answer_has_video="m=video" in answer_sdp,
            # 诊断:本地(server)候选。应含 Tailnet 100.94.x + LAN 192.168.x(host)。
            local_candidates=_summarize_candidates(answer_sdp),
        )

        return answer_sdp, pc.localDescription.type

    async def _wait_ice_gathering(self, pc: RTCPeerConnection, ice_timeout: float = 5.0) -> None:
        """等到 ICE gathering 完成（iceGatheringState == 'complete'）。

        超时后继续，SDP 里可能缺部分 candidate，大多数局域网场景仍能连。
        """
        if pc.iceGatheringState == "complete":
            return

        loop = asyncio.get_event_loop()
        done: asyncio.Future[None] = loop.create_future()

        @pc.on("icegatheringstatechange")
        def _on_gathering() -> None:
            if pc.iceGatheringState == "complete" and not done.done():
                done.set_result(None)

        try:
            await asyncio.wait_for(done, timeout=ice_timeout)
        except TimeoutError:
            self._log.warning("webrtc_ice_gathering_timeout", ice_timeout=ice_timeout)

    async def _close_player_pc(self, player_id: str) -> None:
        """关闭指定玩家的 PeerConnection，stop 其 track 并从字典移除（2026-06-12 M1）。"""
        pc = self._pcs.pop(player_id, None)
        for track in self._tracks.pop(player_id, []):
            with contextlib.suppress(Exception):
                track.stop()
        if pc is not None:
            try:
                await pc.close()
            except Exception:
                self._log.exception("webrtc_pc_close_error")
        self._log.info("webrtc_pc_closed", player_id=player_id, remaining=len(self._pcs))

    async def close_all(self) -> None:
        """关闭全部活跃 PeerConnection（server 关闭时调用）。"""
        pcs = list(self._pcs.values())  # 2026-06-12 M1：dict → .values()
        self._pcs.clear()
        for tracks in self._tracks.values():
            for track in tracks:
                with contextlib.suppress(Exception):
                    track.stop()
        self._tracks.clear()
        # track.stop() → release_fn() → 各轨独享的 grabber.stop()（#516 后无共享池）
        for pc in pcs:
            with contextlib.suppress(Exception):
                await pc.close()
        if pcs:
            self._log.info("webrtc_all_closed", count=len(pcs))

    async def close_player(self, player_id: str) -> None:
        """关闭指定玩家的 PeerConnection（玩家断线时调用，2026-06-12 M1）。"""
        if player_id in self._pcs:
            await self._close_player_pc(player_id)


# ---------------------------------------------------------------------------
# 轻量 asyncio HTTP 服务（信令端点）
# ---------------------------------------------------------------------------

# 最大请求体大小（SDP 通常 < 8KB，设 64KB 防止恶意请求）
_MAX_BODY = 65536

_CORS_HEADERS = (
    "Access-Control-Allow-Origin: *\r\n"
    "Access-Control-Allow-Methods: POST, OPTIONS\r\n"
    "Access-Control-Allow-Headers: Content-Type\r\n"
)


async def _read_http_request(
    reader: asyncio.StreamReader,
) -> tuple[str, str, dict[str, str], bytes]:
    """从 asyncio StreamReader 读取 HTTP/1.1 请求。

    返回 (method, path, headers_dict, body)。
    简化实现：只支持 POST/OPTIONS，不支持 chunked encoding。
    """
    # 读请求行
    line = await reader.readline()
    request_line = line.decode("latin-1").strip()
    parts = request_line.split(" ")
    if len(parts) < 2:
        return "", "", {}, b""

    method = parts[0].upper()
    path = parts[1]

    # 读请求头
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        decoded = line.decode("latin-1").strip()
        if not decoded:
            break  # 空行 = 头结束
        if ":" in decoded:
            k, _, v = decoded.partition(":")
            headers[k.strip().lower()] = v.strip()

    # 读请求体。reader.read(n) 不保证读满 n 字节 —— HTTP body 跨多个 TCP
    # 段时只会读到半个 → JSON 解析失败 → 误报 400。readexactly 等齐 n 字节。
    body = b""
    content_length = int(headers.get("content-length", "0"))
    if content_length > 0:
        to_read = min(content_length, _MAX_BODY)
        try:
            body = await reader.readexactly(to_read)
        except asyncio.IncompleteReadError as exc:
            # 连接提前断 → 留 partial，交给上层 JSON 解析自然报 400
            body = exc.partial

    return method, path, headers, body


def _http_response(
    status: int,
    status_text: str,
    content_type: str,
    body: bytes,
    extra_headers: str = "",
) -> bytes:
    """构造 HTTP/1.1 响应字节串。"""
    return (
        f"HTTP/1.1 {status} {status_text}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"{_CORS_HEADERS}"
        f"{extra_headers}"
        "Connection: close\r\n"
        "\r\n"
    ).encode() + body


class WebRtcSignalServer:
    """POST /webrtc/offer 的轻量 asyncio HTTP 服务。

    监听在独立端口（默认 main_port + 1），避免与 websockets 的 process_request
    体系冲突（websockets 不缓冲 POST body，读取需访问底层 transport，升级风险高）。

    前端通过 window.location.port + 1 计算信令端口，或由服务端推送端口号。
    """

    def __init__(self, manager: WebRtcManager, port: int, host: str = "0.0.0.0") -> None:
        self._manager = manager
        self._port = port
        self._host = host
        self._server: asyncio.AbstractServer | None = None
        self._log = logger.bind(component="webrtc_signal", port=port, host=host)

    async def start(self) -> None:
        """启动 asyncio TCP 服务。"""
        self._server = await asyncio.start_server(
            self._handle_connection,
            host=self._host,
            port=self._port,
        )
        self._log.info("webrtc_signal_server_started")

    async def stop(self) -> None:
        """停止服务。"""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._log.info("webrtc_signal_server_stopped")

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """处理单个 TCP 连接。"""
        peer = writer.get_extra_info("peername")
        log = self._log.bind(remote=str(peer))
        try:
            method, path, _headers, body = await asyncio.wait_for(
                _read_http_request(reader), timeout=10.0
            )

            # CORS preflight
            if method == "OPTIONS" and path == "/webrtc/offer":
                writer.write(_http_response(204, "No Content", "text/plain", b""))
                await writer.drain()
                return

            if method != "POST" or path != "/webrtc/offer":
                resp = _http_response(404, "Not Found", "text/plain", b"Not Found")
                writer.write(resp)
                await writer.drain()
                return

            # 解析请求体
            try:
                payload: dict[str, Any] = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                log.warning("webrtc_offer_bad_json")
                writer.write(
                    _http_response(
                        400,
                        "Bad Request",
                        "application/json",
                        json.dumps({"error": "invalid JSON"}).encode(),
                    )
                )
                await writer.drain()
                return

            sdp = payload.get("sdp", "")
            type_ = payload.get("type", "")
            if not sdp or type_ != "offer":
                log.warning("webrtc_offer_missing_fields", type_=type_)
                writer.write(
                    _http_response(
                        400,
                        "Bad Request",
                        "application/json",
                        json.dumps({"error": "sdp and type='offer' required"}).encode(),
                    )
                )
                await writer.drain()
                return

            log.info("webrtc_offer_received")

            # 处理 offer → 生成 answer
            answer_sdp, answer_type = await self._manager.handle_offer(sdp, type_)
            resp_body = json.dumps({"sdp": answer_sdp, "type": answer_type}).encode()
            writer.write(_http_response(200, "OK", "application/json", resp_body))
            await writer.drain()
            log.info("webrtc_answer_sent")

        except TimeoutError:
            log.warning("webrtc_signal_timeout")
        except Exception:
            log.exception("webrtc_signal_error")
            try:
                writer.write(
                    _http_response(
                        500,
                        "Internal Server Error",
                        "application/json",
                        json.dumps({"error": "internal error"}).encode(),
                    )
                )
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def make_webrtc_manager(turn_config: TurnConfig | None = None) -> WebRtcManager:
    """创建 WebRtcManager 实例（由 BotService 持有）。turn_config=None → 无 TURN（行为不变）。"""
    return WebRtcManager(turn_config=turn_config)
